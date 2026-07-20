"""Cardio + pre-workout tests.

Covers two additions to the gym module:
  * Pre-workout logging — gym_sets.pre_workout_type, carried on every set of a
    session and surfaced in session history.
  * Standalone cardio — the cardio_sessions table, which is deliberately NOT a
    gym_session: logging cardio must never create a lifting session, advance the
    Push/Pull rotation, or bump the workout streak.

Runs either way — standalone (no pytest dependency) or under pytest:

    python tests/test_gym_cardio_preworkout.py
    pytest tests/test_gym_cardio_preworkout.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so nothing touches asfa.db.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_cardio_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


def setup_module(module=None):
    db.init_gym_data()


def _client():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    return client


def _lifting_exercise_id():
    """A non-cardio exercise to log sets against."""
    for e in db.get_all_exercises():
        if (e.get("exercise_type") != "cardio") and (e.get("muscle_group") != "cardio"):
            return e["id"]
    raise AssertionError("no non-cardio exercise in the library")


# ══ Part 2 — pre-workout ══════════════════════════════════════════════════════

def test_1_pre_workout_is_stored_on_the_set():
    ex = _lifting_exercise_id()
    sid = db.create_session(None, "2026-07-18", "2026-07-18T10:00:00")
    res = db.log_set(sid, ex, 1, "working", 60, 8, pre_workout_type="origin_pre_workout")
    assert res["pre_workout_type"] == "origin_pre_workout"
    sets = db.get_session_sets(sid)
    assert sets[0]["pre_workout_type"] == "origin_pre_workout"


def test_2_pre_workout_defaults_to_none():
    ex = _lifting_exercise_id()
    sid = db.create_session(None, "2026-07-18", "2026-07-18T11:00:00")
    res = db.log_set(sid, ex, 1, "working", 60, 8)
    assert res["pre_workout_type"] == "none"
    assert db.get_session_sets(sid)[0]["pre_workout_type"] == "none"


def test_3_invalid_pre_workout_collapses_to_none():
    ex = _lifting_exercise_id()
    for bad in ("", None, "monster", "coffee", "  ENERGY_DRINK  x"):
        sid = db.create_session(None, "2026-07-18", "2026-07-18T12:00:00")
        res = db.log_set(sid, ex, 1, "working", 60, 8, pre_workout_type=bad)
        assert res["pre_workout_type"] == "none", f"{bad!r} must normalise to none"


def test_4_pre_workout_is_case_insensitive():
    ex = _lifting_exercise_id()
    sid = db.create_session(None, "2026-07-18", "2026-07-18T12:30:00")
    res = db.log_set(sid, ex, 1, "working", 60, 8, pre_workout_type="  Energy_Drink ")
    assert res["pre_workout_type"] == "energy_drink"


def test_5_recent_sessions_surface_the_session_pre_workout():
    ex = _lifting_exercise_id()
    sid = db.create_session(None, "2026-07-19", "2026-07-19T10:00:00")
    db.log_set(sid, ex, 1, "working", 60, 8, pre_workout_type="energy_drink")
    db.log_set(sid, ex, 2, "working", 60, 8, pre_workout_type="energy_drink")
    row = [s for s in db.get_recent_sessions(50) if s["id"] == sid][0]
    assert row["pre_workout_type"] == "energy_drink"


def test_6_recent_sessions_report_none_as_absent():
    ex = _lifting_exercise_id()
    sid = db.create_session(None, "2026-07-20", "2026-07-20T10:00:00")
    db.log_set(sid, ex, 1, "working", 60, 8)   # no pre-workout
    row = [s for s in db.get_recent_sessions(50) if s["id"] == sid][0]
    assert row["pre_workout_type"] is None, "a 'none' session must not report a badge"


def test_7_pre_workout_logs_through_the_api():
    client = _client()
    sid = db.create_session(None, "2026-07-21", "2026-07-21T10:00:00")
    ex = _lifting_exercise_id()
    r = client.post("/api/gym/sets", json={
        "session_id": sid, "exercise_id": ex, "set_number": 1,
        "weight_kg": 60, "reps": 8, "pre_workout_type": "origin_pre_workout"},
        headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["pre_workout_type"] == "origin_pre_workout"
    assert db.get_session_sets(sid)[0]["pre_workout_type"] == "origin_pre_workout"


# ══ Part 3 — cardio ═══════════════════════════════════════════════════════════

def test_8_cardio_logs_and_reads_back():
    cid = db.log_cardio_session("2026-07-18", "cycling", 7.9, 46, 3, "easy spin")
    assert cid
    row = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid][0]
    assert row["type"] == "cycling"
    assert row["distance_miles"] == 7.9
    assert row["duration_minutes"] == 46
    assert row["perceived_effort"] == 3
    assert row["notes"] == "easy spin"


def test_9_cardio_type_and_effort_are_validated():
    cid = db.log_cardio_session("2026-07-18", "moonwalk", perceived_effort=11)
    row = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid][0]
    assert row["type"] == "other", "unknown type must fall back to 'other'"
    assert row["perceived_effort"] is None, "effort outside 1-10 must be dropped"

    cid2 = db.log_cardio_session("2026-07-18", "treadmill", perceived_effort=0)
    row2 = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid2][0]
    assert row2["perceived_effort"] is None

    # RPE now runs 1-10 (Strava-style), so 7 and 9 must survive.
    cid3 = db.log_cardio_session("2026-07-18", "cycling", perceived_effort=7)
    row3 = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid3][0]
    assert row3["perceived_effort"] == 7, "effort inside 1-10 must be kept"


def test_10_cardio_does_not_create_a_gym_session():
    before = len(db.get_recent_sessions(200))
    db.log_cardio_session("2026-07-22", "treadmill", 2.0, 30, 4)
    after = len(db.get_recent_sessions(200))
    assert after == before, "cardio must not appear as a lifting session"


def test_11_cardio_does_not_advance_the_streak():
    streak_before = db.get_streak()
    db.log_cardio_session("2026-07-23", "cycling", 5.0, 40, 3)
    assert db.get_streak() == streak_before, "cardio must not touch the workout streak"


def test_12_cardio_day_is_not_a_workout_on_the_calendar():
    """A cardio-only day must not show up as a workout in the streak calendar —
    that calendar drives the 'gym day' consistency grid."""
    db.log_cardio_session("2026-07-24", "cycling", 8.0, 45, 3)
    cal = db.get_streak_calendar(months=3)
    assert cal.get("2026-07-24") in (False, None), \
        "a cardio-only day must not read as a gym day"


def test_13_cardio_deletes():
    cid = db.log_cardio_session("2026-07-25", "other", 1.0, 15, 2)
    assert db.delete_cardio_session(cid) is True
    assert not any(c["id"] == cid for c in db.get_recent_cardio_sessions(50))
    assert db.delete_cardio_session(cid) is False, "deleting again is a no-op"


def test_14_cardio_round_trips_through_the_api():
    client = _client()
    r = client.post("/api/gym/cardio", json={
        "date": "2026-07-26", "type": "cycling", "distance_miles": 7.9,
        "duration_minutes": 46, "perceived_effort": 3, "notes": "commute"},
        headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    cid = r.get_json()["id"]

    listed = client.get("/api/gym/cardio?limit=50").get_json()
    assert any(c["id"] == cid for c in listed)

    d = client.delete(f"/api/gym/cardio/{cid}", headers={"X-CSRF-Token": "tok"})
    assert d.status_code == 200
    assert client.delete(f"/api/gym/cardio/{cid}",
                         headers={"X-CSRF-Token": "tok"}).status_code == 404


def test_15_cardio_api_does_not_advance_gym_state():
    """End-to-end guard: a cardio POST leaves the lifting session count and streak
    exactly where they were."""
    client = _client()
    sessions_before = len(db.get_recent_sessions(200))
    streak_before = db.get_streak()
    r = client.post("/api/gym/cardio", json={"type": "treadmill", "duration_minutes": 30},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200
    assert len(db.get_recent_sessions(200)) == sessions_before
    assert db.get_streak() == streak_before


def test_16_strava_metrics_round_trip_and_autocalc_steps():
    """The full Strava payload persists, and steps_equivalent defaults to
    distance x 1400 when the caller does not supply one."""
    client = _client()
    r = client.post("/api/gym/cardio", json={
        "date": "2026-07-27", "time": "19:30", "type": "cycling",
        "distance_miles": 7.93, "elevation_gain": 450, "avg_speed": 10.3,
        "max_speed": 22.5, "perceived_effort": 7, "notes": "Strava activity #12345",
        "steps_equivalent": 14274}, headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    cid = r.get_json()["id"]

    row = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid][0]
    assert row["start_time"] == "19:30"
    assert row["elevation_gain"] == 450
    assert row["avg_speed"] == 10.3 and row["max_speed"] == 22.5
    assert row["perceived_effort"] == 7, "1-10 RPE must survive the round trip"
    assert row["steps_equivalent"] == 14274

    # No explicit steps_equivalent -> derived from distance.
    cid2 = db.log_cardio_session("2026-07-27", "cycling", distance_miles=2.0)
    row2 = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == cid2][0]
    assert row2["steps_equivalent"] == 2800, "2.0 mi x 1400 = 2800"


def test_17_cardio_steps_add_to_the_day_and_are_reclaimed_on_delete():
    """Cardio steps must ADD to (never replace) the day's natural step count,
    and deleting the session must take only its own steps back out."""
    day = "2026-07-28"
    db.add_step_entry(day, "manual", 3000, {"src": "watch"})
    assert db.get_steps_day_total(day) == 3000

    cid = db.log_cardio_session(day, "cycling", distance_miles=5.0)
    assert db.get_steps_day_total(day) == 3000 + 7000, "cardio steps must add on top"

    assert db.delete_cardio_session(cid) is True
    assert db.get_steps_day_total(day) == 3000, "watch steps must survive the delete"


def test_18_legacy_cardio_payload_still_works():
    """Backward compatibility: a pre-Strava payload logs exactly as before and
    contributes no step entry when it carries no distance."""
    client = _client()
    r = client.post("/api/gym/cardio", json={
        "date": "2026-07-29", "type": "treadmill", "duration_minutes": 25,
        "perceived_effort": 3, "notes": "legacy"}, headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200
    row = [c for c in db.get_recent_cardio_sessions(50) if c["id"] == r.get_json()["id"]][0]
    assert row["duration_minutes"] == 25 and row["notes"] == "legacy"
    assert row["start_time"] is None and row["elevation_gain"] is None
    assert db.get_steps_day_total("2026-07-29") == 0, "no distance -> no steps row"


def main():
    setup_module()
    tests = [
        test_1_pre_workout_is_stored_on_the_set,
        test_2_pre_workout_defaults_to_none,
        test_3_invalid_pre_workout_collapses_to_none,
        test_4_pre_workout_is_case_insensitive,
        test_5_recent_sessions_surface_the_session_pre_workout,
        test_6_recent_sessions_report_none_as_absent,
        test_7_pre_workout_logs_through_the_api,
        test_8_cardio_logs_and_reads_back,
        test_9_cardio_type_and_effort_are_validated,
        test_10_cardio_does_not_create_a_gym_session,
        test_11_cardio_does_not_advance_the_streak,
        test_12_cardio_day_is_not_a_workout_on_the_calendar,
        test_13_cardio_deletes,
        test_14_cardio_round_trips_through_the_api,
        test_15_cardio_api_does_not_advance_gym_state,
        test_16_strava_metrics_round_trip_and_autocalc_steps,
        test_17_cardio_steps_add_to_the_day_and_are_reclaimed_on_delete,
        test_18_legacy_cardio_payload_still_works,
    ]
    print("Cardio + pre-workout tests:")
    passed = 0
    for t in tests:
        t()
        print(f"  {t.__name__}  OK")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
