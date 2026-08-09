# Mission Control Approval Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface ASFA's existing plan-approval gate inside the Mission Control HUD and add real per-agent access-rule enforcement to plan execution.

**Architecture:** Reuse the existing `execution_plans` / `plan_executions` / audit tables as the single source of truth for approvals and activity. Add exactly one new table (`agent_access_rules`) and enforce it inside `execute_skill`; everything else is thin read wrappers over existing data plus frontend that reuses the existing design system.

**Tech Stack:** Python 3.12, Flask 3.0, SQLite (local) / PostgreSQL (prod via `DATABASE_URL`, psycopg2), raw SQL in `database.py`, vanilla JS + Jinja templates, pytest.

## Global Constraints

- Storage is dual-backend: **SQLite locally, PostgreSQL in prod.** Every SQL statement uses the existing placeholder idiom `ph = "%s" if USE_POSTGRES else "?"` and the `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY` rewrite already applied in `database.py`.
- **Never name the SQLAlchemy instance `db`.** `database.py` is imported as `db` everywhere. This feature is pure raw-SQL `database.py` work — do **not** touch `models.py`.
- **Auth gate fails closed.** Every new route requires `session["authed"]`; do NOT add anything to `_PUBLIC_ENDPOINTS` in `app.py`.
- **All frontend fetches use `credentials:'include'`.** Match the existing `API` object idiom in `mission_control.html` (line ~429).
- **Read-only toward the trading bots.** Nothing in this feature may issue trades or mutate the stock-scanner/crypto bots. It gates ASFA's *own* plan execution only.
- **Enforcement rule (verbatim from spec):** `is_skill_allowed` returns `False` only when an explicit `allowed=FALSE` row exists for `(agent_id, skill_name)`; otherwise `True`. Denials are always explicit.
- **Blocked-step behavior:** a disallowed step is logged with `status='blocked'` and the plan **continues** to the next step (matches existing per-step failure handling).
- Tests isolate the DB with a temp SQLite file via `ASFA_DB_PATH` and must `os.environ.pop("DATABASE_URL", None)` before importing `database` (existing pattern, e.g. `tests/test_finance.py`).

---

## Task 1: `agent_access_rules` table + access-rule DB helpers

**Files:**
- Modify: `database.py` — add table to `_ensure_agent_data_tables()` (def at line ~3665); add helper functions near the other agent helpers (after `get_all_skills`, ~line 4310); call seed in `init_db()`.
- Test: `tests/test_access_rules.py` (create)

**Interfaces:**
- Produces:
  - `db.is_skill_allowed(agent_id: str, skill_name: str) -> bool`
  - `db.get_access_rules(agent_id: str) -> list[dict]` — rows `{skill_name, allowed}` (allowed as bool), ordered by skill_name
  - `db.set_access_rule(agent_id: str, skill_name: str, allowed: bool) -> None` — upsert
  - `db.seed_access_rules() -> int` — inserts `allowed=TRUE` rows for every `(agent_id, skill_name)` in `agent_skills`, idempotent; returns count inserted

- [ ] **Step 1: Write the failing test**

Create `tests/test_access_rules.py`:

```python
"""agent_access_rules — enforcement allowlist for plan execution.

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db.
"""
import os
import tempfile

_TMP_DB = tempfile.mktemp(suffix="_access.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "testpass")

import database as db  # noqa: E402

db.init_db()


def test_absent_pair_is_allowed_by_default():
    # No row exists for this pair -> allowed.
    assert db.is_skill_allowed("nexus", "no_such_skill") is True


def test_explicit_deny_blocks():
    db.set_access_rule("nexus", "market_data", allowed=False)
    assert db.is_skill_allowed("nexus", "market_data") is False


def test_explicit_allow_permits():
    db.set_access_rule("nexus", "market_data", allowed=True)
    assert db.is_skill_allowed("nexus", "market_data") is True


def test_get_access_rules_returns_rows():
    db.set_access_rule("scout", "scan_jobs", allowed=True)
    db.set_access_rule("scout", "apply_for_role", allowed=False)
    rules = {r["skill_name"]: r["allowed"] for r in db.get_access_rules("scout")}
    assert rules["scan_jobs"] is True
    assert rules["apply_for_role"] is False


def test_seed_creates_allow_rows_for_registered_skills():
    # Register a skill, then seed -> an allow row appears for it.
    db.add_agent_skill("tester", "ping", "test skill")
    db.seed_access_rules()
    assert db.is_skill_allowed("tester", "ping") is True
    assert any(r["skill_name"] == "ping" for r in db.get_access_rules("tester"))
```

> Note: `db.add_agent_skill(agent_id, skill_name, description)` already exists in `database.py` (used by the skill registry). Confirm its exact name with `grep -n "def add_agent_skill" database.py`; if it differs, insert a skill row directly with the same INSERT the registry uses.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_access_rules.py -v`
Expected: FAIL — `AttributeError: module 'database' has no attribute 'is_skill_allowed'`

- [ ] **Step 3: Add the table to `_ensure_agent_data_tables()`**

In `database.py`, inside the `stmts = [ ... ]` list in `_ensure_agent_data_tables()`, add this CREATE alongside the other agent tables:

```python
        """CREATE TABLE IF NOT EXISTS agent_access_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            allowed BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(agent_id, skill_name)
        )""",
