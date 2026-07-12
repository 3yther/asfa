"""Nutrition helpers (Tier 7): barcode lookup + text search + portion estimation.

Two food sources, both free and per-100g so the caller can scale to the portion
eaten. Barcode data comes from Open Food Facts (packaged/branded goods, no key).
Text search comes from USDA FoodData Central (whole foods). Every network path
fails soft (returns None / []) so a lookup miss or an outage just drops the user
back to manual entry rather than blanking the UI.
"""
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_OFF_URL = "https://world.openfoodfacts.org/api/v0/product/{code}.json"
# Open Food Facts full-text product search (fallback for whole foods USDA misses:
# regional/branded items). Keyless; same descriptive User-Agent policy applies.
_OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
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
        # USDA household portions when present (Survey/FNDDS foods) — the UI
        # surfaces these as the most-accurate unit options for this food.
        "portions": _fdc_portions(food),
        "source": "usda",
    }


# ── Serving-size units (Tier 9b) ────────────────────────────────────────────────
# Grams-only entry is friction — nobody weighs honey or a splash of milk. This
# layer converts household measures (cups, tbsp, ml, pieces…) to grams so the
# EXISTING per-100g scaling can consume the result unchanged. Two hard cases:
#   • solids — 1 cup of oats (~80g) ≠ 1 cup of honey (~340g), so volume→grams
#     needs a per-food density (g/ml), not a universal factor.
#   • liquids — ml→g is also density, just closer to 1.0.
# When USDA carries real portion weights for a food (Survey/FNDDS foods do; SR
# Legacy / Foundation mostly don't) those are preferred over this table — see
# _fdc_portions() and the `portions` field folded into search results.

# Volume → millilitres (exact, universal — independent of what's in the spoon).
VOLUME_TO_ML = {
    "tsp": 4.93, "tbsp": 14.79, "cup": 240.0,   # US legal cup
    "fl_oz": 29.57, "ml": 1.0, "l": 1000.0,
}
# Mass → grams (also exact/universal).
MASS_TO_G = {"g": 1.0, "kg": 1000.0, "oz": 28.35, "lb": 453.6}
# Count-based units resolved via PIECE_WEIGHTS below. `scoop` is a fixed 30g
# alias (a standard protein scoop) rather than a per-food lookup.
_COUNT_UNITS = {"piece", "scoop"}
VALID_UNITS = set(VOLUME_TO_ML) | set(MASS_TO_G) | _COUNT_UNITS

# Fallback density (g/ml) when a food matches nothing below. 0.6 is a middling
# dry-good; convert_to_grams flags estimated=True whenever it is used so the UI
# can show "~estimated". Real densities beat this — expand the table over guesses.
DEFAULT_DENSITY = 0.6

# Density table (g per ml), keyed by lowercase food-name SUBSTRING. Ordered
# most-specific-first: "peanut butter" must win before "butter", "brown sugar"
# before "sugar", "coconut milk" before "milk", "cooked rice" before "rice".
# First matching keyword wins. Values are typical/curated (USDA + cooking refs);
# judgment calls noted in the build report.
_DENSITY_TABLE = [
    # liquids (near water, spec-provided)
    ("coconut milk", 0.97), ("almond milk", 1.03), ("milk", 1.03), ("water", 1.03),
    ("orange juice", 1.05), ("juice", 1.05),
    ("olive oil", 0.92), ("coconut oil", 0.92), ("oil", 0.92),
    ("honey", 1.42), ("maple syrup", 1.37), ("syrup", 1.37),
    ("heavy cream", 1.01), ("cream", 1.01),
    ("greek yogurt", 1.04), ("yoghurt", 1.04), ("yogurt", 1.04),
    ("soy sauce", 1.15), ("ketchup", 1.14), ("mayonnaise", 0.91), ("mayo", 0.91),
    ("vinegar", 1.01), ("broth", 1.00), ("stock", 1.00),
    ("coffee", 1.00), ("tea", 1.00), ("soda", 1.04), ("wine", 0.99), ("beer", 1.01),
    # dry / semi-solid (spec-provided anchors + common extras)
    ("peanut butter", 1.08), ("almond butter", 1.08), ("nut butter", 1.08),
    ("butter", 0.96),
    ("rolled oats", 0.34), ("oatmeal", 0.34), ("oats", 0.34),
    ("bread flour", 0.53), ("almond flour", 0.45), ("flour", 0.53),
    ("brown sugar", 0.90), ("powdered sugar", 0.56), ("sugar", 0.85),
    ("cooked rice", 0.72), ("rice, cooked", 0.72), ("rice", 0.78),  # bare "rice" = dry
    ("cooked pasta", 0.60), ("pasta", 0.60), ("spaghetti", 0.60),
    ("protein powder", 0.42), ("whey", 0.42), ("casein", 0.42),
    ("grated cheese", 0.38), ("shredded cheese", 0.38), ("parmesan", 0.42), ("cheese", 0.38),
    ("granola", 0.42), ("cereal", 0.42), ("muesli", 0.38),
    ("cocoa", 0.41), ("cacao", 0.41),
    ("peanut", 0.58), ("almond", 0.58), ("cashew", 0.58), ("walnut", 0.51), ("nuts", 0.58),
    ("couscous", 0.72), ("quinoa", 0.72),  # cooked
    ("lentils", 0.85), ("chickpeas", 0.80), ("beans", 0.85),  # cooked/canned
    ("salt", 1.22), ("corn", 0.72),
]

