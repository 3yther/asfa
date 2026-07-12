"""Multi-source food search tests — USDA whole-food → USDA Branded → Open Food
Facts fallback chain.

Self-contained, no pytest dependency: run directly with

    python tests/test_food_fallback.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) and stubs requests.get so no
real USDA/OFF calls are made. Verifies: USDA whole-food hits win (no fallback),
whole-food misses fall through USDA Branded then to OFF with
source='open_food_facts', all-empty is cached, and a transient outage on any
side is NOT cached. (USDA whole-food and Branded share the FDC endpoint; the
stub tells them apart by the `dataType` param.)
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_food_fallback_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import nutrition  # noqa: E402
import requests as _requests     # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fdc_food(desc, p, c, f, kcal):
    """One USDA FDC search hit (nutrients keyed by nutrientNumber)."""
    return {"description": desc, "foodNutrients": [
        {"nutrientNumber": 203, "value": p, "unitName": "G"},
        {"nutrientNumber": 204, "value": f, "unitName": "G"},
        {"nutrientNumber": 205, "value": c, "unitName": "G"},
        {"nutrientNumber": 208, "value": kcal, "unitName": "KCAL"},
    ]}


_OFF_PRODUCT = {
    "product_name": "Amlu Berry Spread",
    "code": "3017620422003",
    "nutriments": {
        "proteins_100g": "3",
        "carbohydrates_100g": "60",
        "fat_100g": "25",
        "energy-kcal_100g": "520",
    },
}

CALLS = {"usda": 0, "branded": 0, "off": 0}


def _install(usda_by_q=None, off_by_q=None, branded_by_q=None,
             usda_error=False, off_error=False):
    """Point nutrition.requests.get at a stub and reset the call counters +
    search cache so each test starts clean. `usda_error` fails BOTH USDA tiers
    (they share the endpoint)."""
    usda_by_q = usda_by_q or {}
    off_by_q = off_by_q or {}
    branded_by_q = branded_by_q or {}
    CALLS["usda"] = CALLS["branded"] = CALLS["off"] = 0
    nutrition._search_cache.clear()

    def fake_get(url, *args, **kwargs):
        params = kwargs.get("params", {})
        if "api.nal.usda.gov" in url:
            branded = "Branded" in (params.get("dataType") or "")
            CALLS["branded" if branded else "usda"] += 1
            if usda_error:
                raise _requests.RequestException("usda down")
            q = (params.get("query") or "").lower()
            table = branded_by_q if branded else usda_by_q
            return _FakeResp({"foods": table.get(q, [])})
        if "search.pl" in url:
            CALLS["off"] += 1
            if off_error:
                raise _requests.RequestException("off down")
            q = (params.get("search_terms") or "").lower()
            return _FakeResp({"products": off_by_q.get(q, [])})
        return _FakeResp({})

    nutrition.requests.get = fake_get


def test_1_usda_hit_no_fallback():
    _install(usda_by_q={"honey": [_fdc_food("Honey", 0.3, 82.4, 0.0, 304)]})
    res = nutrition.search_foods("honey")
    assert len(res) == 1, res
    assert res[0]["food_name"] == "Honey", res
    assert res[0]["source"] == "usda", res
    assert res[0]["kcal_per_100g"] == 304, res
    assert CALLS["off"] == 0, "OFF must not be queried when USDA has hits"
    assert CALLS["branded"] == 0, "Branded must not be queried when whole-food hits"
    print("  1. 'honey' -> USDA whole-food hit, source=usda, no branded/OFF call  OK")


def test_2_fallback_to_off():
    _install(usda_by_q={}, off_by_q={"amlu berry": [_OFF_PRODUCT]})
    res = nutrition.search_foods("amlu berry")
    assert len(res) == 1, res
    assert res[0]["source"] == "open_food_facts", res
    assert res[0]["food_name"] == "Amlu Berry Spread", res
    assert res[0]["protein_per_100g"] == 3.0, res
    assert res[0]["kcal_per_100g"] == 520.0, res
    assert res[0]["barcode"] == "3017620422003", res
    assert CALLS["usda"] == 1 and CALLS["branded"] == 1 and CALLS["off"] == 1, CALLS
    print("  2. 'amlu berry' -> whole-food + branded empty, OFF fallback  OK")


def test_2b_fallback_to_branded():
    # Whole-food empty, Branded has the hit → source=usda_branded, no OFF call.
    _install(usda_by_q={},
             branded_by_q={"clif bar": [_fdc_food("CLIF BAR", 10, 44, 6, 250)]})
    res = nutrition.search_foods("clif bar")
    assert len(res) == 1, res
    assert res[0]["source"] == "usda_branded", res
    assert res[0]["kcal_per_100g"] == 250, res
    assert CALLS["usda"] == 1 and CALLS["branded"] == 1 and CALLS["off"] == 0, CALLS
    print("  2b. 'clif bar' -> whole-food empty, USDA Branded hit, no OFF call  OK")


def test_3_off_kj_fallback():
    # Product with only kJ energy -> kcal derived (kJ / 4.184).
    item = {"product_name": "Oats", "code": "1",
            "nutriments": {"proteins_100g": 13, "carbohydrates_100g": 60,
                           "fat_100g": 7, "energy_100g": 1600}}
    parsed = nutrition._parse_off_food(item)
    assert parsed["source"] == "open_food_facts", parsed
    assert parsed["kcal_per_100g"] == round(1600 / 4.184, 1), parsed
    print(f"  3. _parse_off_food kJ→kcal: 1600kJ -> {parsed['kcal_per_100g']}kcal  OK")


def test_4_both_empty_cached():
    _install(usda_by_q={}, off_by_q={})
    res = nutrition.search_foods("zzzznope")
    assert res == [], res
    assert CALLS["usda"] == 1 and CALLS["branded"] == 1 and CALLS["off"] == 1, CALLS
    # Second call must be served from cache (all three sources were reached).
    res2 = nutrition.search_foods("zzzznope")
    assert res2 == [], res2
    assert CALLS["usda"] == 1 and CALLS["branded"] == 1 and CALLS["off"] == 1, \
        ("empty should be cached", CALLS)
    print("  4. all sources empty -> [] and cached (no re-query)  OK")


def test_5_transient_not_cached():
    # USDA errors, OFF also errors -> [] but NOT cached, so a retry re-queries.
    _install(usda_error=True, off_error=True)
    res = nutrition.search_foods("chicken")
    assert res == [], res
    first = (CALLS["usda"], CALLS["off"])
    res2 = nutrition.search_foods("chicken")
    assert res2 == [], res2
    assert (CALLS["usda"], CALLS["off"]) != first or CALLS["usda"] == 2, \
        ("transient failure must not be cached", CALLS)
    print("  5. USDA+OFF outage -> [] not cached (retries next call)  OK")


def test_6_endpoint_source_surfaced():
    _install(usda_by_q={}, off_by_q={"amlu berry": [_OFF_PRODUCT]})
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
    resp = client.get("/api/nutrition/search?q=amlu%20berry")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert isinstance(body, list) and body, body
    assert body[0]["source"] == "open_food_facts", body
    print("  6. GET /api/nutrition/search fallback surfaces source=open_food_facts  OK")


def main():
    tests = [test_1_usda_hit_no_fallback, test_2_fallback_to_off,
             test_2b_fallback_to_branded, test_3_off_kj_fallback,
             test_4_both_empty_cached, test_5_transient_not_cached,
             test_6_endpoint_source_surfaced]
    print("Multi-source food search (USDA → Open Food Facts) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
