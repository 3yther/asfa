"""
Heartbeat service — agents check in on a schedule and report status.
Follows the pattern: context gate → classify importance → schedule/suppress → deliver.
"""
import database as db
from datetime import datetime

# Importance tiers
TIER_CRITICAL = "critical"    # Always surface immediately
TIER_HIGH = "high"            # Surface during active hours
TIER_LOW = "low"              # Batch and surface once daily

def check_agent_health(agent_id: str) -> dict:
    """
    Run a health check for a single agent.
    Returns: {
        "agent_id": ...,
        "status": "healthy"|"warning"|"critical",
        "energy": 0-100,
        "budget_health": "healthy"|"warning"|"critical",
        "last_activity": "...",
        "message": "human-readable status summary"
    }
    """
    energy_row = db.get_energy(agent_id)
    energy = energy_row["energy"] if energy_row else 100.0
    budget = db.get_error_budget(agent_id)
    budget_health = db.get_budget_health(agent_id)
    recent = db.get_episodic(agent_id, limit=1)
    last_activity = recent[0]["created_at"] if recent else "Never"

    # Determine overall status
    if energy < 30 or budget_health == "critical":
        status = "critical"
        message = f"{agent_id} is struggling — energy {energy:.0f}%, success rate critical."
    elif energy < 60 or budget_health == "warning":
        status = "warning"
        message = f"{agent_id} needs attention — energy {energy:.0f}%, performance degraded."
    else:
        status = "healthy"
        message = f"{agent_id} is operating normally — energy {energy:.0f}%."

    return {
        "agent_id": agent_id,
        "status": status,
        "energy": round(energy, 1),
        "budget_health": budget_health,
        "last_activity": str(last_activity),
        "message": message,
        "checked_at": datetime.utcnow().isoformat()
    }

def run_heartbeat() -> dict:
    """
    Run health checks for all agents.
    Returns summary with counts by status tier.
    Logs critical agents to episodic memory so they surface in diaries.
    """
    results = {}
    critical = []
    warnings = []

    for agent_id in db.AGENT_IDS:
        result = check_agent_health(agent_id)
        results[agent_id] = result

        if result["status"] == "critical":
            critical.append(agent_id)
            # Log to episodic memory so it surfaces in diaries
            db.log_episodic(
                agent_id, "heartbeat_critical",
                f"Critical health check: {result['message']}"
            )
        elif result["status"] == "warning":
            warnings.append(agent_id)

    summary = {
        "checked_at": datetime.utcnow().isoformat(),
        "total": len(results),
        "healthy": len(results) - len(critical) - len(warnings),
        "warnings": len(warnings),
        "critical": len(critical),
        "critical_agents": critical,
        "warning_agents": warnings,
        "results": results
    }

    # Log overall heartbeat to audit
    db.log_audit(
        "sentinel", "heartbeat_check",
        "success" if not critical else "failure",
        reason="Scheduled heartbeat",
        details={"critical": critical, "warnings": warnings}
    )

    return summary