```

(The existing loop at the end of `_ensure_agent_data_tables()` already applies the `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY` rewrite for Postgres. `BOOLEAN DEFAULT 1` is valid in SQLite; for Postgres it is rewritten below in Step 4's helpers by using `TRUE`/`FALSE` literals in inserts — the column type `BOOLEAN` is valid in both.)

- [ ] **Step 4: Add the helper functions**

In `database.py`, after `get_all_skills()` (~line 4310), add:

```python
def is_skill_allowed(agent_id, skill_name):
    """Access-rule check. False only when an explicit allowed=FALSE row exists
    for (agent_id, skill_name); otherwise True (default-allow)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT allowed FROM agent_access_rules "
            f"WHERE agent_id = {ph} AND skill_name = {ph}",
            (agent_id, skill_name))
        row = cur.fetchone()
        if row is None:
            return True
        return bool(row["allowed"])


def get_access_rules(agent_id):
    """All access rules for an agent: [{skill_name, allowed(bool)}], by name."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT skill_name, allowed FROM agent_access_rules "
            f"WHERE agent_id = {ph} ORDER BY skill_name",
            (agent_id,))
        return [{"skill_name": r["skill_name"], "allowed": bool(r["allowed"])}
                for r in cur.fetchall()]


def set_access_rule(agent_id, skill_name, allowed=True):
    """Upsert one access rule."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        val = (True if allowed else False) if USE_POSTGRES else (1 if allowed else 0)
        if USE_POSTGRES:
            cur.execute(
                f"INSERT INTO agent_access_rules (agent_id, skill_name, allowed) "
                f"VALUES ({ph},{ph},{ph}) "
                f"ON CONFLICT (agent_id, skill_name) DO UPDATE SET allowed = {ph}",
                (agent_id, skill_name, val, val))
        else:
            cur.execute(
                f"INSERT INTO agent_access_rules (agent_id, skill_name, allowed) "
                f"VALUES ({ph},{ph},{ph}) "
                f"ON CONFLICT(agent_id, skill_name) DO UPDATE SET allowed = {ph}",
                (agent_id, skill_name, val, val))


def seed_access_rules():
    """Create an allowed=TRUE rule for every registered (agent_id, skill_name)
    in agent_skills. Idempotent; returns number of new rows inserted."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT agent_id, skill_name FROM agent_skills")
        pairs = [(r["agent_id"], r["skill_name"]) for r in cur.fetchall()]
        ph = "%s" if USE_POSTGRES else "?"
        inserted = 0
        for agent_id, skill_name in pairs:
            if USE_POSTGRES:
                cur.execute(
                    f"INSERT INTO agent_access_rules (agent_id, skill_name, allowed) "
                    f"VALUES ({ph},{ph},TRUE) "
                    f"ON CONFLICT (agent_id, skill_name) DO NOTHING",
                    (agent_id, skill_name))
            else:
                cur.execute(
                    f"INSERT OR IGNORE INTO agent_access_rules "
                    f"(agent_id, skill_name, allowed) VALUES ({ph},{ph},1)",
                    (agent_id, skill_name))
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return inserted
```

- [ ] **Step 5: Seed on startup**

In `database.py`, find the end of `init_db()` (search `def init_db`). After the existing seed calls near the end of the function body, add:

```python
    try:
        seed_access_rules()
    except Exception as e:
        logger.warning("seed_access_rules failed (non-fatal): %s", e)
```

(If `init_db` has no `logger` in scope, use `print` or the module logger already imported at top of `database.py` — grep `^logger` in the file to confirm; the module defines one.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_access_rules.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_access_rules.py
git commit -m "feat: agent_access_rules table + allowlist helpers"
```

---

## Task 2: Enforce access rules in `execute_skill` + `blocked` status in `execute_plan`

**Files:**
- Modify: `services/skill_executor.py` — `execute_skill()` (def ~line 39)
- Modify: `services/planner.py` — `execute_plan()` status mapping (~lines 201-235)
- Test: `tests/test_access_rules.py` (extend)

**Interfaces:**
- Consumes: `db.is_skill_allowed` (Task 1); existing `execute_skill(agent_id, skill_name, params) -> {success, output, error, duration_ms}`.
- Produces: `execute_skill` now also returns `"blocked": True` in the envelope when access is denied. `execute_plan` logs blocked steps with `status="blocked"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_access_rules.py`:

