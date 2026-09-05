"""Finishing a gym session — the paths that used to dead-end.

The reported failure: a "Push · Monday" session left open since Monday, still on
screen on Saturday with the timer reading 92:37, whose FINISH button returned
"Could not finish session" every single time.

Three defects behind it, each covered here:

  1. `POST /api/gym/sets` never checked that the session existed, so a browser
     holding a dead session id kept "saving" sets into nothing. Only FINISH
     checked, which is why nothing looked wrong until the end.
  2. `get_active_session()` was scoped to `date = today`, so a session opened on
     any earlier day — including one that merely crossed midnight — was invisible
     to the resume banner, the only thing that reconciles the browser's
     localStorage session against the server. Nothing could resume or discard it.
  3. `/end` computed duration by subtracting the stored naive `start_time` from
     the browser's `toISOString()` value, which carries a "Z". On Python 3.11+
     that parses as aware, the subtraction raised TypeError, and the handler's
     `except` recorded duration_minutes = 0 — for every workout finished from a
     browser, along with every efficiency figure derived from it.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_finish_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
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


_HDRS = {"X-CSRF-Token": "tok"}


def _exercise_id():
    for e in db.get_all_exercises():
        if (e.get("exercise_type") != "cardio") and (e.get("muscle_group") != "cardio"):
            return e["id"]
    raise AssertionError("no non-cardio exercise in the library")


def _browser_end_time(dt=None):
    """What gym.js actually sends: `new Date().toISOString()` — UTC with a Z."""
    dt = dt or datetime.now()
    return dt.astimezone(db.APP_TZ).astimezone(
        __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")


# ── 1. A dead session id is reported as such, not swallowed ──────────────────

def test_1_finishing_an_unknown_session_is_404_with_a_code():
    c = _client()
    r = c.post("/api/gym/sessions/424242/end",
               json={"end_time": _browser_end_time()}, headers=_HDRS)
    assert r.status_code == 404
    assert r.get_json()["code"] == "session_not_found"


def test_2_logging_a_set_against_an_unknown_session_is_refused():
    """Previously inserted happily and was invisible from then on — the reason a
    stale browser session looked healthy right up until FINISH."""
    c = _client()
    before = len(db.get_session_sets(424242))
    r = c.post("/api/gym/sets",
               json={"session_id": 424242, "exercise_id": _exercise_id(),
                     "set_number": 1, "weight_kg": 60, "reps": 8},
               headers=_HDRS)
    assert r.status_code == 404
    assert r.get_json()["code"] == "session_not_found"
    assert len(db.get_session_sets(424242)) == before, "the set must not be written"


# ── 2. An open session from an earlier day stays reachable ──────────────────

def test_3_active_session_is_found_after_the_day_it_started():
    """The reported session: opened Monday, still open on Saturday."""
    monday = datetime.now() - timedelta(days=4, hours=1)
    sid = db.create_session(None, monday.date().isoformat(), monday.isoformat())
    db.log_set(sid, _exercise_id(), 1, "working", 60, 8)

    active = db.get_active_session()
    assert active is not None, "a session open since Monday must still be resumable"
    assert active["id"] == sid
    assert active["date"] == monday.date().isoformat()
    assert len(active["sets"]) == 1

    # …and it can then actually be closed, which is what FINISH does.
    c = _client()
    r = c.post(f"/api/gym/sessions/{sid}/end",
               json={"end_time": _browser_end_time()}, headers=_HDRS)
    assert r.status_code == 200
    assert db.get_session(sid)["end_time"]
    assert db.get_active_session() is None


def test_4_a_closed_session_is_not_reported_as_active():
    start = datetime.now() - timedelta(minutes=45)
    sid = db.create_session(None, start.date().isoformat(), start.isoformat())
    _client().post(f"/api/gym/sessions/{sid}/end",
                   json={"end_time": _browser_end_time()}, headers=_HDRS)
    act = db.get_active_session()
    assert act is None or act["id"] != sid


# ── 3. Duration survives the browser's Z-suffixed timestamp ─────────────────

def test_5_duration_is_real_for_a_browser_finish():
    start = datetime.now() - timedelta(minutes=75)
    sid = db.create_session(None, start.date().isoformat(), start.isoformat())
    db.log_set(sid, _exercise_id(), 1, "working", 60, 10)

    r = _client().post(f"/api/gym/sessions/{sid}/end",
                       json={"end_time": _browser_end_time()}, headers=_HDRS)
    assert r.status_code == 200
    got = r.get_json()["duration_minutes"]
    assert 73 <= got <= 77, f"expected ~75 minutes, got {got} (0 = the tz bug)"
    assert db.get_session(sid)["duration_minutes"] == got


def test_6_efficiency_is_reported_now_that_duration_is_not_zero():
    """Efficiency is volume ÷ duration, so duration = 0 silently nulled it."""
    start = datetime.now() - timedelta(minutes=60)
    sid = db.create_session(None, start.date().isoformat(), start.isoformat())
    db.log_set(sid, _exercise_id(), 1, "working", 100, 10)   # 1000 kg

    body = _client().post(f"/api/gym/sessions/{sid}/end",
                          json={"end_time": _browser_end_time()},
                          headers=_HDRS).get_json()
    assert body["total_volume_kg"] == 1000
    assert body["efficiency"] is not None and body["efficiency"] > 0


def test_7_explicit_duration_from_the_client_still_wins():
    start = datetime.now() - timedelta(minutes=90)
    sid = db.create_session(None, start.date().isoformat(), start.isoformat())
    r = _client().post(f"/api/gym/sessions/{sid}/end",
                       json={"end_time": _browser_end_time(), "duration": 42},
                       headers=_HDRS)
    assert r.get_json()["duration_minutes"] == 42


def test_8_unparseable_timestamps_degrade_to_zero_not_a_500():
    sid = db.create_session(None, datetime.now().date().isoformat(), "not-a-date")
    r = _client().post(f"/api/gym/sessions/{sid}/end",
                       json={"end_time": _browser_end_time()}, headers=_HDRS)
    assert r.status_code == 200
    assert r.get_json()["duration_minutes"] == 0


def test_9_duration_is_never_negative():
    """Clock skew between phone and server must not write a negative duration."""
    start = datetime.now() + timedelta(minutes=30)      # "starts" in the future
    sid = db.create_session(None, datetime.now().date().isoformat(), start.isoformat())
    r = _client().post(f"/api/gym/sessions/{sid}/end",
                       json={"end_time": _browser_end_time()}, headers=_HDRS)
    assert r.get_json()["duration_minutes"] == 0


if __name__ == "__main__":
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
