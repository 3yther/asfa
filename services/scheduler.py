"""Background scheduler — smart notifications, Telegram pushes, trade alerts.

All jobs degrade gracefully: Telegram skipped if not configured, in-app
notifications always stored so the dashboard bell still works.
"""
import functools
import json
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import database as db
from services import alerts, insights, telegram_bot
from services.agent_intelligence import generate_all_diaries
from services.bots import get_bots_status, get_trading_activity
from services.heartbeat import run_heartbeat

logger = logging.getLogger(__name__)
_scheduler = None

# Timezone for the weekly CSV export trigger. Kept in sync with
# services.weekly_export._tz() so the job fires exactly when the window closes.
EXPORT_TZ = os.environ.get("EXPORT_TZ", "Europe/London")


def _notify(message: str, kind: str = "info", telegram: bool = True):
    try:
        db.add_notification(message, kind)
    except Exception as e:
        logger.error(f"notification store failed: {e}")
    # Respect the Settings → Notifications Telegram toggle. Fails OPEN so a prefs
    # lookup error never mutes notifications. The in-app bell above is unaffected.
    if telegram:
        try:
            telegram = bool(db.get_notification_prefs().get("telegram_notifications", True))
        except Exception:
            telegram = True
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
    today = db.today_str()
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
    """Daytime nudge if no water logged for 3+ hours. Skipped when the water
    alert is toggled off in Settings or the current time is inside quiet hours."""
    now = datetime.now()
    if not (9 <= now.hour <= 21):
        return
    try:
        if not db.alert_enabled("alert_water_intake"):
            return
    except Exception:
        pass  # fail open — a prefs lookup error must not silence the nudge
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

    today = db.today_str()
    lines = [f"🛰️ ASFA Daily Summary — {db.now_local().strftime('%A, %d %B %Y')}", ""]

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
    today = db.today_str()
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


@audited("scout", "archive_stale_jobs")
def archive_stale_jobs():
    """02:00 Europe/London — move listings older than db.JOB_ACTIVE_DAYS (14)
    out of the active view and into history.

    Archiving is a soft state change (archived_at gets stamped); nothing is
    deleted, and the DB call only touches rows that are still active, so a
    redeploy that re-fires this job the same night changes nothing.
    """
    try:
        archived = db.archive_stale_scout_jobs()
        logger.info("scout auto-archive: %d job(s) older than %d days archived",
                    archived, db.JOB_ACTIVE_DAYS)
    except Exception as e:
        logger.error(f"scout auto-archive failed: {e}")


@audited("scout", "cleanup_job_pipeline")
def cleanup_job_pipeline():
    """01:00 Europe/London — permanently delete job-pipeline history older than
    db.JOB_ACTIVE_DAYS (14). Runs after the midnight Obsidian sync.

    Unlike archive_stale_jobs (a soft state change at 02:00), this is a hard
    delete of stale listings. Applied jobs (applied = 1) are spared by
    db.cleanup_job_pipeline, so active applications are never removed.
    """
    try:
        deleted = db.cleanup_job_pipeline()
        logger.info("Deleted %d job entries older than %d days",
                    deleted, db.JOB_ACTIVE_DAYS)
    except Exception as e:
        logger.error(f"job pipeline cleanup failed: {e}")


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


@audited("summary", "weekly_csv_export")
def weekly_csv_export():
    """Sunday 12:00 Europe/London — email the five CSVs for the week that just
    ended (last Sun 00:00 → Sat 23:59 local). Never raises."""
    from services.weekly_export import send_weekly_export
    try:
        res = send_weekly_export()
        logger.info("weekly CSV export %s→%s: %d rows, emailed=%s%s",
                    res["start"], res["end"], res["rows"], res["emailed"],
                    f" ({res['skipped']})" if res.get("skipped") else "")
    except Exception as e:
        logger.error(f"weekly CSV export failed: {e}")


# ── Startup ────────────────────────────────────────────────────────────────────

