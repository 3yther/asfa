"""Sleep tracking (Tier 6) tests — DB helpers + one Flask-client endpoint check.

Self-contained, no pytest dependency: run directly with

    python tests/test_sleep.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db, and
passes explicit dates so results don't depend on the system clock.
"""
import os
import sys
import tempfile

# Point the DB layer at a throwaway file BEFORE importing database/app, and set
# the auth/session env the Flask app needs. Both must happen pre-import.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_sleep_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


def test_1_log_and_score():
    row, err = db.log_sleep_entry("2026-07-06", 7.5, 4)
    assert err is None, f"expected no error, got {err!r}"
    assert row is not None, "expected inserted row"
    assert row["date"] == "2026-07-06"
    assert db.score_readiness(7.5, 4) == 95, db.score_readiness(7.5, 4)
    print("  1. log_sleep_entry(2026-07-06,7.5,4) -> (row,None); score=95  [actual 95]  OK")


def test_2_duplicate_no_overwrite():
    # Attempt a second insert for the same night with different values.
    row, err = db.log_sleep_entry("2026-07-06", 3.0, 1)
    assert row is None and err == "duplicate", f"expected (None,'duplicate'), got ({row},{err})"
    # Original night must be untouched.
    existing = db.get_sleep("2026-07-06")
    assert existing["duration"] == 7.5 and existing["quality"] == 4, existing
    print("  2. duplicate 2026-07-06 -> (None,'duplicate'); original 7.5/4 intact  OK")


def test_3_second_night():
    row, err = db.log_sleep_entry("2026-07-05", 5.0, 3)
    assert err is None and row is not None
    assert db.score_readiness(5.0, 3) == 65, db.score_readiness(5.0, 3)
    print("  3. log_sleep_entry(2026-07-05,5.0,3) -> (row,None); score=65  [actual 65]  OK")


def test_4_readiness_missing():
    assert db.get_sleep_readiness("2026-07-01") is None
    print("  4. get_sleep_readiness(2026-07-01) -> None  OK")


def test_5_endpoint_post():
    import app as app_module
    client = app_module.app.test_client()
    # Satisfy the global auth gate + CSRF check without a full login round-trip.
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    resp = client.post(
        "/api/sleep/log",
        json={"date": "2026-07-07", "duration": 8, "quality": 5},
        headers={"X-CSRF-Token": "tok"},
    )
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["readiness"] == 100, body
    print(f"  5. POST /api/sleep/log {{8h,q5}} -> 200; ok=True; readiness=100  [actual {body['readiness']}]  OK")


def main():
    tests = [test_1_log_and_score, test_2_duplicate_no_overwrite, test_3_second_night,
             test_4_readiness_missing, test_5_endpoint_post]
    print("Sleep tracking (Tier 6) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
