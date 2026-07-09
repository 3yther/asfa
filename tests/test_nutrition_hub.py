"""Nutrition hub (Tier 7 redesign) tests — goals, per-date, previous-foods,
frequent-at-hour, and undo. Self-contained (no pytest); run with:

    python tests/test_nutrition_hub.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_nutri_hub_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
import app as app_module       # noqa: E402


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


_H = {"X-CSRF-Token": "tok"}


def test_1_goals_defaults():
    c = _client()
    r = c.get("/api/nutrition/goals")
    assert r.status_code == 200, r.get_data(as_text=True)
    g = r.get_json()
    assert g == {"protein_goal": 160, "carbs_goal": 200, "fat_goal": 70,
                 "calorie_goal": 2500}, g
    print("  1. GET /goals -> defaults 160/200/70/2500  OK")


def test_2_goals_upsert():
    c = _client()
    r = c.post("/api/nutrition/goals",
               json={"protein_goal": 180, "carbs_goal": 210,
                     "fat_goal": 65, "calorie_goal": 2600}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["goals"]["protein_goal"] == 180
    g = c.get("/api/nutrition/goals").get_json()
    assert g["protein_goal"] == 180 and g["calorie_goal"] == 2600, g
    print("  2. POST /goals then GET -> protein 180, cal 2600 persisted  OK")


def test_3_previous_foods_empty():
    c = _client()
    r = c.get("/api/nutrition/previous-foods")
    assert r.status_code == 200
    assert r.get_json() == [], r.get_json()
    print("  3. GET /previous-foods (no meals) -> []  OK")


def test_4_log_and_previous():
    c = _client()
    today = app_module._today()
    r = c.post("/api/nutrition/log",
               json={"date": today, "food_name": "Chicken",
                     "protein": 31, "carbs": 0, "fat": 3.6,
                     "time": "07:30", "source": "search"}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    pf = c.get("/api/nutrition/previous-foods").get_json()
    assert len(pf) == 1 and pf[0]["food_name"] == "Chicken" and pf[0]["count"] == 1, pf
    assert pf[0]["protein"] == 31, pf
    print("  4. POST /log {Chicken, source=search} then /previous-foods -> "
          "[{Chicken,1}] with macros  OK")


def test_5_date_today():
    c = _client()
    r = c.get("/api/nutrition/date/today")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["totals"]["total_protein"] == 31, body
    assert len(body["meals"]) == 1 and body["meals"][0]["food_name"] == "Chicken", body
    assert body["meals"][0]["id"] is not None, body
    assert body["goals"]["protein_goal"] == 180, body
    print("  5. GET /date/today -> totals.protein=31, 1 meal (w/ id), goals attached  OK")


def test_6_frequent_at_hour():
    c = _client()
    r = c.get("/api/nutrition/frequent-at-hour?hour=7&limit=5")
    assert r.status_code == 200
    body = r.get_json()
    assert body and body[0]["food_name"] == "Chicken", body
    # A different hour with no logs is empty.
    assert c.get("/api/nutrition/frequent-at-hour?hour=15").get_json() == []
    print("  6. GET /frequent-at-hour?hour=7 -> [Chicken]; hour=15 -> []  OK")


def test_7_yesterday_empty():
    c = _client()
    r = c.get("/api/nutrition/yesterday")
    assert r.status_code == 200
    assert r.get_json()["meals"] == [], r.get_json()
    print("  7. GET /yesterday -> no meals  OK")


def test_8_undo():
    c = _client()
    today = app_module._today()
    # Log a second meal, then undo removes only the most recent.
    c.post("/api/nutrition/log",
           json={"date": today, "food_name": "Rice", "protein": 4,
                 "carbs": 45, "fat": 1, "source": "quick-add"}, headers=_H)
    before = c.get("/api/nutrition/date/today").get_json()["totals"]["meal_count"]
    r = c.post("/api/nutrition/undo", json={"date": today}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True and body["last_meal_id"] is not None, body
    after = body["updated_totals"]["meal_count"]
    assert after == before - 1, (before, after)
    # The removed meal was Rice; Chicken remains.
    remaining = c.get("/api/nutrition/date/today").get_json()["meals"]
    assert [m["food_name"] for m in remaining] == ["Chicken"], remaining
    print(f"  8. POST /undo -> removed last meal ({before}->{after}), Chicken remains  OK")


def main():
    tests = [test_1_goals_defaults, test_2_goals_upsert, test_3_previous_foods_empty,
             test_4_log_and_previous, test_5_date_today, test_6_frequent_at_hour,
             test_7_yesterday_empty, test_8_undo]
    print("Nutrition hub (Tier 7 redesign) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