def _safe_add(sched, func, *args, **kwargs):
    """Register one job, isolating failures.

    A single bad registration must never prevent the remaining jobs from being
    scheduled — most importantly the midnight Obsidian sync. Previously all jobs
    were added in one unguarded sequence, so a failure partway through (e.g. an
    integration raising during registration) skipped every job after it and left
    obsidian_sync_job unregistered. Any failure here is logged and swallowed.
    """
    job_id = kwargs.get("id") or getattr(func, "__name__", "job")
    try:
        return sched.add_job(func, *args, **kwargs)
    except Exception as e:
        logger.error("failed to register scheduler job %s: %s", job_id, e)
        return None


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="Europe/London", daemon=True)
    # Daily/weekly cron jobs carry misfire_grace_time=60: a redeploy that lands on
    # a job's fire time would otherwise silently skip that day's run — the grace
    # window lets it still fire up to 60s late. Interval jobs (water/poll/heartbeat)
    # don't need it; they'll come around again shortly.
    # Morning briefing at 09:00 UTC (explicit tz so it's stable year-round).
    _safe_add(sched, morning_briefing, "cron", hour=9, minute=0, timezone="UTC",
              misfire_grace_time=60)
    _safe_add(sched, bedtime_reminder, "cron", day_of_week="mon-fri", hour=22, minute=30,
              misfire_grace_time=60)
    _safe_add(sched, bedtime_reminder, "cron", day_of_week="sun,sat", hour=0, minute=0,
              misfire_grace_time=60)
    _safe_add(sched, market_open_reminder, "cron", day_of_week="mon-fri", hour=14, minute=0,
              misfire_grace_time=60)
    _safe_add(sched, reflection_prompt, "cron", hour=22, minute=0, misfire_grace_time=60)
    # Autonomous end-of-day summary — auto-sent, no user action required.
    _safe_add(sched, daily_summary, "cron", hour=21, minute=0, timezone="UTC",
              misfire_grace_time=60)
    # Daily Obsidian vault sync at midnight (writes the just-ended day's log).
    _safe_add(sched, obsidian_sync_job, "cron", hour=0, minute=0,
              id="obsidian_midnight_sync", misfire_grace_time=60)
    # Supplement reminders (local time) — morning prompt + evening nudge.
    _safe_add(sched, supplement_reminder, "cron", hour=9, minute=0, misfire_grace_time=60)
    _safe_add(sched, supplement_reminder, "cron", hour=20, minute=0, misfire_grace_time=60)
    _safe_add(sched, water_check, "interval", minutes=30)
    _safe_add(sched, poll_bot_trades, "interval", minutes=5)
    _safe_add(sched, weekly_review, "cron", day_of_week="sun", hour=18, minute=0,
              misfire_grace_time=60)
    # Tier 3 Part 5 — weekly Telegram digest, Sunday 18:00 Europe/London. Explicit
    # tz: Railway runs UTC, and a bare 18:00 would drift an hour under BST.
    _safe_add(sched, weekly_digest, "cron", day_of_week="sun", hour=18, minute=0,
              timezone="Europe/London", id="weekly_digest", replace_existing=True,
              misfire_grace_time=60)
    # Weekly CSV export — Sunday 12:00 Europe/London, matching ASFA_TZ so the
    # week boundaries line up with the London-stamped date columns. The tz is
    # passed explicitly rather than inherited from the scheduler default: it is
    # EXPORT_TZ-overridable, and the trigger must not silently disagree with the
    # window weekly_export computes. Fires well after the Sat 23:59 window
    # closes, so the exported week is always complete.
    _safe_add(sched, weekly_csv_export, "cron", day_of_week="sun", hour=12, minute=0,
              timezone=EXPORT_TZ, id="weekly_csv_export",
              replace_existing=True, misfire_grace_time=60)
    # Daily production-DB backup at 03:00 Europe/London (quiet hours).
    _safe_add(sched, db_backup, "cron", hour=3, minute=0,
              timezone="Europe/London", id="db_backup", misfire_grace_time=60)
    # Scout job lifecycle — auto-archive listings older than 14 days at 02:00
    # Europe/London (quiet hours, and explicit tz because Railway runs UTC).
    _safe_add(sched, archive_stale_jobs, "cron", hour=2, minute=0,
              timezone="Europe/London", id="scout_archive_stale_jobs",
              replace_existing=True, misfire_grace_time=60)
    # Job-pipeline hard-delete — purge scout_jobs history older than 14 days at
    # 01:00 Europe/London (after the midnight Obsidian sync, before the 02:00
    # soft-archive). Explicit tz because Railway runs UTC. Applied jobs are kept.
    _safe_add(sched, cleanup_job_pipeline, "cron", hour=1, minute=0,
              timezone="Europe/London", id="scout_cleanup_job_pipeline",
              replace_existing=True, misfire_grace_time=60)
    # Daily 7-day retention cap on the public CSP-report sink (03:30, quiet hours).
    _safe_add(sched, csp_report_cleanup, "cron", hour=3, minute=30,
              timezone="Europe/London", id="csp_report_cleanup",
              replace_existing=True, misfire_grace_time=60)
    # Phase 4: daily reflective diary generation — 02:00 Europe/London.
    # Diaries for core agents only (see DIARY_AGENTS); infra agents still run.
    _safe_add(sched, generate_all_diaries, trigger="cron", hour=2, minute=0,
              timezone="Europe/London", id="agent_diaries_daily",
              replace_existing=True, misfire_grace_time=60)
    # Phase 4: agent heartbeat / proactive health check every 30 minutes.
    _safe_add(sched, run_heartbeat, trigger="interval", minutes=30,
              id="agent_heartbeat", replace_existing=True)
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started with %d jobs", len(sched.get_jobs()))
    return sched
