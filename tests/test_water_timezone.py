"""Regression tests for the water/hydration timezone bug.

Symptom: the Telegram bot (and daily summary) reported "Water: 0ml" even though
water had just been logged via the web UI.

Root cause: the web logger (/api/asfa/water-intake) derived the storage date from
the browser's `new Date().toISOString()` timestamp — always UTC — and called
strftime on the still-UTC datetime, so water was bucketed by the UTC calendar day.
Every reader (bot AI, daily summary, streak, quick-add) keyed "today" off
`datetime.now()`. When the app timezone isn't UTC (it's Europe/London), the two
calendars disagree for part of every day and the reader queried an empty bucket.

Fix: one canonical, tz-aware day — database.today_str()/now_local()/to_local_day —
used by both writers and readers, and the client timestamp is converted INTO the
app timezone before the day is taken.

Self-contained (no pytest); run with:

    python tests/test_water_timezone.py

Pins ASFA_TZ=Europe/London and uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so
it never touches asfa.db and is deterministic regardless of the host clock/zone.
"""
import os
import sys
import tempfile
from datetime import datetime

# Must be set BEFORE database is imported: APP_TZ is resolved at import time.
os.environ["ASFA_TZ"] = "Europe/London"
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_water_tz_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
import app as app_module       # noqa: E402
from services import skill_executor  # noqa: E402

db.init_db()


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


_H = {"X-CSRF-Token": "tok"}


def test_1_app_tz_is_configured():
    # ASFA_TZ drove the canonical zone (not UTC), which is the whole point.
    assert db.APP_TZ.key == "Europe/London", db.APP_TZ
    print(" 1. APP_TZ resolves to Europe/London  OK")


def test_2_to_local_day_converts_utc_summer():
    # 23:30 UTC in July (BST, +1) is already 00:30 the NEXT day in London.
    parsed = datetime.fromisoformat("2026-07-26T23:30:00+00:00")
    assert db.to_local_day(parsed) == "2026-07-27", db.to_local_day(parsed)
    print(" 2. to_local_day: 2026-07-26T23:30Z -> 2026-07-27 (BST)  OK")


def test_3_to_local_day_converts_utc_winter():
    # January is GMT (+0): the UTC day and London day coincide.
    parsed = datetime.fromisoformat("2026-01-15T23:30:00+00:00")
    assert db.to_local_day(parsed) == "2026-01-15", db.to_local_day(parsed)
    print(" 3. to_local_day: 2026-01-15T23:30Z -> 2026-01-15 (GMT)  OK")


def test_4_web_log_buckets_by_local_day_not_utc():
    """THE regression. A UTC timestamp that is 'tomorrow' in London must land on
    the London day, where the bot reads it — not on the UTC day it never queries."""
    c = _client()
    # 00:30 London on the 27th, expressed as the browser would send it (UTC).
    r = c.post("/api/asfa/water-intake",
               json={"amount": 500, "timestamp": "2026-07-26T23:30:00Z"}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)

    london_day, utc_day = "2026-07-27", "2026-07-26"
    # Stored under the London calendar day...
    assert db.get_hydration_total(london_day) == 500, "ledger keyed by London day"
    assert db.get_water_logged(london_day) == 500, "habits total keyed by London day"
    # ...and NOT under the UTC day (the pre-fix bucket the bot never looked at).
    assert db.get_hydration_total(utc_day) == 0, "must not leak into the UTC day"
    assert db.get_water_logged(utc_day) == 0, "must not leak into the UTC day"
    print(" 4. web water log buckets by London day, not UTC day  OK")


def test_5_bot_reader_sees_web_logged_water():
    """End-to-end: what the web UI writes, the bot's 'today' key reads back. This
    is the assertion that would have failed before the fix (bot saw 0ml)."""
    c = _client()
    today = db.today_str()  # the exact key ai.build_context_block() uses
    before = db.get_water_logged(today)
    r = c.post("/api/asfa/water-intake", json={"amount": 750}, headers=_H)  # no ts
    assert r.status_code == 200, r.get_data(as_text=True)
    after = db.get_water_logged(today)
    assert after - before == 750, f"bot's today key must reflect the log ({before}->{after})"
    print(" 5. bot reader (today_str) sees standalone web-logged water  OK")


def test_6_today_helpers_share_one_source_of_truth():
    # app._today() and skill_executor._today() must agree with database.today_str()
    # so no writer/reader can drift apart again.
    assert app_module._today() == db.today_str(), "app._today drifted"
    assert skill_executor._today() == db.today_str(), "skill_executor._today drifted"
    print(" 6. app._today / skill_executor._today == db.today_str  OK")


def main():
    tests = [
        test_1_app_tz_is_configured,
        test_2_to_local_day_converts_utc_summer,
        test_3_to_local_day_converts_utc_winter,
        test_4_web_log_buckets_by_local_day_not_utc,
        test_5_bot_reader_sees_web_logged_water,
        test_6_today_helpers_share_one_source_of_truth,
    ]
    print("Water timezone regression tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
