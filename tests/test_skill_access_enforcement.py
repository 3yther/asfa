"""Access enforcement in skill execution (Mission Control approval plane, Task 2).

execute_skill() gates on the agent_access_rules allowlist; execute_plan() records
a denied step as status="blocked" and keeps going. Uses an ISOLATED temp SQLite
DB (ASFA_DB_PATH) exactly like tests/test_access_rules.py — the repo has no
conftest.py, so the throwaway DB + env are set BEFORE importing the DB layer.

    python -m pytest tests/test_skill_access_enforcement.py -v
"""
import json
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_enforce_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
from services import skill_executor  # noqa: E402
from services.planner import execute_plan  # noqa: E402

db.init_db()
db.init_agent_data()


def test_execute_skill_with_allowed_access_runs_skill():
    ran = {"called": False}

    def _impl(params):
        ran["called"] = True
        return {"ok": True}

    skill_executor.register_skill_impl("worker", "allowed_skill", _impl)
    db.set_access_rule("worker", "allowed_skill", allowed=True)

    res = skill_executor.execute_skill("worker", "allowed_skill", {})
    assert res["success"] is True
    assert res["output"] == {"ok": True}
    assert ran["called"] is True


def test_execute_skill_with_denied_access_returns_blocked():
    ran = {"called": False}

    def _impl(params):
        ran["called"] = True
        return {"ok": True}

    skill_executor.register_skill_impl("worker", "denied_skill", _impl)
    db.set_access_rule("worker", "denied_skill", allowed=False)

    res = skill_executor.execute_skill("worker", "denied_skill", {})
    assert res["success"] is False
    assert res["output"] == "access denied: denied_skill"
    assert res.get("blocked") is True
    assert ran["called"] is False  # the skill body never ran


def _register_three_step_impls(calls):
    for name in ("s1", "s2", "s3"):
        def _make(n):
            def _impl(params):
                calls.append(n)
                return {"step": n}
            return _impl
        skill_executor.register_skill_impl("crew", name, _make(name))


def test_plan_execution_continues_after_blocked_step():
    calls = []
    _register_three_step_impls(calls)
    db.set_access_rule("crew", "s1", allowed=True)
    db.set_access_rule("crew", "s2", allowed=False)   # middle step blocked
    db.set_access_rule("crew", "s3", allowed=True)

    decomposition = [
        {"step": 0, "agent": "crew", "skill": "s1", "params": {}},
        {"step": 1, "agent": "crew", "skill": "s2", "params": {}},
        {"step": 2, "agent": "crew", "skill": "s3", "params": {}},
    ]
    db.create_plan("plan-continue", "three steps", json.dumps(decomposition), "why")
    db.approve_plan("plan-continue")

    result = execute_plan("plan-continue")

    assert result["ok"] is True
    statuses = {r["step"]: r["status"] for r in result["results"]}
    assert statuses == {0: "success", 1: "blocked", 2: "success"}
    # the blocked step's skill body never ran; the plan still reached step 3
    assert calls == ["s1", "s3"]


def test_plan_execution_logs_blocked_status():
    calls = []
    for name in ("t1", "t2"):
        def _make(n):
            def _impl(params):
                calls.append(n)
                return {"step": n}
            return _impl
        skill_executor.register_skill_impl("crew2", name, _make(name))
    db.set_access_rule("crew2", "t1", allowed=True)
    db.set_access_rule("crew2", "t2", allowed=False)

    decomposition = [
        {"step": 0, "agent": "crew2", "skill": "t1", "params": {}},
        {"step": 1, "agent": "crew2", "skill": "t2", "params": {}},
    ]
    db.create_plan("plan-logblk", "log blocked", json.dumps(decomposition), "why")
    db.approve_plan("plan-logblk")

    execute_plan("plan-logblk")

    # log_plan_execution persisted the denied step with status="blocked".
    rows = {r["step_index"]: r for r in db.get_plan_results("plan-logblk")}
    assert rows[0]["status"] == "success"
    assert rows[1]["status"] == "blocked"
    # the denial reason is captured on the blocked row
    assert "access denied" in (rows[1]["error"] or "")
