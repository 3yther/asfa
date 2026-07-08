"""Nutrition helpers (Tier 7): barcode lookup + portion estimation.

Barcode data comes from Open Food Facts, a free public API with no key. We only
read the per-100g macros; the caller scales them to whatever portion the user
actually ate. Every network path fails soft (returns None) so a lookup miss or
an outage just drops the user back to manual entry.
"""
import requests

_OFF_URL = "https://world.openfoodfacts.org/api/v0/product/{code}.json"
_TIMEOUT = 5
# Open Food Facts blocks requests without a descriptive User-Agent (403), per
# their API policy: identify the app + a contact.
_HEADERS = {"User-Agent": "ASFA-Dashboard/1.0 (nutrition; ami.salax@gmail.com)"}


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
