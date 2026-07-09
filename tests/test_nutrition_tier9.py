"""Nutrition depth (Tier 9a) tests — templates, trends, score/streak, favorites,
insights. Self-contained (no pytest); run with:

    python tests/test_nutrition_tier9.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_nutri_t9_test_"), "test.db")
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


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _shift(date_str, delta):
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def _log(c, date, name, p, cbs, f, **kw):
    body = {"date": date, "food_name": name, "protein": p, "carbs": cbs, "fat": f,
            "source": kw.get("source", "quick-add")}
    if "time" in kw:
        body["time"] = kw["time"]
    if "calories" in kw:
        body["calories"] = kw["calories"]
    r = c.post("/api/nutrition/log", json=body, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["meal"]["id"]


def _clear():
    """Wipe meals so a day-sensitive test starts from a known-empty state (the
    module shares one temp DB across tests)."""
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM meals")


def _set_goals(c, p, cbs, f, cal):
    r = c.post("/api/nutrition/goals",
               json={"protein_goal": p, "carbs_goal": cbs, "fat_goal": f,
                     "calorie_goal": cal}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)


# ── Templates ────────────────────────────────────────────────────────────────────

def test_1_template_roundtrip():
    c = _client()
    today = _today()
    id1 = _log(c, today, "Chicken", 31, 0, 3.6, calories=155)
    id2 = _log(c, today, "Rice", 4, 45, 1, calories=205)
    # Snapshot both into a template.
    r = c.post("/api/nutrition/template",
               json={"name": "Post-Workout", "meal_ids": [id1, id2]}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    tpl = r.get_json()["template"]
    assert tpl["name"] == "Post-Workout" and tpl["item_count"] == 2, tpl
    # Totals sum the snapshot.
    assert tpl["totals"]["protein"] == 35 and tpl["totals"]["carbs"] == 45, tpl["totals"]
    assert tpl["totals"]["kcal"] == 360, tpl["totals"]

    # Listing returns it with totals.
    lst = c.get("/api/nutrition/templates").get_json()
    assert len(lst) == 1 and lst[0]["totals"]["kcal"] == 360, lst

    # Log it onto a fresh day → totals match the template totals.
    other = _shift(today, 3)
    r = c.post("/api/nutrition/log-template",
               json={"template_id": tpl["id"], "date": other}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["meals_logged"] == 2, body
    ut = body["updated_totals"]
    assert ut["total_protein"] == 35 and ut["total_carbs"] == 45, ut
    assert ut["total_calories"] == 360, ut
    print("  1. template save->log round-trip: totals 35P/45C/360kcal match  OK")


def test_2_template_snapshot_is_a_copy():
    c = _client()
    today = _today()
    mid = _log(c, today, "Eggs", 12, 1, 10, calories=150)
    tpl = c.post("/api/nutrition/template",
                 json={"name": "Solo", "meal_ids": [mid]}, headers=_H).get_json()["template"]
    # Deleting the source meal must not change the template.
    db.delete_meal(mid)
    again = c.get("/api/nutrition/templates").get_json()
    solo = [t for t in again if t["name"] == "Solo"][0]
    assert solo["item_count"] == 1 and solo["items"][0]["food_name"] == "Eggs", solo
    print("  2. template snapshot survives source-meal deletion (copy, not ref)  OK")


def test_3_template_delete():
    c = _client()
    mid = _log(c, _today(), "Toast", 6, 20, 2)
    tpl = c.post("/api/nutrition/template",
                 json={"name": "Gone", "meal_ids": [mid]}, headers=_H).get_json()["template"]
    r = c.post("/api/nutrition/template-delete",
               json={"template_id": tpl["id"]}, headers=_H)
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_data(as_text=True)
    remaining = [t["id"] for t in c.get("/api/nutrition/templates").get_json()]
    assert tpl["id"] not in remaining, remaining
    print("  3. template-delete removes it from the list  OK")


def test_4_template_validation():
    c = _client()
    assert c.post("/api/nutrition/template",
                  json={"name": "", "meal_ids": [1]}, headers=_H).status_code == 400
    assert c.post("/api/nutrition/template",
                  json={"name": "X", "meal_ids": []}, headers=_H).status_code == 400
    assert c.post("/api/nutrition/log-template",
                  json={"template_id": 99999}, headers=_H).status_code == 404
    print("  4. template validation: blank name / empty ids / missing template  OK")


# ── Trends ───────────────────────────────────────────────────────────────────────

def test_5_trends_zero_fill():
    c = _client()
    _clear()
    today = _today()
    # Log only today and 4 days ago; the intervening days must appear as zeros.
    _log(c, today, "A", 10, 10, 10, calories=170)
    _log(c, _shift(today, -4), "B", 20, 20, 20, calories=340)
    r = c.get("/api/nutrition/trends?days=7")
    assert r.status_code == 200, r.get_data(as_text=True)
    t = r.get_json()
    assert len(t["dates"]) == 7 and len(t["protein"]) == 7, t
    assert t["dates"][-1] == today and t["dates"][0] == _shift(today, -6), t["dates"]
    # Newest (today) protein = 10; the gap days are zero-filled, not skipped.
    assert t["protein"][-1] == 10, t["protein"]
    assert t["protein"][2] == 20, t["protein"]          # 4 days ago (index 6-4=2)
    assert t["protein"][3] == 0 and t["protein"][4] == 0, t["protein"]
    assert "goals" in t and t["goals"]["protein_goal"], t
    print("  5. trends(7) zero-fills unlogged gap days, dates ascending to today  OK")


def test_6_trends_30_same_path():
    c = _client()
    t = c.get("/api/nutrition/trends?days=30").get_json()
    assert len(t["dates"]) == 30 and len(t["kcal"]) == 30, t
    print("  6. trends(30) powers energy-balance via the same path (30 points)  OK")


# ── Score + streak ───────────────────────────────────────────────────────────────

def test_7_score_grades():
    # Pure-function grading against goals 160/200/70/2500.
    goals = {"protein_goal": 160, "carbs_goal": 200, "fat_goal": 70, "calorie_goal": 2500}

    def score(p, cbs, f, cal):
        totals = {"total_protein": p, "total_carbs": cbs, "total_fat": f,
                  "total_calories": cal, "meal_count": 1}
        return db.score_nutrition_day("2026-01-01", totals=totals, goals=goals)

    # A = all four hit.
    a = score(160, 200, 70, 2500)
    assert a["grade"] == "A" and a["hits"] == 4 and a["misses"] == [], a
    # Protein overshoot still hits (floor rule); carbs at +10% boundary hits.
    a2 = score(200, 220, 70, 2500)
    assert a2["grade"] == "A", a2
    # B = protein short (128 < 144 = 90%): 3/4.
    b = score(120, 200, 70, 2500)
    assert b["grade"] == "B" and b["hits"] == 3 and b["misses"] == ["protein"], b
    # C = 2/4 (carbs + fat off band).
    cc = score(160, 100, 40, 2500)
    assert cc["grade"] == "C" and cc["hits"] == 2, cc
    # D = <2 (only protein floor met).
    d = score(160, 50, 20, 1200)
    assert d["grade"] == "D" and d["hits"] <= 1, d
    print("  7. score grades A/B/C/D correct incl. protein-floor + ±10% boundary  OK")


def test_8_score_boundary():
    goals = {"protein_goal": 160, "carbs_goal": 200, "fat_goal": 70, "calorie_goal": 2500}

    def carbs_hit(cbs):
        totals = {"total_protein": 160, "total_carbs": cbs, "total_fat": 70,
                  "total_calories": 2500, "meal_count": 1}
        return "carbs" not in db.score_nutrition_day("d", totals=totals, goals=goals)["misses"]

    assert carbs_hit(180) is True, "carbs at -10% (180) should hit"
    assert carbs_hit(220) is True, "carbs at +10% (220) should hit"
    assert carbs_hit(179) is False, "carbs below -10% (179) should miss"
    assert carbs_hit(221) is False, "carbs above +10% (221) should miss"
    print("  8. ±10% band boundary: 180/220 hit, 179/221 miss  OK")


def test_9_streak():
    c = _client()
    _clear()
    today = _today()
    _set_goals(c, 160, 200, 70, 2500)
    # Three A/B days ending yesterday, then today unlogged.
    for delta in (-3, -2, -1):
        d = _shift(today, delta)
        _log(c, d, "Meal", 160, 200, 70, calories=2500)
    # Streak measured AT yesterday = 3; AT today (unlogged) = 0.
    y = _shift(today, -1)
    r = c.get(f"/api/nutrition/score?date={y}")
    body = r.get_json()
    assert body["grade"] == "A" and body["streak"] == 3, body
    t = c.get(f"/api/nutrition/score?date={today}").get_json()
    assert t["streak"] == 0 and t["logged"] is False, t
    print("  9. streak counts 3 consecutive A days, breaks at unlogged today (0)  OK")


def test_10_streak_breaks_on_bad_day():
    c = _client()
    base = "2025-03-10"
    _set_goals(c, 160, 200, 70, 2500)
    _log(c, "2025-03-08", "Good", 160, 200, 70, calories=2500)   # A
    _log(c, "2025-03-09", "Bad", 20, 10, 5, calories=200)        # D breaks
    _log(c, base, "Good", 160, 200, 70, calories=2500)           # A
    # Streak at base = 1 (the D day on 03-09 breaks the chain).
    s = c.get(f"/api/nutrition/score?date={base}").get_json()["streak"]
    assert s == 1, s
    print(" 10. streak breaks on an intervening C/D day  OK")


# ── Favorites ────────────────────────────────────────────────────────────────────

def test_11_favorites_average_macros():
    c = _client()
    _clear()
    base = "2025-06-01"
    # Same food logged twice with different portions → favorite shows the AVERAGE.
    _log(c, base, "Oats", 10, 50, 5, calories=290)
    _log(c, _shift(base, 1), "Oats", 20, 70, 5, calories=410)
    _log(c, base, "Banana", 1, 27, 0, calories=112)
    r = c.get("/api/nutrition/favorites?limit=10")
    favs = r.get_json()
    oats = [f for f in favs if f["food_name"] == "Oats"][0]
    assert oats["count"] == 2, oats
    assert oats["protein"] == 15 and oats["carbs"] == 60, oats   # (10+20)/2, (50+70)/2
    assert oats["calories"] == 350, oats                          # (290+410)/2
    # Top food first (Oats has the higher count).
    assert favs[0]["food_name"] == "Oats", favs
    print(" 11. favorites average macros across a food's logs (Oats 15P/60C/350kcal)  OK")


def test_12_log_favorite_uses_average():
    c = _client()
    target = "2025-06-05"
    r = c.post("/api/nutrition/log-favorite",
               json={"food_name": "Oats", "date": target}, headers=_H)
    assert r.status_code == 200, r.get_data(as_text=True)
    ut = r.get_json()["updated_totals"]
    assert ut["total_protein"] == 15 and ut["total_carbs"] == 60, ut
    # Unknown food → 400.
    bad = c.post("/api/nutrition/log-favorite",
                 json={"food_name": "Nonexistent", "date": target}, headers=_H)
    assert bad.status_code == 400, bad.get_data(as_text=True)
    print(" 12. log-favorite logs averaged macros; unknown food -> 400  OK")


# ── Insights ─────────────────────────────────────────────────────────────────────

def test_13_insights_thresholds():
    # 0 days and 2 days both yield the "not enough" line; 7 days yields 2–4 lines.
    fresh = os.path.join(tempfile.mkdtemp(prefix="asfa_ins_"), "t.db")
    # Use the live DB helpers directly against a scratch state by clearing meals.
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM meals")
    zero = db.get_nutrition_insights()
    assert zero == ["Not enough data yet — log a few more days."], zero

    today = _today()
    # 2 logged days → still not enough.
    for delta in (0, -1):
        db.log_meal(_shift(today, delta), "M", 150, 200, 70, calories=2400, source="quick-add")
    two = db.get_nutrition_insights()
    assert two == ["Not enough data yet — log a few more days."], two

    # 7 logged days → real insights (2–4 honest lines).
    for delta in (-2, -3, -4, -5, -6):
        db.log_meal(_shift(today, delta), "M", 150, 200, 70, calories=2400, source="quick-add")
    seven = db.get_nutrition_insights()
    assert 2 <= len(seven) <= 4, seven
    assert any("Protein" in s for s in seven), seven
    assert any("kcal" in s for s in seven), seven
    print(f" 13. insights: 0/2 days -> not-enough; 7 days -> {len(seven)} honest lines  OK")


def main():
    tests = [test_1_template_roundtrip, test_2_template_snapshot_is_a_copy,
             test_3_template_delete, test_4_template_validation,
             test_5_trends_zero_fill, test_6_trends_30_same_path,
             test_7_score_grades, test_8_score_boundary, test_9_streak,
             test_10_streak_breaks_on_bad_day, test_11_favorites_average_macros,
             test_12_log_favorite_uses_average, test_13_insights_thresholds]
    print("Nutrition depth (Tier 9a) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
