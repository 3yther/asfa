"""Finance / spending (Tier 8) tests — transaction helpers + endpoints.

Self-contained, no pytest dependency: run directly with

    python tests/test_finance.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db.
Tier 8 EXTENDS the legacy `spending` table (adds merchant/source) rather than
opening a parallel store, so these helpers read the same rows /api/money does.

Tests use DISTINCT past months (2026-01 … 2026-06) so they never interfere with
each other, except test_4 (pace), which must use the real current month because
days_elapsed keys off datetime.now().
"""
import os
import sys
import tempfile

# Point the DB layer at a throwaway file BEFORE importing database/app, and set
# the auth/session env the Flask app needs. Both must happen pre-import.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_finance_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar          # noqa: E402
from datetime import datetime  # noqa: E402

import database as db     # noqa: E402


def test_1_log_and_summary():
    txn, err = db.log_transaction("2026-01-08", 12.50, "Food", merchant="Tesco")
    assert err is None, f"expected no error, got {err!r}"
    assert txn is not None and txn["id"] is not None
    assert txn["amount"] == 12.5 and txn["category"] == "food", txn  # lower-cased
    assert txn["merchant"] == "Tesco" and txn["source"] == "manual", txn
    s = db.get_month_summary("2026-01")
    assert s["total_spent"] == 12.5, s
    assert s["by_category"]["food"] == 12.5, s
    assert s["transaction_count"] == 1, s
    print(f"  1. log_transaction(12.50 food@Tesco) -> summary.total_spent="
          f"{s['total_spent']}, by_category={s['by_category']}  OK")


def test_2_income_net():
    db.log_transaction("2026-02-05", 100.0, "food")
    db.log_transaction("2026-02-06", -40.0, "refund")   # negative = income
    s = db.get_month_summary("2026-02")
    assert s["total_spent"] == 100.0, s
    assert s["total_income"] == 40.0, s
    assert s["net"] == 60.0, s                            # 100 spent - 40 income
    print(f"  2. income row (-40) -> total_income={s['total_income']}, "
          f"net={s['net']}  OK")


def test_3_by_category_three():
    db.log_transaction("2026-03-01", 30, "food")
    db.log_transaction("2026-03-02", 20, "food")
    db.log_transaction("2026-03-03", 15, "transport")
    db.log_transaction("2026-03-04", 9.99, "subscriptions")
    s = db.get_month_summary("2026-03")
    assert s["by_category"]["food"] == 50, s              # 30 + 20
    assert s["by_category"]["transport"] == 15, s
    assert s["by_category"]["subscriptions"] == 9.99, s
    assert s["total_spent"] == 74.99, s
    print(f"  3. 3 categories -> by_category={s['by_category']}  OK")


def test_4_pace():
    now = datetime.now()
    month = now.strftime("%Y-%m")
    db.log_transaction(now.strftime("%Y-%m-%d"), 50.0, "food")
    p = db.get_spending_pace()                            # defaults to current month
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    assert p["month"] == month, p
    assert p["days_in_month"] == days_in_month, p
    assert p["days_elapsed"] == now.day, p
    assert p["spent_so_far"] >= 50.0, p
    expected = round(p["daily_avg"] * p["days_in_month"], 2)
    assert p["projected_month_total"] == expected, p
    assert p["daily_avg"] == round(p["spent_so_far"] / now.day, 2), p
    print(f"  4. pace -> spent={p['spent_so_far']}, daily_avg={p['daily_avg']}, "
          f"projected={p['projected_month_total']}  OK")


def test_5_endpoint_log():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    resp = client.post(
        "/api/finance/log",
        json={"date": "2026-04-10", "amount": 25.0, "category": "gym",
              "merchant": "PureGym"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["transaction"]["category"] == "gym", body
    assert body["month_summary"]["total_spent"] == 25.0, body
    print(f"  5. POST /api/finance/log {{25 gym@PureGym}} -> 200; "
          f"month_summary.total_spent={body['month_summary']['total_spent']}  OK")


def test_6_endpoint_summary():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
    db.log_transaction("2026-05-01", 60.0, "food")
    db.log_transaction("2026-05-02", 40.0, "transport")
    resp = client.get("/api/finance/summary?month=2026-05")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total_spent"] == 100.0, body
    assert body["transaction_count"] == 2, body
    # Empty month → zeros + empty by_category.
    empty = client.get("/api/finance/summary?month=2026-09").get_json()
    assert empty["total_spent"] == 0, empty
    assert empty["total_income"] == 0, empty
    assert empty["net"] == 0, empty
    assert empty["by_category"] == {}, empty
    assert empty["transaction_count"] == 0, empty
    print(f"  6. GET /api/finance/summary?month=2026-05 -> total_spent="
          f"{body['total_spent']}; empty month -> zeros  OK")


def test_7_bad_amount():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    r1 = client.post(
        "/api/finance/log",
        json={"date": "2026-06-01", "amount": "abc", "category": "food"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert r1.status_code == 400, f"non-numeric: {r1.status_code} {r1.get_data(as_text=True)}"
    r2 = client.post(
        "/api/finance/log",
        json={"date": "2026-06-01", "amount": True, "category": "food"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert r2.status_code == 400, f"bool: {r2.status_code} {r2.get_data(as_text=True)}"
    print("  7. POST /api/finance/log bad amount ('abc', True) -> 400, 400  OK")


def main():
    tests = [test_1_log_and_summary, test_2_income_net, test_3_by_category_three,
             test_4_pace, test_5_endpoint_log, test_6_endpoint_summary,
             test_7_bad_amount]
    print("Finance / spending (Tier 8) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