```python
from services import skill_executor  # noqa: E402


def test_execute_skill_blocks_denied_and_does_not_run():
    ran = {"called": False}

    def _impl(params):
        ran["called"] = True
        return {"ok": True}

    skill_executor.register_skill_impl("guard", "danger", _impl)
    db.set_access_rule("guard", "danger", allowed=False)

    res = skill_executor.execute_skill("guard", "danger", {})
    assert res["success"] is False
    assert res.get("blocked") is True
    assert "access denied" in (res["error"] or "")
    assert ran["called"] is False  # the underlying skill never ran


def test_execute_skill_runs_when_allowed():
    def _impl(params):
        return {"ok": True}

    skill_executor.register_skill_impl("guard", "safe", _impl)
    db.set_access_rule("guard", "safe", allowed=True)

    res = skill_executor.execute_skill("guard", "safe", {})
    assert res["success"] is True
    assert res["output"] == {"ok": True}


def test_execute_plan_marks_blocked_step_and_continues():
    import json

    calls = []

    def _s1(params):
        calls.append("s1")
        return {"ok": 1}

    def _s2(params):
        calls.append("s2")
        return {"ok": 2}

    skill_executor.register_skill_impl("plnr", "step_one", _s1)
    skill_executor.register_skill_impl("plnr", "step_two", _s2)
    db.set_access_rule("plnr", "step_one", allowed=False)  # first step blocked
    db.set_access_rule("plnr", "step_two", allowed=True)

    decomposition = [
        {"step": 0, "agent": "plnr", "skill": "step_one", "params": {}},
        {"step": 1, "agent": "plnr", "skill": "step_two", "params": {}},
    ]
    db.create_plan("plan-blk-1", "test", json.dumps(decomposition), "because")
    db.approve_plan("plan-blk-1")

    from services.planner import execute_plan
    result = execute_plan("plan-blk-1")

    assert result["ok"] is True
    statuses = {r["step"]: r["status"] for r in result["results"]}
    assert statuses[0] == "blocked"
    assert statuses[1] == "success"
    assert calls == ["s2"]  # blocked step never executed, plan continued

    rows = {r["step_index"]: r["status"] for r in db.get_plan_results("plan-blk-1")}
    assert rows[0] == "blocked"
    assert rows[1] == "success"
```

> Confirm `db.create_plan(plan_id, user_request, decomposition, reasoning)`, `db.approve_plan(plan_id)`, and `db.get_plan_results(plan_id)` signatures with `grep -n "def create_plan\|def approve_plan\|def get_plan_results" database.py` (they exist per the spec). `db.get_plan_results` rows expose `step_index` and `status`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_access_rules.py -k "blocked or runs_when_allowed" -v`
Expected: FAIL — `execute_skill` has no access check yet (`blocked` key missing; denied skill still runs).

- [ ] **Step 3: Add the access check to `execute_skill`**

In `services/skill_executor.py`, at the very start of `execute_skill()` (right after `key = f"{agent_id}/{skill_name}"`), insert:

```python
    import database as db
    if not db.is_skill_allowed(agent_id, skill_name):
        return {
            "success": False,
            "output": None,
            "error": f"access denied: {skill_name}",
            "duration_ms": 0,
            "blocked": True,
        }
```

- [ ] **Step 4: Map the `blocked` status in `execute_plan`**

In `services/planner.py`, inside the per-step loop, replace:

```python
        skill_result = execute_skill(agent_id, skill_name, params)
        status = "success" if skill_result["success"] else "failure"
```

with:

```python
        skill_result = execute_skill(agent_id, skill_name, params)
        if skill_result.get("blocked"):
            status = "blocked"
        elif skill_result["success"]:
            status = "success"
        else:
            status = "failure"
```

Leave the rest of the loop unchanged: `db.log_plan_execution(...)` already receives `status`, `db.log_audit(..., status, ...)` records the outcome (now possibly `"blocked"`), `db.update_energy(agent_id, +5 if skill_result["success"] else -10)` applies the failure penalty to blocked steps, and the per-step `results.append({... "status": status ...})` carries it back.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_access_rules.py -v`
Expected: PASS (all, including the 3 new ones)

- [ ] **Step 6: Commit**

```bash
git add services/skill_executor.py services/planner.py tests/test_access_rules.py
git commit -m "feat: enforce agent access rules in execute_skill; log blocked steps"
```

---

## Task 3: Read endpoints — pending plans, agent access, agent activity

**Files:**
- Modify: `database.py` — add `get_pending_plans()` and `get_agent_activity()` near the other plan helpers (~line 4420, after `get_plan_results`).
- Modify: `app.py` — add three routes near the existing agent/plan routes (`/api/plan/<id>` is ~line 4218; `/api/agents/<id>/detail` is ~line 3593).
- Test: `tests/test_access_rules.py` (extend with a route smoke test)

**Interfaces:**
- Consumes: existing `execution_plans`, `plan_executions`, `db.get_agents()`.
- Produces:
  - `db.get_pending_plans() -> list[dict]` — rows `{plan_id, user_request, step_count, created_at}` for `status='pending_approval'`, newest first
  - `db.get_agent_activity(agent_id, limit=10) -> list[dict]` — rows `{skill_name, status, executed_at, error}` from `plan_executions`, newest first
  - `GET /api/plan/pending -> {"plans": [...]}`
  - `GET /api/agents/<agent_id>/access -> {"rules": [...]}`
  - `GET /api/agents/<agent_id>/activity -> {"activity": [...]}`

- [ ] **Step 1: Add the DB helpers**

