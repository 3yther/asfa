"""Workout-split tests — the seeded gym routines that drive the /gym Workout tab's
Quick Start picker and routine grid. Asserts the live 4-day Push/Pull/Push/Pull
split (Mon/Wed/Fri/Sat), that the retired Legs/Upper/Lower days are gone (and get
reconciled away on re-seed), and that each day's set count yields the 60/58/60/58
target durations the grid shows.

Runs either way — standalone (no pytest dependency) or under pytest:

    python tests/test_workout_split.py
    pytest tests/test_workout_split.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so nothing touches asfa.db.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_split_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import gym_seed        # noqa: E402

# The Quick Start / routine grid orders by (order_index, id) and the up-next
# rotation cycles Mon → Wed → Fri → Sat.
EXPECTED = [
    ("Push · Monday", "push", 24, 60),
    ("Pull · Wednesday", "pull", 23, 58),
    ("Push · Friday", "push_b", 24, 60),
    ("Pull · Saturday", "pull_b", 23, 58),
]


def setup_module(module=None):
    """Seed the gym library + routines from a clean slate.

    Named ``setup_module`` (not ``setup``) so pytest runs it too — bare ``setup``
    is nose-style, which pytest dropped in 8.0."""
    db.init_gym_data()


def _routines():
    return db.get_all_routines()


def _target_minutes(routine_id):
    """Mirror gym.js estimateRoutineMinutes(): round(totalSets × 2.5)."""
    total_sets = sum(e["sets"] for e in db.get_routine_exercises(routine_id))
    return max(5, round(total_sets * 2.5))


# ── 1. Only the four split days exist ─────────────────────────────────────────

def test_1_exactly_four_routines():
    rs = _routines()
    assert len(rs) == 4, f"expected 4 routines, got {len(rs)}: {[r['name'] for r in rs]}"


def test_2_retired_days_are_gone():
    names = {r["name"] for r in _routines()}
    for retired in ("Legs Day", "Upper Day", "Lower Day"):
        assert retired not in names, f"{retired} should be removed"
    # The pre-rename Push/Pull day names must not linger either.
    assert "Push Day" not in names and "Pull Day" not in names


def test_3_days_are_push_pull_push_pull_in_order():
    rs = _routines()
    assert [(r["name"], r["day_type"]) for r in rs] == \
        [(name, dt) for name, dt, _, _ in EXPECTED]
    # order_index is 0..3 in weekday order.
    assert [r["order_index"] for r in rs] == [0, 1, 2, 3]


def test_4_day_types_are_unique_so_rotation_cycles_all_four():
    types = [r["day_type"] for r in _routines()]
    assert len(set(types)) == 4, f"day_types must be distinct for rotation: {types}"


# ── 2. Each day carries 8 exercises and the right target duration ─────────────

def test_5_each_day_has_eight_exercises():
    for r in _routines():
        exs = db.get_routine_exercises(r["id"])
        assert len(exs) == 8, f"{r['name']} should have 8 exercises, got {len(exs)}"


def test_6_target_durations_are_60_58_60_58():
    by_name = {r["name"]: r for r in _routines()}
    for name, _dt, _sets, mins in EXPECTED:
        got = _target_minutes(by_name[name]["id"])
        assert got == mins, f"{name}: expected {mins} min, got {got}"


def test_7_set_counts_match_the_documented_totals():
    by_name = {r["name"]: r for r in _routines()}
    for name, _dt, sets, _mins in EXPECTED:
        total = sum(e["sets"] for e in db.get_routine_exercises(by_name[name]["id"]))
        assert total == sets, f"{name}: expected {sets} sets, got {total}"


def test_8_push_days_are_chest_or_shoulder_led_pull_days_back_led():
    by_name = {r["name"]: r for r in _routines()}

    def lead(name):
        return db.get_routine_exercises(by_name[name]["id"])[0]["name"]

    assert lead("Push · Monday") == "Barbell Bench Press", "Mon push leads on chest"
    assert lead("Push · Friday") == "Seated Dumbbell Shoulder Press", \
        "Fri push leads on shoulders"
    assert lead("Pull · Wednesday") == "Barbell Row"
    assert lead("Pull · Saturday") == "Lat Pulldown", "Sat pull leads on back"


def test_9_every_seeded_exercise_exists_in_the_library():
    """A routine that names an exercise not in the library silently drops it,
    which would break the 8-exercise / set-count assertions — guard the seed."""
    library = {e["name"] for e in db.get_all_exercises()}
    for name, entries in gym_seed.ROUTINE_EXERCISES.items():
        for ex_name, *_ in entries:
            assert ex_name in library, f"{name}: '{ex_name}' missing from the library"


# ── 3. Seeding is idempotent and reconciles stale routines ────────────────────

def test_10_reseeding_does_not_duplicate():
    db.seed_gym_routines()
    db.seed_gym_routines()
    assert len(_routines()) == 4, "re-seeding must not duplicate routines"


def test_11_a_stale_routine_is_reconciled_away_on_reseed():
    """A leftover day from an older split (e.g. a redeploy over a DB that still
    has 'Legs Day') is deleted on the next seed, along with its exercise rows."""
    with db.get_db() as conn:
        cur = conn.cursor()
        legs_id = db._gym_insert(
            cur, "gym_routines", "name, day_type, description, order_index",
            ("Legs Day", "legs", "Old split", 9))
        bench_id = db._exercise_id_by_name(cur, "Barbell Squat")
        db._gym_insert(
            cur, "gym_routine_exercises",
            "routine_id, exercise_id, sets, rep_min, rep_max, rest_seconds, order_index",
            (legs_id, bench_id, 4, 8, 10, 90, 0))

    assert any(r["name"] == "Legs Day" for r in _routines()), "fixture must insert it"

    db.seed_gym_routines()   # simulate a redeploy

    assert not any(r["name"] == "Legs Day" for r in _routines()), \
        "stale routine must be reconciled away"
    assert len(_routines()) == 4
    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT COUNT(*) AS n FROM gym_routine_exercises WHERE routine_id = "
            + ("%s" if db.USE_POSTGRES else "?"), (legs_id,)).fetchone()
    assert rows["n"] == 0, "the stale routine's exercise rows must be gone too"


def main():
    setup_module()
    tests = [
        test_1_exactly_four_routines,
        test_2_retired_days_are_gone,
        test_3_days_are_push_pull_push_pull_in_order,
        test_4_day_types_are_unique_so_rotation_cycles_all_four,
        test_5_each_day_has_eight_exercises,
        test_6_target_durations_are_60_58_60_58,
        test_7_set_counts_match_the_documented_totals,
        test_8_push_days_are_chest_or_shoulder_led_pull_days_back_led,
        test_9_every_seeded_exercise_exists_in_the_library,
        test_10_reseeding_does_not_duplicate,
        test_11_a_stale_routine_is_reconciled_away_on_reseed,
    ]
    print("Workout-split tests:")
    passed = 0
    for t in tests:
        t()
        print(f"  {t.__name__}  OK")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
