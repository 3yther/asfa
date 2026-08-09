"""Approval-plane API endpoints (Mission Control approval plane, Task 3).

GET /api/plan/pending, /api/agents/<id>/access, /api/agents/<id>/activity — all
behind the session auth gate. Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) and
the login flow, mirroring tests/test_apprenticeships.py. Env is set BEFORE import.

    python -m pytest tests/test_api_approval_endpoints.py -v
"""
import json
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_apiapproval_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402
import database as db       # noqa: E402


def setup_module(module):
    """Seed fixtures into THIS module's isolated DB.

    The root conftest's autouse `_isolate_module_db` fixture hands each module a
    fresh SQLite file and re-inits the schema *after* import but *before* the
    tests — and it is ordered ahead of setup_module. So seeds belong here, not at
    import time (import-time writes land in the shared session DB and get swapped
    out). Schema + agent roster/skills/access-rule seed are already in place from
    the fixture's init steps.
    """
    # A pending plan for the approval queue.
    db.create_plan(
        "pending-api-1", "Scan retail jobs in London",
        json.dumps([{"step": 0, "agent": "scout", "skill": "scan_jobs"},
                    {"step": 1, "agent": "scout", "skill": "filter_results"}]),
        "manual demo")

    # Access rules for nexus (a roster agent) — one allowed, one denied.
    db.set_access_rule("nexus", "market_data", allowed=True)
    db.set_access_rule("nexus", "trade_write", allowed=False)

    # Two plan_executions for nexus: one success, one blocked.
    db.log_plan_execution("pending-api-1", 0, "nexus", "market_data",
                          json.dumps({"symbols": ["AAPL"]}), json.dumps({"ok": 1}),
                          "success")
    db.log_plan_execution("pending-api-1", 1, "nexus", "trade_write",
                          json.dumps({"qty": 5}), "access denied: trade_write",
                          "blocked", error="access denied: trade_write")


def _client(login=True):
    c = app_module.app.test_client()
    if login:
        c.post("/login", data={"password": os.environ["APP_PASSWORD"]},
               follow_redirects=True)
    return c


def test_get_pending_plans_returns_pending_plans():
    r = _client().get("/api/plan/pending")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    row = next(p for p in data if p["plan_id"] == "pending-api-1")
    assert row["status"] == "pending"
    assert row["step_count"] == 2
    assert row["agent_names"] == ["scout"]
    assert "user_request" in row and "created_at" in row


def test_get_pending_plans_requires_auth():
    r = _client(login=False).get("/api/plan/pending")
    assert r.status_code == 401


def test_get_agent_access_returns_rules():
    r = _client().get("/api/agents/nexus/access")
    assert r.status_code == 200
    data = r.get_json()
    assert data["agent_id"] == "nexus"
    rules = {x["skill_name"]: x["allowed"] for x in data["rules"]}
    assert rules["market_data"] is True
    assert rules["trade_write"] is False
    for x in data["rules"]:
        assert set(x.keys()) == {"skill_name", "allowed"}


def test_get_agent_access_not_found():
    r = _client().get("/api/agents/nonexistent/access")
    assert r.status_code == 404


def test_get_agent_activity_returns_recent():
    r = _client().get("/api/agents/nexus/activity")
    assert r.status_code == 200
    data = r.get_json()
    assert data["agent_id"] == "nexus"
    assert isinstance(data["activity"], list) and data["activity"]
    first = data["activity"][0]
    assert set(first.keys()) == {"action", "details", "timestamp", "status"}
    # newest first: the blocked trade_write (step 1) was logged last
    statuses = {a["status"] for a in data["activity"]}
    assert {"success", "blocked"} <= statuses


def test_get_agent_activity_limits_to_20():
    # 25 rows for a different roster agent -> endpoint returns exactly 20.
    for i in range(25):
        db.log_plan_execution("bulk-plan", i, "axiom", f"skill_{i}",
                              json.dumps({"i": i}), json.dumps({"ok": i}), "success")
    r = _client().get("/api/agents/axiom/activity")
    assert r.status_code == 200
    activity = r.get_json()["activity"]
    assert len(activity) == 20


def test_get_agent_activity_not_found():
    r = _client().get("/api/agents/nonexistent/activity")
    assert r.status_code == 404
