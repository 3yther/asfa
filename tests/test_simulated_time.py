"""Simulated system clock — DB helpers + Settings endpoints + backfill flow.

Runs under pytest (root conftest.py isolates the DB and re-runs init_db, which
creates user_settings) and standalone:

    python tests/test_simulated_time.py

The override lets the Settings page pin "now" to an earlier instant so meals,
workouts, cardio and water logs are backfilled to that date/time until reset.
"""
import os
import sys
import tempfile
from datetime import datetime

# Point the DB layer at a throwaway file BEFORE importing database/app, and set
# the auth/session env the Flask app needs. Both must happen pre-import. Under
# pytest these are already pinned by conftest.py and this preamble is inert.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_simclock_test_"), "test.db")
os.environ.setdefault("ASFA_DB_PATH", _TMP_DB)
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ASFA_TZ", "Europe/London")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

db.init_db()  # no-op under pytest (conftest already ran it); needed standalone


def _authed_client():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    return client


# ── DB layer ─────────────────────────────────────────────────────────────────

def test_default_is_real_clock():
    db.set_simulated_time(db.DEFAULT_USER_ID, None)
    assert db.get_simulated_time() is None
    assert db.is_simulated() is False
    # get_current_time falls through to the real clock within a second of now.
    delta = abs((db.get_current_time() - db.now_local()).total_seconds())
    assert delta < 2, delta
    print("  1. no override -> real clock  OK")


def test_set_and_get_override():
    target = datetime(2026, 4, 10, 20, 15)  # naive -> assumed Europe/London
    db.set_simulated_time(db.DEFAULT_USER_ID, target)
    sim = db.get_simulated_time()
    assert sim is not None, "override should be set"
    assert sim.tzinfo is not None, "must return an aware datetime"
    assert sim.strftime("%Y-%m-%d %H:%M") == "2026-04-10 20:15", sim
    assert db.is_simulated() is True
    assert db.get_current_time().strftime("%Y-%m-%d %H:%M") == "2026-04-10 20:15"
    print("  2. set override -> get_current_time returns it  OK")


def test_reset_clears_override():
    db.set_simulated_time(db.DEFAULT_USER_ID, datetime(2020, 1, 1, 0, 0))
    assert db.is_simulated() is True
    db.set_simulated_time(db.DEFAULT_USER_ID, None)
    assert db.get_simulated_time() is None
    assert db.is_simulated() is False
    print("  3. reset -> override cleared  OK")


# ── Settings endpoints ───────────────────────────────────────────────────────

def test_endpoint_set_and_current_time():
    db.set_simulated_time(db.DEFAULT_USER_ID, None)
    client = _authed_client()

    # GET current-time on the real clock.
    r = client.get("/api/settings/current-time")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["is_simulated"] is False, body

    # POST an override (datetime-local style, no seconds/tz).
    r = client.post("/api/settings/simulated-time",
                    json={"dt": "2026-04-10T20:15"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["success"] is True and body["is_simulated"] is True, body
    assert body["simulated_time"].startswith("2026-04-10T20:15"), body

    # GET now reflects the override.
    body = client.get("/api/settings/current-time").get_json()
    assert body["is_simulated"] is True, body
    assert body["current_time"].startswith("2026-04-10T20:15"), body

    # Reset via {"dt": null}.
    r = client.post("/api/settings/simulated-time", json={"dt": None},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200 and r.get_json()["is_simulated"] is False
    assert db.get_simulated_time() is None
    print("  4. POST set/reset + GET current-time  OK")


def test_endpoint_bad_dt_rejected():
    client = _authed_client()
    r = client.post("/api/settings/simulated-time", json={"dt": "not-a-date"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400, r.get_data(as_text=True)
    db.set_simulated_time(db.DEFAULT_USER_ID, None)
    print("  5. bad dt -> 400  OK")


# ── Backfill flow (the actual point) ─────────────────────────────────────────

def test_meal_backfills_to_simulated_clock():
    client = _authed_client()
    real_today = db.today_str()

    # Set the clock to 8:15 PM on a fixed past date.
    client.post("/api/settings/simulated-time", json={"dt": "2026-04-10T20:15"},
                headers={"X-CSRF-Token": "tok"})

    # Log a meal while the client still sends *today's* date — the server must
    # override it with the simulated instant.
    r = client.post("/api/nutrition/log",
                    json={"date": real_today, "food_name": "Backfill Chicken",
                          "protein": 40, "carbs": 0, "fat": 5, "source": "manual"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    meal = r.get_json()["meal"]
    assert meal["date"] == "2026-04-10", meal
    assert meal["time"] == "20:15", meal
    # And it is retrievable under the backfilled day, not today.
    days = [m["food_name"] for m in db.get_meals("2026-04-10")]
    assert "Backfill Chicken" in days, days

    # Reset, then a new meal lands under the real clock again.
    client.post("/api/settings/simulated-time", json={"dt": None},
                headers={"X-CSRF-Token": "tok"})
    r = client.post("/api/nutrition/log",
                    json={"date": real_today, "food_name": "RealTime Rice",
                          "protein": 5, "carbs": 60, "fat": 1, "source": "manual"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    meal = r.get_json()["meal"]
    assert meal["date"] == real_today, meal
    print("  6. meal backfills to sim clock, resets to real  OK")


def test_water_backfills_to_simulated_clock():
    client = _authed_client()
    client.post("/api/settings/simulated-time", json={"dt": "2026-04-10T20:15"},
                headers={"X-CSRF-Token": "tok"})
    # Browser sends a real "now" timestamp; the sim override must win.
    r = client.post("/api/asfa/water-intake",
                    json={"amount": 500, "timestamp": db.now_local().isoformat()},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.get_hydration_total("2026-04-10") >= 500, "water should land on 2026-04-10"
    client.post("/api/settings/simulated-time", json={"dt": None},
                headers={"X-CSRF-Token": "tok"})
    print("  7. water backfills to sim clock  OK")


def test_steps_backfill_to_simulated_clock():
    client = _authed_client()
    real_today = db.today_str()

    # Clock at 8:15 PM on a fixed past date.
    client.post("/api/settings/simulated-time", json={"dt": "2026-04-10T20:15"},
                headers={"X-CSRF-Token": "tok"})

    # Log steps while the client sends *today's* date — the server must backfill.
    r = client.post("/api/steps/log",
                    json={"date": real_today, "source": "manual", "steps": 3200},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    entry = r.get_json()["entry"]
    assert entry["date"] == "2026-04-10", entry
    assert str(entry.get("created_at", "")).startswith("2026-04-10 20:15"), entry
    assert db.get_steps_day_total("2026-04-10") >= 3200
    assert db.get_steps_day_total(real_today) == 0, "must not land on real today"

    # Reset → new steps land under the real clock again.
    client.post("/api/settings/simulated-time", json={"dt": None},
                headers={"X-CSRF-Token": "tok"})
    r = client.post("/api/steps/log",
                    json={"source": "manual", "steps": 500},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["entry"]["date"] == real_today
    print("  8. steps backfill to sim clock, reset to real  OK")


if __name__ == "__main__":
    print("simulated system clock tests")
    for fn in [test_default_is_real_clock, test_set_and_get_override,
               test_reset_clears_override, test_endpoint_set_and_current_time,
               test_endpoint_bad_dt_rejected, test_meal_backfills_to_simulated_clock,
               test_water_backfills_to_simulated_clock,
               test_steps_backfill_to_simulated_clock]:
        fn()
    print("ALL PASS")
