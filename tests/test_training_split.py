"""Training-split (Sept 1-15 cut) tests — DB helpers, endpoints, weekly review.

Self-contained: run directly with

    python tests/test_training_split.py

or under pytest (conftest.py handles per-module DB isolation). Uses an isolated
temp SQLite DB and passes explicit dates so results never depend on the clock.
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

import database as db          # noqa: E402


def _reset():
    """Wipe the tables these tests touch so each test is independent of the
    module-shared SQLite DB (conftest isolates per module, not per function)."""
    db.init_db()
    with db.get_db() as conn:
        cur = conn.cursor()
        for tbl in ("meals", "body_weight", "bench_progression",
                    "split_checklist", "training_splits"):
            try:
                cur.execute(f"DELETE FROM {tbl}")
            except Exception:
                pass
    # training_splits reseeds DEFAULT_SPLIT on next access.
    db._TRAINING_SPLITS_READY = False


def _client():
    """Authed test client with a CSRF token wired for POSTs."""
    import app as app_module
    _reset()
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    return client


_H = {"X-CSRF-Token": "tok"}


# ── Feature 1: nutrition split targets ───────────────────────────────────────

def test_1_split_targets_remaining_budget():
    _reset()
    t = db.get_split_nutrition_targets("2026-09-03")
    assert t["total_daily"] == 1800, t
    assert t["protein"] == 175, t
    # 1800 - 850(lunch) - breakfast range 805..870 => remaining 80..145
    assert t["remaining_budget"]["kcal_low"] == 80, t
    assert t["remaining_budget"]["kcal_high"] == 145, t
    assert t["remaining_budget"]["protein"] == 62, t   # 175 - 58 - 55
    print("  1. split-targets remaining budget 80-145 kcal / 62g  OK")


def test_2_split_targets_live_logging_status():
    _reset()
    db.log_meal("2026-09-04", "Breakfast", 58, 20, 30, calories=850)
    t = db.get_split_nutrition_targets("2026-09-04")
    assert t["logged"]["calories"] == 850 and t["logged"]["protein"] == 58, t
    assert t["status"] == "on_track", t
    # push the day over target by >50 -> red
    db.log_meal("2026-09-04", "Lunch", 55, 40, 30, calories=850)
    db.log_meal("2026-09-04", "Overshoot", 0, 30, 5, calories=200)  # 1900 total
    t = db.get_split_nutrition_targets("2026-09-04")
    assert t["over_by"] == 100.0, t
    assert t["status"] == "over", t
    print("  2. split-targets live logging + status band  OK")


def test_3_split_targets_endpoint():
    c = _client()
    r = c.get("/api/nutrition/split-targets?date=2026-09-03")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["remaining_budget"]["protein"] == 62, r.get_json()
    print("  3. GET /api/nutrition/split-targets  OK")


# ── Feature 2: weight split trend ────────────────────────────────────────────

def test_4_weight_trend_weekly_targets():
    _reset()
    db.log_body_weight("2026-09-02", 79.5)
    t = db.get_weight_split_trend()
    assert t["current_weight"] == 79.5, t
    assert t["target_by_end"] == 76.0, t
    wk1 = t["weeks"][0]
    assert wk1["target_start"] == 80.0 and wk1["target_end"] == 77.8, wk1
    wk2 = t["weeks"][1]
    assert wk2["target_end"] == 76.0, wk2
    print("  4. weight trend weekly targets 80->77.8->76  OK")


def test_5_weight_trend_on_pace_flag():
    _reset()
    db.log_body_weight("2026-09-07", 77.6)   # ahead of 77.8 target for week 1
    t = db.get_weight_split_trend()
    assert t["weeks"][0]["on_pace"] in ("on_pace", "ahead"), t["weeks"][0]
    print("  5. weight trend on-pace flag  OK")


def test_6_weight_trend_endpoint():
    c = _client()
    r = c.get("/api/fitness/weight-split-trend")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["target_by_end"] == 76.0
    print("  6. GET /api/fitness/weight-split-trend  OK")


# ── Feature 3: countdown + checklist ─────────────────────────────────────────

def test_7_split_progress_countdown():
    _reset()
    p = db.get_split_progress("2026-09-03")
    assert p["days_total"] == 15 and p["days_completed"] == 3, p
    assert p["days_remaining"] == 12 and p["state"] == "active", p
    assert db.get_split_progress("2026-08-20")["state"] == "not_started"
    assert db.get_split_progress("2026-09-16")["state"] == "complete"
    print("  7. split-progress countdown + state transitions  OK")


def test_8_checklist_auto_and_manual():
    _reset()
    # auto: logging a meal satisfies nutrition_logged
    db.log_meal("2026-09-05", "Eggs", 12, 0, 8)
    cl = db.get_split_checklist("2026-09-05")
    assert cl["nutrition_logged"] is True and cl["gym_done"] is False, cl
    # manual toggle satisfies gym_done
    cl = db.set_split_checklist_item("2026-09-05", "gym_done", True)
    assert cl["gym_done"] is True, cl
    print("  8. checklist auto-detect + manual toggle  OK")


def test_9_checklist_endpoint():
    c = _client()
    r = c.post("/api/asfa/split-checklist",
               json={"date": "2026-09-06", "item": "weight_logged", "value": True},
               headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["today_checklist"]["weight_logged"] is True
    # bad item -> 400
    bad = c.post("/api/asfa/split-checklist",
                 json={"date": "2026-09-06", "item": "nope", "value": True}, headers=_H)
    assert bad.status_code == 400, bad.get_data(as_text=True)
    print("  9. POST /api/asfa/split-checklist  OK")


# ── Feature 4: bench progression ─────────────────────────────────────────────

def test_10_bench_lock_in_sessions():
    _reset()
    db.log_bench_session("2026-09-01", 65, 5, sets=5, rpe=7.5)
    b = db.get_bench_progression("2026-09-01")
    assert b["current_phase"] == "lock_in", b
    assert b["sessions_completed"] == 1 and b["target_sessions"] == 4, b
    assert b["1rm_tested"] is False, b
    print("  10. bench lock-in 1/4 sessions  OK")


def test_11_bench_1rm_attempt():
    _reset()
    row, err = db.log_bench_session("2026-09-09", 75, 1, is_1rm_attempt=True)
    assert err is None, err
    b = db.get_bench_progression("2026-09-09")
    assert b["current_phase"] == "test", b
    assert b["1rm_tested"] is True, b
    assert b["1rm_value"] == 77.5, b   # Epley: 75 * (1 + 1/30)
    print("  11. bench 1RM attempt -> tested 77.5kg  OK")


def test_12_bench_endpoints():
    c = _client()
    r = c.post("/api/fitness/bench-progression",
               json={"session_date": "2026-09-01", "weight_kg": 65, "reps": 5, "sets": 5},
               headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["progression"]["sessions_completed"] == 1
    g = c.get("/api/fitness/bench-progression?date=2026-09-01")
    assert g.status_code == 200 and g.get_json()["target"] == "65kg × 5×5"
    print("  12. GET/POST /api/fitness/bench-progression  OK")


# ── Feature 5: weekly review ─────────────────────────────────────────────────

def test_13_weekly_review_service():
    from services.training import generate_weekly_split_review
    _reset()
    db.log_body_weight("2026-09-01", 80.0)
    db.log_body_weight("2026-09-07", 78.5)
    db.log_bench_session("2026-09-01", 65, 5, sets=5)
    wr = generate_weekly_split_review(1)
    assert wr["week"] == 1, wr
    assert wr["weight_start"] == 80.0 and wr["weight_end"] == 78.5, wr
    assert wr["weight_target_end"] == 77.8, wr
    assert "gym_sessions" in wr and "nutrition_adherence" in wr, wr
    print("  13. weekly review service (week 1)  OK")


def test_14_weekly_review_endpoint():
    c = _client()
    r = c.get("/api/asfa/split-weekly-review?week=1")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["week"] == 1
    print("  14. GET /api/asfa/split-weekly-review  OK")


def main():
    tests = [test_1_split_targets_remaining_budget,
             test_2_split_targets_live_logging_status,
             test_3_split_targets_endpoint,
             test_4_weight_trend_weekly_targets,
             test_5_weight_trend_on_pace_flag,
             test_6_weight_trend_endpoint,
             test_7_split_progress_countdown,
             test_8_checklist_auto_and_manual,
             test_9_checklist_endpoint,
             test_10_bench_lock_in_sessions,
             test_11_bench_1rm_attempt,
             test_12_bench_endpoints,
             test_13_weekly_review_service,
             test_14_weekly_review_endpoint]
    print("Training-split (Sept 1-15) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
