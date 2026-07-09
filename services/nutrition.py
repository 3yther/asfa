"""Nutrition helpers (Tier 7): barcode lookup + text search + portion estimation.

Two food sources, both free and per-100g so the caller can scale to the portion
eaten. Barcode data comes from Open Food Facts (packaged/branded goods, no key).
Text search comes from USDA FoodData Central (whole foods). Every network path
fails soft (returns None / []) so a lookup miss or an outage just drops the user
back to manual entry rather than blanking the UI.
"""
import os
import time

import requests

_OFF_URL = "https://world.openfoodfacts.org/api/v0/product/{code}.json"
_TIMEOUT = 5
# Open Food Facts blocks requests without a descriptive User-Agent (403), per
# their API policy: identify the app + a contact.
_HEADERS = {"User-Agent": "ASFA-Dashboard/1.0 (nutrition; ami.salax@gmail.com)"}

# USDA FoodData Central text search. Free; DEMO_KEY works but is rate-limited
# (~30 req/hr, 50/day per IP), so prod should set FDC_API_KEY to a real key
# (https://fdc.nal.usda.gov/api-key-signup). We restrict to whole-food data
# types (Foundation / SR Legacy / Survey) — those carry clean, curated per-100g
# nutrients; branded/packaged goods are already covered by the barcode path.
_FDC_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
_FDC_DATA_TYPES = "Foundation,SR Legacy,Survey (FNDDS)"
# FDC nutrient numbers (stable across data types; ids are not).
_N_PROTEIN, _N_FAT, _N_CARBS, _N_KCAL = "203", "204", "205", "208"
# Cache search results for 24h so repeated "chicken" queries don't burn the
# per-IP rate limit. In-memory is fine: single gunicorn worker, one process.
_SEARCH_TTL = 24 * 60 * 60
_search_cache: dict[str, tuple[float, list]] = {}


def _num(value):
    """Coerce an Open Food Facts numeric field to float, or None if missing/blank.
    OFF returns numbers as strings ("25.3") or "" for unknown values."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lookup_barcode(code: str):
    """Look up a product by barcode on Open Food Facts.

    Returns {food_name, protein_per_100g, carbs_per_100g, fat_per_100g,
    energy_per_100g} on a hit, or None when the barcode is unknown or the request
    fails for any reason (network error, timeout, malformed JSON).
    """
    code = (code or "").strip()
    if not code:
        return None
    try:
        r = requests.get(_OFF_URL.format(code=code), headers=_HEADERS,
                         timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    # status == 1 means "product found"; anything else (0 / missing) is a miss.
    if data.get("status") != 1:
        return None

    product = data.get("product") or {}
    nutriments = product.get("nutriments") or {}

    name = (product.get("product_name")
            or product.get("generic_name")
            or "").strip()
    if not name:
        return None

    # OFF exposes energy in kcal via energy-kcal_100g; energy_100g is kJ.
    energy = _num(nutriments.get("energy-kcal_100g"))
    if energy is None:
        kj = _num(nutriments.get("energy_100g"))
        energy = round(kj / 4.184, 1) if kj is not None else None

    return {
        "food_name": name,
        "protein_per_100g": _num(nutriments.get("proteins_100g")),
        "carbs_per_100g": _num(nutriments.get("carbohydrates_100g")),
        "fat_per_100g": _num(nutriments.get("fat_100g")),
        "energy_per_100g": energy,
    }


def estimate_portion(energy_per_100g, user_input_calories):
    """Back out an approximate portion weight (grams) from the calories the user
    thinks they ate, for people logging without a scale:

        portion_grams = (user_input_calories / energy_per_100g) * 100

    Returns None if energy_per_100g is missing or zero (can't divide)."""
    energy = _num(energy_per_100g)
    cals = _num(user_input_calories)
    if not energy or cals is None:
        return None
    return round((cals / energy) * 100, 1)


def _fdc_api_key() -> str:
    return (os.getenv("FDC_API_KEY") or "").strip() or "DEMO_KEY"


def _parse_fdc_food(food: dict):
    """Map one FDC search hit to our per-100g shape, or None if it has no macros.

    FDC reports search nutrients per 100g regardless of data type. Values are
    keyed by nutrientNumber (stable) rather than nutrientId. Two quirks handled:
    carbs "by difference" can go slightly negative (clamp to 0), and Foundation
    foods often omit the kcal nutrient — fall back to Atwater (4/4/9)."""
    name = (food.get("description") or "").strip()
    if not name:
        return None
    by_num = {}
    for n in food.get("foodNutrients") or []:
        num = str(n.get("nutrientNumber") or "")
        if num and num not in by_num:
            by_num[num] = n

    def val(num, floor0=False):
        v = _num((by_num.get(num) or {}).get("value"))
        if v is None:
            return None
        return max(0.0, v) if floor0 else v

    protein = val(_N_PROTEIN, floor0=True) or 0.0
    carbs = val(_N_CARBS, floor0=True) or 0.0
    fat = val(_N_FAT, floor0=True) or 0.0

    kcal_node = by_num.get(_N_KCAL) or {}
    kcal = _num(kcal_node.get("value")) if (kcal_node.get("unitName") or "").upper() == "KCAL" else None
    if kcal is None:
        kcal = round(protein * 4 + carbs * 4 + fat * 9, 1)

    # Title-case the SHOUTING data ("CHICKEN, BACK") into something readable.
    if name.isupper():
        name = name.title()
    return {
        "food_name": name,
        "protein_per_100g": round(protein, 1),
        "carbs_per_100g": round(carbs, 1),
        "fat_per_100g": round(fat, 1),
        "kcal_per_100g": round(kcal, 1),
    }


def search_foods(query: str, limit: int = 10):
    """Search USDA FoodData Central for whole foods matching `query`.

    Returns up to `limit` foods as
    [{food_name, protein_per_100g, carbs_per_100g, fat_per_100g, kcal_per_100g}]
    ordered by USDA relevance. Results are cached for 24h per query. Any failure
    (timeout, HTTP error, malformed JSON) returns [] so the search UI never
    blanks — the user can still fall back to previous foods / barcode / manual."""
    q = (query or "").strip()
    if len(q) < 2:
        return []

    key = q.lower()
    hit = _search_cache.get(key)
    if hit and (time.time() - hit[0]) < _SEARCH_TTL:
        return hit[1][:limit]

    params = {
        "query": q,
        "pageSize": max(1, min(25, limit)),
        "dataType": _FDC_DATA_TYPES,
        "api_key": _fdc_api_key(),
    }
    try:
        r = requests.get(_FDC_URL, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []

    out = []
    for food in (data.get("foods") or []):
        parsed = _parse_fdc_food(food)
        if parsed:
            out.append(parsed)
        if len(out) >= limit:
            break

    _search_cache[key] = (time.time(), out)
    return out