# Per-piece weights (grams) for count-based ("piece") foods, keyed by substring.
# Ordered most-specific-first (medium egg before the generic egg). Fallback for
# an unmatched piece is 100g with estimated=True.
_PIECE_WEIGHTS = [
    ("medium egg", 44.0), ("large egg", 50.0), ("egg white", 33.0), ("egg", 50.0),
    ("banana", 118.0), ("apple", 182.0), ("orange", 131.0),
    ("slice of bread", 30.0), ("bread", 30.0), ("tortilla", 45.0),
    ("scoop", 30.0),
]


def _density_for(food_name: str):
    """Return (density_g_per_ml, used_default). used_default=True means no keyword
    matched and DEFAULT_DENSITY was substituted (caller flags as estimated)."""
    lower = (food_name or "").lower()
    for kw, dens in _DENSITY_TABLE:
        if kw in lower:
            return dens, False
    return DEFAULT_DENSITY, True


def _piece_weight_for(food_name: str):
    """Return (grams_per_piece, matched). matched=False → 100g fallback."""
    lower = (food_name or "").lower()
    for kw, grams in _PIECE_WEIGHTS:
        if kw in lower:
            return grams, True
    return 100.0, False


def convert_to_grams(food_name: str, amount: float, unit: str):
    """Convert a household measure to grams.

    Returns (grams: float, estimated: bool), or raises ValueError for a bad
    amount/unit so the endpoint can answer 400. `estimated` is True only when we
    fell back to DEFAULT_DENSITY (volume) or the 100g piece fallback — a matched
    density or an exact mass unit is not flagged.
    """
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")
    if amt <= 0:
        raise ValueError("amount must be > 0")

    u = (unit or "").strip().lower().replace(" ", "_")
    if u in ("floz", "fluid_ounce", "fluid_ounces"):
        u = "fl_oz"
    if u in ("gram", "grams"):
        u = "g"
    if u in ("milliliter", "milliliters", "millilitre", "millilitres"):
        u = "ml"

    if u in MASS_TO_G:                       # exact, food-independent
        return round(amt * MASS_TO_G[u], 1), False
    if u == "scoop":                         # fixed 30g alias
        return round(amt * 30.0, 1), False
    if u == "piece":
        grams_each, matched = _piece_weight_for(food_name)
        return round(amt * grams_each, 1), (not matched)
    if u in VOLUME_TO_ML:
        ml = amt * VOLUME_TO_ML[u]
        density, used_default = _density_for(food_name)
        return round(ml * density, 1), used_default
    raise ValueError(f"unknown unit: {unit}")


def _fdc_portions(food: dict):
    """Extract USDA household portions from an FDC search hit as
    [{label, gram_weight}]. Survey (FNDDS) foods carry these in `foodMeasures`
    (disseminationText + gramWeight); SR Legacy / Foundation usually don't.
    Drops noise ("Quantity not specified") and entries without a real weight."""
    out, seen = [], set()
    for m in food.get("foodMeasures") or []:
        label = (m.get("disseminationText") or m.get("measureUnitName") or "").strip()
        gw = _num(m.get("gramWeight"))
        if not label or gw is None or gw <= 0:
            continue
        low = label.lower()
        if "not specified" in low or low in seen:
            continue
        seen.add(low)
        out.append({"label": label, "gram_weight": round(gw, 1)})
        if len(out) >= 6:
            break
    return out


