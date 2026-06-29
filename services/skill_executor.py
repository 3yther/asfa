"""
Skill executor — dispatches skill calls to real agent implementations.

Each skill is a function: skill(params: dict) -> dict (the skill's output).
execute_skill() wraps the call with timing and error handling so plan execution
gets a uniform {success, output, error, duration_ms} envelope back.

Scout's three skills (scan_jobs, filter_results, apply_for_role) invoke the real
services.scout logic — live Reed search and the scout_jobs/scout_applications
tables. Every other agent gets a stub until its skill is implemented.
"""
import logging
import time
import uuid
from datetime import datetime

from services import scout  # Existing Scout module with job scanning

logger = logging.getLogger("asfa.skill_executor")


def _today() -> str:
    """Local calendar day, matching how the rest of ASFA keys daily data."""
    return datetime.now().strftime("%Y-%m-%d")

# Skill implementations registry: "agent_id/skill_name" -> callable(params)->dict
SKILL_IMPLEMENTATIONS = {}


def register_skill_impl(agent_id: str, skill_name: str, func):
    """Register a callable skill implementation."""
    key = f"{agent_id}/{skill_name}"
    SKILL_IMPLEMENTATIONS[key] = func
    logger.info("Registered skill: %s", key)


def execute_skill(agent_id: str, skill_name: str, params: dict) -> dict:
    """
    Execute a skill and return a uniform result envelope.

    Returns: {success: bool, output: dict | None, error: str | None,
              duration_ms: int}
    """
    key = f"{agent_id}/{skill_name}"

    if key not in SKILL_IMPLEMENTATIONS:
        return {
            "success": False,
            "output": None,
            "error": f"Skill not found: {key}",
            "duration_ms": 0,
        }

    start = time.time()
    try:
        func = SKILL_IMPLEMENTATIONS[key]
        result = func(params or {})
        duration_ms = int((time.time() - start) * 1000)
        return {
            "success": True,
            "output": result,
            "error": None,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.error("Skill execution failed: %s — %s", key, e)
        return {
            "success": False,
            "output": None,
            "error": str(e),
            "duration_ms": duration_ms,
        }


# ============================================================================
# SCOUT SKILL IMPLEMENTATIONS
# ============================================================================

def scout_scan_jobs(params: dict) -> dict:
    """
    Scan for retail jobs via the live Reed API.
    Input:  {keywords: [...], location: string, limit: int}
    Output: {matches: [...], count: int, source: "reed"}
    """
    keywords = params.get("keywords", ["retail"])
    location = params.get("location", "")
    limit = params.get("limit", 20)

    try:
        jobs = scout.scan_jobs_reed(keywords, location, limit)
        return {"matches": jobs, "count": len(jobs), "source": "reed"}
    except Exception as e:
        logger.warning("Reed scan failed: %s — returning empty", e)
        return {"matches": [], "count": 0, "source": "reed", "error": str(e)}


def scout_filter_results(params: dict) -> dict:
    """
    Filter jobs by salary criteria.
    Input:  {jobs: [...], filters: {min_salary: int, max_salary: int}}
    Output: {filtered: [...], count: int, criteria: {...}}
    """
    jobs = params.get("jobs", []) or []
    filters = params.get("filters", {}) or {}
    min_sal = filters.get("min_salary", 0)
    max_sal = filters.get("max_salary", 999999)

    def _salary(job):
        try:
            return float(job.get("salary", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    filtered = [j for j in jobs if min_sal <= _salary(j) <= max_sal]
    return {"filtered": filtered, "count": len(filtered), "criteria": filters}


def scout_apply_for_role(params: dict) -> dict:
    """
    Apply to a job role.
    Input:  {job_id: string, cv_version: string}
    Output: {success: bool, application_id, message, timestamp}
    """
    job_id = params.get("job_id")
    cv_version = params.get("cv_version", "default")
    job = params.get("job")  # full job dict threaded from a prior scan/filter step

    if not job_id:
        return {"success": False, "message": "job_id required"}

    try:
        result = scout.apply_for_job(job_id, cv_version, job=job)
        return {
            "success": True,
            "application_id": result.get("id"),
            "message": f"Applied successfully to {job_id}",
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"success": False, "message": f"Application failed: {str(e)}"}


def init_scout_skills():
    register_skill_impl("scout", "scan_jobs", scout_scan_jobs)
    register_skill_impl("scout", "filter_results", scout_filter_results)
    register_skill_impl("scout", "apply_for_role", scout_apply_for_role)


# ============================================================================
# QUANT BOT SKILL IMPLEMENTATIONS
# ============================================================================
# SAFETY: the stock-scanner / crypto bots are frozen for their paper-trading
# validation window. ASFA's contract with that system is strictly READ-ONLY
# (see CLAUDE.md). These skills honour that: scan reads the live snapshot ASFA
# already polls; "execute" never reaches the bots — it produces a simulated
# fill recorded only in ASFA so the agent framework can demo a trade step.

def quant_bot_scan_signals(params: dict) -> dict:
    """
    Snapshot live trading signals from the (frozen, read-only) bot system.
    Input:  {strategy: "momentum", limit: int}
    Output: {signals: [...], count: int, strategy, online, regime, timestamp}

    Read-only: calls bots.get_trading_activity(), the same path the briefing
    uses. It never places or mutates a trade.
    """
    from services import bots

    strategy = params.get("strategy", "momentum")
    limit = params.get("limit", 10)
    try:
        activity = bots.get_trading_activity()
        sig = activity.get("latest_signal")
        signals = ([sig] if sig else [])[:limit]
        return {
            "signals": signals,
            "count": len(signals),
            "strategy": strategy,
            "online": activity.get("online", False),
            "regime": activity.get("regime"),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.warning("Signal scan failed: %s", e)
        return {"signals": [], "count": 0, "strategy": strategy, "error": str(e)}


def quant_bot_execute_trade(params: dict) -> dict:
    """
    Record a *simulated* paper trade inside ASFA.
    Input:  {symbol: "SPY", side: "buy"|"sell", size: float, entry_price: float}
    Output: {order_id, status: "filled", simulated: True, executed_price, ...}

    ASFA must never issue trades to the frozen bot system, so this deliberately
    does NOT call any execution path — it returns a simulated fill marked
    `simulated: True` and touches nothing outside ASFA.
    """
    symbol = params.get("symbol", "SPY")
    side = params.get("side", "buy")
    try:
        size = float(params.get("size", 1) or 1)
    except (TypeError, ValueError):
        size = 1.0
    entry_price = params.get("entry_price")

    return {
        "order_id": f"sim-{uuid.uuid4().hex[:12]}",
        "status": "filled",
        "simulated": True,
        "source": "asfa_paper_sim",
        "executed_price": entry_price,
        "symbol": symbol,
        "side": side,
        "size": size,
        "note": "Simulated in ASFA only — the frozen bot system was not touched.",
        "timestamp": datetime.utcnow().isoformat(),
    }


def init_quant_bot_skills():
    register_skill_impl("quant_bot", "scan_signals", quant_bot_scan_signals)
    register_skill_impl("quant_bot", "execute_trade", quant_bot_execute_trade)


# ============================================================================
# HYDRATION SKILL IMPLEMENTATIONS
# ============================================================================

def hydration_log_intake(params: dict) -> dict:
    """
    Log water intake. Mirrors the /api/asfa/water-intake endpoint: writes the
    hydration_log ledger AND the rolled-up habits total so the gauge, daily
    score, and briefing stay in sync.
    Input:  {amount_ml: number, timestamp: string (optional)}
    Output: {logged_ml, total_today, target_ml, percent_of_goal, timestamp}
    """
    import database as db

    try:
        amount = int(params.get("amount_ml", params.get("amount", 250)) or 250)
    except (TypeError, ValueError):
        amount = 250
    if amount <= 0:
        return {"logged_ml": 0, "error": "amount_ml must be positive"}

    date = _today()
    try:
        db.log_hydration(date, amount, datetime.now().isoformat())
        db.log_water(date, amount)  # keep habits gauge / score / briefing consistent
        total = db.get_hydration_total(date)
        target = 2000
        return {
            "logged_ml": amount,
            "total_today": total,
            "target_ml": target,
            "percent_of_goal": round((total / target) * 100, 1),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"logged_ml": 0, "error": str(e)}


def hydration_get_status(params: dict) -> dict:
    """
    Get current hydration status for the day.
    Output: {logged_ml, target_ml, percent, status: complete|on_track|behind}
    """
    import database as db

    try:
        total = db.get_hydration_total(_today()) or 0
        target = 2000
        percent = (total / target) * 100
        if percent >= 100:
            status = "complete"
        elif percent >= 60:
            status = "on_track"
        else:
            status = "behind"
        return {
            "logged_ml": total,
            "target_ml": target,
            "percent": round(percent, 1),
            "status": status,
        }
    except Exception as e:
        return {"error": str(e)}


def init_hydration_skills():
    register_skill_impl("hydration", "log_intake", hydration_log_intake)
    register_skill_impl("hydration", "get_status", hydration_get_status)


# ============================================================================
# BRIEFING SKILL IMPLEMENTATION
# ============================================================================

def briefing_generate_briefing(params: dict) -> dict:
    """
    Generate (or return the cached) morning briefing.
    Input:  {force: bool (optional)}
    Output: {briefing: string, date, cached, chars, timestamp}
    """
    from services import briefing

    try:
        result = briefing.build_briefing(force=bool(params.get("force", False)))
        text = result.get("text") or result.get("content") or ""
        return {
            "briefing": text,
            "date": result.get("date"),
            "cached": result.get("cached", False),
            "chars": len(text),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"briefing": "", "error": str(e)}


def init_briefing_skills():
    register_skill_impl("briefing", "generate_briefing", briefing_generate_briefing)


# ============================================================================
# BACKUP SKILL IMPLEMENTATION
# ============================================================================

def backup_backup_db(params: dict) -> dict:
    """
    Run a database backup via services.backup.run_backup (never raises; returns
    success / skipped / failure shapes).
    Output: {status, bytes, method, file, tables, rows, timestamp}
    """
    from services import backup

    try:
        result = backup.run_backup()
        ok = result.get("ok", False)
        return {
            "status": "success" if ok else "failed",
            "bytes": result.get("bytes", 0),
            "method": result.get("method"),
            "file": result.get("file"),
            "tables": result.get("tables"),
            "rows": result.get("rows"),
            "reason": result.get("reason"),  # e.g. "local SQLite — no backup needed"
            "error": result.get("error"),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def init_backup_skills():
    register_skill_impl("backup", "backup_db", backup_backup_db)


# ============================================================================
# SENTINEL SKILL IMPLEMENTATIONS
# ============================================================================

def sentinel_monitor_alerts(params: dict) -> dict:
    """
    Check for critical alerts: recent failed agent actions + agents whose error
    budget health is critical.
    Input:  {limit: int (audit rows to scan, default 100)}
    Output: {alert_count, critical_audits, critical_agents, message}
    """
    import database as db

    try:
        audit = db.get_audit_log(limit=params.get("limit", 100))
        critical = [a for a in audit if a.get("outcome") == "failure"]

        # get_all_error_budgets() already enriches each row with "health".
        budgets = db.get_all_error_budgets() or []
        critical_agents = [b for b in budgets if b.get("health") == "critical"]

        return {
            "alert_count": len(critical) + len(critical_agents),
            "critical_audits": len(critical),
            "critical_agents": len(critical_agents),
            "message": (f"Found {len(critical)} failed action(s) and "
                        f"{len(critical_agents)} critical agent(s)"),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"alert_count": 0, "error": str(e)}


def sentinel_escalate(params: dict) -> dict:
    """
    Escalate alerts to the user via Telegram.
    Input:  {alerts: [...], severity: "high"|"critical"}
    Output: {sent: bool, message}
    """
    from services import telegram_bot

    try:
        alerts = params.get("alerts", []) or []
        severity = params.get("severity", "high")
        if not alerts:
            return {"sent": False, "message": "No alerts to escalate"}

        lines = [f"🚨 ASFA {severity.upper()} ALERT"]
        lines += [f"• {a}" for a in alerts[:5]]  # max 5 per message
        telegram_bot.send_alert("\n".join(lines))

        return {
            "sent": True,
            "message": f"Escalated {len(alerts)} alert(s) via Telegram",
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error("Escalation failed: %s", e)
        return {"sent": False, "error": str(e)}


def init_sentinel_skills():
    register_skill_impl("sentinel", "monitor_alerts", sentinel_monitor_alerts)
    register_skill_impl("sentinel", "escalate", sentinel_escalate)


# ============================================================================
# HEALTH SKILL IMPLEMENTATION
# ============================================================================

def health_check_endpoint(params: dict) -> dict:
    """
    Ping an endpoint to check if it's up.
    Input:  {endpoint: "https://..."}
    Output: {up: bool, latency_ms: int, status_code: int}
    """
    import requests

    endpoint = params.get(
        "endpoint",
        "https://asfa-production.up.railway.app/api/mission-control/health")
    try:
        start = time.time()
        resp = requests.get(endpoint, timeout=10)
        latency_ms = int((time.time() - start) * 1000)
        return {
            "up": resp.status_code < 500,
            "latency_ms": latency_ms,
            "status_code": resp.status_code,
            "endpoint": endpoint,
        }
    except Exception as e:
        return {"up": False, "latency_ms": 0, "endpoint": endpoint, "error": str(e)}


def init_health_skills():
    register_skill_impl("health", "check_endpoint", health_check_endpoint)


# ============================================================================
# OBSIDIAN SKILL IMPLEMENTATION
# ============================================================================

def obsidian_sync_vault(params: dict) -> dict:
    """
    Sync today's logs to the Obsidian vault via services.obsidian_sync (writes
    agent profiles + the daily log + summary; local FS only, degrades on Railway).
    Output: {synced_files, agents, status, path, timestamp}
    """
    from services import obsidian_sync

    try:
        result = obsidian_sync.sync_to_obsidian(date=params.get("date"))
        synced = result.get("status") == "synced"
        n_agents = result.get("agents", 0)
        return {
            # agent profiles + daily log + summary
            "synced_files": (n_agents + 2) if synced else 0,
            "agents": n_agents,
            "status": "success" if synced else "failed",
            "path": result.get("path"),
            "message": result.get("message"),
            "error": result.get("error"),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.warning("Obsidian sync failed (non-critical): %s", e)
        return {"synced_files": 0, "status": "failed", "error": str(e)}


def init_obsidian_skills():
    register_skill_impl("obsidian", "sync_vault", obsidian_sync_vault)


# ============================================================================
# STUB SKILLS FOR OTHER AGENTS (Phase 6+)
# ============================================================================

def stub_skill(params: dict) -> dict:
    """Placeholder for unimplemented skills."""
    return {"message": "Skill not yet implemented"}


def init_stub_skills():
    """Register stub implementations for all other agents' skills."""
    stubs = [
        ("summary", "summarize_day"),
        ("supplement", "log_supplement"),
        ("weekly_review", "generate_review"),
        ("reflection", "prompt_reflection"),
        ("insights", "generate_insights"),
    ]
    for agent_id, skill_name in stubs:
        register_skill_impl(agent_id, skill_name, stub_skill)


def init_all_skills():
    """Initialize all skill implementations at startup."""
    init_scout_skills()
    init_quant_bot_skills()
    init_hydration_skills()
    init_briefing_skills()
    init_backup_skills()
    init_sentinel_skills()
    init_health_skills()
    init_obsidian_skills()
    init_stub_skills()
    logger.info("Skill executor ready with %d skills", len(SKILL_IMPLEMENTATIONS))