In `database.py`, after `get_plan_results()`:

```python
def get_pending_plans():
    """Execution plans awaiting approval, newest first, with a step count."""
    _ensure_agent_data_tables()
    import json as _json
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT plan_id, user_request, decomposition, created_at "
            "FROM execution_plans WHERE status = 'pending_approval' "
            "ORDER BY created_at DESC")
        out = []
        for r in cur.fetchall():
            try:
                steps = _json.loads(r["decomposition"]) if r["decomposition"] else []
            except (TypeError, ValueError):
                steps = []
            out.append({
                "plan_id": r["plan_id"],
                "user_request": r["user_request"],
                "step_count": len(steps),
                "created_at": r["created_at"],
            })
        return out


def get_agent_activity(agent_id, limit=10):
    """Recent plan-execution rows for one agent, newest first."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT skill_name, status, error, executed_at FROM plan_executions "
            f"WHERE agent_id = {ph} ORDER BY executed_at DESC LIMIT {ph}",
            (agent_id, int(limit)))
        return [{"skill_name": r["skill_name"], "status": r["status"],
                 "error": r["error"], "executed_at": r["executed_at"]}
                for r in cur.fetchall()]
```

- [ ] **Step 2: Add the routes**

In `app.py`, near the existing plan routes, add:

```python
@app.route("/api/plan/pending")
def api_plan_pending():
    return jsonify({"plans": db.get_pending_plans()})
```

And near the existing `/api/agents/<agent_id>/detail` route, add:

```python
@app.route("/api/agents/<agent_id>/access")
def api_agent_access(agent_id):
    return jsonify({"rules": db.get_access_rules(agent_id)})


@app.route("/api/agents/<agent_id>/activity")
def api_agent_activity(agent_id):
    return jsonify({"activity": db.get_agent_activity(agent_id, 10)})
```

- [ ] **Step 3: Write a route smoke test**

Append to `tests/test_access_rules.py`:

```python
import app as app_module  # noqa: E402


def _client():
    c = app_module.app.test_client()
    c.post("/login", data={"password": os.environ["APP_PASSWORD"]},
           follow_redirects=True)
    return c


def test_pending_plans_endpoint():
    import json
    db.create_plan("plan-pending-1", "do a thing",
                   json.dumps([{"step": 0, "agent": "nexus", "skill": "x"}]),
                   "reason")
    c = _client()
    data = c.get("/api/plan/pending").get_json()
    ids = {p["plan_id"] for p in data["plans"]}
    assert "plan-pending-1" in ids
    row = next(p for p in data["plans"] if p["plan_id"] == "plan-pending-1")
    assert row["step_count"] == 1


def test_agent_access_endpoint_requires_auth():
    # No login -> gate redirects or 401 (never 200 JSON).
    raw = app_module.app.test_client().get("/api/agents/nexus/access")
    assert raw.status_code in (302, 401)


def test_agent_access_and_activity_endpoints():
    db.set_access_rule("nexus", "market_data", allowed=False)
    c = _client()
    rules = c.get("/api/agents/nexus/access").get_json()["rules"]
    assert any(r["skill_name"] == "market_data" and r["allowed"] is False
               for r in rules)
    act = c.get("/api/agents/nexus/activity").get_json()["activity"]
    assert isinstance(act, list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_access_rules.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add database.py app.py tests/test_access_rules.py
git commit -m "feat: read endpoints for pending plans, agent access rules, agent activity"
```

---

## Task 4: Agent modal — access badges, approve-pending button, approval modal, color-coded activity

**Files:**
- Modify: `templates/mission_control.html` — extend the `API` object (~line 429), the modal markup (`#modal-card`, ~line 281), CSS (~line 90-160), and the modal JS (`openModal`, ~line 1105).

No JS unit-test harness exists in this repo; this task is verified manually in Task 6.

- [ ] **Step 1: Extend the `API` object**

In `templates/mission_control.html`, inside the `API = { ... }` object (line ~429), add these members:

```javascript
  access:         (id)     => fetch('/api/agents/'+encodeURIComponent(id)+'/access',{credentials:'include'}).then(r=>r.json()),
  activity:       (id)     => fetch('/api/agents/'+encodeURIComponent(id)+'/activity',{credentials:'include'}).then(r=>r.json()),
  pendingPlans:   ()       => fetch('/api/plan/pending',{credentials:'include'}).then(r=>r.json()),
  planGet:        (pid)    => fetch('/api/plan/'+encodeURIComponent(pid),{credentials:'include'}).then(r=>r.json()),
  planApprove:    (pid)    => fetch('/api/plan/'+encodeURIComponent(pid)+'/approve',{method:'POST',credentials:'include',headers:J}).then(r=>r.json()),
  planReject:     (pid)    => fetch('/api/plan/'+encodeURIComponent(pid)+'/reject',{method:'POST',credentials:'include',headers:J}).then(r=>r.json()),
  planExecute:    (pid)    => fetch('/api/plan/'+encodeURIComponent(pid)+'/execute',{method:'POST',credentials:'include',headers:J}).then(r=>r.json()),
```

