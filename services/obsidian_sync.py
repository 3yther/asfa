"""Obsidian sync — mirror ASFA's agent ecosystem + second brain into a vault.

Writes a structured `asfa/` tree into the local Obsidian vault:

    <vault>/asfa/
      agents/<id>.md       one profile per agent (regenerated every sync)
      daily-logs/<date>.md  one dated log per day (regenerated for *today*)
      summary.md            live dashboard snapshot (regenerated every sync)
      decision-log.md       \
      market-context.md      } "second brain" notes — SEEDED ONCE, never
      learnings.md          /  overwritten, so your hand edits are preserved
      goals.md             /

Design rules:
  * Everything is driven by *real* data (the agents DB, mission/battle tables,
    live bot P&L, habits). We never fabricate metrics — a missing source just
    yields an empty/"unavailable" line.
  * The four narrative notes are human-maintained. We only create them if they
    don't already exist; an existing file is left untouched.
  * Every read and the file writes are best-effort: a DB hiccup degrades to an
    empty section, and a read-only/cloud filesystem (Railway) returns a
    structured error instead of crashing the scheduler/route.

NOTE: this writes to the *local* filesystem, so it only truly syncs when ASFA
runs on your Mac. A future GitHub/Dropbox bridge can replace the file write
without changing callers.
"""
import logging
import os
from datetime import datetime, timedelta

import database as db
from services.bots import get_trading_activity

logger = logging.getLogger("asfa.obsidian")

# Vault root. Default to ~/Obsidian; the ASFA tree lives under <root>/asfa.
VAULT_ROOT = os.path.expanduser(os.getenv("OBSIDIAN_VAULT_PATH", "~/Obsidian"))
ASFA_DIR = os.path.join(VAULT_ROOT, "asfa")
AGENTS_DIR = os.path.join(ASFA_DIR, "agents")
DAILY_DIR = os.path.join(ASFA_DIR, "daily-logs")

# Exported for app.py (route display) — kept as the vault root for continuity.
OBSIDIAN_VAULT_PATH = VAULT_ROOT

# How a stored status maps to a display label.
_STATUS_LABEL = {"active": "Active", "idle": "Idle", "locked": "Locked"}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _xp_max_for_level(level: int) -> int:
    """Mirror of database._xp_max_for_level — XP to clear a level."""
    return (int(level) + 1) * 100


def _lifetime_xp(agent: dict) -> int:
    """Derived total XP earned: every level cleared plus current progress.

    sum_{l=0}^{level-1} (l+1)*100  ==  100 * level*(level+1)/2
    """
    lvl = int(agent.get("level") or 0)
    cleared = 100 * lvl * (lvl + 1) // 2
    return cleared + int(agent.get("xp") or 0)


def _ts_date(ts) -> str:
    """Best-effort 'YYYY-MM-DD' from a log timestamp (str or datetime)."""
    if ts is None:
        return ""
    return str(ts)[:10]


def _pct_to_next(agent: dict) -> int:
    xp_max = int(agent.get("xp_max") or 0)
    if xp_max <= 0:
        return 0
    return round(int(agent.get("xp") or 0) / xp_max * 100)


# ── Shared data sources ──────────────────────────────────────────────────────

def fetch_agents() -> list:
    """All agents with current XP/level/status (ordered by the seed roster)."""
    try:
        return db.get_agents() or []
    except Exception as e:
        logger.warning("fetch_agents failed: %s", e)
        return []


def _agent_logs(agent_id: str, limit: int = 60) -> list:
    try:
        return db.get_agent_log(agent_id, limit) or []
    except Exception:
        return []


def _trading() -> dict:
    """Live trading snapshot; {} when the bots are unreachable."""
    try:
        return get_trading_activity() or {}
    except Exception as e:
        logger.warning("trading fetch failed: %s", e)
        return {}


def _pnl(activity: dict) -> dict:
    """Extract {value, pct, online} dollar P&L from a trading snapshot."""
    p = (activity or {}).get("portfolio") or {}
    return {
        "online": bool((activity or {}).get("online")),
        "value": p.get("total_pnl"),
        "pct": p.get("total_pnl_pct"),
        "holdings": p.get("holdings"),
    }


