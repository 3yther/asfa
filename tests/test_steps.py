"""Steps + cardio→step-equivalent tests — pure conversion layer plus the
/api/steps/* endpoints. Self-contained (no pytest); run with:

    python tests/test_steps.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
The conversion functions are pure (no DB); the endpoint tests boot the app.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_steps_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import steps as steps_svc   # noqa: E402
import app as app_module                  # noqa: E402
import database as db                      # noqa: E402


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


_HDR = {"X-CSRF-Token": "tok"}


# ── Pure conversion layer ────────────────────────────────────────────────────────
def test_1_treadmill_walking_figure():
    steps, note = steps_svc.treadmill_to_steps(30, 6.5, 0)
    # stride 0.675m over 3250m → ~4810. A sane walking figure for 30 brisk min.
    assert 3000 <= steps <= 5500, steps
    assert note == "", note
    assert steps % 10 == 0, steps
    print(f"  1. treadmill 30min @6.5kph flat = {steps} steps  OK")


def test_2_incline_adds_steps():
    flat, _ = steps_svc.treadmill_to_steps(30, 6.5, 0)
    incl, _ = steps_svc.treadmill_to_steps(30, 6.5, 4)
    # +5% per 1% grade → 4% grade ≈ +20% (spec formula 1+0.05*pct).
    ratio = incl / flat
    assert 1.15 <= ratio <= 1.25, ratio
    print(f"  2. incline 4%: {flat}→{incl} (×{ratio:.2f})  OK")


def test_3_bike_terrain_multiplier():
    flat, note = steps_svc.bike_to_steps(10, 20, "flat")
    hilly, _ = steps_svc.bike_to_steps(10, 20, "hilly")
    assert flat == 11000, flat
    assert hilly == 14300, hilly            # exactly 1.3× flat
    assert abs(hilly / flat - 1.3) < 0.001, hilly / flat
    assert note == "effort-equivalent, not measured", note
    print(f"  3. bike 10km@20kph flat={flat}, hilly={hilly} (×1.3)  OK")


def test_4_bike_speed_effort():
    fast, _ = steps_svc.bike_to_steps(10, 30, "flat")
    slow, _ = steps_svc.bike_to_steps(10, 15, "flat")
    assert fast > slow, (fast, slow)
    print(f"  4. bike 30kph={fast} > 15kph={slow} (same 10km)  OK")


def test_5_clamps_reject_not_coerce():
    bad = [
        lambda: steps_svc.treadmill_to_steps(30, 0, 0),       # kph too low
        lambda: steps_svc.treadmill_to_steps(-5, 6.5, 0),     # minutes negative
        lambda: steps_svc.treadmill_to_steps(30, "x", 0),     # non-numeric
        lambda: steps_svc.bike_to_steps(1000, 20, "flat"),    # distance too far
        lambda: steps_svc.bike_to_steps(10, 20, "mountain"),  # bad terrain
        lambda: steps_svc.bike_to_steps(10, 3, "flat"),       # kph too low
    ]
    for fn in bad:
        try:
            fn()
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError from {fn}")
    print("  5. out-of-range inputs raise ValueError (never coerced)  OK")


# ── DB layer ─────────────────────────────────────────────────────────────────────
def test_6_manual_stored_verbatim():
    entry = db.add_step_entry("2026-07-10", "manual", 5000, {"steps": 5000})
    assert entry["steps"] == 5000, entry
    rows = db.get_steps_for_date("2026-07-10")
    assert any(r["steps"] == 5000 and r["source"] == "manual" for r in rows), rows
    print("  6. manual 5000 stored verbatim (no conversion)  OK")


def test_7_multi_source_sum_and_delete():
    d = "2026-07-11"
    db.add_step_entry(d, "manual", 3000, {"steps": 3000})
    tread = db.add_step_entry(d, "treadmill", 4200, {"minutes": 30, "kph": 8})
    bike = db.add_step_entry(d, "bike", 11000, {"distance_km": 10, "kph": 20, "terrain": "flat"})
    assert db.get_steps_day_total(d) == 3000 + 4200 + 11000, db.get_steps_day_total(d)
    # Delete the bike entry — the other two survive.
    deleted_date = db.delete_step_entry(bike["id"])
    assert deleted_date == d, deleted_date
    assert db.get_steps_day_total(d) == 3000 + 4200, db.get_steps_day_total(d)
    remaining = {r["source"] for r in db.get_steps_for_date(d)}
    assert remaining == {"manual", "treadmill"}, remaining
    print("  7. day total = SUM; deleting bike leaves the other two  OK")


def test_8_goal_default_and_upsert():
    assert db.get_steps_goal() == 10000, db.get_steps_goal()
    assert db.set_steps_goal(12000) == 12000
    assert db.get_steps_goal() == 12000
    db.set_steps_goal(10000)   # reset for later assertions
    print("  8. goal defaults to 10000; upsert works  OK")


# ── Endpoints ────────────────────────────────────────────────────────────────────
def test_9_log_endpoints():
    c = _client()
    r = c.post("/api/steps/log", json={"date": "2026-07-12", "source": "manual", "steps": 5000}, headers=_HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["day_total"] == 5000 and body["goal"] == 10000, body

    r = c.post("/api/steps/log", json={"date": "2026-07-12", "source": "treadmill",
                                       "minutes": 30, "kph": 6.5, "incline_pct": 2}, headers=_HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    tread_steps = r.get_json()["entry"]["steps"]

    r = c.post("/api/steps/log", json={"date": "2026-07-12", "source": "bike",
                                       "distance_km": 10, "kph": 20, "terrain": "hilly"}, headers=_HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    entry = r.get_json()["entry"]
    assert entry["note"] == "effort-equivalent, not measured", entry
    assert entry["steps"] == 14300, entry
    print(f"  9. log manual/treadmill({tread_steps})/bike(14300) via endpoint  OK")


def test_10_bad_inputs_400():
    c = _client()
    for payload in [
        {"date": "2026-07-12", "source": "treadmill", "minutes": 30, "kph": 0},
        {"date": "2026-07-12", "source": "bike", "distance_km": 10, "kph": 20, "terrain": "mountain"},
        {"date": "2026-07-12", "source": "manual", "steps": 0},
        {"date": "2026-07-12", "source": "rowing", "steps": 100},
        {"date": "bad-date", "source": "manual", "steps": 100},
    ]:
        r = c.post("/api/steps/log", json=payload, headers=_HDR)
        assert r.status_code == 400, (payload, r.status_code)
    print("  10. kph=0 / terrain=mountain / steps=0 / bad source / bad date → 400  OK")


def test_11_date_week_delete_endpoints():
    c = _client()
    r = c.get("/api/steps/date/2026-07-12")
    day = r.get_json()
    assert day["total"] > 0 and len(day["entries"]) >= 3, day
    bike_entry = next(e for e in day["entries"] if e["source"] == "bike")

    wk = c.get("/api/steps/week?end=2026-07-12").get_json()
    assert len(wk["days"]) == 7, wk
    assert wk["days"][-1]["date"] == "2026-07-12", wk

    before = day["total"]
    r = c.post("/api/steps/delete", json={"entry_id": bike_entry["id"]}, headers=_HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["day_total"] == before - 14300, r.get_json()
    # deleting a non-existent id → 404
    assert c.post("/api/steps/delete", json={"entry_id": 999999}, headers=_HDR).status_code == 404
    print("  11. date/week/delete endpoints behave; delete drops the bike steps  OK")


def test_12_goal_endpoints():
    c = _client()
    assert c.get("/api/steps/goal").get_json()["steps_goal"] == 10000
    r = c.post("/api/steps/goal", json={"steps_goal": 8000}, headers=_HDR)
    assert r.status_code == 200 and r.get_json()["steps_goal"] == 8000
    assert c.post("/api/steps/goal", json={"steps_goal": 0}, headers=_HDR).status_code == 400
    db.set_steps_goal(10000)
    print("  12. goal GET/POST endpoints + validation  OK")


def main():
    tests = [test_1_treadmill_walking_figure, test_2_incline_adds_steps,
             test_3_bike_terrain_multiplier, test_4_bike_speed_effort,
             test_5_clamps_reject_not_coerce, test_6_manual_stored_verbatim,
             test_7_multi_source_sum_and_delete, test_8_goal_default_and_upsert,
             test_9_log_endpoints, test_10_bad_inputs_400,
             test_11_date_week_delete_endpoints, test_12_goal_endpoints]
    print("Steps + cardio→step-equivalent tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
