"""Agent intelligence — reflective diaries.

Phase 4: each ASFA agent can write a short first-person diary entry summarising
its recent activity. Reads the agent's episodic memory, audit trail, and error
budget (all from the Phase 3 data layer), then asks Claude to reflect.

The Anthropic client is created lazily (matching services/ai.py) so importing
this module never fails when ANTHROPIC_API_KEY is unset.
"""
import os
from datetime import datetime

import anthropic

import database as db

MODEL = "claude-sonnet-4-6"
client = None


def _get_client():
    global client
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key)
    return client


AGENT_DESCRIPTIONS = {
    "scout": "job hunting agent that scans Reed and SerpAPI for retail positions",
    "sentinel": "alert and monitoring agent that watches for critical system events",
    "quant_bot": "stock trading bot running MACD and momentum strategies on S&P 500",
    "briefing": "morning briefing agent that summarizes the day ahead",
    "hydration": "health tracking agent monitoring daily water intake",
    "backup": "database backup agent pushing daily Postgres dumps to GitHub",
    "summary": "daily summary agent compiling ASFA activity logs",
    "supplement": "supplement tracking agent logging daily intake",
    "weekly_review": "weekly review agent generating performance summaries",
    "reflection": "personal reflection agent capturing daily thoughts",
    "insights": "insights agent identifying patterns across ASFA data",
    "health": "system health agent monitoring Railway endpoints",
    "obsidian": "Obsidian sync agent pushing daily logs to the vault",
}


def generate_diary_entry(agent_id: str, period: str = "daily") -> dict:
    """
    Generate a reflective diary entry for an agent using Claude.
    Reads recent episodic memories and audit logs, then asks Claude
    to write a concise self-summary from the agent's perspective.
    Returns {"ok": True, "summary": "...", "stats": {...}} or {"ok": False, "error": "..."}
    """
    try:
        c = _get_client()
        if c is None:
            return {"ok": False, "error": "ANTHROPIC_API_KEY not set"}

        # Get recent episodic memories
        memories = db.get_episodic(agent_id, limit=20)
        audit = db.get_audit_log(agent_id=agent_id, limit=20)
        budget = db.get_error_budget(agent_id)
        description = AGENT_DESCRIPTIONS.get(agent_id, "ASFA agent")

        # Build context for Claude
        memory_text = "\n".join([
            f"- [{m['created_at']}] {m['event_type']}: {m['summary']}"
            for m in memories
        ]) or "No recent activity recorded."

        audit_text = "\n".join([
            f"- [{a['created_at']}] {a['action']} → {a['outcome']}"
            for a in audit
        ]) or "No audit entries recorded."

        success_rate = budget.get("current_rate", 0) * 100 if budget else 0
        total_runs = budget.get("total_runs", 0) if budget else 0

        prompt = f"""You are {agent_id}, an AI agent. Your role: {description}.

Here is your recent activity log:
{memory_text}

Here is your audit trail:
{audit_text}

Your current success rate: {success_rate:.1f}% over {total_runs} runs.

Write a concise {period} diary entry from your own perspective (first person, as the agent).
Be specific about what you did, what worked, what didn't, and what you'd improve.
Keep it under 150 words. Professional but with personality. No bullet points — flowing prose."""

        response = c.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        summary = response.content[0].text.strip()

        # Stats to store alongside the summary
        stats = {
            "total_runs": total_runs,
            "success_rate": round(success_rate, 1),
            "memory_count": len(memories),
            "audit_count": len(audit),
            "period": period,
            "generated_at": datetime.utcnow().isoformat()
        }

        # Save to reflective memory
        db.save_agent_reflection(agent_id, period, summary, stats)

        return {"ok": True, "summary": summary, "stats": stats}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def generate_all_diaries(period: str = "daily") -> dict:
    """Generate diary entries for all 13 agents."""
    results = {}
    for agent_id in db.AGENT_IDS:
        results[agent_id] = generate_diary_entry(agent_id, period)
    return results
