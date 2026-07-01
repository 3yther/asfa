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
from datetime import datetime

from services import scout  # Existing Scout module with job scanning

logger = logging.getLogger("asfa.skill_executor")

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
# IMPLEMENTED SKILLS FOR OTHER AGENTS (Phase 6+)
# ============================================================================

def summary_summarize_day(params: dict) -> dict:
    from services.scheduler import _build_daily_summary
    try:
        summary = _build_daily_summary()
        return {
            "summary": summary,
            "chars": len(summary),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception:
        raise


def supplement_log_supplement(params: dict) -> dict:
    import database as db
    try:
        name = params.get("name", "unknown")
        dose = params.get("dose", "")
        # log_supplement stores (supplement_name, taken_at); there is no dose
        # column, so persist only the name and echo dose back informationally.
        db.log_supplement(name)
        return {
            "logged": True,
            "name": name,
            "dose": dose,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"logged": False, "error": str(e)}


def weekly_review_generate_review(params: dict) -> dict:
    from services.ai import generate_weekly_review
    try:
        review = generate_weekly_review()
        return {
            "review": review,
            "chars": len(review) if review else 0,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"review": "", "error": str(e)}


def reflection_prompt_reflection(params: dict) -> dict:
    from services.scheduler import reflection_prompt
    try:
        reflection_prompt()
        return {
            "prompted": True,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"prompted": False, "error": str(e)}


def insights_generate_insights(params: dict) -> dict:
    from services.insights import generate_insights, gather_metrics
    try:
        metrics = gather_metrics()
        insights = generate_insights(metrics)
        return {
            "insights": insights,
            "count": len(insights) if insights else 0,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"insights": [], "error": str(e)}


def init_implemented_skills():
    register_skill_impl("summary", "summarize_day", summary_summarize_day)
    register_skill_impl("supplement", "log_supplement", supplement_log_supplement)
    register_skill_impl("weekly_review", "generate_review", weekly_review_generate_review)
    register_skill_impl("reflection", "prompt_reflection", reflection_prompt_reflection)
    register_skill_impl("insights", "generate_insights", insights_generate_insights)


# ============================================================================
# STUB SKILLS FOR OTHER AGENTS (Phase 6+)
# ============================================================================

def stub_skill(params: dict) -> dict:
    """Placeholder for unimplemented skills."""
    return {"message": "Skill not yet implemented"}


def init_stub_skills():
    """Register stub implementations for all other agents' skills."""
    stubs = [
        ("sentinel", "monitor_alerts"),
        ("sentinel", "escalate"),
        ("quant_bot", "scan_signals"),
        ("quant_bot", "execute_trade"),
        ("briefing", "generate_briefing"),
        ("hydration", "log_intake"),
        ("hydration", "get_status"),
        ("health", "check_endpoint"),
        ("obsidian", "sync_vault"),
        ("backup", "backup_db"),
    ]
    for agent_id, skill_name in stubs:
        register_skill_impl(agent_id, skill_name, stub_skill)


def init_all_skills():
    """Initialize all skill implementations at startup."""
    init_scout_skills()
    init_implemented_skills()
    init_stub_skills()
    logger.info("Skill executor ready with %d skills", len(SKILL_IMPLEMENTATIONS))
