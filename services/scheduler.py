"""Background scheduler — smart notifications, Telegram pushes, trade alerts.

All jobs degrade gracefully: Telegram skipped if not configured, in-app
notifications always stored so the dashboard bell still works.
"""
import functools
import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import database as db
from services import alerts, insights, telegram_bot
from services.agent_intelligence import generate_all_diaries
from services.bots import get_bots_status, get_trading_activity
from services.heartbeat import run_heartbeat

logger = logging.getLogger(__name__)
_scheduler = None


def _notify(message: str, kind: str = "info", telegram: bool = True):
    try:
        db.add_notification(message, kind)
    except Exception as e:
        logger.error(f"notification store failed: {e}")
    if telegram:
        telegram_bot.send_message(message)


def audited(agent_id: str, action: str):
    """Phase 3: wrap a scheduler job so each run is timed and recorded in the
    agent audit trail + error budget. Never lets audit failures break the job."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            started = datetime.now()
            outcome = "success"
            try:
                return fn(*args, **kwargs)
            except Exception:
                outcome = "failure"
                raise
            finally:
                try:
                    dur_ms = int((datetime.now() - started).total_seconds() * 1000)
                    db.log_audit(agent_id, action, outcome,
                                 reason="scheduled job", duration_ms=dur_ms)
                    db.update_error_budget(agent_id, outcome == "success")
                    # Phase 4: energy economy — reward success, penalise failure.
                    db.update_energy(agent_id, 5 if outcome == "success" else -10)
                except Exception as e:
                    logger.error(f"audit log failed for {agent_id}.{action}: {e}")
        return wrapper
    return deco


# ── Jobs ───────────────────────────────────────────────────────────────────────

@audited("briefing", "morning_briefing")
def morning_briefing():
    from services.briefing import build_briefing
    try:
        b = build_briefing(force=True)
        _notify(f"☀️ Morning briefing ready.\n\n{b['text'][:3500]}", "briefing")
    except Exception as e:
        logger.error(f"morning briefing failed: {e}")
    # Proactive pattern check rides along with the morning briefing.
    proactive_check()


@audited("sentinel", "proactive_check")
def proactive_check():
    """Run predictive-alert rules and push anything concerning. Deduped so the
    same alert isn't re-sent multiple times in one day."""
    try:
        metrics = insights.gather_metrics()
        fired = insights.predictive_alerts(metrics)
    except Exception as e:
        logger.error(f"proactive check failed: {e}")
        return
    today = datetime.now().strftime("%Y-%m-%d")
    sent_key = f"alerts_sent_{today}"
    already = set((db.kv_get(sent_key) or "").split("||")) - {""}
    for a in fired:
        msg = a["message"]
        if msg in already:
            continue
        alerts.send_alert(msg, kind=a.get("kind", "alert"))
        already.add(msg)
    db.kv_set(sent_key, "||".join(already))


def bedtime_reminder():
    _notify("🌙 Bedtime. Wind down — 7h+ sleep keeps the streak (and tomorrow's score) alive.", "bedtime")


def market_open_reminder():
    _notify("📈 US market opens in 30 minutes. Check your bots.", "market")


@audited("reflection", "reflection_prompt")
def reflection_prompt():
    _notify("📝 End-of-day reflection: how was today, 1-10, and why? Log it in ASFA.", "reflection")


@audited("hydration", "water_check")
def water_check():
    """Daytime nudge if no water logged for 3+ hours."""
    now = datetime.now()
    if not (9 <= now.hour <= 21):
        return
    last = db.kv_get("last_water_ts")
    last_nudge = db.kv_get("last_water_nudge_ts")
    try:
        last_dt = datetime.fromisoformat(last) if last else None
        nudge_dt = datetime.fromisoformat(last_nudge) if last_nudge else None
    except ValueError:
        last_dt = nudge_dt = None
    hours_since = (now - last_dt).total_seconds() / 3600 if last_dt else 99
    nudge_gap = (now - nudge_dt).total_seconds() / 3600 if nudge_dt else 99
    if hours_since >= 3 and nudge_gap >= 3:
        db.kv_set("last_water_nudge_ts", now.isoformat())
        _notify("💧 No water logged in 3+ hours. Hydrate!", "water")