def _habits(date: str) -> dict:
    out = {"water_ml": 0, "water_logs": 0, "supp_taken": 0, "supp_total": 0,
           "sleep": 0}
    try:
        habits = db.get_habits(1)
        today = next((h for h in habits if h.get("date") == date), {})
        out["water_ml"] = today.get("water_ml") or 0
        out["sleep"] = today.get("sleep_hours") or 0
    except Exception:
        pass
    try:
        out["water_logs"] = db.get_hydration_count(date) or 0
    except Exception:
        pass
    try:
        taken = db.get_supplements_today(date)
        out["supp_taken"] = len(taken) if taken else 0
        out["supp_total"] = len(db.SUPPLEMENTS)
    except Exception:
        pass
    return out


def _missions_today() -> list:
    try:
        return db.get_today_missions() or []
    except Exception:
        return []


def _alerts(agents: list, habits: dict, trading_online: bool) -> list:
    """Re-derive Mission Control's attention list from real state (max 3)."""
    out = []
    if habits["supp_total"] and habits["supp_taken"] < habits["supp_total"]:
        out.append("Supplements not logged today "
                   f"({habits['supp_taken']}/{habits['supp_total']})")
    if habits["water_ml"] < 500:
        out.append(f"Water intake low ({habits['water_ml']}/2000ml)")
    for a in agents:
        if a.get("status") == "locked":
            continue
        if _pct_to_next(a) >= 90:
            out.append(f"{a['name']} ready to level up")
            break
    if not trading_online:
        out.append("Trading bots offline / deployment issue detected")
    return out[:3]


# ── Agent profiles ───────────────────────────────────────────────────────────

def build_agent_profile(agent: dict) -> str:
    """One agent's note. Only fields we actually track — no invented stats."""
    name = agent.get("name", agent.get("id"))
    icon = agent.get("icon", "")
    status = _STATUS_LABEL.get(agent.get("status"), str(agent.get("status")))
    level = int(agent.get("level") or 0)
    xp = int(agent.get("xp") or 0)
    xp_max = int(agent.get("xp_max") or _xp_max_for_level(level))
    pct = _pct_to_next(agent)
    won = int(agent.get("battles_won") or 0)
    lost = int(agent.get("battles_lost") or 0)
    total_battles = won + lost
    win_rate = round(won / total_battles * 100) if total_battles else None

    lines = [
        "---",
        f"agent: {agent.get('id')}",
        f"level: {level}",
        f"status: {agent.get('status')}",
        "type: agent-profile",
        "---",
        "",
        f"# {icon} {name}".strip(),
        f"**Role:** {agent.get('role') or '—'}  ",
        f"**Status:** {status}",
        "",
        "## Stats",
        f"- Level: {level}",
        f"- XP: {xp}/{xp_max} ({pct}% to next level)",
        f"- Lifetime XP: {_lifetime_xp(agent):,}",
        f"- Tasks Run: {agent.get('tasks_run') or 0}",
        f"- Findings: {agent.get('findings') or 0}",
        f"- Battles: {won} won / {lost} lost"
        + (f" ({win_rate}% win rate)" if win_rate is not None else ""),
        f"- Last Active: {agent.get('last_active') or '—'}",
        "",
        "## Activity Log",
    ]

    logs = _agent_logs(agent.get("id"), 20)
    if logs:
        for entry in logs:
            ts = _ts_date(entry.get("timestamp"))
            msg = (entry.get("message") or "").strip()
            xp_earned = int(entry.get("xp_earned") or 0)
            suffix = f" (+{xp_earned} XP)" if xp_earned else ""
            lines.append(f"- {ts}: {msg}{suffix}")
    else:
        lines.append("- _No activity logged yet._")

    lines += ["", "## Notes"]
    notes = []
    if agent.get("status") == "locked":
        notes.append("- 🔒 Locked — awaiting activation.")
    elif pct >= 90:
        notes.append("- ⬆️ Ready to level up within reach.")
    if total_battles == 0 and agent.get("status") != "locked":
        notes.append("- No battles fought yet.")
    notes.append("- _Add your own observations here (preserved across syncs in "
                 "the daily logs, not this file)._")
    lines += notes
    lines.append("")
    return "\n".join(lines)