def _search_fdc(q: str, limit: int):
    """Query USDA FoodData Central. Returns a list of parsed per-100g foods
    (possibly empty) when the API is reached, or None on a transient failure
    (network / HTTP error / malformed JSON) so the caller can fall back and avoid
    caching an outage as 'no results'."""
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
        return None
    out = []
    for food in (data.get("foods") or []):
        parsed = _parse_fdc_food(food)
        if parsed:
            out.append(parsed)
        if len(out) >= limit:
            break
    return out


def _parse_off_food(item: dict):
    """Map one Open Food Facts search product to our per-100g shape, or None if
    it has no name. OFF nutriment keys are per-100g with '-' separators and
    values that may be strings or absent; kcal falls back to kJ→kcal then Atwater
    so items carrying only kJ or only macros still surface."""
    name = (item.get("product_name") or "").strip()
    if not name:
        return None
    nutr = item.get("nutriments") or {}
    protein = _num(nutr.get("proteins_100g")) or 0.0
    carbs = _num(nutr.get("carbohydrates_100g")) or 0.0
    fat = _num(nutr.get("fat_100g")) or 0.0
    kcal = _num(nutr.get("energy-kcal_100g"))
    if kcal is None:
        kj = _num(nutr.get("energy_100g"))
        kcal = round(kj / 4.184, 1) if kj is not None else round(
            protein * 4 + carbs * 4 + fat * 9, 1)
    if name.isupper():
        name = name.title()
    return {
        "food_name": name,
        "protein_per_100g": round(max(0.0, protein), 1),
        "carbs_per_100g": round(max(0.0, carbs), 1),
        "fat_per_100g": round(max(0.0, fat), 1),
        "kcal_per_100g": round(max(0.0, kcal), 1),
        # OFF search doesn't return household portions; UI falls back to its
        # density/measure table. Kept for shape-parity with USDA results.
        "portions": [],
        "source": "open_food_facts",
        "barcode": (item.get("code") or ""),
    }


def search_open_food_facts(q: str, limit: int = 10):
    """Full-text search Open Food Facts for foods matching `q` (fallback for the
    regional/branded items USDA misses). Returns a list of per-100g foods
    (possibly empty) when reached, or None on a transient failure. Products with
    no usable energy are skipped so the dropdown never shows blank macros."""
    params = {
        "search_terms": q,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": max(1, min(25, limit)),
        "fields": "product_name,nutriments,code",
    }
    try:
        r = requests.get(_OFF_SEARCH_URL, params=params, headers=_HEADERS,
                         timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    out = []
    for item in (data.get("products") or []):
        parsed = _parse_off_food(item)
        if parsed and parsed["kcal_per_100g"] > 0:
            out.append(parsed)
        if len(out) >= limit:
            break
    return out


def search_foods(query: str, limit: int = 10):
    """Search whole foods matching `query`, USDA first then Open Food Facts.

    Returns up to `limit` foods as
    [{food_name, protein_per_100g, carbs_per_100g, fat_per_100g, kcal_per_100g,
      portions, source}] ordered by the winning source's relevance. USDA (curated
    whole-food data) is tried first; if it returns zero hits, Open Food Facts is
    queried as a fallback for the regional/branded items USDA misses. Results are
    cached for 24h. Transient failures (network/HTTP/JSON) are never cached, so an
    outage retries next call instead of pinning [] for a day. The search UI never
    blanks — a total miss still leaves previous foods / barcode / manual entry."""
    q = (query or "").strip()
    if len(q) < 2:
        return []

    key = q.lower()
    hit = _search_cache.get(key)
    if hit and (time.time() - hit[0]) < _SEARCH_TTL:
        return hit[1][:limit]

    usda = _search_fdc(q, limit)
    if usda:
        _search_cache[key] = (time.time(), usda)
        return usda[:limit]

    logger.info("USDA returned no results for %r, trying Open Food Facts", q)
    off = search_open_food_facts(q, limit)
    if off:
        _search_cache[key] = (time.time(), off)
        return off[:limit]

    # Cache an empty result only when BOTH sources were actually reached (both
    # returned a list, not None). A transient failure on either side must not be
    # remembered as "no such food" for 24h.
    if usda is not None and off is not None:
        logger.info("No results for %r from USDA or Open Food Facts", q)
        _search_cache[key] = (time.time(), [])
    return []
