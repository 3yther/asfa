"""Workout-split tests — the seeded gym routines that drive the /gym Workout tab's
Quick Start picker and routine grid. Asserts the live Sept 1-15 upper/lower/arms
split (Tue Upper A heavy, Wed Lower A, Fri Upper B light, Sat Lower B, Sun Arms +
Shoulders; Mon/Thu rest), that retired days are reconciled away on re-seed, that
each day's prescribed weights and set counts are what the plan says, and — the
point of the lock — that a routine whose exercise order has drifted is put back
in order on the next boot.

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
# rotation cycles Tue → Wed → Fri → Sat → Sun. Rest days (Mon/Thu) carry no routine.
# (name, day_type, exercise count, total sets, target minutes)
EXPECTED = [
    ("Upper A · Tuesday", "upper_a", 7, 20, 50),
    ("Lower A · Wednesday", "lower_a", 4, 12, 30),
    ("Upper B · Friday", "upper_b", 4, 11, 28),
    ("Lower B · Saturday", "lower_b", 4, 12, 30),
    ("Arms + Shoulders · Sunday", "arms", 6, 18, 45),
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
                    "Push · Saturday", "Pull · Monday", "Push · Wednesday",
                    # the previous 6-day push/pull/bike split, now retired
                    "Push · Monday", "Pull · Tuesday", "Bike + Core · Wednesday",
                    "Push · Thursday", "Pull · Friday"):
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

    assert lead("Upper A · Tuesday") == "Barbell Bench Press", "Upper A leads on heavy bench"
    assert lead("Upper B · Friday") == "Barbell Bench Press", "Upper B leads on volume bench"
    assert lead("Lower A · Wednesday") == "Barbell Squat", "Lower A leads on the squat"
    assert lead("Lower B · Saturday") == "Leg Press"
    assert lead("Arms + Shoulders · Sunday") == "Seated Dumbbell Shoulder Press"


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
    ua = {e["name"]: e for e in _exercises("Upper A · Tuesday")}
    assert ua["Barbell Bench Press"]["target_weight"] == 65
    assert ua["Barbell Bench Press"]["sets"] == 5
    assert (ua["Barbell Bench Press"]["rep_min"],
            ua["Barbell Bench Press"]["rep_max"]) == (5, 5)
    assert "warm-up" in (ua["Barbell Bench Press"]["notes"] or "")
    assert ua["Lat Pulldown"]["target_weight"] == 73
    assert ua["Seated Cable Row"]["target_weight"] == 66
    # accessories with no prescribed load carry no target
    assert ua["Cable Lateral Raises"]["target_weight"] is None
    assert ua["Dumbbell Curls"]["target_weight"] is None

    ub = {e["name"]: e for e in _exercises("Upper B · Friday")}
    assert ub["Barbell Bench Press"]["target_weight"] == 65, "same bar, lighter scheme"
    assert ub["Barbell Bench Press"]["sets"] == 3
    assert (ub["Barbell Bench Press"]["rep_min"],
            ub["Barbell Bench Press"]["rep_max"]) == (8, 8)


def test_11_split_carries_no_cardio_slots():
    """The upper/lower/arms split is pure resistance work — no incline walk, no
    bike. Cardio is tracked separately, not inside these routines."""
    for name, _dt, _c, _s, _m in EXPECTED:
        names = [e["name"] for e in _exercises(name)]
        assert "Incline Walk" not in names, f"{name} must not carry the incline walk"
        assert "Cycling" not in names, f"{name} must not carry the bike"
        assert not any(e["is_cardio"] for e in _exercises(name)), \
            f"{name} must have no cardio slots"


def test_12_upper_b_is_the_light_version_of_upper_a():
    """Upper B repeats Upper A's big lifts on the same bar but a lighter scheme —
    bench drops from 5×5 to 3×8 at the identical 65kg."""
    ua = {e["name"]: e for e in _exercises("Upper A · Tuesday")}
    ub = {e["name"]: e for e in _exercises("Upper B · Friday")}
    assert ua["Barbell Bench Press"]["target_weight"] == \
        ub["Barbell Bench Press"]["target_weight"] == 65
    assert ua["Barbell Bench Press"]["sets"] == 5
    assert ub["Barbell Bench Press"]["sets"] == 3
    # both keep the same pulldown/row targets
    assert ua["Lat Pulldown"]["target_weight"] == ub["Lat Pulldown"]["target_weight"] == 73
    assert ua["Seated Cable Row"]["target_weight"] == ub["Seated Cable Row"]["target_weight"] == 66


def test_13_non_cardio_lifts_are_flagged_correctly():
    """is_cardio comes from the exercise's own library type, so the logger shows
    weight × reps rows for these lifts, never duration rows."""
    ua = {e["name"]: e for e in _exercises("Upper A · Tuesday")}
    assert bool(ua["Barbell Bench Press"]["is_cardio"]) is False
    la = {e["name"]: e for e in _exercises("Lower A · Wednesday")}
    assert bool(la["Barbell Squat"]["is_cardio"]) is False


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
    before = [e["name"] for e in _exercises("Upper A · Tuesday")]
    rid = _by_name()["Upper A · Tuesday"]["id"]
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

    assert [e["name"] for e in _exercises("Upper A · Tuesday")] == list(reversed(before)), \
        "fixture must actually scramble the order"

    db.seed_gym_routines()   # simulate a redeploy

    assert [e["name"] for e in _exercises("Upper A · Tuesday")] == before, \
        "the locked order must be restored"


def test_17_an_edited_prescription_is_restored_on_reseed():
    rid = _by_name()["Upper B · Friday"]["id"]
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"UPDATE gym_routine_exercises SET sets = 9, target_weight = 5 "
            f"WHERE routine_id = {ph}", (rid,))
    db.seed_gym_routines()
    fri = {e["name"]: e for e in _exercises("Upper B · Friday")}
    assert fri["Barbell Bench Press"]["sets"] == 3
    assert fri["Barbell Bench Press"]["target_weight"] == 65


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


# ── 4. New-exercise + bodyweight linking ──────────────────────────────────────

def test_19_hip_thrust_is_in_the_library_and_on_lower_b():
    """Hip Thrust was the one gap the new split needed — it must exist in the
    library (so the seed doesn't silently drop it) and appear on Lower B."""
    library = {e["name"] for e in db.get_all_exercises()}
    assert "Hip Thrust" in library, "Hip Thrust must be seeded into the library"
    lower_b = [e["name"] for e in _exercises("Lower B · Saturday")]
    assert "Hip Thrust" in lower_b, "Lower B must carry the Hip Thrust"


def test_20_gym_bodyweight_reads_latest_rephno_scan():
    """The Gym tab's bodyweight comes from the latest body_composition scan — the
    same source the Command page reads — newest date wins, and a synced scan
    (source_id present) is flagged 'rephno'."""
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True

    # no scans yet
    empty = client.get("/api/gym/bodyweight").get_json()
    assert empty["has_data"] is False and empty["weight_kg"] is None, empty

    # a manual entry, then a newer Rephno sync
    db.upsert_body_composition("2026-09-01", {"weight_kg": 79.8})
    db.upsert_body_composition("2026-09-03", {"weight_kg": 79.2}, source_id="renpho-x")
    body = client.get("/api/gym/bodyweight").get_json()
    assert body["has_data"] is True, body
    assert body["weight_kg"] == 79.2, "newest weigh-in wins"
    assert body["weight_lbs"] == 174.6, body
    assert body["date_scanned"] == "2026-09-03", body
    assert body["source"] == "rephno", "a synced scan is flagged rephno"


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
        test_11_split_carries_no_cardio_slots,
        test_12_upper_b_is_the_light_version_of_upper_a,
        test_13_non_cardio_lifts_are_flagged_correctly,
        test_14_reseeding_does_not_duplicate,
        test_15_routines_are_marked_locked,
        test_16_a_reordered_routine_is_put_back_on_reseed,
        test_17_an_edited_prescription_is_restored_on_reseed,
        test_18_a_stale_routine_is_reconciled_away_on_reseed,
        test_19_hip_thrust_is_in_the_library_and_on_lower_b,
        test_20_gym_bodyweight_reads_latest_rephno_scan,
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
