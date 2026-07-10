"""Cardio → step-equivalent conversion (pure, testable, no DB).

Two cardio modes convert to a step count so treadmill and outdoor-bike sessions
land in the same "ASFA STEPS" total as manually-logged walking. Deliberately
NARROW: only treadmill and outdoor bike — no stationary bike, rowing, or
elliptical (the user doesn't do them; we don't model what we can't ground).

Both converters return (steps, note):
  - treadmill: a real stride-length estimate, note is "".
  - bike: an EFFORT-EQUIVALENT (cycling has no stride), note says so plainly.

All inputs are clamped to sane ranges and rounded to the nearest 10 steps —
anything finer is false precision for an estimate. Bad inputs raise ValueError
(the caller turns that into a 400); nothing is silently coerced past a clamp.
"""

# Treadmill stride grows with speed (short walking steps, long running strides).
# Linear approximation, deliberately simple so ANY speed works without a table:
#   stride_m = _STRIDE_BASE + _STRIDE_PER_KPH * kph
#   → ~0.65 m at 6 kph (walk), ~1.0 m at 13 kph (run).
_STRIDE_BASE = 0.35
_STRIDE_PER_KPH = 0.05

# Incline: +10% steps per 2% grade → +5% per 1%. Capped at 15% grade.
_INCLINE_PER_PCT = 0.05
_INCLINE_MAX_PCT = 15.0

_KPH_MIN, _KPH_MAX = 1.0, 20.0
_MIN_MIN, _MIN_MAX = 1.0, 300.0

# Bike effort-equivalent: base ~1000 "steps" per km, rising with speed (harder
# effort per km the faster you push), and 1.3× on hilly terrain.
_BIKE_BASE_STEPS_PER_KM = 1000.0
_BIKE_STEPS_PER_KPH = 20.0
_BIKE_PIVOT_KPH = 15.0
_BIKE_TERRAIN = {"flat": 1.0, "hilly": 1.3}

_DIST_MIN, _DIST_MAX = 0.1, 300.0
_BIKE_KPH_MIN, _BIKE_KPH_MAX = 5.0, 60.0


def _num(value, name):
    """Coerce to float or raise ValueError(name...). Rejects bools and blanks so
    a stray True or "" never sneaks through as 1.0/0.0."""
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{name} is required")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")


def _require_range(value, lo, hi, name):
    """Value must already sit within [lo, hi]. Out-of-range is REJECTED, not
    clamped — a treadmill logged at kph=0 or 999 is a mistake we surface."""
    if value < lo or value > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return value


def _round10(x):
    """Round to the nearest 10 steps (never below 10 for a valid session)."""
    return max(10, int(round(x / 10.0) * 10))


def treadmill_to_steps(minutes, kph, incline_pct=0):
    """Treadmill session → estimated steps via stride-length physics.

    Args:
        minutes: session length (1–300).
        kph: belt speed (1–20).
        incline_pct: grade %, adds ~10% steps per 2% (capped 15%); default 0.

    Returns (steps:int, note:str). note is "" — this is a measurement-grade
    estimate, not an effort proxy. Raises ValueError on non-numeric or
    out-of-range inputs (never silently clamped).
    """
    minutes = _require_range(_num(minutes, "minutes"), _MIN_MIN, _MIN_MAX, "minutes")
    kph = _require_range(_num(kph, "kph"), _KPH_MIN, _KPH_MAX, "kph")
    # incline defaults to 0 and is the one field we clamp (0..15) rather than
    # reject: a grade above the treadmill's max just tops out at the cap.
    incline = _num(incline_pct if incline_pct not in (None, "") else 0, "incline_pct")
    incline = max(0.0, min(_INCLINE_MAX_PCT, incline))

    stride_m = _STRIDE_BASE + _STRIDE_PER_KPH * kph
    distance_m = kph * 1000.0 / 60.0 * minutes
    steps = distance_m / stride_m
    steps *= (1 + _INCLINE_PER_PCT * incline)
    return _round10(steps), ""


def bike_to_steps(distance_km, kph, terrain):
    """Outdoor-bike session → step EFFORT-EQUIVALENT.

    Cycling has no footstrike, so this is NOT a measured step count — it maps
    ride effort onto a comparable step figure so a bike ride and a walk sit in
    the same daily total. The returned note says exactly that.

    Args:
        distance_km: ride distance (0.1–300).
        kph: average speed (5–60); faster = more effort per km.
        terrain: "flat" (1.0×) or "hilly" (1.3×).

    Returns (steps:int, note:str) where note is "effort-equivalent, not measured".
    Raises ValueError on bad numbers, out-of-range values, or unknown terrain.
    """
    distance = _require_range(_num(distance_km, "distance_km"), _DIST_MIN, _DIST_MAX, "distance_km")
    kph = _require_range(_num(kph, "kph"), _BIKE_KPH_MIN, _BIKE_KPH_MAX, "kph")
    terrain = str(terrain or "").strip().lower()
    if terrain not in _BIKE_TERRAIN:
        raise ValueError("terrain must be flat or hilly")

    steps_per_km = _BIKE_BASE_STEPS_PER_KM + (kph - _BIKE_PIVOT_KPH) * _BIKE_STEPS_PER_KPH
    steps = distance * steps_per_km * _BIKE_TERRAIN[terrain]
    return _round10(steps), "effort-equivalent, not measured"
