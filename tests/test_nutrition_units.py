"""Serving-size unit conversion tests (Tier 9b) — pure conversion layer plus the
/api/nutrition/convert endpoint. Self-contained (no pytest); run with:

    python tests/test_nutrition_units.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
The conversion functions are pure (no DB), but the endpoint tests boot the app.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_nutri_units_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import nutrition   # noqa: E402
import app as app_module         # noqa: E402


def _approx(a, b, tol=0.5):
    return abs(a - b) <= tol


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


# ── Pure conversion layer ────────────────────────────────────────────────────────
def test_1_volume_with_density():
    g, est = nutrition.convert_to_grams("whole milk water blend water", 1, "cup")
    # water density 1.03 → 240 * 1.03 = 247.2
    assert _approx(g, 247.2), g
    assert est is False, est
    g2, _ = nutrition.convert_to_grams("water", 1, "cup")
    assert _approx(g2, 247.2), g2
    print(f"  1. 1 cup water = {g2}g (density 1.03, not estimated)  OK")


def test_2_honey_tbsp():
    g, est = nutrition.convert_to_grams("Honey", 1, "tbsp")
    # 14.79 * 1.42 = 21.0
    assert _approx(g, 21.0), g
    assert est is False, est
    print(f"  2. 1 tbsp honey = {g}g  OK")


def test_3_oats_half_cup():
    g, est = nutrition.convert_to_grams("Rolled oats", 0.5, "cup")
    # 0.5 * 240 * 0.34 = 40.8  (NOT 500g — the bug that motivated this feature)
    assert _approx(g, 40.8), g
    assert g < 100, f"oats half-cup must be ~41g, got {g}"
    assert est is False, est
    print(f"  3. 0.5 cup oats = {g}g (not 500)  OK")


def test_4_scoop_protein():
    g, est = nutrition.convert_to_grams("Whey protein powder", 1, "scoop")
    assert _approx(g, 30.0), g
    assert est is False, est
    print(f"  4. 1 scoop protein powder = {g}g  OK")


def test_5_pieces_eggs():
    g, est = nutrition.convert_to_grams("Egg, large, raw", 2, "piece")
    assert _approx(g, 100.0), g
    assert est is False, est
    print(f"  5. 2 large eggs (piece) = {g}g  OK")


def test_6_mass_passthrough():
    g, est = nutrition.convert_to_grams("anything", 150, "g")
    assert g == 150.0 and est is False, (g, est)
    g2, est2 = nutrition.convert_to_grams("anything", 1, "lb")
    assert _approx(g2, 453.6) and est2 is False, (g2, est2)
    print(f"  6. mass passthrough: 150 g = {g}g, 1 lb = {g2}g  OK")


def test_7_unknown_food_default_density():
    g, est = nutrition.convert_to_grams("zzq mystery substance", 1, "cup")
    # DEFAULT_DENSITY 0.6 → 240 * 0.6 = 144
    assert _approx(g, 144.0), g
    assert est is True, "unknown food + volume must be estimated"
    print(f"  7. unknown food + cup = {g}g (DEFAULT_DENSITY, estimated=True)  OK")


def test_8_piece_no_match_fallback():
    g, est = nutrition.convert_to_grams("dragonfruit", 1, "piece")
    assert _approx(g, 100.0) and est is True, (g, est)
    print(f"  8. unmatched piece = {g}g (fallback, estimated=True)  OK")


def test_9_bad_input_raises():
    for bad in [("food", 0, "cup"), ("food", -1, "g"), ("food", "x", "g"),
                ("food", 1, "furlong"), ("food", 1, "")]:
        try:
            nutrition.convert_to_grams(*bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")
    print("  9. bad amount/unit raise ValueError  OK")


# ── /api/nutrition/convert endpoint ──────────────────────────────────────────────
def test_10_convert_endpoint_ok():
    c = _client()
    r = c.get("/api/nutrition/convert?food=oats&amount=0.5&unit=cup")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert _approx(body["grams"], 40.8), body
    assert body["estimated"] is False, body
    print(f"  10. GET /convert oats 0.5 cup -> {body}  OK")


def test_11_convert_endpoint_400s():
    c = _client()
    # <2-char food
    assert c.get("/api/nutrition/convert?food=o&amount=1&unit=cup").status_code == 400
    # bad unit
    assert c.get("/api/nutrition/convert?food=oats&amount=1&unit=furlong").status_code == 400
    # bad amount
    assert c.get("/api/nutrition/convert?food=oats&amount=0&unit=cup").status_code == 400
    print("  11. /convert 400s on <2-char food / bad unit / bad amount  OK")


def test_12_fl_oz_alias():
    # "fl oz" (with a space) normalizes to fl_oz
    g, _ = nutrition.convert_to_grams("water", 1, "fl oz")
    assert _approx(g, 29.57 * 1.03), g
    print(f"  12. 'fl oz' alias -> {g}g  OK")


def main():
    tests = [test_1_volume_with_density, test_2_honey_tbsp, test_3_oats_half_cup,
             test_4_scoop_protein, test_5_pieces_eggs, test_6_mass_passthrough,
             test_7_unknown_food_default_density, test_8_piece_no_match_fallback,
             test_9_bad_input_raises, test_10_convert_endpoint_ok,
             test_11_convert_endpoint_400s, test_12_fl_oz_alias]
    print("Nutrition serving-unit (Tier 9b) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
