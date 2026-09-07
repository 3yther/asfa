"""Apple Watch health sync — storage, API and the recovery badge.

The two behaviours worth the most attention here are both consequences of the
sync running HOURLY against tables that were not designed for a repeating writer:

  * `upsert_health_metrics` must MERGE. The Shortcut posts sleep+HRV for
    yesterday and activity for today, every hour. A replace-style write would
    erase yesterday's HRV the moment an activity-only payload arrived.
  * `sync_health_metrics` must REPLACE the watch's row in `steps`, never append.
    `steps` is an append-only log whose day total is SUM(steps), and the watch
    posts a running total — appending would multiply the day's step count by the
    number of syncs.

Runs standalone (`python tests/test_health_sync.py`) or under pytest.
"""
import os
import sys
import tempfile
from datetime import timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_health_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

_HDRS = {"X-CSRF-Token": "tok"}


def setup_module(module=None):
    db._ensure_health_metrics_table()
    db._ensure_steps_tables()


def _client():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    return client


def _day(offset=0):
    return (db.now_local() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _wipe():
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM health_metrics")
        cur.execute("DELETE FROM steps")


# ── 1. Storage merges rather than replaces ──────────────────────────────────

def test_1_partial_upsert_keeps_fields_it_was_not_given():
    _wipe()
    d = _day(-1)
    db.upsert_health_metrics(d, {"sleep_duration_minutes": 450, "hrv_ms": 42.5})
    # The next hour's payload carries activity only, for the same date.
    row = db.upsert_health_metrics(d, {"calories_active": 600, "steps": 8000})
    assert row["sleep_duration_minutes"] == 450, "sleep was clobbered by an activity-only sync"
    assert row["hrv_ms"] == 42.5, "HRV was clobbered by an activity-only sync"
    assert row["calories_active"] == 600
    assert row["steps"] == 8000


def test_2_repeated_sync_updates_one_row_not_many():
    _wipe()
    d = _day()
    for steps in (2000, 5000, 9000):
        db.sync_health_metrics(d, {"steps": steps, "calories_active": steps // 10})
    rows = db.get_health_metrics(30)
    assert len(rows) == 1, f"expected one row for {d}, got {len(rows)}"
    assert rows[0]["steps"] == 9000, "later syncs must win, not accumulate"


def test_3_negative_and_junk_values_are_dropped_not_stored():
    _wipe()
    d = _day()
    db.upsert_health_metrics(d, {"steps": 5000, "hrv_ms": 40.0})
    row = db.upsert_health_metrics(d, {"steps": -12, "hrv_ms": "not-a-number",
                                       "calories_active": 300})
    assert row["steps"] == 5000, "a negative reading must not overwrite a good one"
    assert row["hrv_ms"] == 40.0, "unparseable HRV must not overwrite a good one"
    assert row["calories_active"] == 300, "valid fields alongside junk still apply"


def test_4_unknown_fields_are_ignored():
    _wipe()
    d = _day()
    row = db.sync_health_metrics(d, {"steps": 100, "id": 999, "user_id": 42,
                                     "metric_date": "1999-01-01", "drop table": 1})
    assert row["metric_date"] == d
    assert row["steps"] == 100


# ── 2. The steps mirror replaces, never appends ─────────────────────────────

def test_5_hourly_sync_does_not_multiply_the_day_step_total():
    _wipe()
    d = _day()
    for running_total in (2000, 4500, 7000, 10000):
        db.sync_health_metrics(d, {"steps": running_total})
    assert db.get_steps_day_total(d) == 10000, (
        "the watch's running total was appended instead of replaced — this is the "
        "bug that turns a 10k day into six figures by evening")
    watch_rows = [r for r in db.get_steps_for_date(d)
                  if r["source"] == db.WATCH_STEPS_SOURCE]
    assert len(watch_rows) == 1


def test_6_mirror_leaves_manual_step_rows_alone():
    _wipe()
    d = _day()
    db.add_step_entry(d, "manual", 3000)
    db.sync_health_metrics(d, {"steps": 6000})
    db.sync_health_metrics(d, {"steps": 6500})
    assert db.get_steps_day_total(d) == 3000 + 6500
    sources = sorted(r["source"] for r in db.get_steps_for_date(d))
    assert sources == ["apple_watch", "manual"]


def test_7_zero_steps_writes_no_mirror_row():
    _wipe()
    d = _day()
    db.sync_health_metrics(d, {"steps": 0, "hrv_ms": 50})
    assert db.get_steps_for_date(d) == []
    assert db.get_latest_health_metrics()["hrv_ms"] == 50.0


# ── 3. Endpoints ────────────────────────────────────────────────────────────

def test_8_sync_endpoint_stores_a_flat_payload():
    _wipe()
    d = _day(-1)
    r = _client().post("/api/health/sync", json={
        "metric_date": d, "sleep_duration_minutes": 450, "sleep_deep_minutes": 90,
        "sleep_light_minutes": 260, "sleep_rem_minutes": 100, "hrv_ms": 42.5,
        "calories_active": 600, "calories_total": 2400, "steps": 9000,
    }, headers=_HDRS)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["success"] is True and body["count"] == 1
    assert body["synced"][0]["hrv_ms"] == 42.5


def test_9_sync_endpoint_accepts_the_two_day_shortcut_payload():
    _wipe()
    y, t = _day(-1), _day()
    r = _client().post("/api/health/sync", json={"days": [
        {"metric_date": y, "sleep_duration_minutes": 465, "hrv_ms": 44.0},
        {"metric_date": t, "calories_active": 350, "steps": 4200},
    ]}, headers=_HDRS)
    assert r.status_code == 200
    assert r.get_json()["count"] == 2
    assert db.get_health_metrics_for_date(y)["hrv_ms"] == 44.0
    assert db.get_health_metrics_for_date(t)["steps"] == 4200


def test_10_future_dates_are_refused():
    _wipe()
    r = _client().post("/api/health/sync",
                       json={"metric_date": _day(3), "steps": 1}, headers=_HDRS)
    assert r.status_code == 400
    assert "future" in r.get_json()["errors"][0]["error"]
    assert db.get_health_metrics(30) == []


def test_11_bad_date_and_empty_payloads_are_refused():
    _wipe()
    c = _client()
    assert c.post("/api/health/sync", json={"metric_date": "yesterday", "steps": 1},
                  headers=_HDRS).status_code == 400
    assert c.post("/api/health/sync", json={"metric_date": _day()},
                  headers=_HDRS).status_code == 400
    assert db.get_health_metrics(30) == []


def test_12_metrics_and_latest_endpoints():
    _wipe()
    for i, hrv in enumerate([38.0, 41.0, 45.0]):
        db.sync_health_metrics(_day(-2 + i), {"hrv_ms": hrv, "steps": 1000 * (i + 1)})
    c = _client()
    rows = c.get("/api/health/metrics?days=7").get_json()
    assert [r["hrv_ms"] for r in rows] == [45.0, 41.0, 38.0], "must be newest first"
    latest = c.get("/api/health/metrics/latest").get_json()
    assert latest["metric_date"] == _day() and latest["hrv_ms"] == 45.0


def test_13_latest_is_empty_object_when_nothing_synced():
    _wipe()
    assert _client().get("/api/health/metrics/latest").get_json() == {}


def test_14_days_with_no_sync_are_omitted_not_zero_filled():
    """A gap must stay a gap: a zero-filled day reads as 'HRV crashed'."""
    _wipe()
    db.sync_health_metrics(_day(-6), {"hrv_ms": 40.0})
    db.sync_health_metrics(_day(), {"hrv_ms": 44.0})
    rows = _client().get("/api/health/metrics?days=7").get_json()
    assert len(rows) == 2


# ── 4. Recovery badge ───────────────────────────────────────────────────────

def test_15_absolute_bands_apply_before_a_baseline_exists():
    assert db.recovery_status(45.0)["status"] == "good"
    assert db.recovery_status(35.0)["status"] == "fair"
    assert db.recovery_status(22.0)["status"] == "low"
    assert db.recovery_status(45.0)["band"] == "absolute"
    assert db.recovery_status(None)["status"] == "unknown"


def test_16_baseline_needs_enough_history_then_takes_over():
    _wipe()
    for i in range(3):
        db.sync_health_metrics(_day(-5 + i), {"hrv_ms": 60.0})
    assert db.get_hrv_baseline() is None, "3 days is too few to be a baseline"
    db.sync_health_metrics(_day(-2), {"hrv_ms": 60.0})
    assert db.get_hrv_baseline() == 60.0


def test_17_a_high_baseline_makes_a_high_reading_read_as_low():
    """The reason for a personal baseline at all: 45 ms is 'good' in the
    abstract and poor for someone who normally sits at 60."""
    _wipe()
    for i in range(5):
        db.sync_health_metrics(_day(-6 + i), {"hrv_ms": 60.0})
    db.sync_health_metrics(_day(), {"hrv_ms": 45.0})
    summary = db.get_recovery_summary(7)
    assert summary["recovery"]["band"] == "baseline"
    assert summary["recovery"]["baseline"] == 60.0
    assert summary["recovery"]["status"] == "low"
    assert db.recovery_status(45.0)["status"] == "good", "absolute band unchanged"


def test_18_baseline_excludes_the_day_being_judged():
    _wipe()
    for i in range(5):
        db.sync_health_metrics(_day(-6 + i), {"hrv_ms": 50.0})
    db.sync_health_metrics(_day(), {"hrv_ms": 10.0})
    assert db.get_recovery_summary(7)["recovery"]["baseline"] == 50.0


def test_19_baseline_is_a_median_so_one_bad_night_cannot_move_it():
    _wipe()
    for i, hrv in enumerate([50.0, 51.0, 49.0, 50.0, 5.0]):
        db.sync_health_metrics(_day(-5 + i), {"hrv_ms": hrv})
    assert db.get_hrv_baseline() == 50.0


def test_20_recovery_summary_shape_and_trend_order():
    _wipe()
    for i, hrv in enumerate([38.0, 40.0, 42.0]):
        db.sync_health_metrics(_day(-2 + i), {"hrv_ms": hrv})
    s = _client().get("/api/health/recovery?days=7").get_json()
    assert s["has_data"] is True
    assert s["latest"]["hrv_ms"] == 42.0
    assert [p["hrv_ms"] for p in s["trend"]] == [38.0, 40.0, 42.0], "oldest first for drawing"


def test_21_recovery_summary_is_safe_with_no_data():
    _wipe()
    s = _client().get("/api/health/recovery").get_json()
    assert s["has_data"] is False and s["latest"] is None
    assert s["recovery"]["status"] == "unknown" and s["trend"] == []


if __name__ == "__main__":
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
