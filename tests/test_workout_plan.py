"""Workout-plan tests — the /gym/plan page, the seeded split, the progression
roadmap's % complete, and the goal countdown.

Runs either way — standalone (no pytest dependency) or under pytest:

    python tests/test_workout_plan.py
    pytest tests/test_workout_plan.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so nothing touches asfa.db.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_plan_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

# Plan baselines are read from the environment (they're biometric, so they aren't
# committed — see .env.example). Fix them here so the suite asserts against its own
# declared fixture rather than whatever real values happen to be in the dev's .env.
#
# These are deliberately round, synthetic numbers — NOT anyone's real bodyweight or
# lifts. Round spans (90→80kg, 50→70kg) also make the percentage maths checkable by
# eye. This repo is public; test fixtures are committed, so real values don't go here.
os.environ.update({
    "PLAN_BASELINE_KG": "90",
    "PLAN_TARGET_KG": "80",
    "PLAN_BENCH_START_KG": "50",
    "PLAN_BENCH_START_REPS": "2",
    "PLAN_BENCH_TARGET_KG": "70",
    "PLAN_BENCH_TARGET_REPS": "5",
    "PLAN_BENCH_REACH_KG": "80",
    "PLAN_BENCH_GOAL_KG": "60",
    "PLAN_BENCH_STEP_KG": "5",
    "PLAN_BENCH_STEP_WEEKS": "2",
    "PLAN_PRIVATE_GOALS": '[{"name": "Example private goal", "notes": "Example."}]',
})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

BENCH = "Barbell Bench Press"


def setup_module(module=None):
    """Create + seed the gym library and the plan against this module's fixture env.

    Named ``setup_module`` (not ``setup``) so pytest runs it too — bare ``setup``
    is nose-style, which pytest dropped in 8.0.

    The plan tables are wiped first. Test modules share one SQLite file (database.py
    caches SQLITE_PATH at first import), and app.py seeds the plan at import time —
    so under the full suite an earlier module importing app will already have seeded
    the plan from the developer's real .env. Re-seeding from a clean slate makes
    these assertions depend on the fixture env above, not on import order."""
    db.init_gym_data()
    db._ensure_workout_plan_tables()
    with db.get_db() as conn:
        cur = conn.cursor()
        for t in ("workout_sessions", "workout_plan", "progression_targets",
                  "workout_goals", "gym_body_stats", "gym_prs"):
            cur.execute(f"DELETE FROM {t}")
    db.init_workout_plan()


def _client():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    return client


def _bench_id():
    return [e for e in db.get_all_exercises() if e["name"] == BENCH][0]["id"]


def _log_bench_pr(weight, reps, one_rm, on="2026-07-03"):
    db.update_pr(_bench_id(), weight, reps, one_rm, on, None)


# ── 1. The plan renders ───────────────────────────────────────────────────────

def test_1_plan_page_renders():
    client = _client()
    r = client.get("/gym/plan")
    assert r.status_code == 200, r.get_data(as_text=True)
    html = r.get_data(as_text=True)
    assert "Workout Plan" in html
    # The four sections the page promises.
    for anchor in ("quick-stats", "week-grid", "progression", "goals"):
        assert f'id="{anchor}"' in html, f"missing section: {anchor}"
    assert 'id="edit-plan-btn"' in html, "Edit Plan button must be present"


def test_2_plan_page_is_session_gated():
    """The global before_request gate must cover the page and its API."""
    import app as app_module
    anon = app_module.app.test_client()   # no session
    for path in ("/gym/plan", "/api/gym/plan"):
        r = anon.get(path)
        assert r.status_code in (302, 401), f"{path} leaked to anonymous: {r.status_code}"


def test_3_api_returns_all_four_sections():
    r = _client().get("/api/gym/plan")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    for key in ("plan", "progression", "goals", "stats"):
        assert key in body, f"missing key: {key}"
    assert body["plan"]["split_name"] == db.PLAN_SPLIT_NAME


# ── 2. The split displays correctly ───────────────────────────────────────────

def test_4_split_is_4_gym_2_cardio_1_rest():
    plan = db.get_workout_plan()
    s = plan["summary"]
    assert s["total_days"] == 7, s
    assert s["gym_days"] == 4, f"expected 4 gym days, got {s['gym_days']}"
    assert s["cardio_days"] == 2, f"expected 2 cardio days, got {s['cardio_days']}"
    assert s["rest_days"] == 1, f"expected 1 rest day, got {s['rest_days']}"


def test_5_days_are_mon_to_sun_in_order():
    days = db.get_workout_plan()["days"]
    assert [d["day_number"] for d in days] == [1, 2, 3, 4, 5, 6, 7]
    assert [d["day_name"] for d in days] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def test_6_each_day_has_the_right_session_type():
    by_day = {d["day_name"]: d for d in db.get_workout_plan()["days"]}
    expected = {
        "Monday": "Push", "Tuesday": "Cycling", "Wednesday": "Pull",
        "Thursday": "Cycling", "Friday": "Push", "Saturday": "Pull",
        "Sunday": "Rest",
    }
    for day, stype in expected.items():
        assert by_day[day]["session_type"] == stype, \
            f"{day} should be {stype}, got {by_day[day]['session_type']}"


def test_7_push_pull_days_carry_exercises_and_treadmill():
    by_day = {d["day_name"]: d for d in db.get_workout_plan()["days"]}

    monday = by_day["Monday"]
    assert "Incline Barbell Bench" in monday["exercises"]
    assert "Triceps" in monday["exercises"]
    assert "13% incline" in monday["cardio"] and "3.5" in monday["cardio"]

    wednesday = by_day["Wednesday"]
    assert "Lat Pulldown" in wednesday["exercises"]
    assert "Back Finisher" in wednesday["exercises"]
    assert "treadmill" in wednesday["cardio"]

    # Push/Pull/Push/Pull: Friday mirrors Monday's Push, Saturday mirrors
    # Wednesday's Pull.
    assert by_day["Friday"]["exercises"] == monday["exercises"]
    assert by_day["Saturday"]["exercises"] == wednesday["exercises"]


def test_8_cycling_and_rest_days_carry_no_lifting():
    by_day = {d["day_name"]: d for d in db.get_workout_plan()["days"]}
    for day in ("Tuesday", "Thursday"):
        assert by_day[day]["exercises"] == [], f"{day} should have no lifts"
        assert "7.9 miles" in by_day[day]["cardio"]
    sunday = by_day["Sunday"]
    assert sunday["exercises"] == []
    assert not sunday["cardio"]
    assert "weigh-in" in (sunday["notes"] or "").lower()


def test_9_plan_notes_cover_abs_steps_and_rpe():
    notes = (db.get_workout_plan()["notes"] or "").lower()
    for token in ("abs 2x/week", "10k steps", "rpe", "pre-workout"):
        assert token in notes, f"plan notes should mention {token}"


# ── 3. Progression targets calculate % complete ───────────────────────────────

def test_10_percent_complete_is_derived_from_the_logged_pr():
    # Halfway from the 50kg start to the 70kg target.
    _log_bench_pr(60.0, 3, 66.0)
    t = db.get_progression_targets()[0]
    assert t["start_weight"] == 50.0
    assert t["target_weight"] == 70.0
    assert t["reach_weight"] == 80.0
    assert t["current_weight"] == 60.0, "current must come from gym_prs"
    assert t["current_source"] == "logged PR"
    assert t["percent_complete"] == 50, f"50→60 of 50→70 is 50%, got {t['percent_complete']}"
    assert t["percent_to_reach"] == 33, f"50→60 of 50→80 is 33%, got {t['percent_to_reach']}"
    assert t["kg_to_target"] == 10.0
    assert t["kg_to_reach"] == 20.0


def test_11_percent_complete_clamps_and_hits_100():
    _log_bench_pr(70.0, 5, 81.7)
    assert db.get_progression_targets()[0]["percent_complete"] == 100

    # Past the target — clamped, never over 100.
    _log_bench_pr(82.5, 1, 85.3)
    t = db.get_progression_targets()[0]
    assert t["percent_complete"] == 100, "must clamp at 100"
    assert t["percent_to_reach"] == 100
    assert t["kg_to_target"] == -12.5, "gap stays signed so the UI can say 'past it'"


def test_12_pr_below_the_documented_start_is_flagged_not_hidden():
    """A logged PR under the documented roadmap start pins progress at 0%. It must
    be reported, not silently rendered as an empty bar — this is a real scenario
    when the start was a heavy single that never got logged through a session."""
    _log_bench_pr(45.0, 9, 58.5)
    t = db.get_progression_targets()[0]
    assert t["percent_complete"] == 0, "must clamp at 0, not go negative"
    assert t["below_start"] is True
    assert t["kg_below_start"] == 5.0


def test_13_progression_reads_none_without_a_logged_pr():
    """No PR logged -> no invented number."""
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM gym_prs")
    t = db.get_progression_targets()[0]
    assert t["current_weight"] is None
    assert t["percent_complete"] is None, "must be None, not 0 — absence isn't zero progress"
    assert t["kg_to_target"] is None
    assert t["below_start"] is False
    _log_bench_pr(50.0, 9, 65.0)   # restore the fixture baseline


# ── 4. Goals show a countdown ─────────────────────────────────────────────────

def test_14_goals_have_a_day_countdown():
    goals = db.get_workout_goals()
    assert goals, "goals must be seeded"
    expected = (date(2026, 9, 15) - date.today()).days
    for g in goals:
        assert g["target_date"] == db.PLAN_TARGET_DATE
        assert g["days_remaining"] == expected, \
            f"{g['goal_name']}: expected {expected} days, got {g['days_remaining']}"


def test_15_days_until_helper_counts_down_and_goes_negative():
    today = date(2026, 7, 15)
    assert db._days_until("2026-09-15", today) == 62
    assert db._days_until("2026-07-16", today) == 1
    assert db._days_until("2026-07-15", today) == 0
    assert db._days_until("2026-07-14", today) == -1, "past dates go negative"
    assert db._days_until(None, today) is None
    assert db._days_until("not-a-date", today) is None


def test_16_bodyweight_goal_tracks_the_latest_weigh_in():
    goal = [g for g in db.get_workout_goals() if g["metric_key"] == "bodyweight"][0]
    assert goal["start_value"] == 90.0
    assert goal["target_value"] == 80.0
    assert goal["current_value"] == 90.0, "seeded baseline weigh-in"
    assert goal["percent_complete"] == 0
    assert goal["remaining"] == 10.0

    # A new weigh-in moves the needle — the goal reads the log, not a snapshot.
    db.log_body_stat(date.today().isoformat(), 85.0)
    goal = [g for g in db.get_workout_goals() if g["metric_key"] == "bodyweight"][0]
    assert goal["current_value"] == 85.0
    assert goal["percent_complete"] == 50, "90→85 of 90→80 is 50%"
    assert goal["remaining"] == 5.0


def test_17_qualitative_goals_get_no_invented_progress():
    """Goals from PLAN_PRIVATE_GOALS have no metric, so they must not get a
    fabricated progress number."""
    goals = {g["goal_name"]: g for g in db.get_workout_goals()}
    g = goals["Example private goal"]
    assert g["qualitative"] is True
    assert g["percent_complete"] is None, "no number to measure"
    assert g["current_value"] is None
    assert g["days_remaining"] is not None, "still counts down"


# ── 5. Quick stats ────────────────────────────────────────────────────────────

def test_18_quick_stats_countdown_and_gaps():
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM gym_body_stats")
    db.log_body_stat(date.today().isoformat(), 90.0)
    _log_bench_pr(50.0, 9, 65.0)

    s = db.get_plan_quick_stats()
    assert s["days_until_september"] == (date(2026, 9, 1) - date.today()).days
    assert s["days_until_target"] == (date(2026, 9, 15) - date.today()).days
    assert s["goal_bodyweight"] == 80.0
    assert s["kg_to_lose"] == 10.0, "90 - 80"
    assert s["current_bench_kg"] == 50.0
    assert s["current_bench_1rm"] == 65.0
    assert s["kg_to_bench_target"] == 20.0, "50 → 70kg progression target"
    assert s["kg_to_bench_reach"] == 30.0, "50 → 80kg reach"


def test_19_quick_stats_degrade_without_data():
    """No weigh-in and no PR -> nulls, not zeros or guesses."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM gym_body_stats")
        cur.execute("DELETE FROM gym_prs")
    s = db.get_plan_quick_stats()
    assert s["current_bodyweight"] is None
    assert s["kg_to_lose"] is None
    assert s["current_bench_kg"] is None
    assert s["kg_to_bench_target"] is None
    assert s["days_until_september"] is not None, "the countdown never depends on logs"
    db.log_body_stat(date.today().isoformat(), 90.0)
    _log_bench_pr(50.0, 9, 65.0)