> `J` is the JSON headers const already defined next to the existing `API` members (`headers:J` is used by `awardXp`/`setStatus`). Reuse it.

- [ ] **Step 2: Add CSS for badges, activity colors, and the approval modal**

In the `<style>` block of `templates/mission_control.html`, add near the other `.arow` / `.log-list` rules (~line 140):

```css
  /* access-rule badges */
  .m-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px;}
  .m-abadge{font-size:.5rem;font-weight:700;letter-spacing:.04em;padding:2px 7px;border:1px solid;border-radius:2px;text-transform:uppercase;}
  .m-abadge.ok{color:var(--green);border-color:var(--green);box-shadow:0 0 6px #39ff1440;}
  .m-abadge.no{color:#ff2a6d;border-color:#ff2a6d;box-shadow:0 0 6px #ff2a6d40;}
  /* per-agent recent activity, color-coded by status */
  .m-actlist{font-size:.54rem;line-height:1.7;max-height:120px;overflow-y:auto;margin-top:4px;}
  .m-actlist .al{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:2px solid transparent;padding-left:6px;}
  .m-actlist .al.success{color:var(--green);border-color:var(--green);}
  .m-actlist .al.blocked{color:#ff2a6d;border-color:#ff2a6d;}
  .m-actlist .al.failure{color:#ff8c42;border-color:#ff8c42;}
  .m-actlist .al.info{color:var(--cyan);border-color:var(--cyan);}
  /* approve-pending button */
  .m-approve-btn{margin-top:10px;width:100%;padding:9px;background:#031007;color:var(--green);border:1px solid var(--green);font-weight:700;letter-spacing:.06em;cursor:pointer;box-shadow:0 0 10px #39ff1440;text-transform:uppercase;font-size:.62rem;display:none;}
  .m-approve-btn.show{display:block;}
  .m-approve-btn:hover{background:#39ff1414;text-shadow:0 0 8px var(--green);}
  /* approval modal (reuses .modal-overlay) */
  #approval.modal-overlay .modal-card{border-color:#ff2a6d;box-shadow:0 0 30px #ff2a6d55;}
  #approval .ap-h{color:#ff2a6d;text-shadow:0 0 10px #ff2a6d;font-weight:800;letter-spacing:.08em;font-size:.9rem;margin-bottom:8px;}
  #approval .ap-args{background:#000;border:1px solid var(--cyan);color:var(--cyan);font-family:'JetBrains Mono',monospace;font-size:.6rem;padding:8px;margin:8px 0;white-space:pre-wrap;max-height:180px;overflow:auto;}
  #approval .ap-row{display:flex;gap:8px;margin-top:12px;}
  #approval .ap-btn{flex:1;padding:10px;font-weight:700;letter-spacing:.06em;cursor:pointer;background:#020a02;text-transform:uppercase;font-size:.66rem;}
  #approval .ap-btn.ok{color:var(--green);border:1px solid var(--green);}
  #approval .ap-btn.ok:hover{background:#39ff1414;text-shadow:0 0 8px var(--green);}
  #approval .ap-btn.no{color:#ff2a6d;border:1px solid #ff2a6d;}
  #approval .ap-btn.no:hover{background:#ff2a6d14;text-shadow:0 0 8px #ff2a6d;}
```

- [ ] **Step 3: Add markup to the agent modal + a new approval modal**

In `templates/mission_control.html`, inside `#modal-card` (after the existing telemetry/log section, before the closing `</div>` of the card), add:

```html
    <div class="m-sub" style="margin-top:10px;">ACCESS RULES</div>
    <div class="m-badges" id="m-access"></div>
    <div class="m-sub" style="margin-top:10px;">RECENT ACTIVITY</div>
    <div class="m-actlist" id="m-activity"></div>
    <button class="m-approve-btn" id="m-approve-btn">⚠ APPROVE PENDING (<span id="m-approve-n">0</span>)</button>
```

> Reuse whatever the existing section-label class is (grep for `m-sub` or the class used by "ACCESS"/existing labels like the XP/log headers; if there is no `.m-sub`, copy the class used on the existing telemetry heading). Cosmetic only.

Then, immediately after the existing `#modal` overlay `</div>` (the agent modal, ~line 300), add the approval modal:

```html
<div class="modal-overlay" id="approval">
  <div class="modal-card">
    <button class="modal-close" id="approval-close">×</button>
    <div class="ap-h">APPROVAL REQUIRED</div>
    <div id="ap-agent" style="font-weight:700;"></div>
    <div id="ap-request" style="font-size:.66rem;color:#9ccf9c;margin-top:4px;"></div>
    <div class="ap-args" id="ap-args"></div>
    <div class="ap-row">
      <button class="ap-btn ok" id="ap-approve">APPROVE</button>
      <button class="ap-btn no" id="ap-deny">DENY</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Wire the modal JS**

In `templates/mission_control.html`, at the end of `openModal(id)` (after `loadTelemetry(id);`), add:

```javascript
  loadAccess(id);
  loadAgentActivity(id);
  refreshApproveButton();
