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


def _needs_resolution(value, missing_ok: bool = False) -> bool:
    """True if a param value should be replaced by a depended-on step's output:
    an unresolved "{{...}}" template placeholder, or (when missing_ok) absent."""
    if value is None:
        return missing_ok
    return isinstance(value, str) and "{{" in value


def execute_plan(plan_id: str) -> dict:
    """
    Execute all steps in an approved plan — for real this time.

    Each step is routed to its agent's registered skill implementation via the
    skill executor, the real output is logged to plan_executions and the agent
    audit trail, and energy is adjusted (+5 success / -10 failure). Outputs from
    depended-on steps are threaded into the next step's params (e.g. scan_jobs's
    `matches` become filter_results's `jobs`) so a multi-step plan actually
    flows data end to end.

    Returns {"ok": True, "plan_id": ..., "results": [...]} or
    {"ok": False, "error": "..."}.
    """
    from services.skill_executor import execute_skill

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
    step_outputs = {}  # step_index -> output dict, for dependency threading

    for idx, step in enumerate(decomposition):
        agent_id = step.get("agent", "")
        skill_name = step.get("skill", "")
        step_index = step.get("step", idx)
        params = dict(step.get("params", {}) or {})

        # Thread outputs from depended-on steps into this step's params. The
        # planner decides params at decomposition time and can't know upstream
        # results, so it leaves placeholders like "{{step_0.matches}}". We
        # resolve the common feeds (a job list, and a single job_id) from the
        # depended-on step's real output, overriding any placeholder value.
        for dep in step.get("depends_on", []) or []:
            dep_out = step_outputs.get(dep)
            if not isinstance(dep_out, dict):
                continue
            job_list = dep_out.get("matches")
            if job_list is None:
                job_list = dep_out.get("filtered")
            if job_list is not None and _needs_resolution(params.get("jobs"), missing_ok=True):
                params["jobs"] = job_list
            if _needs_resolution(params.get("job_id")) and job_list:
                # Thread both the id and the full job dict, so the apply step can
                # record the real company/title even though scan ids are the
                # upstream provider's, not scout_jobs row ids.
                params["job_id"] = job_list[0].get("id")
                params.setdefault("job", job_list[0])

        # Execute the skill (this calls real code now).
        skill_result = execute_skill(agent_id, skill_name, params)
        status = "success" if skill_result["success"] else "failure"

        db.log_plan_execution(
            plan_id,
            step_index,
            agent_id,
            skill_name,
            json.dumps(params),
            json.dumps(skill_result["output"]) if skill_result["success"] else None,
            status,
            error=skill_result.get("error"),
            duration_ms=skill_result["duration_ms"],
        )

        db.log_audit(
            agent_id, f"execute_skill:{skill_name}", status,
            reason=f"Plan step {step_index}",
            details={"params": params, "output": skill_result["output"]},
            duration_ms=skill_result["duration_ms"],
        )

        db.update_energy(agent_id, +5 if skill_result["success"] else -10)

        step_outputs[step_index] = skill_result.get("output") or {}
        results.append({
            "step": step_index,
            "agent": agent_id,
            "skill": skill_name,
            "status": status,
            "output": skill_result["output"],
            "error": skill_result.get("error"),
            "duration_ms": skill_result["duration_ms"],
        })

    db.set_plan_status(plan_id, "complete")
    return {"ok": True, "plan_id": plan_id, "results": results}
