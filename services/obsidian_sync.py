"""Obsidian sync — write a daily markdown log to a local vault folder.

Generates a clean, front-matter'd `YYYY-MM-DD.md` from today's ASFA data
(habits, trading, spending, insights, notes) and writes it to
OBSIDIAN_VAULT_PATH. Every data source and the file write are best-effort: a
DB hiccup just yields an empty section, and a read-only/cloud filesystem
(Railway) returns a structured error instead of crashing the scheduler/route.

NOTE: this writes to the *local* filesystem, so it only truly syncs when ASFA
runs on your Mac. A future GitHub/Dropbox bridge can replace the file write
without changing callers.
"""
import logging
import os
from datetime import datetime

import database as db
from services import insights
from services.bots import get_bots_health, get_trading_activity

logger = logging.getLogger("asfa.obsidian")

OBSIDIAN_VAULT_PATH = os.getenv(
    "OBSIDIAN_VAULT_PATH", os.path.expanduser("~/Obsidian/ASFA-Logs"))


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _habits_section(date):
    lines = ["## Habits"]
    try:
        habits = db.get_habits(1)
        today = next((h for h in habits if h.get("date") == date), {})
    except Exception:
        today = {}
    water_ml = today.get("water_ml") or 0
    sleep = today.get("sleep_hours") or 0
    try:
        count = db.get_hydration_count(date)
    except Exception:
        count = 0
    mark = "✓" if water_ml >= 2000 else "·"
    if count:
        lines.append(f"- Water: {mark} {water_ml}ml across {count} log{'s' if count != 1 else ''}")
    else:
        lines.append(f"- Water: {mark} {water_ml}ml")
    lines.append(f"- Sleep: {sleep} hours" if sleep else "- Sleep: not logged")
    try:
        taken = db.get_supplements_today(date)
        total = len(db.SUPPLEMENTS)
        mark = f"✓ all {total}" if len(taken) >= total else f"{len(taken)}/{total}"
        lines.append(f"- Supplements: {mark} taken")
    except Exception:
        pass
    return "\n".join(lines)


def _trading_section():
    lines = ["## Trading"]
    try:
        health = get_bots_health()
        activity = get_trading_activity()
    except Exception:
        health, activity = {"bots": []}, {}
    bots = {b.get("key"): b for b in (health.get("bots") or [])}

    scanner = bots.get("scanner")
    if scanner:
        s = "online" if scanner.get("online") else "offline"
        extra = scanner.get("last_signal") or (scanner.get("status") if scanner.get("online") else None)
        lines.append(f"- Stock Scanner: {s}" + (f" — {extra}" if extra else ""))

    crypto = bots.get("crypto")
    if crypto:
        s = "online" if crypto.get("online") else "offline"
        bits = []
        p = (activity or {}).get("portfolio") or {}
        if p.get("total_pnl_pct") is not None:
            bits.append(f"P&L {p.get('total_pnl_pct')}%")
        holdings = p.get("holdings")
        if isinstance(holdings, (list, dict)):
            n = len(holdings)
            bits.append(f"{n} holding{'s' if n != 1 else ''}")
        sig = (activity or {}).get("latest_signal")
        if sig and sig.get("symbol"):
            bits.append(f"latest {sig.get('symbol')} {sig.get('direction', '')}".strip())
        lines.append(f"- Crypto Bot: {s}" + (f" — {', '.join(bits)}" if bits else ""))

    if len(lines) == 1:
        lines.append("- No bot data available.")
    return "\n".join(lines)


def _spending_section(date):
    lines = ["## Spending"]
    try:
        spend = [s for s in db.get_spending(1) if s.get("date") == date]
    except Exception:
        spend = []
    total = round(sum(float(s.get("amount") or 0) for s in spend), 2)
    lines.append(f"- Total today: £{total:.2f}")
    if spend:
        by = {}
        for s in spend:
            c = (s.get("category") or "other").lower()
            by[c] = round(by.get(c, 0) + float(s.get("amount") or 0), 2)
        cats = ", ".join(f"{k} £{v:.2f}" for k, v in sorted(by.items(), key=lambda x: -x[1]))
        lines.append(f"- Categories: {cats}")
    else:
        lines.append("- Nothing logged.")
    return "\n".join(lines)


def _insights_section():
    lines = ["## Insights"]
    try:
        ins = insights.generate_insights() or []
    except Exception:
        ins = []
    if ins:
        lines.extend(f"- {i}" for i in ins[:2])
    else:
        lines.append("- No standout patterns today.")
    return "\n".join(lines)


def _notes_section(date):
    lines = ["## Notes"]
    try:
        notes = db.get_voice_notes(date)
    except Exception:
        notes = []
    if notes:
        for n in notes:
            content = (n.get("content") or "").strip()
            if content:
                lines.append(f"- {content}")
    if len(lines) == 1:
        lines.append("_No notes captured._")
    return "\n".join(lines)


def build_markdown(date=None):
    date = date or _today()
    d = datetime.strptime(date, "%Y-%m-%d")
    front_matter = (
        "---\n"
        f"date: {date}\n"
        f"day: {d.strftime('%A')}\n"
        "type: daily-log\n"
        "---\n"
    )
    body = "\n\n".join([
        f"# ASFA Daily Log — {d.strftime('%A, %B %d, %Y')}",
        _habits_section(date),
        _trading_section(),
        _spending_section(date),
        _insights_section(),
        _notes_section(date),
    ])
    return front_matter + "\n" + body + "\n"


def sync_to_obsidian(user_id=None, date=None):
    """Write today's markdown log to the vault. Returns a structured result;
    never raises (safe for the scheduler and the manual route)."""
    date = date or _today()
    try:
        content = build_markdown(date)
    except Exception as e:
        logger.error("Obsidian markdown build failed: %s", e)
        return {"status": "error", "error": str(e)}

    path = os.path.join(OBSIDIAN_VAULT_PATH, f"{date}.md")
    try:
        os.makedirs(OBSIDIAN_VAULT_PATH, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        # Cloud/read-only filesystem (e.g. Railway) — degrade gracefully.
        logger.warning("Obsidian write failed (%s): %s", OBSIDIAN_VAULT_PATH, e)
        return {
            "status": "error", "error": str(e), "path": path,
            "message": "Couldn't write locally — run ASFA on your Mac to sync to Obsidian.",
        }
    logger.info("Obsidian sync wrote %s", path)
    return {"status": "synced", "file": f"{date}.md", "path": path}
