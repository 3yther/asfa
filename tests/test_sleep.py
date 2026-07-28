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


# ── Regression: the sleep → briefing bridge (F-02) ─────────────────────────────
# Sleep has two writers — the Tier 6 `sleep` table (what the UI posts to) and the
# legacy habits.sleep_hours column (the chat/Telegram "slept 7h" command). The
# briefing and insights layers read only `habits` for months, so every night
# logged through the UI averaged to 0.0h, which also permanently disabled the
# "0 < avg < 6" under-sleeping alert. These tests pin the merge so the two stores
# can never silently drift apart again.

def test_6_merge_reads_both_stores():
    from datetime import date, timedelta
    today = date.today()
    ui_day = (today - timedelta(days=1)).isoformat()
    legacy_day = (today - timedelta(days=2)).isoformat()

    db.log_sleep_entry(ui_day, 7.5, 4)      # Tier 6 table (the UI path)
    db.log_sleep(legacy_day, 6.5)           # legacy habits.sleep_hours

    merged = db.get_sleep_hours_by_day(7)
    assert merged.get(ui_day) == 7.5, merged
    assert merged.get(legacy_day) == 6.5, merged
    print(f"  6. get_sleep_hours_by_day merges both stores "
          f"({ui_day}=7.5 from `sleep`, {legacy_day}=6.5 from habits)  OK")


def test_7_structured_sleep_wins_on_conflict():
    from datetime import date, timedelta
    day = (date.today() - timedelta(days=3)).isoformat()
    db.log_sleep_entry(day, 8.0, 5)   # structured
    db.log_sleep(day, 3.0)            # legacy, same night, conflicting
    assert db.get_sleep_hours_by_day(7).get(day) == 8.0, db.get_sleep_hours_by_day(7)
    print("  7. conflicting night -> structured `sleep` row wins over legacy 3.0h  OK")


def test_8_insights_sees_ui_logged_sleep():
    """The actual bug: nights logged through the UI reported 0.0h/night.

    Clears BOTH stores first so the assertion can only pass by reading the
    Tier 6 `sleep` table — earlier tests in this module write legacy
    habits.sleep_hours rows, which would otherwise mask a regression.
    """
    from datetime import date, timedelta
    from services import insights

    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sleep")
        cur.execute("UPDATE habits SET sleep_hours = 0")

    # Three nights inside the 7-day window, written ONLY to the `sleep` table.
    for days_ago, hours in ((1, 7.0), (2, 6.0), (3, 8.0)):
        day = (date.today() - timedelta(days=days_ago)).isoformat()
        row, err = db.log_sleep_entry(day, hours, 4)
        assert err is None, (day, err)

    m = insights.gather_metrics()
    assert m.get("sleep") is not None, m
    assert m["sleep"]["recent_avg_h"] == 7.0, (
        f"expected 7.0h/night, got {m['sleep']['recent_avg_h']}h — the "
        "habits/sleep bridge is broken again (0.0 means habits-only reads)")
    print(f"  8. gather_metrics() sees UI-logged sleep: "
          f"{m['sleep']['recent_avg_h']}h/night (was 0.0 before the fix)  OK")


def test_9_undersleep_alert_fires():
    """predictive_alerts' 0 < avg < 6 guard was dead while avg was pinned at 0."""
    from services import insights
    fired = insights.predictive_alerts({"sleep": {"recent_avg_h": 5.2}})
    assert any("Sleep averaging" in a["message"] for a in fired), fired
    # And stays quiet on a healthy average.
    assert not any("Sleep averaging" in a["message"]
                   for a in insights.predictive_alerts({"sleep": {"recent_avg_h": 7.5}}))
    print("  9. under-sleep alert fires at 5.2h, silent at 7.5h  OK")


def main():
    tests = [test_1_log_and_score, test_2_duplicate_no_overwrite, test_3_second_night,
             test_4_readiness_missing, test_5_endpoint_post,
             test_6_merge_reads_both_stores, test_7_structured_sleep_wins_on_conflict,
             test_8_insights_sees_ui_logged_sleep, test_9_undersleep_alert_fires]
    print("Sleep tracking (Tier 6) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