@audited("quant_bot", "poll_bot_trades")
def poll_bot_trades():
    """Every 5 min: diff bot positions vs last snapshot → trade alerts."""
    try:
        status = get_bots_status()
    except Exception as e:
        logger.error(f"bot poll failed: {e}")
        return
    snapshot = {}
    for key, b in status.items():
        if not b.get("online"):
            continue
        positions = b.get("positions") or b.get("open_positions") or []
        if isinstance(positions, list):
            snapshot[key] = sorted(
                p.get("symbol", str(p)) if isinstance(p, dict) else str(p) for p in positions
            )
        else:
            snapshot[key] = positions
    if not snapshot:
        return
    prev_raw = db.kv_get("bot_positions_snapshot")
    db.kv_set("bot_positions_snapshot", json.dumps(snapshot))
    if prev_raw is None:
        return
    try:
        prev = json.loads(prev_raw)
    except (TypeError, ValueError):
        return
    for key, current in snapshot.items():
        before = prev.get(key)
        if before is None or before == current:
            continue
        name = status[key].get("bot_name", key)
        if isinstance(current, list) and isinstance(before, list):
            opened = set(current) - set(before)
            closed = set(before) - set(current)
            parts = []
            if opened:
                parts.append(f"opened {', '.join(sorted(opened))}")
            if closed:
                parts.append(f"closed {', '.join(sorted(closed))}")
            if parts:
                _notify(f"🤖 {name} {' / '.join(parts)}", "trade")
        else:
            _notify(f"🤖 {name} positions changed: {before} → {current}", "trade")


def _build_daily_summary() -> str:
    """Compose the auto end-of-day summary: trades, habits met/missed,
    tomorrow's calendar, one actionable insight. Each section is safe."""
    from services.gcal import get_tomorrow_events

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"🛰️ ASFA Daily Summary — {datetime.now().strftime('%A, %d %B %Y')}", ""]

    # Today's trades / bot performance
    try:
        trading = get_trading_activity()
        if trading.get("online"):
            p = trading.get("portfolio") or {}
            sig = trading.get("latest_signal")
            lines.append("TRADING")
            if p:
                lines.append(f"  Equity ${p.get('equity','?')}  P&L ${p.get('total_pnl','?')} ({p.get('total_pnl_pct','?')}%)")
            if sig:
                lines.append(f"  Latest: {sig.get('symbol')} MSS {sig.get('direction')} @ {sig.get('price')} [{sig.get('regime')}]")
        else:
            lines.append("TRADING\n  Bots offline")
    except Exception as e:
        logger.warning("summary trading failed: %s", e)

    # Habits met / missed
    try:
        habits = db.get_habits(1)
        h = next((x for x in habits if x["date"] == today), {})
        water = h.get("water_ml", 0) or 0
        sleep = h.get("sleep_hours", 0) or 0
        lines.append("")
        lines.append("HABITS")
        lines.append(f"  Water {'✅' if water >= 2000 else '❌'} {water}/2000ml")
        lines.append(f"  Sleep {'✅' if sleep >= 7 else '❌'} {sleep}h")
    except Exception as e:
        logger.warning("summary habits failed: %s", e)

    # Tomorrow's calendar
    try:
        events = [e for e in get_tomorrow_events() if "error" not in e]
        lines.append("")
        lines.append("TOMORROW")
        if events:
            for e in events[:5]:
                lines.append(f"  {e.get('start','?')} — {e.get('title','?')}")
        else:
            lines.append("  Nothing scheduled")
    except Exception as e:
        logger.warning("summary calendar failed: %s", e)

    # One actionable insight
    try:
        ins = insights.generate_insights()
        if ins:
            lines.append("")
            lines.append("INSIGHT")
            lines.append(f"  💡 {ins[0]}")
    except Exception as e:
        logger.warning("summary insight failed: %s", e)

    return "\n".join(lines)


@audited("summary", "daily_summary")
def daily_summary():
    """21:00 UTC — auto-send the end-of-day summary across all channels.
    The user never has to ask for this."""
    try:
        body = _build_daily_summary()
        alerts.send_alert(body, kind="summary",
                          subject="ASFA Daily Summary", email=True)
        logger.info("Daily summary sent.")
    except Exception as e:
        logger.error(f"daily summary failed: {e}")