```

Then add these functions near `loadTelemetry` (module scope):

```javascript
function loadAccess(id){
  const box=$('m-access'); if(!box) return; box.innerHTML='…';
  API.access(id).then(d=>{
    if(modalAgentId!==id) return;
    const rules=(d&&d.rules)||[];
    box.innerHTML = rules.length
      ? rules.map(r=>`<span class="m-abadge ${r.allowed?'ok':'no'}">${esc(r.skill_name)}</span>`).join('')
      : '<span style="color:#5a7a5a;font-size:.54rem">No skills registered.</span>';
  }).catch(()=>{ box.innerHTML='<span style="color:#5a7a5a;font-size:.54rem">Access unavailable.</span>'; });
}

function loadAgentActivity(id){
  const box=$('m-activity'); if(!box) return; box.innerHTML='…';
  API.activity(id).then(d=>{
    if(modalAgentId!==id) return;
    const rows=(d&&d.activity)||[];
    box.innerHTML = rows.length
      ? rows.map(r=>{ const t=String(r.executed_at||'').slice(11,16);
          const cls=({success:'success',blocked:'blocked',failure:'failure'})[r.status]||'info';
          return `<div class="al ${cls}"><span style="opacity:.6">[${esc(t)}]</span> ${esc(r.skill_name||'')} · ${esc(r.status||'')}</div>`; }).join('')
      : '<div class="al info" style="color:#5a7a5a;border-color:transparent">No recent activity.</div>';
  }).catch(()=>{ box.innerHTML='<div class="al info" style="border-color:transparent">Activity unavailable.</div>'; });
}