def build_agent_profiles() -> int:
    """Write agents/<id>.md for every agent. Returns the count written."""
    agents = fetch_agents()
    os.makedirs(AGENTS_DIR, exist_ok=True)
    n = 0
    for a in agents:
        path = os.path.join(AGENTS_DIR, f"{a.get('id')}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_agent_profile(a))
        n += 1
    return n


# ── Daily log ────────────────────────────────────────────────────────────────

def build_daily_log(date=None) -> str:
    date = date or _today()
    d = datetime.strptime(date, "%Y-%m-%d")
    agents = fetch_agents()
    activity = _trading()
    pnl = _pnl(activity)
    habits = _habits(date)
    missions = _missions_today()
    alerts = _alerts(agents, habits, pnl["online"])

    active = [a for a in agents if a.get("status") == "active"]
    deployed = [a for a in agents if a.get("status") != "locked"]

    # XP earned + level-ups today, summed from the agent logs.
    xp_today = 0
    levelups = 0
    agent_today = {}  # id -> (count, latest message)
    for a in agents:
        for entry in _agent_logs(a.get("id"), 60):
            if _ts_date(entry.get("timestamp")) != date:
                continue
            xp_today += int(entry.get("xp_earned") or 0)
            msg = entry.get("message") or ""
            if "Leveled up" in msg:
                levelups += 1
            cnt, _latest = agent_today.get(a.get("id"), (0, None))
            agent_today[a.get("id")] = (cnt + 1, agent_today.get(a.get("id"), (0, msg))[1] or msg)

    done_missions = [m for m in missions if m.get("completed")]

    # ── front matter + header ──
    fm = ("---\n"
          f"date: {date}\n"
          f"day: {d.strftime('%A')}\n"
          "type: daily-log\n"
          "---\n")

    lines = [
        f"# ASFA Daily Log — {d.strftime('%A, %B %d, %Y')}",
        "",
        f"**Active Agents:** {len(active)}/{len(deployed)} deployed  ",
        f"**Time Period:** 00:00 — 23:59 (local)",
        "",
        "## Daily Summary",
    ]
    if pnl["value"] is not None:
        pct = f" ({pnl['pct']}%)" if pnl.get("pct") is not None else ""
        lines.append(f"- **P&L:** ${pnl['value']}{pct}")
    else:
        lines.append("- **P&L:** unavailable (bots offline)")
    lines += [
        f"- **XP Earned Today:** {xp_today} XP across all agents",
        f"- **Level-Ups:** {levelups}",
        f"- **Missions Completed:** {len(done_missions)}/{len(missions)}",
        f"- **Alerts:** {len(alerts)}",
        "",
        "## Agent Activity",
    ]

    if active:
        for a in active:
            cnt, latest = agent_today.get(a.get("id"), (0, None))
            tail = f"{cnt} log{'s' if cnt != 1 else ''} today" if cnt else "on station, no new logs"
            detail = f" — {latest.strip()}" if (latest and cnt) else ""
            lines.append(f"- **{a['name']}** ({a.get('role')}): {tail}{detail}")
    else:
        lines.append("- No agents active.")

    idle = [a for a in agents if a.get("status") == "idle"]
    locked = [a for a in agents if a.get("status") == "locked"]
    lines += ["", "### Idle / Locked"]
    lines.append("- Idle: " + (", ".join(a["name"] for a in idle) if idle else "none"))
    lines.append("- Locked: " + (", ".join(a["name"] for a in locked) if locked else "none"))

    # ── Trading detail ──
    lines += ["", "## Trading"]
    if pnl["online"]:
        if pnl["value"] is not None:
            pct = f" ({pnl['pct']}%)" if pnl.get("pct") is not None else ""
            lines.append(f"- Crypto Bot P&L: ${pnl['value']}{pct}")
        holdings = pnl.get("holdings")
        if isinstance(holdings, (list, dict)):
            lines.append(f"- Holdings: {len(holdings)}")
        sig = (activity or {}).get("latest_signal")
        if sig and sig.get("symbol"):
            lines.append(f"- Latest signal: {sig.get('symbol')} "
                         f"{sig.get('direction', '')} @ {sig.get('price', '')}".rstrip())
    else:
        lines.append("- Bots offline — no live trading data this sync.")

    # ── Habits ──
    lines += ["", "## Habits"]
    wmark = "✓" if habits["water_ml"] >= 2000 else "·"
    lines.append(f"- Water: {wmark} {habits['water_ml']}/2000ml"
                 + (f" across {habits['water_logs']} logs" if habits["water_logs"] else ""))
    if habits["supp_total"]:
        smark = "✓" if habits["supp_taken"] >= habits["supp_total"] else "·"
        lines.append(f"- Supplements: {smark} {habits['supp_taken']}/{habits['supp_total']}")
    lines.append(f"- Sleep: {habits['sleep']}h" if habits["sleep"] else "- Sleep: not logged")

    # ── Missions ──
    lines += ["", "## Missions"]
    if missions:
        for m in missions:
            mark = "✓" if m.get("completed") else "○"
            reward = f" (+{m.get('xp_reward')} XP)" if m.get("xp_reward") else ""
            lines.append(f"- {mark} {m.get('title')}{reward}")
    else:
        lines.append("- No missions generated today.")

    # ── Alerts ──
    lines += ["", "## Alerts"]
    if alerts:
        lines.extend(f"- ⚠ {a}" for a in alerts)
    else:
        lines.append("- None — all clear.")

    lines.append("")
    return fm + "\n" + "\n".join(lines) + "\n"


# ── Live summary dashboard ───────────────────────────────────────────────────

def build_summary(agents=None) -> str:
    agents = agents if agents is not None else fetch_agents()
    activity = _trading()
    pnl = _pnl(activity)
    habits = _habits(_today())

    total_lifetime = sum(_lifetime_xp(a) for a in agents)
    active = [a for a in agents if a.get("status") == "active"]
    deployed = [a for a in agents if a.get("status") != "locked"]
    status_ok = pnl["online"]

    lines = [
        "---",
        f"updated: {_now_str()}",
        "type: summary",
        "---",
        "",
        "# ASFA Command Center — Live Dashboard",
        "",
        f"**Last Updated:** {_now_str()} (local)  ",
        f"**System Status:** {'✓ OPERATIONAL' if status_ok else '⚠ DEGRADED (bots offline)'}",
        "",
        "## Facility Metrics",
        "| Metric | Value |",
        "|--------|-------|",
        f"| **Active Agents** | {len(active)}/{len(deployed)} |",
        f"| **Total Lifetime XP** | {total_lifetime:,} |",
    ]
    if pnl["value"] is not None:
        pct = f" ({pnl['pct']}%)" if pnl.get("pct") is not None else ""
        lines.append(f"| **Current P&L** | ${pnl['value']}{pct} |")
    else:
        lines.append("| **Current P&L** | unavailable |")
    wflag = "" if habits["water_ml"] >= 2000 else " ⚠"
    sflag = "" if (habits["supp_total"] and habits["supp_taken"] >= habits["supp_total"]) else " ⚠"
    lines += [
        f"| **Water Intake** | {habits['water_ml']}/2000ml{wflag} |",
        f"| **Supplements** | {habits['supp_taken']}/{habits['supp_total']}{sflag} |",
        "",
        "## Agent Roster (by level)",
    ]

    roster = sorted(agents, key=lambda a: (int(a.get("level") or 0),
                                           int(a.get("xp") or 0)), reverse=True)
    for i, a in enumerate(roster, 1):
        status = _STATUS_LABEL.get(a.get("status"), str(a.get("status"))).upper()
        xp_max = int(a.get("xp_max") or _xp_max_for_level(int(a.get("level") or 0)))
        lines.append(
            f"{i}. **{a['name']}** — LV {a.get('level')} "
            f"({a.get('xp')}/{xp_max} XP) — {status} — {a.get('role')}")

    lines.append("")
    return "\n".join(lines) + "\n"


# ── Second-brain notes (seeded once, never overwritten) ──────────────────────

_SECOND_BRAIN = {
    "decision-log.md": (
        "---\ntype: decision-log\n---\n\n"
        "# Strategic Decision Log\n\n"
        "_A running journal of strategic choices. ASFA seeds this file once and "
        "never overwrites it — maintain it by hand._\n\n"
        "## Recent Decisions\n\n"
        "### " + datetime.now().strftime("%Y-%m-%d") + "\n"
        "- **Decision:** \n- **Reasoning:** \n- **Outcome:** \n- **Status:** \n\n"
        "## Patterns & Lessons\n- \n"
    ),
    "market-context.md": (
        "---\ntype: market-context\n---\n\n"
        "# Market Context & Trading Insights\n\n"
        "_Human-maintained note. The live P&L / signal numbers live in the daily "
        "logs and summary; capture qualitative context here._\n\n"
        "## Current Read\n- Crypto:\n- Stocks:\n\n"
        "## Opportunities\n1. \n\n"
        "## Recommendations\n- \n"
    ),
    "learnings.md": (
        "---\ntype: learnings\n---\n\n"
        "# Discoveries & Patterns\n\n"
        "_Things you've learned about the agent ecosystem, trading, and "
        "automation. Seeded once; yours to grow._\n\n"
        "## About the Agent Ecosystem\n- \n\n"
        "## About Trading\n- \n\n"
        "## About Automation\n- \n\n"
        "## Open Questions\n- \n"
    ),
    "goals.md": (
        "---\ntype: goals\n---\n\n"
        "# ASFA Roadmap & Targets\n\n"
        "_Seeded once; edit freely._\n\n"
        "## Short-term (next 30 days)\n- [ ] \n\n"
        "## Medium-term (next 90 days)\n- [ ] \n\n"
        "## Long-term (next year)\n- [ ] \n"
    ),
}


def _seed_second_brain() -> int:
    """Create the narrative notes only if they don't already exist."""
    os.makedirs(ASFA_DIR, exist_ok=True)
    created = 0
    for fname, content in _SECOND_BRAIN.items():
        path = os.path.join(ASFA_DIR, fname)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            created += 1
    return created


# Backwards-compat alias: the old API exposed build_markdown() for the daily log.
def build_markdown(date=None) -> str:
    return build_daily_log(date)


# ── Orchestration ────────────────────────────────────────────────────────────

def sync_to_obsidian(user_id=None, date=None):
    """Write the full ASFA vault tree. Returns a structured result; never raises
    (safe for the scheduler and the manual route)."""
    date = date or _today()
    try:
        os.makedirs(ASFA_DIR, exist_ok=True)
        os.makedirs(DAILY_DIR, exist_ok=True)

        n_agents = build_agent_profiles()

        daily_path = os.path.join(DAILY_DIR, f"{date}.md")
        with open(daily_path, "w", encoding="utf-8") as f:
            f.write(build_daily_log(date))

        summary_path = os.path.join(ASFA_DIR, "summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(build_summary())

        seeded = _seed_second_brain()
    except OSError as e:
        # Cloud/read-only filesystem (e.g. Railway) — degrade gracefully.
        logger.warning("Obsidian write failed (%s): %s", ASFA_DIR, e)
        return {
            "status": "error", "error": str(e), "path": ASFA_DIR,
            "message": "Couldn't write locally — run ASFA on your Mac to sync to Obsidian.",
        }
    except Exception as e:
        logger.error("Obsidian sync failed: %s", e)
        return {"status": "error", "error": str(e)}

    logger.info("Obsidian sync wrote %d agents + daily log + summary to %s",
                n_agents, ASFA_DIR)
    return {
        "status": "synced",
        "file": f"daily-logs/{date}.md",
        "path": ASFA_DIR,
        "agents": n_agents,
        "seeded_notes": seeded,
    }
