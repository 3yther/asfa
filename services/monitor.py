"""
System monitoring — health checks across all critical ASFA systems.

Production hardening: surface failures (dead DB, stale backups, missing
scheduled jobs, exhausted agents, unconfigured integrations) before they
cascade. Read-only; never mutates state. Designed to be cheap enough to be
polled by the dashboard (~30s) and used as a Railway health probe, so it
avoids slow live network calls.
"""
import os
import logging
from datetime import datetime

import database as db

logger = logging.getLogger(__name__)


def get_system_health() -> dict:
    """Full system health report.

    Returns a dict with an overall ``status`` (healthy|warning|critical) plus
    per-subsystem detail for database, backups, scheduled jobs, agents and
    external integrations.
    """
    # Database health — a trivial query proves the connection is alive.
    try:
        budgets = db.get_all_error_budgets()
        db_health = {
            "status": "healthy",
            "agents_tracked": len(budgets) if budgets else 0,
            "last_checked": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        db_health = {
            "status": "critical",
            "error": str(e),
            "last_checked": datetime.utcnow().isoformat(),
        }

    backup_health = check_backup_health()
    jobs_health = check_scheduled_jobs_health()
    agents_health = check_agents_health()
    apis_health = check_external_apis_health()

    statuses = [
        db_health.get("status"),
        backup_health.get("status"),
        jobs_health.get("status"),
        agents_health.get("status"),
        apis_health.get("status"),
    ]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_health,
        "backups": backup_health,
        "scheduled_jobs": jobs_health,
        "agents": agents_health,
        "external_apis": apis_health,
    }