let pendingPlansCache=[];
function refreshApproveButton(){
  const btn=$('m-approve-btn'); if(!btn) return;
  API.pendingPlans().then(d=>{
    pendingPlansCache=(d&&d.plans)||[];
    const n=pendingPlansCache.length;
    $('m-approve-n').textContent=n;
    btn.classList.toggle('show', n>0);
  }).catch(()=>{ btn.classList.remove('show'); });
}
$('m-approve-btn').onclick=()=>{ if(pendingPlansCache.length) openApproval(pendingPlansCache[0].plan_id); };
```

Then add the approval-modal controller:

```javascript
let approvalPlanId=null;
function openApproval(planId){
  approvalPlanId=planId;
  $('ap-agent').textContent='';
  $('ap-request').textContent='Loading…';
  $('ap-args').textContent='';
  $('approval').classList.add('show');
  API.planGet(planId).then(p=>{
    if(approvalPlanId!==planId) return;
    $('ap-request').textContent=p.user_request||'(no request text)';
    let steps=[]; try{ steps=JSON.parse(p.decomposition||'[]'); }catch(e){}
    const first=steps[0]||{};
    $('ap-agent').textContent=(first.agent?('Agent: '+first.agent):'')+(first.skill?('  ·  '+first.skill):'');
    $('ap-args').textContent=JSON.stringify(steps, null, 2);
  }).catch(()=>{ $('ap-request').textContent='Plan unavailable.'; });
}
function closeApproval(){ $('approval').classList.remove('show'); approvalPlanId=null; }
$('approval-close').onclick=closeApproval;
$('approval').onclick=e=>{ if(e.target===$('approval')) closeApproval(); };
$('ap-approve').onclick=async()=>{
  if(!approvalPlanId) return; const pid=approvalPlanId;
  try{ await API.planApprove(pid); await API.planExecute(pid);
    toast('Approved · plan executing'); }catch(e){ toast('Approve failed'); }
  closeApproval(); refreshApproveButton(); if(modalAgentId) loadAgentActivity(modalAgentId);
};
$('ap-deny').onclick=async()=>{
  if(!approvalPlanId) return; const pid=approvalPlanId;
  try{ await API.planReject(pid); toast('Denied'); }catch(e){ toast('Deny failed'); }
  closeApproval(); refreshApproveButton();
};
```

> `toast(msg)` — grep `function toast` / `toast(` in `mission_control.html`. If a toast helper exists, use it. If not, replace the three `toast(...)` calls with a one-line fallback: define `function toast(m){ const d=document.createElement('div'); d.textContent=m; d.style.cssText='position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#020a02;border:1px solid var(--green);color:var(--green);padding:8px 16px;z-index:200;font-size:.7rem;box-shadow:0 0 14px #39ff1455'; document.body.appendChild(d); setTimeout(()=>d.remove(),2200); }` near the modal JS.

- [ ] **Step 5: Commit**

```bash
git add templates/mission_control.html
git commit -m "feat: agent modal access badges, approval modal, color-coded activity"
```

---

## Task 5: Approvals inbox (`approvals.html`)

**Files:**
- Modify: `templates/approvals.html` — replace the placeholder `<main>` body and add an inline approval modal + JS.

**Interfaces:**
- Consumes: `GET /api/plan/pending`, `GET /api/plan/<id>`, `POST /api/plan/<id>/approve|reject|execute` (Tasks 2-3, existing).

- [ ] **Step 1: Replace the placeholder body**

In `templates/approvals.html`, replace:

```html
<main class="screen-placeholder">
  <h1 class="screen-title" data-decode>Approvals Inbox</h1>
  <p class="screen-sub">Pending actions awaiting your sign-off</p>
  <div class="screen-empty sci-fi-panel">No pending approvals</div>
</main>
```

with:

```html
<main class="screen-placeholder">
  <h1 class="screen-title" data-decode>Approvals Inbox</h1>
  <p class="screen-sub">Pending actions awaiting your sign-off</p>
  <div id="approvals-list" class="sci-fi-panel" style="padding:14px;min-height:80px;">
    <div style="color:#5a7a5a">Loading…</div>
  </div>
</main>

<div class="modal-overlay" id="approval" style="position:fixed;inset:0;background:rgba(2,5,10,.92);z-index:100;display:none;align-items:center;justify-content:center;padding:20px;">
  <div class="modal-card sci-fi-panel" style="width:min(500px,94vw);max-height:88vh;overflow:auto;padding:20px;position:relative;border:1px solid #ff2a6d;box-shadow:0 0 30px #ff2a6d55;">
    <button id="approval-close" style="position:absolute;top:8px;right:12px;font-size:1.3rem;color:#ff2a6d;background:none;border:none;cursor:pointer;">×</button>
    <div style="color:#ff2a6d;text-shadow:0 0 10px #ff2a6d;font-weight:800;letter-spacing:.08em;margin-bottom:8px;">APPROVAL REQUIRED</div>
    <div id="ap-request" style="font-size:.85rem;margin-top:4px;"></div>
    <pre id="ap-args" style="background:#000;border:1px solid #00d9ff;color:#00d9ff;font-family:'JetBrains Mono',monospace;font-size:.7rem;padding:8px;margin:8px 0;white-space:pre-wrap;max-height:200px;overflow:auto;"></pre>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button id="ap-approve" style="flex:1;padding:10px;color:#39ff14;border:1px solid #39ff14;background:#020a02;font-weight:700;cursor:pointer;">APPROVE</button>
      <button id="ap-deny" style="flex:1;padding:10px;color:#ff2a6d;border:1px solid #ff2a6d;background:#020a02;font-weight:700;cursor:pointer;">DENY</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add the inbox JS**

In `templates/approvals.html`, inside the existing `<script>` block (before `</script>`), add:

```javascript
const J={'Content-Type':'application/json'};
const $=id=>document.getElementById(id);
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
let curPlan=null;

function loadApprovals(){
  fetch('/api/plan/pending',{credentials:'include'}).then(r=>r.json()).then(d=>{
    const plans=(d&&d.plans)||[]; const box=$('approvals-list');
    box.innerHTML = plans.length
      ? plans.map(p=>`<div class="ap-item" data-pid="${esc(p.plan_id)}" style="display:flex;justify-content:space-between;align-items:center;padding:10px;border-bottom:1px solid #0d2010;cursor:pointer;">
          <div><div style="font-weight:700">${esc(p.user_request||p.plan_id)}</div>
          <div style="font-size:.7rem;color:#5a7a5a">${esc(p.step_count)} step(s) · ${esc(String(p.created_at||'').slice(0,16))}</div></div>
          <div style="color:#ffd60a">REVIEW →</div></div>`).join('')
      : '<div style="color:#5a7a5a">No pending approvals</div>';
    box.querySelectorAll('.ap-item').forEach(el=>{ el.onclick=()=>openApproval(el.dataset.pid); });
  }).catch(()=>{ $('approvals-list').innerHTML='<div style="color:#ff2a6d">Failed to load approvals</div>'; });
}

function openApproval(pid){
  curPlan=pid; $('ap-request').textContent='Loading…'; $('ap-args').textContent='';
  $('approval').style.display='flex';
  fetch('/api/plan/'+encodeURIComponent(pid),{credentials:'include'}).then(r=>r.json()).then(p=>{
    if(curPlan!==pid) return;
    $('ap-request').textContent=p.user_request||'(no request)';
    let steps=[]; try{ steps=JSON.parse(p.decomposition||'[]'); }catch(e){}
    $('ap-args').textContent=JSON.stringify(steps,null,2);
  }).catch(()=>{ $('ap-request').textContent='Plan unavailable.'; });
}
function closeApproval(){ $('approval').style.display='none'; curPlan=null; }
$('approval-close').onclick=closeApproval;
$('approval').onclick=e=>{ if(e.target===$('approval')) closeApproval(); };
$('ap-approve').onclick=async()=>{ const pid=curPlan; if(!pid) return;
  await fetch('/api/plan/'+encodeURIComponent(pid)+'/approve',{method:'POST',credentials:'include',headers:J});
  await fetch('/api/plan/'+encodeURIComponent(pid)+'/execute',{method:'POST',credentials:'include',headers:J});
  closeApproval(); loadApprovals(); };
$('ap-deny').onclick=async()=>{ const pid=curPlan; if(!pid) return;
  await fetch('/api/plan/'+encodeURIComponent(pid)+'/reject',{method:'POST',credentials:'include',headers:J});
  closeApproval(); loadApprovals(); };

loadApprovals();
```

> If `esc`/`$` are already defined by an included script on this page, remove the duplicate declarations to avoid a redeclare error. Check by loading the page with devtools open.

- [ ] **Step 3: Commit**

```bash
git add templates/approvals.html
git commit -m "feat: real approvals inbox reusing the plan approval flow"
```

---

## Task 6: Full verification & deployment checklist

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `python -m pytest tests/test_access_rules.py -v`
Expected: PASS (all tasks' tests green).

Run: `python -m pytest -q`
Expected: no new failures introduced elsewhere (pre-existing unrelated failures, if any, are unchanged).

- [ ] **Step 2: Seed a pending plan for manual UI checks**

With the app able to reach SQLite `asfa.db`, create one pending plan (from a Python shell in the repo root):

```python
import json, database as db
db.init_db()
db.create_plan("manual-demo-1", "Scan retail jobs in London",
               json.dumps([{"step":0,"agent":"scout","skill":"scan_jobs",
                            "params":{"keywords":["retail"],"location":"London"}}]),
               "manual UI demo")
print(db.get_pending_plans())
```

- [ ] **Step 3: Manual UI verification**

Run: `python app.py`, open `http://localhost:5000`, log in with `APP_PASSWORD`.

Verify each, checking the box only when observed:
- Mission Control (`/mission` or the nav link) → click an agent → modal shows **ACCESS RULES** badges (green allowed / magenta denied) and **RECENT ACTIVITY**.
- The **APPROVE PENDING (N)** button shows with N≥1 while `manual-demo-1` is pending; click it → approval modal (magenta border) shows the request + args JSON.
- Click **APPROVE** → toast appears, modal closes, button count drops, activity list refreshes; the executed step appears color-coded. Set a deny rule (`db.set_access_rule("scout","scan_jobs",allowed=False)`), re-run a plan, and confirm the step shows **blocked** (magenta) in RECENT ACTIVITY.
- `/approvals` page → pending plans listed in the `sci-fi-panel`; clicking one opens the approval modal; Approve/Deny updates the list.
- Confirm no unstyled elements and no console errors (especially no `esc`/`$` redeclare errors on `/approvals`).

- [ ] **Step 4: Confirm the frozen-bots boundary is untouched**

Run: `git diff --stat main -- ../../.. 2>/dev/null; git diff --name-only main`
Expected: only `database.py`, `app.py`, `services/skill_executor.py`, `services/planner.py`, `templates/mission_control.html`, `templates/approvals.html`, `tests/test_access_rules.py`, and the docs. **No** changes to `strategies.py`, `paper_trader.py`, `scanner.py`, `market_data.py`, `config.py`, or anything under the stock-scanner bot paths.

- [ ] **Step 5: Deployment checklist (Railway / Postgres)**

- [ ] `agent_access_rules` is created by `_ensure_agent_data_tables()` on first access and `init_db()` runs on boot — no manual migration needed. Confirm the Postgres branch of `set_access_rule`/`seed_access_rules` (uses `ON CONFLICT (agent_id, skill_name)`) matches the `UNIQUE(agent_id, skill_name)` constraint.
- [ ] Single gunicorn worker (per project rules) — `seed_access_rules()` is idempotent, so a boot re-seed is safe.
- [ ] No new env vars, no new dependencies, no CDN links added (verify: `grep -rn "unpkg\|cybercore" templates/ static/` returns nothing).
- [ ] New routes are auth-gated: `grep -n "_PUBLIC_ENDPOINTS" app.py` still lists only the pre-existing exemptions (none added).
- [ ] Push branch, open PR, confirm Railway build is green and `/approvals` + Mission Control load in prod.

- [ ] **Step 6: Final commit (if any verification fixups were made)**

```bash
git add -A
git commit -m "chore: verification fixups for mission-control approval plane"
```

---

## Self-Review Notes

- **Spec coverage:** `agent_access_rules` table + seeding (Task 1) ✓; enforcement in `execute_skill` with block+log+continue (Task 2) ✓; read endpoints `/api/plan/pending`, `/api/agents/<id>/access`, `/api/agents/<id>/activity` (Task 3) ✓; agent-modal extension with energy bar (pre-existing `loadTelemetry`), badges, recent activity, approve-pending button (Task 4) ✓; approval modal (Task 4) ✓; approvals.html inbox (Task 5) ✓; mission-log color coding via status-colored activity list (Task 4 CSS/JS) ✓; tests for allow/deny/blocked/seeding + endpoint auth (Tasks 1-3) ✓; frozen-bots boundary check (Task 6) ✓.
- **No parallel systems:** no `approvals`/`activity_log` tables, no `/api/approvals` or `/api/activity` endpoints — reuses `execution_plans`/`plan_executions` per the spec's "out of scope."
- **No CDN:** styling reuses `sci-fi-panel` + existing tokens; deployment checklist greps to confirm no `unpkg`/`cybercore`.
- **Type consistency:** `is_skill_allowed(agent_id, skill_name) -> bool`, `blocked` key in the `execute_skill` envelope, and `status="blocked"` in `plan_executions` are used identically across Tasks 1-4.