# ── 6. Seeding + edits ────────────────────────────────────────────────────────

def test_20a_renaming_the_split_does_not_reseed_a_duplicate_plan():
    """Regression: seeding used to look the plan up by split_name, which the Edit
    Plan button lets you change — so renaming then restarting seeded a second plan
    and 7 orphan day rows."""
    original = db.get_workout_plan()["split_name"]
    try:
        db.update_workout_plan(split_name="My Renamed Split")
        db.init_workout_plan()          # simulate a Railway redeploy
        db.init_workout_plan()

        with db.get_db() as conn:
            cur = conn.cursor()
            plans = cur.execute("SELECT COUNT(*) AS n FROM workout_plan").fetchone()["n"]
            days = cur.execute("SELECT COUNT(*) AS n FROM workout_sessions").fetchone()["n"]
        assert plans == 1, f"restart after a rename duplicated the plan ({plans} rows)"
        assert days == 7, f"restart after a rename duplicated the days ({days} rows)"
        assert db.get_workout_plan()["split_name"] == "My Renamed Split", \
            "the rename must survive a restart"
    finally:
        db.update_workout_plan(split_name=original)


def test_20b_day_edits_are_scoped_to_the_plan():
    """Regression: update_workout_session filtered on day_number alone, so it
    rewrote that weekday across every plan row."""
    with db.get_db() as conn:
        cur = conn.cursor()
        plan_id = db._plan_id(cur)
        assert plan_id is not None
    db.update_workout_session(1, session_type="Push")
    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT plan_id FROM workout_sessions WHERE day_number = 1").fetchall()
    assert len(rows) == 1, "day 1 must exist once"
    assert rows[0]["plan_id"] == plan_id


