#!/usr/bin/env python3
"""ASFA MCP server — exposes ASFA as tools over stdio so Claude Desktop / Code
can drive it in natural language.

DISCOVERY (Part 6): the current official Python SDK is the `mcp` package
(stable 1.28.x; v2.0.0b1 is a pre-release we avoid). High-level API:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("asfa"); @mcp.tool(); mcp.run()  # run() defaults to stdio.

Design: tools call the ASFA `database` layer DIRECTLY (not the Flask HTTP API),
so there's no auth cookie/CSRF dance and no need to boot Flask + the scheduler
+ Telegram bot in this subprocess.

Auth: write tools require a `token` argument matching ASFA_MCP_TOKEN (which
defaults to APP_PASSWORD). If neither is set, write tools refuse — reads stay
open since stdio access is already local/trusted.

Run manually:   python scripts/asfa_mcp.py
Claude Desktop: add to claude_desktop_config.json (see README note below).
"""

import hmac
import json
import os
import sys
from datetime import date, datetime, timedelta

# Import the ASFA database layer from the repo root (this file lives in scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("asfa")

# Write-tool auth: ASFA_MCP_TOKEN, falling back to APP_PASSWORD.
_WRITE_TOKEN = os.environ.get("ASFA_MCP_TOKEN") or os.environ.get("APP_PASSWORD") or ""


def _check_token(token: str) -> bool:
    """Constant-time comparison; writes are refused if no token is configured."""
    return bool(_WRITE_TOKEN) and hmac.compare_digest(token or "", _WRITE_TOKEN)


# ── Read tools ───────────────────────────────────────────────────────────────

@mcp.tool()
def get_mission_status() -> dict:
    """Today's missions/tasks and how many are still open."""
    missions = db.get_today_missions()
    open_count = sum(1 for m in missions if not m.get("completed"))
    return {"date": date.today().isoformat(), "missions": missions,
            "open_count": open_count, "total": len(missions)}


@mcp.tool()
def get_gym_summary(days: int = 7) -> dict:
    """Training summary for the last `days`: sessions, sets, total volume (kg),
    XP earned, and any PRs hit."""
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    end = date.today().isoformat()
    rows = db.get_gym_sets_for_export(start, end)
    sessions = {r["date"] for r in rows}
    volume = 0.0
    for r in rows:
        try:
            volume += float(r["weight_kg"] or 0) * int(r["reps"] or 0)
        except (TypeError, ValueError):
            pass
    prs = [f'{r["exercise"]} {r["weight_kg"]}kg x{r["reps"]}' for r in rows if r.get("pr") == "yes"]
    xp = sum(int(r.get("xp_earned") or 0) for r in rows)
    return {"days": days, "sessions": len(sessions), "sets": len(rows),
            "total_volume_kg": round(volume, 1), "xp_earned": xp, "prs": prs}


@mcp.tool()
def get_scout_pipeline() -> list:
    """All jobs in the Scout pipeline (Kanban), with stage and CV match score."""
    out = []
    for j in db.get_scout_pipeline():
        out.append({"id": j["id"], "job_title": j["job_title"], "company": j["company"],
                    "stage": j["stage"], "cv_match_score": j.get("cv_match_score"),
                    "date_saved": j.get("date_saved")})
    return out


@mcp.tool()
def get_scent_recommendation() -> dict:
    """Fragrance suggestion: your signature scent plus neglected bottles due for
    a wear (from collection rotation stats)."""
    stats = db.get_fragrance_stats()
    return {"signature": stats.get("signature"), "neglected": stats.get("neglected"),
            "least_worn": stats.get("least_worn")}


@mcp.tool()
def get_body_comp_latest() -> dict:
    """Most recent body-composition scan (weight, body-fat %, FFM, etc.), or a
    note if none logged."""
    latest = db.latest_body_composition()
    return latest or {"note": "No body-composition scans logged yet."}


@mcp.tool()
def search_audit_log(query: str = "", limit: int = 20) -> list:
    """Recent agent audit-log entries, optionally filtered by a substring match
    across action/status/reason fields."""
    entries = db.get_audit_log(limit=200)
    q = (query or "").lower()
    if q:
        entries = [e for e in entries
                   if q in json.dumps(e, default=str).lower()]
    return entries[:limit]


@mcp.tool()
def list_agents() -> list:
    """All registered ASFA agents with their status."""
    return db.get_agents()


# ── Write tools (token-guarded) ──────────────────────────────────────────────

@mcp.tool()
def log_gym_set(exercise: str, weight: float, reps: int, rpe: int = 0,
                token: str = "") -> dict:
    """Log a working set for `exercise` (matched by name) into today's session,
    creating a session if none is open. rpe 6–10 optional (0/blank = none).
    Requires a valid write token."""
    if not _check_token(token):
        return {"ok": False, "error": "unauthorized — pass a valid write token"}
    # Resolve the exercise by (case-insensitive) name.
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, name FROM gym_exercises WHERE LOWER(name) = LOWER({ph}) LIMIT 1",
                    (exercise,))
        row = cur.fetchone()
        if not row:
            cur.execute(f"SELECT id, name FROM gym_exercises WHERE LOWER(name) LIKE LOWER({ph}) LIMIT 1",
                        (f"%{exercise}%",))
            row = cur.fetchone()
    if not row:
        return {"ok": False, "error": f"no exercise matching '{exercise}'"}
    ex_id, ex_name = row["id"], row["name"]

    active = db.get_active_session()
    if active:
        session_id = active["id"]
    else:
        session_id = db.create_session(None, date.today().isoformat(),
                                       datetime.now().isoformat())
    # Next set number for this exercise in this session.
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM gym_sets WHERE session_id = {ph} AND exercise_id = {ph}",
                    (session_id, ex_id))
        set_number = (cur.fetchone()["n"] or 0) + 1

    rpe_val = rpe if rpe and 6 <= int(rpe) <= 10 else None
    result = db.log_set(session_id, ex_id, set_number, "working",
                        float(weight), int(reps), rpe=rpe_val)
    return {"ok": True, "exercise": ex_name, "session_id": session_id,
            "set_number": set_number, "is_pr": result.get("is_pr"),
            "xp_earned": result.get("xp_earned")}


@mcp.tool()
def add_scout_job(title: str, company: str, url: str = "", token: str = "") -> dict:
    """Add a job to the Scout pipeline (stage 'saved'). Requires a write token."""
    if not _check_token(token):
        return {"ok": False, "error": "unauthorized — pass a valid write token"}
    if not title or not company:
        return {"ok": False, "error": "title and company are required"}
    pid = db.add_scout_pipeline_job(title, company, job_url=url or None, source="mcp")
    return {"ok": True, "id": pid, "title": title, "company": company, "stage": "saved"}


@mcp.tool()
def move_scout_job(job_id: int, stage: str, token: str = "") -> dict:
    """Move a pipeline job to a new stage (saved/applied/interview/offer/rejected).
    Requires a write token."""
    if not _check_token(token):
        return {"ok": False, "error": "unauthorized — pass a valid write token"}
    if stage not in db.SCOUT_STAGES:
        return {"ok": False, "error": f"stage must be one of {', '.join(db.SCOUT_STAGES)}"}
    job = db.update_scout_pipeline(job_id, stage=stage)
    if not job:
        return {"ok": False, "error": f"no pipeline job with id {job_id}"}
    return {"ok": True, "id": job_id, "stage": stage}


if __name__ == "__main__":
    mcp.run()  # stdio transport (default)
