"""Nutrition logging (Tier 7) tests — DB helpers, barcode service, endpoints.

Self-contained, no pytest dependency: run directly with

    python tests/test_nutrition.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db, and
passes explicit dates so results don't depend on the system clock.

Barcode lookups are tested against a STUBBED Open Food Facts response rather than
the live API. OFF is a crowd-sourced database whose contents drift (the barcodes
in the original spec no longer resolve to the same products), and hitting the
network would make the suite flaky. The stub exercises the real parsing path in
services.nutrition.lookup_barcode; a live end-to-end check confirmed the parser
against a populated product (3017620422003 = Nutella) during development.
"""
import os
import sys
import tempfile

# Point the DB layer at a throwaway file BEFORE importing database/app, and set
# the auth/session env the Flask app needs. Both must happen pre-import.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_nutrition_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
from services import nutrition  # noqa: E402


# ── Open Food Facts stub ─────────────────────────────────────────────────────
# A minimal fake of requests.get returning canned OFF JSON, keyed by barcode.
# "5000112126701" -> a whey product with full macros (status 1)
# anything else    -> not found (status 0)

_WHEY_JSON = {
    "status": 1,
    "product": {
        "product_name": "Whey Isolate",
        "nutriments": {
            "proteins_100g": 90.0,
            "carbohydrates_100g": 2.0,
            "fat_100g": 1.5,
            "energy-kcal_100g": 373.0,
        },
    },
}
_NOT_FOUND_JSON = {"status": 0}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get(url, *args, **kwargs):
    payload = _WHEY_JSON if "5000112126701" in url else _NOT_FOUND_JSON
    return _FakeResp(payload)


def _patch_off():
    nutrition.requests.get = _fake_get


def test_1_lookup_hit():
    _patch_off()
    p = nutrition.lookup_barcode("5000112126701")
    assert p is not None, "expected a product"
    assert p["food_name"] == "Whey Isolate", p
    assert p["protein_per_100g"] == 90.0, p
    assert p["energy_per_100g"] == 373.0, p
    print(f"  1. lookup_barcode(5000112126701) -> {p['food_name']} "
          f"({p['protein_per_100g']}g protein/100g)  OK")


def test_2_lookup_miss():
    _patch_off()
    assert nutrition.lookup_barcode("999999999") is None
    print("  2. lookup_barcode(999999999) -> None (not in database)  OK")


def test_3_log_meal_totals():
    meal, err = db.log_meal("2026-07-07", "Chicken 150g", 40, 0, 8, source="manual")
    assert err is None, f"expected no error, got {err!r}"
    assert meal is not None and meal["id"] is not None
    assert meal["calories"] == 232.0, meal   # 40*4 + 0*4 + 8*9
    daily = db.get_daily_macros("2026-07-07")
    assert daily["total_protein"] == 40, daily
    assert daily["meal_count"] == 1, daily
    print(f"  3. log_meal(Chicken 150g,40/0/8) -> daily.protein={daily['total_protein']}, "
          f"cal={meal['calories']}  OK")


def test_4_endpoint_lookup_barcode():
    _patch_off()
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    resp = client.post(
        "/api/nutrition/lookup-barcode",
        json={"barcode": "5000112126701"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["protein_per_100g"] == 90.0, body
    print(f"  4. POST /api/nutrition/lookup-barcode -> 200; ok=True; "
          f"protein_per_100g={body['protein_per_100g']}  OK")


def test_5_endpoint_log():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    resp = client.post(
        "/api/nutrition/log",
        json={"date": "2026-07-07", "food_name": "Whey shake",
              "protein": 25, "carbs": 30, "fat": 10, "source": "manual"},
        headers={"X-CSRF-Token": "tok"},
    )
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["meal"]["food_name"] == "Whey shake", body
    assert body["meal"]["calories"] == 310.0, body   # 25*4 + 30*4 + 10*9
    print(f"  5. POST /api/nutrition/log {{Whey shake,25/30/10}} -> 200; "
          f"meal.cal={body['meal']['calories']}  OK")


def test_6_endpoint_today():
    # After tests 3 and 5, two meals exist for 2026-07-07:
    #   Chicken 150g (40g protein) + Whey shake (25g protein) = 65g, 2 meals.
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
    # /today reports the server's current date; log a meal for it, then read back.
    today = app_module._today()
    db.log_meal(today, "Test eggs", 12, 1, 10, source="manual")
    db.log_meal(today, "Test oats", 8, 50, 6, source="manual")
    resp = client.get("/api/nutrition/today")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total_protein"] == 20, body       # 12 + 8
    assert body["meal_count"] == 2, body
    assert len(body["meals"]) == 2, body
    assert body["meals"][0]["food_name"] == "Test eggs", body
    print(f"  6. GET /api/nutrition/today -> total_protein={body['total_protein']}, "
          f"meal_count={body['meal_count']}  OK")


def main():
    tests = [test_1_lookup_hit, test_2_lookup_miss, test_3_log_meal_totals,
             test_4_endpoint_lookup_barcode, test_5_endpoint_log,
             test_6_endpoint_today]
    print("Nutrition logging (Tier 7) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