@audited("supplement", "supplement_reminder")
def supplement_reminder():
    """Nudge if any daily supplement is still unchecked (09:00 + 20:00 local)."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        taken = db.get_supplements_today(today)
    except Exception as e:
        logger.error(f"supplement reminder failed: {e}")
        return
    missing = [label for key, label in db.SUPPLEMENTS if key not in taken]
    if not missing:
        return
    alerts.send_alert(
        f"💊 Supplements — still to take today: {', '.join(missing)} "
        f"({len(taken)}/{len(db.SUPPLEMENTS)} done).",
        kind="supplement",
    )


@audited("obsidian", "obsidian_sync")
def obsidian_sync_job():
    """Midnight Obsidian sync: agent profiles, summary, and the daily log for the
    day that just ended (no-op on cloud filesystems)."""
    from services.obsidian_sync import sync_to_obsidian
    # Runs at 00:00, so the completed day is yesterday's date.
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        res = sync_to_obsidian(date=yesterday)
        if res.get("status") == "synced":
            logger.info("Obsidian daily sync (%s): %s agents → %s",
                        yesterday, res.get("agents"), res.get("path"))
        else:
            logger.warning("Obsidian daily sync skipped: %s", res.get("error"))
    except Exception as e:
        logger.error("obsidian sync job failed: %s", e)


@audited("backup", "db_backup")
def db_backup():
    """03:00 Europe/London — dump the prod Postgres DB and push it to the private
    backups repo. No-op on local SQLite. run_backup() never raises."""
    from services.backup import run_backup
    res = run_backup()
    if not res.get("ok"):
        logger.error("DB backup failed: %s", res.get("error"))
    elif res.get("method") == "skipped":
        logger.info("DB backup skipped: %s", res.get("reason"))
    else:
        logger.info("DB backup ok: %s — %s bytes, %s tables, %s rows",
                    res.get("file"), res.get("bytes"), res.get("tables"), res.get("rows"))


def csp_report_cleanup():
    """Daily — cap the CSP-report sink at 7 days. The /api/csp-report endpoint is
    public and only rate-limited, so the table would otherwise grow unbounded."""
    try:
        removed = db.purge_old_csp_reports(days=7)
        logger.info("csp report cleanup: removed %d rows older than 7 days", removed)
    except Exception as e:
        logger.error(f"csp report cleanup failed: {e}")


@audited("weekly_review", "weekly_review")
def weekly_review():
    from services.ai import generate_weekly_review
    try:
        review = generate_weekly_review()
        db.kv_set("weekly_review", json.dumps(
            {"date": datetime.now().strftime("%Y-%m-%d"), "content": review}))
        _notify(f"📊 Weekly review:\n\n{review[:3500]}", "review")
    except Exception as e:
        logger.error(f"weekly review failed: {e}")


@audited("summary", "weekly_digest")
def weekly_digest():
    """Tier 3 Part 5 — Sunday-evening cross-module Telegram digest. Idempotent
    (skips if one already went out in the last 24h)."""
    from services.digest import send_weekly_digest
    try:
        res = send_weekly_digest(force=False)
        logger.info("weekly digest: %s", res)
    except Exception as e:
        logger.error(f"weekly digest failed: {e}")


# ── Startup ────────────────────────────────────────────────────────────────────

def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="Europe/London", daemon=True)
    # Morning briefing at 09:00 UTC (explicit tz so it's stable year-round).
    sched.add_job(morning_briefing, "cron", hour=9, minute=0, timezone="UTC")
    sched.add_job(bedtime_reminder, "cron", day_of_week="mon-fri", hour=22, minute=30)
    sched.add_job(bedtime_reminder, "cron", day_of_week="sun,sat", hour=0, minute=0)
    sched.add_job(market_open_reminder, "cron", day_of_week="mon-fri", hour=14, minute=0)
    sched.add_job(reflection_prompt, "cron", hour=22, minute=0)
    # Autonomous end-of-day summary — auto-sent, no user action required.
    sched.add_job(daily_summary, "cron", hour=21, minute=0, timezone="UTC")
    # Daily Obsidian vault sync at midnight (writes the just-ended day's log).
    sched.add_job(obsidian_sync_job, "cron", hour=0, minute=0,
                  id="obsidian_midnight_sync")
    # Supplement reminders (local time) — morning prompt + evening nudge.
    sched.add_job(supplement_reminder, "cron", hour=9, minute=0)
    sched.add_job(supplement_reminder, "cron", hour=20, minute=0)
    sched.add_job(water_check, "interval", minutes=30)
    sched.add_job(poll_bot_trades, "interval", minutes=5)
    sched.add_job(weekly_review, "cron", day_of_week="sun", hour=18, minute=0)
    # Tier 3 Part 5 — weekly Telegram digest, Sunday 18:00 Europe/London. Explicit
    # tz: Railway runs UTC, and a bare 18:00 would drift an hour under BST.
    sched.add_job(weekly_digest, "cron", day_of_week="sun", hour=18, minute=0,
                  timezone="Europe/London", id="weekly_digest", replace_existing=True)
    # Daily production-DB backup at 03:00 Europe/London (quiet hours).
    sched.add_job(db_backup, "cron", hour=3, minute=0,
                  timezone="Europe/London", id="db_backup")
    # Daily 7-day retention cap on the public CSP-report sink (03:30, quiet hours).
    sched.add_job(csp_report_cleanup, "cron", hour=3, minute=30,
                  timezone="Europe/London", id="csp_report_cleanup",
                  replace_existing=True)
    # Phase 4: daily reflective diary generation — 02:00 Europe/London.
    # Diaries for core agents only (see DIARY_AGENTS); infra agents still run.
    sched.add_job(generate_all_diaries, trigger="cron", hour=2, minute=0,
                  timezone="Europe/London", id="agent_diaries_daily",
                  replace_existing=True)
    # Phase 4: agent heartbeat / proactive health check every 30 minutes.
    sched.add_job(run_heartbeat, trigger="interval", minutes=30,
                  id="agent_heartbeat", replace_existing=True)
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started with %d jobs", len(sched.get_jobs()))
    return sched