def test_20c_description_and_notes_can_be_cleared():
    """Regression: the route collapsed "" to None via `or None`, so clearing a
    field silently did nothing and the old text came back."""
    original = db.get_workout_plan()
    client = _client()
    try:
        r = client.post("/api/gym/plan", json={"notes": ""}, headers={"X-CSRF-Token": "tok"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert db.get_workout_plan()["notes"] == "", "an empty string must clear the field"

        # Absent key still means "leave alone".
        r = client.post("/api/gym/plan", json={"description": "Only this"},
                        headers={"X-CSRF-Token": "tok"})
        assert r.status_code == 200
        assert db.get_workout_plan()["notes"] == "", "unrelated field must be untouched"

        # But the headline can't be blanked.
        r = client.post("/api/gym/plan", json={"split_name": "   "},
                        headers={"X-CSRF-Token": "tok"})
        assert r.status_code == 400, "blank split_name must 400"
    finally:
        db.update_workout_plan(split_name=original["split_name"],
                               description=original["description"],
                               notes=original["notes"])


def test_20d_fractional_reps_are_rejected():
    """Regression: int(5.9) silently stored 5."""
    client = _client()
    r = client.post(f"/api/gym/plan/progression/{BENCH}", json={"target_reps": 5.9},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400, "a fractional rep count must 400, not truncate"
    assert db.get_progression_targets()[0]["target_reps"] == 5


def test_20e_payload_matches_the_individual_reads():
    """get_plan_payload shares live reads across sections — it must not drift
    from calling each getter directly."""
    p = db.get_plan_payload()
    assert p["plan"]["split_name"] == db.get_workout_plan()["split_name"]
    assert p["progression"] == db.get_progression_targets()
    assert p["goals"] == db.get_workout_goals()
    assert p["stats"] == db.get_plan_quick_stats()


def test_19b_no_personal_data_is_committed_to_source():
    """This repo is public. Bodyweight/lift baselines must come from the
    environment, never from constants in database.py.

    Checks structurally rather than by naming the sensitive values — a guard that
    spells out the private strings would leak them itself."""
    import inspect
    import re
    src = inspect.getsource(db)
    plan_src = src[src.index("WORKOUT PLAN —"):src.index("AUTH FAILURES")]

    # A bare NN.N / NN.NN literal in the plan seed is bodyweight/lift-shaped.
    stray = [m for m in re.findall(r"(?<![\w.])\d{2,3}\.\d{1,2}(?![\w.])", plan_src)]
    assert not stray, f"numeric personal baselines hardcoded in database.py: {stray}"

    # And the seeds must actually source their values from the environment.
    for fn in (db.plan_goals_seed, db.plan_progression_seed, db.seed_baseline_bodyweight):
        body = inspect.getsource(fn)
        assert "_plan_env" in body or "PLAN_" in body, \
            f"{fn.__name__} must read its baselines from the environment"


def test_19c_unconfigured_env_seeds_nothing_personal():
    """A fresh clone with no PLAN_* vars must still work and seed no baselines."""
    saved = {k: os.environ.pop(k) for k in list(os.environ)
             if k.startswith("PLAN_")}
    try:
        assert db.plan_progression_seed() == [], "no roadmap without a configured start"
        assert db.plan_goals_seed() == [], "no goals without configured baselines"
        assert db.seed_baseline_bodyweight() is False, "no weigh-in without a baseline"
        # The quick stats still answer the non-personal question.
        s = db.get_plan_quick_stats()
        assert s["goal_bodyweight"] is None
        assert s["kg_to_lose"] is None
        assert s["days_until_september"] is not None
    finally:
        os.environ.update(saved)


def test_19d_malformed_env_is_ignored_not_guessed():
    saved = os.environ.get("PLAN_BASELINE_KG")
    try:
        for bad in ("", "  ", "heavy", "-5", "0"):
            os.environ["PLAN_BASELINE_KG"] = bad
            assert db._plan_env_float("PLAN_BASELINE_KG") is None, \
                f"{bad!r} must read as unset, never as 0"
    finally:
        os.environ["PLAN_BASELINE_KG"] = saved


def test_20_seeding_is_idempotent():
    before = db.get_workout_plan()
    db.init_workout_plan()
    db.init_workout_plan()
    after = db.get_workout_plan()
    assert len(after["days"]) == 7, "re-seeding must not duplicate days"
    assert after["id"] == before["id"]
    assert len(db.get_progression_targets()) == 1
    assert len(db.get_workout_goals()) == len(db.plan_goals_seed())


def test_21_baseline_bodyweight_never_double_seeds():
    """Once any weigh-in exists the baseline seed is a permanent no-op, so a
    redeploy can't inject a stale baseline on top of real weigh-ins."""
    assert db.seed_baseline_bodyweight() is False, "should not seed over existing data"

    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM gym_body_stats")
    assert db.seed_baseline_bodyweight() is True, "seeds into an empty table"
    stats = db.get_body_stats(10)
    assert len(stats) == 1 and stats[0]["weight_kg"] == 90.0
    assert db.seed_baseline_bodyweight() is False, "second call is a no-op"


def test_22_edit_plan_persists_and_never_touches_the_log():
    client = _client()
    pr_before = db._current_bench_pr()
    bw_before = db._latest_bodyweight()

    r = client.post("/api/gym/plan", json={"description": "Edited description"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.get_workout_plan()["description"] == "Edited description"

    r = client.post(f"/api/gym/plan/progression/{BENCH}",
                    json={"target_weight": 77.5, "target_date": "2026-09-20"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    t = db.get_progression_targets()[0]
    assert t["target_weight"] == 77.5
    assert t["target_date"] == "2026-09-20"
    assert t["start_weight"] == 50.0, "the documented start must be immutable"

    assert db._current_bench_pr() == pr_before, "plan edits must not touch gym_prs"
    assert db._latest_bodyweight() == bw_before, "plan edits must not touch weigh-ins"

    # restore
    db.update_progression_target(BENCH, target_weight=75.0, target_date="2026-09-15")


def test_23_edit_rejects_bad_input():
    client = _client()
    bad = [
        ("/api/gym/plan/progression/" + BENCH, {"target_weight": "heavy"}),
        ("/api/gym/plan/progression/" + BENCH, {"target_weight": -5}),
        ("/api/gym/plan/progression/" + BENCH, {"target_date": "15-09-2026"}),
        ("/api/gym/plan/goal/Bench PR 60kg", {"target_value": "abc"}),
    ]
    for path, payload in bad:
        r = client.post(path, json=payload, headers={"X-CSRF-Token": "tok"})
        assert r.status_code == 400, f"{path} {payload} should 400, got {r.status_code}"

    # A typo'd number must not have silently landed.
    assert db.get_progression_targets()[0]["target_weight"] == 75.0


def test_24_edit_day_of_the_split():
    client = _client()
    r = client.post("/api/gym/plan/day/6",
                    json={"session_type": "Pull", "exercises": ["Lat Pulldown", "Rows"]},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    by_day = {d["day_name"]: d for d in db.get_workout_plan()["days"]}
    assert by_day["Saturday"]["session_type"] == "Pull"
    assert by_day["Saturday"]["exercises"] == ["Lat Pulldown", "Rows"]

    # Summary recounts live off the edit.
    assert db.get_workout_plan()["summary"]["gym_days"] == 4

    r = client.post("/api/gym/plan/day/9", json={"session_type": "Push"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400, "day_number outside 1-7 must 400"

    # restore — Saturday's seed default is now Pull
    db.update_workout_session(6, session_type="Pull", exercises=db._PULL_EXERCISES)


def main():
    setup_module()
    tests = [
        test_1_plan_page_renders,
        test_2_plan_page_is_session_gated,
        test_3_api_returns_all_four_sections,
        test_4_split_is_4_gym_2_cardio_1_rest,
        test_5_days_are_mon_to_sun_in_order,
        test_6_each_day_has_the_right_session_type,
        test_7_push_pull_days_carry_exercises_and_treadmill,
        test_8_cycling_and_rest_days_carry_no_lifting,
        test_9_plan_notes_cover_abs_steps_and_rpe,
        test_10_percent_complete_is_derived_from_the_logged_pr,
        test_11_percent_complete_clamps_and_hits_100,
        test_12_pr_below_the_documented_start_is_flagged_not_hidden,
        test_13_progression_reads_none_without_a_logged_pr,
        test_14_goals_have_a_day_countdown,
        test_15_days_until_helper_counts_down_and_goes_negative,
        test_16_bodyweight_goal_tracks_the_latest_weigh_in,
        test_17_qualitative_goals_get_no_invented_progress,
        test_18_quick_stats_countdown_and_gaps,
        test_19_quick_stats_degrade_without_data,
        test_19b_no_personal_data_is_committed_to_source,
        test_19c_unconfigured_env_seeds_nothing_personal,
        test_19d_malformed_env_is_ignored_not_guessed,
        test_20a_renaming_the_split_does_not_reseed_a_duplicate_plan,
        test_20b_day_edits_are_scoped_to_the_plan,
        test_20c_description_and_notes_can_be_cleared,
        test_20d_fractional_reps_are_rejected,
        test_20e_payload_matches_the_individual_reads,
        test_20_seeding_is_idempotent,
        test_21_baseline_bodyweight_never_double_seeds,
        test_22_edit_plan_persists_and_never_touches_the_log,
        test_23_edit_rejects_bad_input,
        test_24_edit_day_of_the_split,
    ]
    print("Workout-plan tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