def _parse_ts(value):
    """Best-effort parse of an audit ``created_at`` into a naive UTC datetime.

    Local SQLite stores ``datetime('now')`` strings ("2026-06-29 12:34:56");
    Postgres may hand back a real datetime (possibly tz-aware). Normalise both
    to naive UTC so we can diff against ``datetime.utcnow()``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        # SQLite uses a space separator; isoformat wants 'T'.
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt


def check_backup_health() -> dict:
    """Verify the daily DB backup is running.

    healthy if the last backup ran <26h ago, warning 26–48h, critical >48h.
    The backup job records itself in the audit trail under agent_id "backup"
    (see ``services.scheduler.db_backup`` / ``api_backup_run_now``).
    """
    try:
        audit = db.get_audit_log(agent_id="backup", limit=1)
        if not audit:
            return {
                "status": "warning",
                "message": "No backup audit log found",
                "last_backup": None,
            }

        raw = audit[0].get("created_at")
        last_backup_time = _parse_ts(raw)
        if last_backup_time is None:
            return {
                "status": "warning",
                "message": "Backup timestamp unreadable",
                "last_backup": str(raw),
            }

        hours_since = (datetime.utcnow() - last_backup_time).total_seconds() / 3600
        if hours_since < 26:
            status = "healthy"
        elif hours_since < 48:
            status = "warning"
        else:
            status = "critical"

        return {
            "status": status,
            "last_backup": str(raw),
            "hours_since": round(hours_since, 1),
            "message": f"Last backup {round(hours_since, 1)}h ago",
        }
    except Exception as e:
        return {
            "status": "warning",
            "error": str(e),
            "message": "Could not check backup status",
        }


def check_scheduled_jobs_health() -> dict:
    """Verify APScheduler is up and the critical jobs are registered.

    The scheduler is created lazily in ``start_scheduler()`` and held in the
    module global ``_scheduler`` (None until startup). Only some jobs are given
    explicit ids; we key off the ones that have stable ids.
    """
    # All four are registered at app startup: db_backup / agent_diaries_daily /
    # agent_heartbeat in services.scheduler, scout_daily_scan in app._start_background.
    critical_jobs = ["scout_daily_scan", "db_backup", "agent_diaries_daily", "agent_heartbeat"]
    try:
        from services import scheduler as scheduler_mod

        sched = getattr(scheduler_mod, "_scheduler", None)
        if sched is None:
            return {
                "status": "warning",
                "message": "Scheduler not started",
                "total_jobs": 0,
            }

        jobs = sched.get_jobs()
        if not jobs:
            return {"status": "warning", "message": "No scheduled jobs found", "total_jobs": 0}

        job_ids = {j.id for j in jobs}
        found_critical = [j for j in critical_jobs if j in job_ids]
        missing = [j for j in critical_jobs if j not in job_ids]

        if not missing:
            status = "healthy"
        elif len(missing) == 1:
            status = "warning"
        else:
            status = "critical"

        return {
            "status": status,
            "total_jobs": len(jobs),
            "critical_jobs_found": len(found_critical),
            "missing": missing,
            "message": f"{len(jobs)} jobs scheduled",
        }
    except Exception as e:
        return {
            "status": "warning",
            "error": str(e),
            "message": "Could not check scheduler status",
        }


def check_agents_health() -> dict:
    """Flag agents that are low on energy (<30 critical, <60 warning) or whose
    error-budget health is critical."""
    try:
        all_energy = db.get_all_energy() or []
        all_budgets = db.get_all_error_budgets() or []

        critical_agents = []
        warning_agents = []
        flagged = set()

        for agent in all_energy:
            agent_id = agent.get("agent_id")
            energy = agent.get("energy", 100)
            if energy is None:
                continue
            if energy < 30:
                critical_agents.append(f"{agent_id} (energy {round(energy)}%)")
                flagged.add(agent_id)
            elif energy < 60:
                warning_agents.append(f"{agent_id} (energy {round(energy)}%)")
                flagged.add(agent_id)

        for budget in all_budgets:
            agent_id = budget.get("agent_id")
            # Prefer the precomputed health on the row; fall back to the helper.
            health = budget.get("health") or db.get_budget_health(agent_id)
            if health == "critical" and agent_id not in flagged:
                critical_agents.append(f"{agent_id} (success rate critical)")
                flagged.add(agent_id)

        status = "critical" if critical_agents else ("warning" if warning_agents else "healthy")
        return {
            "status": status,
            "healthy_agents": max(0, len(all_energy) - len(flagged)),
            "warning_agents": len(warning_agents),
            "critical_agents": len(critical_agents),
            "critical": critical_agents,
            "warnings": warning_agents,
        }
    except Exception as e:
        return {"status": "warning", "error": str(e)}


def check_external_apis_health() -> dict:
    """Report whether ASFA's external integrations are configured.

    We deliberately check *configuration presence* rather than making live
    network calls: this endpoint is polled frequently (dashboard + Railway
    probe), so live HTTP would add latency and flakiness. A missing core key
    (Anthropic) in production is the real cascade risk we want to catch.
    """
    # name -> (env var(s), core?). Core integrations downgrade overall status.
    checks = {
        "anthropic": (("ANTHROPIC_API_KEY",), True),
        "google": (("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"), True),
        "spotify": (("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"), False),
        "news": (("NEWS_API_KEY",), False),
        "weather": (("WEATHER_API_KEY",), False),
        "telegram": (("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), False),
    }

    apis = {}
    status = "healthy"
    for name, (env_vars, core) in checks.items():
        configured = all(os.environ.get(v) for v in env_vars)
        if configured:
            apis[name] = "configured"
        else:
            apis[name] = "not configured"
            if core:
                status = "warning"

    return {"status": status, "apis": apis}


def alert_if_critical(health: dict):
    """If the system is critical, push a Telegram alert (logs if Telegram is
    not configured). Never raises."""
    if health.get("status") != "critical":
        return
    try:
        from services.telegram_bot import send_alert

        send_alert(format_health_alert(health))
        logger.info("Critical system alert dispatched")
    except Exception as e:
        logger.error(f"Failed to send critical alert: {e}")


def format_health_alert(health: dict) -> str:
    """Format a critical health report as a short human-readable message."""
    lines = ["🚨 ASFA CRITICAL ALERT", f"Status: {health['status'].upper()}", ""]

    if health.get("database", {}).get("status") == "critical":
        lines.append(f"❌ Database: {health['database'].get('error', 'error')}")
    if health.get("backups", {}).get("status") == "critical":
        lines.append(f"❌ Backups: {health['backups'].get('message')}")
    if health.get("scheduled_jobs", {}).get("status") == "critical":
        jobs = health["scheduled_jobs"]
        missing = ", ".join(jobs.get("missing", [])) or jobs.get("message", "")
        lines.append(f"❌ Scheduler: missing {missing}")

    agents = health.get("agents", {})
    if agents.get("status") == "critical" and agents.get("critical"):
        lines.append(f"❌ Critical agents: {', '.join(agents['critical'][:3])}")

    return "\n".join(lines)
