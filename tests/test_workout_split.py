"""Workout-split tests — the seeded gym routines that drive the /gym Workout tab's
Quick Start picker and routine grid. Asserts the live 6-day split (Mon Push heavy,
Tue Pull, Wed Bike + Core, Thu Push volume, Fri Pull, Sat/Sun rest), that retired
days are reconciled away on re-seed, that each day's prescribed weights and set
counts are what the plan says, and — the point of the lock — that a routine whose
exercise order has drifted is put back in order on the next boot.

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
# rotation cycles Mon → Tue → Wed → Thu → Fri. Rest days carry no routine.
# (name, day_type, exercise count, total sets, target minutes)
EXPECTED = [
    ("Push · Monday", "push", 5, 15, 38),
    ("Pull · Tuesday", "pull", 6, 20, 50),
    ("Bike + Core · Wednesday", "bike_core", 5, 13, 33),
    ("Push · Thursday", "push_b", 4, 12, 30),
    ("Pull · Friday", "pull_b", 6, 20, 50),
]


def setup_module(module=None):
    """Seed the gym library + routines from a clean slate.

    Named ``setup_module`` (not ``setup``) so pytest runs it too — bare ``setup``
    is nose-style, which pytest dropped in 8.0."""
    db.init_gym_data()


def _routines():
    return db.get_all_routines()


def _by_name():
    return {r["name"]: r for r in _routines()}


def _exercises(name):
    return db.get_routine_exercises(_by_name()[name]["id"])


def _target_minutes(routine_id):
    """Mirror gym.js estimateRoutineMinutes(): round(totalSets × 2.5).

    floor(x + 0.5), not Python's round(), because JS Math.round breaks .5 upwards
    while Python breaks it to even — 32.5 would read as 32 here and 33 in the UI."""
    import math
    total_sets = sum(e["sets"] for e in db.get_routine_exercises(routine_id))
    return max(5, math.floor(total_sets * 2.5 + 0.5))


# ── 1. Only the five trained days exist ───────────────────────────────────────

def test_1_exactly_five_routines():
    rs = _routines()
    assert len(rs) == 5, f"expected 5 routines, got {len(rs)}: {[r['name'] for r in rs]}"


def test_2_retired_days_are_gone():
    names = {r["name"] for r in _routines()}
    # Days from every earlier split, including the 4-day Sat/Mon/Wed/Fri one.
    for retired in ("Legs Day", "Upper Day", "Lower Day", "Push Day", "Pull Day",
                    "Push · Saturday", "Pull · Monday", "Push · Wednesday"):
        assert retired not in names, f"{retired} should be removed"


def test_3_days_are_in_calendar_order():
    rs = _routines()
    assert [(r["name"], r["day_type"]) for r in rs] == \
        [(name, dt) for name, dt, _, _, _ in EXPECTED]
    # order_index is 0..4 in weekday order.
    assert [r["order_index"] for r in rs] == [0, 1, 2, 3, 4]


def test_4_day_types_are_unique_so_rotation_cycles_all_five():
    types = [r["day_type"] for r in _routines()]
    assert len(set(types)) == 5, f"day_types must be distinct for rotation: {types}"


# ── 2. Each day carries the prescribed work ───────────────────────────────────

def test_5_exercise_counts_match_the_plan():
    by_name = _by_name()
    for name, _dt, count, _sets, _mins in EXPECTED:
        got = len(db.get_routine_exercises(by_name[name]["id"]))
        assert got == count, f"{name}: expected {count} exercises, got {got}"


def test_6_target_durations_follow_the_set_counts():
    by_name = _by_name()
    for name, _dt, _count, _sets, mins in EXPECTED:
        got = _target_minutes(by_name[name]["id"])
        assert got == mins, f"{name}: expected {mins} min, got {got}"


def test_7_set_counts_match_the_documented_totals():
    by_name = _by_name()
    for name, _dt, _count, sets, _mins in EXPECTED:
        total = sum(e["sets"] for e in db.get_routine_exercises(by_name[name]["id"]))
        assert total == sets, f"{name}: expected {sets} sets, got {total}"


def test_8_each_day_leads_on_the_right_movement():
    def lead(name):
        return _exercises(name)[0]["name"]

    assert lead("Push · Monday") == "Barbell Bench Press", "Mon leads on heavy bench"
    assert lead("Push · Thursday") == "Barbell Bench Press", "Thu leads on volume bench"
    assert lead("Pull · Tuesday") == "Lat Pulldown"
    assert lead("Pull · Friday") == "Lat Pulldown"
    assert lead("Bike + Core · Wednesday") == "Cycling", "the bike comes first"


def test_9_every_seeded_exercise_exists_in_the_library():
    """A routine that names an exercise not in the library silently drops it,
    which would break the count / set-count assertions — guard the seed."""
    library = {e["name"] for e in db.get_all_exercises()}
    for name, entries in gym_seed.ROUTINE_EXERCISES.items():
        for slot in entries:
            assert slot.exercise in library, \
                f"{name}: '{slot.exercise}' missing from the library"


def test_10_prescribed_weights_are_stored_as_targets():
    """The weights in the plan reach the logger as target_weight — a prescription
    it can show, never a logged value."""
    mon = {e["name"]: e for e in _exercises("Push · Monday")}
    assert mon["Barbell Bench Press"]["target_weight"] == 60
    assert mon["Barbell Bench Press"]["sets"] == 5
    assert (mon["Barbell Bench Press"]["rep_min"],
            mon["Barbell Bench Press"]["rep_max"]) == (5, 5)
    assert "warm-up" in (mon["Barbell Bench Press"]["notes"] or "")
    assert mon["Incline Dumbbell Press"]["target_weight"] == 24

    thu = {e["name"]: e for e in _exercises("Push · Thursday")}
    assert thu["Barbell Bench Press"]["target_weight"] == 60, "same bar, lighter scheme"
    assert thu["Barbell Bench Press"]["sets"] == 3
    assert thu["Lateral Raises"]["target_weight"] == 4.5

    tue = {e["name"]: e for e in _exercises("Pull · Tuesday")}
    assert tue["Lat Pulldown"]["target_weight"] == 73
    assert tue["Pull-ups"]["target_weight"] is None, "bodyweight carries no target"


def test_11_incline_walk_is_on_monday_only():
    for name, _dt, _c, _s, _m in EXPECTED:
        names = [e["name"] for e in _exercises(name)]
        if name == "Push · Monday":
            assert "Incline Walk" in names, "the heavy bench day keeps the walk"
        else:
            assert "Incline Walk" not in names, f"{name} must not carry the incline walk"


def test_12_friday_repeats_tuesday_exactly():
    def shape(name):
        return [(e["name"], e["sets"], e["rep_min"], e["rep_max"], e["target_weight"])
                for e in _exercises(name)]

    assert shape("Pull · Friday") == shape("Pull · Tuesday")


def test_13_cardio_slots_are_flagged_from_the_library():
    """is_cardio comes from the exercise's own type, so the logger shows duration
    rows (not weight × reps) for the walk and the bike without a name special-case."""
    mon = {e["name"]: e for e in _exercises("Push · Monday")}
    assert bool(mon["Incline Walk"]["is_cardio"]) is True
    assert bool(mon["Barbell Bench Press"]["is_cardio"]) is False
    wed = {e["name"]: e for e in _exercises("Bike + Core · Wednesday")}
    assert bool(wed["Cycling"]["is_cardio"]) is True
    assert bool(wed["Plank"]["is_cardio"]) is False


# ── 3. Seeding is idempotent, reconciles, and re-imposes the locked order ──────

def test_14_reseeding_does_not_duplicate():
    db.seed_gym_routines()
    db.seed_gym_routines()
    assert len(_routines()) == 5, "re-seeding must not duplicate routines"


def test_15_routines_are_marked_locked():
    for r in _routines():
        assert r["locked"] is True, f"{r['name']} must advertise its locked order"
        assert r["metadata"] == {"locked": True}


def test_16_a_reordered_routine_is_put_back_on_reseed():
    """The lock's whole purpose: if anything shuffles a day's exercises, the next
    boot restores the seeded order rather than letting it drift."""
    before = [e["name"] for e in _exercises("Push · Monday")]
    rid = _by_name()["Push · Monday"]["id"]
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        # Reverse the order_index of every slot in the day.
        cur = conn.cursor()
        cur.execute(f"SELECT id, order_index FROM gym_routine_exercises "
                    f"WHERE routine_id = {ph}", (rid,))
        rows = [(r["id"], r["order_index"]) for r in cur.fetchall()]
        for row_id, idx in rows:
            cur.execute(f"UPDATE gym_routine_exercises SET order_index = {ph} "
                        f"WHERE id = {ph}", (len(rows) - 1 - idx, row_id))

    assert [e["name"] for e in _exercises("Push · Monday")] == list(reversed(before)), \
        "fixture must actually scramble the order"

    db.seed_gym_routines()   # simulate a redeploy

    assert [e["name"] for e in _exercises("Push · Monday")] == before, \
        "the locked order must be restored"


def test_17_an_edited_prescription_is_restored_on_reseed():
    rid = _by_name()["Push · Thursday"]["id"]
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"UPDATE gym_routine_exercises SET sets = 9, target_weight = 5 "
            f"WHERE routine_id = {ph}", (rid,))
    db.seed_gym_routines()
    thu = {e["name"]: e for e in _exercises("Push · Thursday")}
    assert thu["Barbell Bench Press"]["sets"] == 3
    assert thu["Barbell Bench Press"]["target_weight"] == 60


def test_18_a_stale_routine_is_reconciled_away_on_reseed():
    """A leftover day from an older split (e.g. a redeploy over a DB that still
    has 'Legs Day') is deleted on the next seed, along with its exercise rows."""
    with db.get_db() as conn:
        cur = conn.cursor()
        legs_id = db._gym_insert(
            cur, "gym_routines", "name, day_type, description, order_index",
            ("Legs Day", "legs", "Old split", 9))
        squat_id = db._exercise_id_by_name(cur, "Barbell Squat")
        db._gym_insert(
            cur, "gym_routine_exercises",
            "routine_id, exercise_id, sets, rep_min, rep_max, rest_seconds, order_index",
            (legs_id, squat_id, 4, 8, 10, 90, 0))

    assert any(r["name"] == "Legs Day" for r in _routines()), "fixture must insert it"

    db.seed_gym_routines()   # simulate a redeploy

    assert not any(r["name"] == "Legs Day" for r in _routines()), \
        "stale routine must be reconciled away"
    assert len(_routines()) == 5
    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT COUNT(*) AS n FROM gym_routine_exercises WHERE routine_id = "
            + ("%s" if db.USE_POSTGRES else "?"), (legs_id,)).fetchone()
    assert rows["n"] == 0, "the stale routine's exercise rows must be gone too"


def main():
    setup_module()
    tests = [
        test_1_exactly_five_routines,
        test_2_retired_days_are_gone,
        test_3_days_are_in_calendar_order,
        test_4_day_types_are_unique_so_rotation_cycles_all_five,
        test_5_exercise_counts_match_the_plan,
        test_6_target_durations_follow_the_set_counts,
        test_7_set_counts_match_the_documented_totals,
        test_8_each_day_leads_on_the_right_movement,
        test_9_every_seeded_exercise_exists_in_the_library,
        test_10_prescribed_weights_are_stored_as_targets,
        test_11_incline_walk_is_on_monday_only,
        test_12_friday_repeats_tuesday_exactly,
        test_13_cardio_slots_are_flagged_from_the_library,
        test_14_reseeding_does_not_duplicate,
        test_15_routines_are_marked_locked,
        test_16_a_reordered_routine_is_put_back_on_reseed,
        test_17_an_edited_prescription_is_restored_on_reseed,
        test_18_a_stale_routine_is_reconciled_away_on_reseed,
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
