"""Dual-account balance tests — snapshot helpers + endpoints.

Self-contained, no pytest dependency: run directly with

    python tests/test_accounts.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db.
account_balances is a NEW table (point-in-time balance snapshots per account),
independent of the `spending` transaction store.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

# Point the DB layer at a throwaway file BEFORE importing database/app, and set
# the auth/session env the Flask app needs. Both must happen pre-import.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_accounts_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db     # noqa: E402


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def test_1_add_and_current():
    row, err = db.add_account_balance("checking", 1200.0, "2026-07-01", notes="payday")
    assert err is None, f"expected no error, got {err!r}"
    assert row is not None and row["id"] is not None
    assert row["account_type"] == "checking" and row["balance"] == 1200.0, row
    assert row["notes"] == "payday", row
    print("  1. add checking 1200 -> row created  OK")


def test_2_validation():
    # bad account type
    _, e1 = db.add_account_balance("crypto", 100, "2026-07-01")
    assert e1 and "account_type" in e1, e1
    # negative balance
    _, e2 = db.add_account_balance("savings", -5, "2026-07-01")
    assert e2 and ">= 0" in e2, e2
    # bool rejected (True would coerce to 1.0)
    _, e3 = db.add_account_balance("savings", True, "2026-07-01")
    assert e3 and "number" in e3, e3
    # bad date
    _, e4 = db.add_account_balance("savings", 100, "2026-13-40")
    assert e4 and "date" in e4, e4
    print("  2. validation (type/negative/bool/date) -> all rejected  OK")


def test_3_trend_math():
    # Fresh DB rows for a clean 30-day-window assertion. Use dates relative to
    # today so the trailing-30d baseline resolves deterministically.
    db.add_account_balance("checking", 1500.0, _days_ago(0))   # today
    # savings: baseline ~40 days ago (covers the full 30d window) + today
    db.add_account_balance("savings", 5000.0, _days_ago(40))
    db.add_account_balance("savings", 5300.0, _days_ago(0))

    s = db.get_accounts_summary()
    # checking: snapshots at 2026-07-01 (1200, from test_1) and today (1500).
    # No snapshot older than 30d -> baseline falls back to earliest (1200),
    # so trend = 1500 - 1200 = 300.
    assert s["checking"]["current"] == 1500.0, s["checking"]
    assert s["checking"]["trend"] == 300.0, s["checking"]
    # savings: baseline is the 40-days-ago 5000 (older than the 30d cutoff),
    # current 5300 -> trend 300.
    assert s["savings"]["current"] == 5300.0, s["savings"]
    assert s["savings"]["trend"] == 300.0, s["savings"]
    # net worth aggregates.
    assert s["net_worth"]["current"] == 6800.0, s["net_worth"]
    assert s["net_worth"]["trend"] == 600.0, s["net_worth"]
    print("  3. trend math: checking +300, savings +300, net_worth +600  OK")


def test_4_empty_account_zeroes():
    # A type with no rows in a fresh DB reports zeros, no crash. get_db() reads
    # the module-global SQLITE_PATH (resolved once at import), so re-point that
    # directly rather than the env var, and reset the lazy-create flag so the
    # new file gets its own table.
    _TMP2 = os.path.join(tempfile.mkdtemp(prefix="asfa_accounts_empty_"), "e.db")
    db.SQLITE_PATH = _TMP2
    db._ACCOUNT_BALANCES_READY = False
    # Only log savings; checking stays empty.
    db.add_account_balance("savings", 10.0, _days_ago(0))
    s = db.get_accounts_summary()
    assert s["checking"]["current"] == 0.0 and s["checking"]["trend"] == 0.0, s["checking"]
    assert s["checking"]["has_data"] is False, s["checking"]
    assert s["net_worth"]["current"] == 10.0, s["net_worth"]
    print("  4. empty account -> zeros, has_data False; net worth = savings  OK")


def test_5_endpoint_post_and_summary():
    # Restore the primary temp DB for the endpoint round-trip.
    db.SQLITE_PATH = _TMP_DB
    db._ACCOUNT_BALANCES_READY = False
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"

    resp = client.post(
        "/api/finance/account-balance",
        json={"account_type": "checking", "balance": 1500.0, "date": "2026-07-12"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body["ok"] is True and body["balance"] == 1500.0, body

    r2 = client.get("/api/finance/accounts/summary")
    assert r2.status_code == 200, r2.get_data(as_text=True)
    summ = r2.get_json()
    assert "checking" in summ and "savings" in summ and "net_worth" in summ, summ
    print("  5. POST /account-balance + GET /accounts/summary -> 200  OK")


def test_6_endpoint_bad_input():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    r = client.post(
        "/api/finance/account-balance",
        json={"account_type": "checking", "balance": -5, "date": "2026-07-12"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert r.status_code == 400, f"{r.status_code}: {r.get_data(as_text=True)}"
    print("  6. POST negative balance -> 400  OK")


def main():
    tests = [test_1_add_and_current, test_2_validation, test_3_trend_math,
             test_4_empty_account_zeroes, test_5_endpoint_post_and_summary,
             test_6_endpoint_bad_input]
    print("Dual-account balance tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
