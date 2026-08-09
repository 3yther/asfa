"""agent_access_rules — per-agent skill allowlist (Mission Control approval plane, Task 1).

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db. This
mirrors the isolation pattern used by the rest of the suite (e.g. test_finance.py);
the repo has no conftest.py, so the throwaway DB + env are set BEFORE importing
the database layer.

    python -m pytest tests/test_access_rules.py -v
"""
import os
import sys
import tempfile

# Point the DB layer at a throwaway file BEFORE importing database, and force
# SQLite (never prod Postgres). Both must happen pre-import.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_access_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

db.init_db()
db.init_agent_data()   # registers agent_skills and seeds access rules


def test_is_skill_allowed_when_allowed_returns_true():
    db.set_access_rule("nexus", "market_data", allowed=True)
    assert db.is_skill_allowed("nexus", "market_data") is True


def test_is_skill_allowed_when_denied_returns_false():
    db.set_access_rule("nexus", "market_data", allowed=False)
    assert db.is_skill_allowed("nexus", "market_data") is False


def test_is_skill_allowed_for_unregistered_skill_defaults_to_allowed():
    # No row exists for this pair -> default-allow.
    assert db.is_skill_allowed("nexus", "totally_unregistered_skill") is True


def test_get_access_rules_returns_agent_rules_list():
    db.set_access_rule("scout", "scan_jobs", allowed=True)
    db.set_access_rule("scout", "apply_for_role", allowed=False)
    rules = {r["skill_name"]: r["allowed"] for r in db.get_access_rules("scout")}
    assert rules["scan_jobs"] is True
    assert rules["apply_for_role"] is False
    # shape: each entry is {skill_name, allowed(bool)}
    for r in db.get_access_rules("scout"):
        assert set(r.keys()) == {"skill_name", "allowed"}
        assert isinstance(r["allowed"], bool)


def test_set_access_rule_blocks_skill():
    # Upsert flips an allowed skill to denied.
    db.set_access_rule("sentinel", "escalate", allowed=True)
    assert db.is_skill_allowed("sentinel", "escalate") is True
    db.set_access_rule("sentinel", "escalate", allowed=False)
    assert db.is_skill_allowed("sentinel", "escalate") is False


def test_seed_access_rules_creates_rules_for_all_agent_skills():
    # A brand-new registered skill (untouched by other tests) is seeded allowed.
    db.register_skill("seedtest_agent", "seed_ping", "seed-test skill")
    db.seed_access_rules()
    seeded = {r["skill_name"] for r in db.get_access_rules("seedtest_agent")}
    assert "seed_ping" in seeded
    assert db.is_skill_allowed("seedtest_agent", "seed_ping") is True

    # Every registered (agent_id, skill_name) has an access rule after seeding.
    # (State-only, not allow/deny: earlier tests may have flipped some to denied,
    # and seeding uses INSERT-OR-IGNORE, so it never overwrites an explicit rule.)
    db.seed_skills()
    db.seed_access_rules()
    skills = db.get_all_skills()
    assert skills, "expected the skill registry to be seeded"
    for s in skills:
        rules = {r["skill_name"] for r in db.get_access_rules(s["agent_id"])}
        assert s["skill_name"] in rules, (
            f"{s['agent_id']}/{s['skill_name']} missing an access rule")
