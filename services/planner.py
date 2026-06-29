"""
Plan decomposition: user request → Claude breaks it down into agent-executable
subtasks. The execution engine then routes each subtask to the right
agent/skill.

The Anthropic client is created lazily (matching services/ai.py and
services/agent_intelligence.py) so importing this module never fails when
ANTHROPIC_API_KEY is unset.
"""
import json
import os
import time
import uuid

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


def get_all_skills_for_prompt():
    """Format all available skills, grouped by agent, as a JSON string for the
    planning prompt."""
    skills_by_agent = {}
    for skill in db.get_all_skills():
        agent = skill["agent_id"]
        skills_by_agent.setdefault(agent, []).append({
            "name": skill["skill_name"],
            "description": skill["description"],
            "input": skill.get("input_schema"),
            "output": skill.get("output_schema"),
        })
    return json.dumps(skills_by_agent, indent=2)


def _parse_plan_json(result_text: str) -> dict:
    """Parse Claude's response into a plan dict, tolerating markdown fences."""
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        if "```" in result_text:
            block = result_text.split("```")[1]
            if block.lstrip().lower().startswith("json"):
                block = block.lstrip()[4:]
            return json.loads(block.strip())
        raise ValueError(f"Could not parse Claude's response: {result_text}")


def decompose_plan(user_request: str) -> dict:
    """
    Take a complex user request and decompose it into agent-executable steps
    using Claude, then persist it as a pending_approval plan.

    Returns:
        {
            "ok": True,
            "plan_id": "uuid",
            "decomposition": [
                {"step": 0, "agent": "scout", "skill": "scan_jobs",
                 "params": {...}, "depends_on": []},
                ...
            ],
            "reasoning": "Why I chose this decomposition...",
        }
    or {"ok": False, "error": "..."} on failure.
    """
    try:
        c = _get_client()
        if c is None:
            return {"ok": False, "error": "ANTHROPIC_API_KEY not set"}

        skills_available = get_all_skills_for_prompt()

        prompt = f"""You are an AI task planner for ASFA, a personal operating system with specialized agents.

User request: {user_request}

Available agents and their skills:
{skills_available}

Decompose this request into steps that ASFA agents can execute. For each step:
1. Pick an agent and one of their skills
2. Define the input parameters (JSON)
3. Note dependencies on prior steps (if any)

Return ONLY valid JSON in this exact format:
{{
  "reasoning": "Why you chose this decomposition",
  "steps": [
    {{"step": 0, "agent": "scout", "skill": "scan_jobs", "params": {{"keywords": ["retail"], "location": "Erith"}}, "depends_on": []}},
    {{"step": 1, "agent": "scout", "skill": "filter_results", "params": {{"filters": {{"min_salary": 25000}}}}, "depends_on": [0]}}
  ]
}}

Think step-by-step. Only decompose into steps that are actually executable by the available skills. Be concise."""

        response = c.messages.create(
            model=MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        result_text = response.content[0].text.strip()
        plan_json = _parse_plan_json(result_text)

        plan_id = str(uuid.uuid4())
        decomposition = plan_json.get("steps", [])
        reasoning = plan_json.get("reasoning", "")

        db.create_plan(plan_id, user_request, json.dumps(decomposition), reasoning)

        return {
            "ok": True,
            "plan_id": plan_id,
            "decomposition": decomposition,
            "reasoning": reasoning,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def execute_plan(plan_id: str) -> dict:
    """
    Execute all steps in an approved plan, logging each step's result.

    This is a stub — real execution would invoke agents via RPC, subprocess, or
    HTTP. For now each step is logged as a simulated success so the approval →
    execution → results chain is fully exercised end to end.

    Returns {"ok": True, "plan_id": ..., "results": [{step, status}, ...]} or
    {"ok": False, "error": "..."}.
    """
    plan = db.get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "Plan not found"}

    if plan["status"] != "approved":
        return {"ok": False, "error": f"Plan status is {plan['status']}, not approved"}

    try:
        decomposition = json.loads(plan["decomposition"]) if plan["decomposition"] else []
    except (TypeError, ValueError):
        decomposition = []

    db.set_plan_status(plan_id, "executing")
    results = []

    for step in decomposition:
        started = time.monotonic()
        agent = step.get("agent", "")
        skill = step.get("skill", "")
        params = step.get("params", {})
        # For now, just log that we would execute this. Real implementation
        # would actually call the agent's skill here.
        output = {"simulated": True, "message": f"Would execute {agent}.{skill}"}
        dur_ms = int((time.monotonic() - started) * 1000)
        db.log_plan_execution(
            plan_id,
            step.get("step", len(results)),
            agent,
            skill,
            json.dumps(params),
            json.dumps(output),
            "success",
            duration_ms=dur_ms,
        )
        results.append({"step": step.get("step", len(results)), "status": "success"})

    db.set_plan_status(plan_id, "complete")
    return {"ok": True, "plan_id": plan_id, "results": results}
