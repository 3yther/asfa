"""Nutrition expansion v2 tests — restaurant/branded search, quick-add favorites,
meal-prep mode, and meal-linked hydration. Self-contained (no pytest); run with:

    python tests/test_nutrition_expansion.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
Network paths (USDA/OFF) are monkeypatched so tests are deterministic + offline.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_nutri_exp_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
import app as app_module       # noqa: E402
from services import nutrition  # noqa: E402


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


_H = {"X-CSRF-Token": "tok"}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _shift(date_str, delta):
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def _log(c, date, name, p, cbs, f, **kw):
    body = {"date": date, "food_name": name, "protein": p, "carbs": cbs, "fat": f,
            "source": kw.get("source", "quick-add")}
    for k in ("time", "calories", "food_source"):
        if k in kw:
            body[k] = kw[k]
    r = c.post("/api/nutrition/log", json=body, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["meal"]


def _clear():
    with db.get_db() as conn:
        cur = conn.cursor()
        for t in ("meals", "hydration_log", "restaurant_items", "restaurants",
                  "meal_preps", "meal_prep_items", "meal_prep_usage"):
            try:
                cur.execute(f"DELETE FROM {t}")
            except Exception:
                pass


# ── Food source provenance column ────────────────────────────────────────────────

def test_1_food_source_persists():
    c = _client()
    _clear()
    meal = _log(c, _today(), "Chicken", 31, 0, 3.6, calories=155, food_source="usda")
    row = db.get_meal(meal["id"])
    assert row["food_source"] == "usda", row
    # And it surfaces on the per-day meal projection for the badge.
    day = c.get(f"/api/nutrition/date/{_today()}").get_json()
    assert day["meals"][-1]["food_source"] == "usda", day["meals"][-1]
    print("  1. food_source column persists + surfaces on the meal row  OK")


def test_2_food_source_optional():
    c = _client()
    meal = _log(c, _today(), "Mystery", 10, 10, 10)   # no food_source
    row = db.get_meal(meal["id"])
    assert row["food_source"] is None, row
    print("  2. food_source is optional (NULL when unspecified)  OK")


# ── USDA Branded parsing + fallback ──────────────────────────────────────────────

def test_3_parse_branded_food_prefixes_brand():
    food = {
        "description": "MCDOUBLE",
        "brandOwner": "MCDONALD'S",
        "foodNutrients": [
            {"nutrientNumber": "208", "value": 250, "unitName": "KCAL"},
            {"nutrientNumber": "203", "value": 15},
            {"nutrientNumber": "205", "value": 20},
            {"nutrientNumber": "204", "value": 12},
        ],
    }
    parsed = nutrition._parse_fdc_food(food, source="usda_branded")
    assert parsed["source"] == "usda_branded", parsed
    assert "Mcdouble" in parsed["food_name"], parsed
    assert "Mcdonald" in parsed["food_name"], parsed          # brand prefixed
    assert parsed["kcal_per_100g"] == 250, parsed
    assert parsed["protein_per_100g"] == 15, parsed
    print("  3. branded parse: brand-prefixed name, source=usda_branded, macros  OK")


def test_4_whole_food_source_unchanged():
    food = {"description": "CHICKEN, BROILERS, MEAT",
            "foodNutrients": [{"nutrientNumber": "203", "value": 27}]}
    parsed = nutrition._parse_fdc_food(food)              # default source
    assert parsed["source"] == "usda", parsed
    print("  4. whole-food parse still tagged source=usda (unchanged)  OK")


def test_5_search_falls_back_to_branded():
    # Whole-food empty, branded has a hit → search_foods returns the branded item.
    nutrition._search_cache.clear()
    orig_whole, orig_branded, orig_off = (
        nutrition._search_fdc, nutrition._search_fdc_branded,
        nutrition.search_open_food_facts)
    nutrition._search_fdc = lambda q, limit, **kw: []
    nutrition._search_fdc_branded = lambda q, limit: [
        {"food_name": "Clif Bar", "protein_per_100g": 10, "carbs_per_100g": 60,
         "fat_per_100g": 8, "kcal_per_100g": 370, "portions": [], "source": "usda_branded"}]
    nutrition.search_open_food_facts = lambda q, limit=10: []
    try:
        res = nutrition.search_foods("clif bar zzz")
        assert len(res) == 1 and res[0]["source"] == "usda_branded", res
    finally:
        nutrition._search_fdc, nutrition._search_fdc_branded = orig_whole, orig_branded
        nutrition.search_open_food_facts = orig_off
        nutrition._search_cache.clear()
    print("  5. search_foods falls back to USDA Branded when whole-food is empty  OK")


def test_6_search_prefers_whole_food():
    nutrition._search_cache.clear()
    orig_whole, orig_branded = nutrition._search_fdc, nutrition._search_fdc_branded
    nutrition._search_fdc = lambda q, limit, **kw: [
        {"food_name": "Chicken breast", "protein_per_100g": 31, "carbs_per_100g": 0,
         "fat_per_100g": 3.6, "kcal_per_100g": 165, "portions": [], "source": "usda"}]
    nutrition._search_fdc_branded = lambda q, limit: (_ for _ in ()).throw(
        AssertionError("branded should not be queried when whole-food hits"))
    try:
        res = nutrition.search_foods("chicken breast zzz")
        assert res[0]["source"] == "usda", res
    finally:
        nutrition._search_fdc, nutrition._search_fdc_branded = orig_whole, orig_branded
        nutrition._search_cache.clear()
    print("  6. search_foods prefers whole-food USDA, skips branded on a hit  OK")


# ── Restaurant / chain items ─────────────────────────────────────────────────────

def test_7_add_restaurant_and_item():
    _clear()
    rid = db.add_restaurant("McDonald's", category="fast_food", country="US")
    assert rid > 0
    # Same name reuses the row (no dupes).
    rid2 = db.add_restaurant("mcdonald's")
    assert rid2 == rid, (rid, rid2)
    item_id = db.add_restaurant_item(rid, "McDouble", 400, 22, 33, 20,
                                     notes="verify path")
    assert item_id > 0
    print("  7. add_restaurant dedupes by name; add_restaurant_item inserts  OK")


def test_8_search_restaurant_items():
    hits = db.search_restaurant_items("mcdouble")
    assert len(hits) == 1, hits
    h = hits[0]
    assert h["source"] == "restaurant" and h["food_source"] == "restaurant", h
    assert h["per_serving"] is True, h
    assert h["kcal"] == 400 and h["protein"] == 22, h
    assert "McDonald's" in h["food_name"], h            # restaurant prefixed
    # Also findable by restaurant name.
    assert db.search_restaurant_items("mcdonald"), "search by restaurant name"
    print("  8. search_restaurant_items matches item+restaurant, per-serving shape  OK")


def test_9_search_route_merges_restaurant_first():
    # Route puts curated restaurant items ahead of external (USDA/OFF) results.
    c = _client()
    orig = nutrition.search_foods
    nutrition.search_foods = lambda q, limit=10: [
        {"food_name": "Generic burger", "kcal_per_100g": 250, "source": "usda_branded",
         "protein_per_100g": 15, "carbs_per_100g": 20, "fat_per_100g": 12, "portions": []}]
    try:
        res = c.get("/api/nutrition/search?q=mcdouble").get_json()
        assert res[0]["source"] == "restaurant", res
        assert any(r["source"] == "usda_branded" for r in res), res
    finally:
        nutrition.search_foods = orig
    print("  9. /search merges local restaurant items ahead of external hits  OK")


def test_10_search_route_short_query():
    c = _client()
    assert c.get("/api/nutrition/search?q=a").get_json() == []
    print(" 10. /search returns [] for <2 char query  OK")


# ── Quick-add favorites ──────────────────────────────────────────────────────────

def test_11_favorite_after_repeat_logs():
    c = _client()
    _clear()
    for d in (0, -1, -2):
        _log(c, _shift(_today(), d), "Oats", 10, 50, 5, calories=290)
    favs = c.get("/api/nutrition/favorites?limit=10").get_json()
    oats = [f for f in favs if f["food_name"] == "Oats"]
    assert oats and oats[0]["count"] == 3, favs
    print(" 11. a food logged 3x shows up in favorites (count 3)  OK")


def test_12_log_favorite_path_form():
    c = _client()
    target = _shift(_today(), 5)
    r = c.post(f"/api/nutrition/log-favorite/Oats", json={"date": target}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    ut = r.get_json()["updated_totals"]
    assert ut["total_protein"] == 10 and ut["total_carbs"] == 50, ut
    # Unknown food via path form → 400.
    bad = c.post("/api/nutrition/log-favorite/Nonexistent", json={"date": target}, headers=_H)
    assert bad.status_code == 400, bad.get_data(as_text=True)
    print(" 12. POST /log-favorite/<name> path form logs averaged macros  OK")


def test_13_add_manual_favorite():
    c = _client()
    _clear()
    r = c.post("/api/nutrition/favorites/add-manual",
               json={"food_name": "Protein Shake", "protein": 30, "carbs": 5,
                     "fat": 2, "date": _today()}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    # Immediately appears in favorites without prior history.
    names = [f["food_name"] for f in body["favorites"]]
    assert "Protein Shake" in names, body["favorites"]
    # Calories auto-derived via Atwater (30*4 + 5*4 + 2*9 = 158).
    shake = [f for f in body["favorites"] if f["food_name"] == "Protein Shake"][0]
    assert shake["calories"] == 158, shake
    print(" 13. add-manual favorite appears instantly, kcal auto-derived  OK")


def test_14_add_manual_favorite_validation():
    c = _client()
    assert c.post("/api/nutrition/favorites/add-manual",
                  json={"food_name": "", "protein": 1, "carbs": 1, "fat": 1},
                  headers=_H).status_code == 400
    assert c.post("/api/nutrition/favorites/add-manual",
                  json={"food_name": "X", "protein": -1, "carbs": 1, "fat": 1},
                  headers=_H).status_code == 400
    print(" 14. add-manual validation: blank name / negative macro -> 400  OK")


# ── Meal prep mode ───────────────────────────────────────────────────────────────

def test_15_meal_prep_create_totals():
    c = _client()
    _clear()
    r = c.post("/api/nutrition/meal-prep/create", json={
        "name": "Chicken rice batch", "portions": 4,
        "items": [
            {"food_name": "Chicken", "amount": 600, "unit": "g",
             "protein": 108, "carbs": 0, "fat": 12, "kcal": 588},
            {"food_name": "Rice", "amount": 400, "unit": "g",
             "protein": 8, "carbs": 90, "fat": 1, "kcal": 401},
        ]}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    prep = r.get_json()["meal_prep"]
    assert prep["total_protein"] == 116 and prep["total_carbs"] == 90, prep
    assert prep["total_kcal"] == 989, prep
    assert prep["portions"] == 4 and prep["item_count"] == 2, prep
    # Per-portion = totals / 4.
    assert prep["per_portion"]["protein"] == 29, prep["per_portion"]
    assert prep["portions_remaining"] == 4, prep
    print(" 15. meal-prep create sums ingredient totals + per-portion (116P/989kcal)  OK")


def test_16_meal_prep_kcal_atwater_fallback():
    # Ingredient without kcal → Atwater from its macros.
    prep, err = db.create_meal_prep(
        "Eggs batch", [{"food_name": "Eggs", "protein": 12, "carbs": 1, "fat": 10}],
        portions=2)
    assert err is None, err
    assert prep["total_kcal"] == 142.0, prep      # 12*4 + 1*4 + 10*9
    print(" 16. meal-prep ingredient kcal falls back to Atwater when omitted  OK")


def test_17_meal_prep_create_validation():
    c = _client()
    assert c.post("/api/nutrition/meal-prep/create",
                  json={"name": "", "items": [{"food_name": "X", "protein": 1}]},
                  headers=_H).status_code == 400
    assert c.post("/api/nutrition/meal-prep/create",
                  json={"name": "Y", "items": []}, headers=_H).status_code == 400
    assert c.post("/api/nutrition/meal-prep/create",
                  json={"name": "Z", "portions": 0,
                        "items": [{"food_name": "X", "protein": 1}]},
                  headers=_H).status_code == 400
    print(" 17. meal-prep create validation: blank name / no items / portions<1  OK")


def test_18_meal_prep_list_active_only():
    c = _client()
    _clear()
    db.create_meal_prep("Spent", [{"food_name": "A", "protein": 10, "carbs": 10,
                                    "fat": 10, "kcal": 170}], portions=1)
    preps = c.get("/api/nutrition/meal-prep/list").get_json()
    assert len(preps) == 1 and preps[0]["portions_remaining"] == 1, preps
    print(" 18. meal-prep list returns active preps with remaining portions  OK")


def test_19_meal_prep_log_usage_adds_meal():
    c = _client()
    _clear()
    prep, _ = db.create_meal_prep(
        "Chicken rice batch",
        [{"food_name": "Chicken", "protein": 100, "carbs": 0, "fat": 10, "kcal": 490},
         {"food_name": "Rice", "protein": 8, "carbs": 92, "fat": 2, "kcal": 418}],
        portions=4)
    day = _shift(_today(), 2)
    r = c.post(f"/api/nutrition/meal-prep/{prep['id']}/log-usage",
               json={"date": day, "portions_consumed": 2}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    # Per-portion protein = (100+8)/4 = 27; 2 portions → 54.
    assert body["updated_totals"]["total_protein"] == 54, body["updated_totals"]
    assert body["prep"]["portions_remaining"] == 2, body["prep"]
    # A meal row was actually written for that day, source=meal-prep.
    meals = db.get_meals(day)
    assert len(meals) == 1 and meals[0]["source"] == "meal-prep", meals
    assert meals[0]["food_source"] == "meal_prep", meals[0]
    print(" 19. log-usage adds a meal (2×27P=54) + decrements remaining to 2  OK")


def test_20_meal_prep_multiple_usages():
    c = _client()
    _clear()
    prep, _ = db.create_meal_prep(
        "Batch", [{"food_name": "X", "protein": 40, "carbs": 40, "fat": 40, "kcal": 680}],
        portions=5)
    c.post(f"/api/nutrition/meal-prep/{prep['id']}/log-usage",
           json={"date": _today(), "portions_consumed": 2}, headers=_H)
    c.post(f"/api/nutrition/meal-prep/{prep['id']}/log-usage",
           json={"date": _shift(_today(), 1), "portions_consumed": 1}, headers=_H)
    fresh = db.get_meal_prep(prep["id"])
    assert fresh["portions_used"] == 3 and fresh["portions_remaining"] == 2, fresh
    print(" 20. multiple usages accumulate (used 3, remaining 2)  OK")


def test_21_meal_prep_delete_keeps_past_meals():
    c = _client()
    _clear()
    prep, _ = db.create_meal_prep(
        "Gone", [{"food_name": "X", "protein": 20, "carbs": 20, "fat": 20, "kcal": 340}],
        portions=2)
    day = _today()
    c.post(f"/api/nutrition/meal-prep/{prep['id']}/log-usage",
           json={"date": day, "portions_consumed": 1}, headers=_H)
    r = c.delete(f"/api/nutrition/meal-prep/{prep['id']}", headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    # Prep gone from list, but the already-eaten meal stays in the day record.
    assert db.get_meal_prep(prep["id"]) is None
    assert len(db.get_meals(day)) == 1, "past-consumed meal must survive prep delete"
    print(" 21. delete removes prep but keeps meals already logged from usage  OK")


def test_22_meal_prep_usage_missing_prep():
    c = _client()
    r = c.post("/api/nutrition/meal-prep/999999/log-usage",
               json={"date": _today(), "portions_consumed": 1}, headers=_H)
    assert r.status_code == 404, r.get_data(as_text=True)
    assert c.delete("/api/nutrition/meal-prep/999999", headers=_H).status_code == 404
    print(" 22. usage/delete on unknown prep -> 404  OK")


# ── Meal-linked hydration ────────────────────────────────────────────────────────

def test_23_log_water_linked_to_meal():
    c = _client()
    _clear()
    meal = _log(c, _today(), "Lunch", 30, 40, 15, calories=475)
    r = c.post("/api/nutrition/log-water",
               json={"amount": 400, "meal_id": meal["id"]}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["meal_water_ml"] == 400, r.get_json()
    # The meal row now carries the linked water for the "💧 400ml" chip.
    day = c.get(f"/api/nutrition/date/{_today()}").get_json()
    row = [m for m in day["meals"] if m["id"] == meal["id"]][0]
    assert row["water_ml"] == 400, row
    print(" 23. log-water?meal_id links water; meal row shows water_ml=400  OK")


def test_24_log_water_accumulates_on_meal():
    c = _client()
    meal = db.get_meals(_today())[0]
    c.post("/api/nutrition/log-water",
           json={"amount": 250, "meal_id": meal["id"]}, headers=_H)
    assert db.get_hydration_for_meal(meal["id"]) == 650, "400 + 250"
    print(" 24. multiple water logs on a meal accumulate (400+250=650)  OK")


def test_25_log_water_standalone():
    c = _client()
    _clear()
    r = c.post("/api/nutrition/log-water", json={"amount": 500}, headers=_H)
    assert r.status_code == 200 and r.get_json()["meal_water_ml"] == 0, r.get_json()
    assert db.get_hydration_total(_today()) == 500, "standalone water still counts"
    print(" 25. standalone water (no meal_id) logs to the day, meal_water 0  OK")


def test_26_log_water_validation():
    c = _client()
    assert c.post("/api/nutrition/log-water", json={"amount": 0}, headers=_H).status_code == 400
    assert c.post("/api/nutrition/log-water",
                  json={"amount": 300, "meal_id": 999999}, headers=_H).status_code == 404
    print(" 26. log-water validation: amount<=0 -> 400, unknown meal -> 404  OK")


def test_27_water_attributed_to_meal_date():
    # Water for a past-dated meal lands on the meal's date, not today.
    c = _client()
    _clear()
    past = _shift(_today(), -3)
    meal = _log(c, past, "Old dinner", 20, 30, 10, calories=290)
    c.post("/api/nutrition/log-water",
           json={"amount": 300, "meal_id": meal["id"]}, headers=_H)
    assert db.get_hydration_total(past) == 300, "water on the meal's date"
    assert db.get_hydration_total(_today()) == 0, "not today"
    print(" 27. meal-linked water is attributed to the meal's date  OK")


def main():
    tests = [
        test_1_food_source_persists, test_2_food_source_optional,
        test_3_parse_branded_food_prefixes_brand, test_4_whole_food_source_unchanged,
        test_5_search_falls_back_to_branded, test_6_search_prefers_whole_food,
        test_7_add_restaurant_and_item, test_8_search_restaurant_items,
        test_9_search_route_merges_restaurant_first, test_10_search_route_short_query,
        test_11_favorite_after_repeat_logs, test_12_log_favorite_path_form,
        test_13_add_manual_favorite, test_14_add_manual_favorite_validation,
        test_15_meal_prep_create_totals, test_16_meal_prep_kcal_atwater_fallback,
        test_17_meal_prep_create_validation, test_18_meal_prep_list_active_only,
        test_19_meal_prep_log_usage_adds_meal, test_20_meal_prep_multiple_usages,
        test_21_meal_prep_delete_keeps_past_meals, test_22_meal_prep_usage_missing_prep,
        test_23_log_water_linked_to_meal, test_24_log_water_accumulates_on_meal,
        test_25_log_water_standalone, test_26_log_water_validation,
        test_27_water_attributed_to_meal_date,
    ]
    print("Nutrition expansion v2 tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
