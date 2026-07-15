"""Exercise matching + session-aware suggestion logic for /gym's inline
"Try Something New" discovery.

Two jobs, both driven only by the local ``exercises`` catalogue cache (never a
network call at request time):

1. ``build_gym_gif_map`` — the 43-row curated gym_exercises library uses its own
   names ("Barbell Row") that rarely equal the dataset's ("barbell bent over
   row"); only ~8/43 match on a plain normalise. So we match with token overlap
   constrained to the muscle-mapped category, giving every gym exercise its best
   demo GIF (~40/43; the rest fall back to a clean placeholder).

2. ``suggest_exercises`` — rank catalogue exercises by relevance to the CURRENT
   session: muscle-match first, then novelty (never logged), then staleness
   (>30 days), excluding whatever is already in today's session.

The gym-library ⇄ catalogue vocabulary gap is bridged by MUSCLE_MAP below.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import database as db

# Catalogue equipment values we treat as cardio when bridging into the gym log.
CARDIO_EQUIPMENT = {"stationary bike", "elliptical machine", "stepmill machine",
                    "skierg machine", "upper body ergometer"}

# ── Vocabulary bridge ────────────────────────────────────────────────────────
# gym_exercises.muscle_group (gym convention) → catalogue exercises.category
# (bodybuilding-dataset convention) + the target_muscle values that refine it.
# ``targets=None`` means "any target in this category". biceps/triceps share the
# "upper arms" category but split on target; quads/hamstrings share "upper legs".
#
# Keys fall in two tiers. The BROAD tier is the vocabulary gym_exercises
# .muscle_group actually stores, so a /gym session always resolves against it —
# never rename or drop these. The GRANULAR tier is additive: extra keys a caller
# may ask for by name to narrow a broad group. Every ``targets`` value below is
# a target_muscle that exists in the dataset; a value that matches no row would
# silently return nothing, so verify against the table before adding one.
MUSCLE_MAP = {
    # ── Broad (gym_exercises.muscle_group) ──
    # chest and shoulders stay whole: the dataset files all 158 chest rows under
    # the single target "pectorals" and all 143 shoulder rows under "delts",
    # so there is no finer split to make.
    "chest":      {"category": "chest",      "targets": None},
    "back":       {"category": "back",       "targets": None},
    "shoulders":  {"category": "shoulders",  "targets": None},
    "biceps":     {"category": "upper arms", "targets": {"biceps"}},
    "triceps":    {"category": "upper arms", "targets": {"triceps"}},
    "quads":      {"category": "upper legs",
                   "targets": {"quads", "glutes", "abductors", "adductors"}},
    "hamstrings": {"category": "upper legs", "targets": {"hamstrings", "glutes"}},
    "calves":     {"category": "lower legs", "targets": None},
    "core":       {"category": "waist",      "targets": None},
    "cardio":     {"category": "cardio",     "targets": None},
    # ── Granular ──
    # These four partition "back" exactly (81+88+15+19 = all 203 back rows), so
    # asking for lats no longer buries pulldowns under rows and shrugs.
    "lats":       {"category": "back", "targets": {"lats"}},
    "upper back": {"category": "back", "targets": {"upper back"}},
    "traps":      {"category": "back", "targets": {"traps"}},
    # The dataset has no "lower back"; it files that work (back extensions,
    # straight-leg deadlifts) under "spine". Accept both names for it.
    "lower back": {"category": "back", "targets": {"spine"}},
    "spine":      {"category": "back", "targets": {"spine"}},
    # Forearms are category "lower arms", NOT "upper arms" — they were
    # previously listed as a target of "biceps", where the category check made
    # them unreachable, hiding all 37 from every caller.
    "forearms":   {"category": "lower arms", "targets": None},
    "glutes":     {"category": "upper legs", "targets": {"glutes"}},
}

# Balanced default when the session is empty AND there is no logged history yet.
_DEFAULT_MUSCLES = ["chest", "back", "quads", "shoulders"]

_STALE_DAYS = 30

# Equipment/brand words dropped before token comparison so "Barbell Row" still
# matches "barbell bent over row" on the meaningful token ("row").
_MATCH_STOP = {
    "the", "a", "with", "and", "of", "to", "machine", "seated", "standing",
    "barbell", "dumbbell", "cable", "smith", "ez", "olympic", "trap",
    "weighted", "assisted", "band", "bands", "lever", "leverage", "sled",
}


# ── Normalisation ────────────────────────────────────────────────────────────
def normalise_name(name: str) -> str:
    """Lowercase, drop parenthesised qualifiers like "(Barbell)", strip
    punctuation, collapse whitespace. "Bench Press (Barbell)" → "bench press"."""
    s = (name or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set:
    """Meaningful, singularised tokens of a name (stopwords removed)."""
    out = set()
    for w in normalise_name(name).split():
        if w.endswith("ies"):
            w = w[:-3] + "y"
        elif w.endswith("ses"):
            w = w[:-2]
        elif w.endswith("s") and len(w) > 3:
            w = w[:-1]
        if w and w not in _MATCH_STOP:
            out.add(w)
    return out


# ── GIF matching (gym library → catalogue) ───────────────────────────────────
_GIF_MAP_CACHE = None
_MATCH_THRESHOLD = 0.30


def _best_gif(gym_name, muscle_group, catalogue):
    """Best catalogue (gif_url, image_url) for a gym exercise, or (None, None).
    Candidates are restricted to the muscle-mapped category; scoring is token
    Jaccard plus a subset bonus when every gym token appears in the candidate."""
    spec = MUSCLE_MAP.get((muscle_group or "").lower())
    want_cat = spec["category"] if spec else None
    gtok = _tokens(gym_name)
    if not gtok:
        return None, None
    best, best_score = None, 0.0
    for row in catalogue:
        if want_cat and row.get("category") != want_cat:
            continue
        ctok = _tokens(row.get("name", ""))
        if not ctok:
            continue
        score = len(gtok & ctok) / len(gtok | ctok)
        if gtok <= ctok:
            score += 0.5
        if score > best_score:
            best_score, best = score, row
    if best and best_score >= _MATCH_THRESHOLD:
        return best.get("gif_url"), best.get("image_url")
    return None, None


def build_gym_gif_map(force: bool = False) -> dict:
    """{gym_exercise_id: {"gif_url", "image_url"}} for the curated library,
    matched to catalogue demo GIFs. Cached module-side (the library + catalogue
    change only on re-seed / re-sync); pass ``force=True`` to rebuild."""
    global _GIF_MAP_CACHE
    if _GIF_MAP_CACHE is not None and not force:
        return _GIF_MAP_CACHE
    catalogue = db.get_all_catalogue_min()
    out = {}
    for ex in db.get_all_exercises():
        gif, img = _best_gif(ex.get("name"), ex.get("muscle_group"), catalogue)
        if gif or img:
            out[ex["id"]] = {"gif_url": gif, "image_url": img}
    _GIF_MAP_CACHE = out
    return out


def enrich_gym_exercises_with_gifs(exercises: list) -> list:
    """Attach ``gif_url``/``image_url`` (best catalogue demo, or None) to each
    gym-library row in place, so the workout cards / how-to modal can show a GIF
    instead of a YouTube embed. Returns the same list for chaining."""
    gif_map = build_gym_gif_map()
    for ex in exercises:
        m = gif_map.get(ex.get("id")) or {}
        ex["gif_url"] = m.get("gif_url")
        ex["image_url"] = m.get("image_url")
    return exercises


# ── Suggestions ──────────────────────────────────────────────────────────────
def _days_since(date_str) -> int | None:
    """Whole days between a YYYY-MM-DD string and today, or None if unparseable."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (date.today() - d).days


def _target_muscles(session_muscles) -> list:
    """Resolve which gym muscle groups to suggest for. Prefer the current
    session's groups; else the most-trained historical group; else a balanced
    default. Only groups present in MUSCLE_MAP survive."""
    picked = [m.lower() for m in (session_muscles or [])
              if m and m.lower() in MUSCLE_MAP]
    if picked:
        seen, ordered = set(), []
        for m in picked:
            if m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered
    freq = [r["muscle_group"] for r in db.get_gym_muscle_frequency()
            if (r.get("muscle_group") or "").lower() in MUSCLE_MAP
            and r["muscle_group"] != "cardio"]
    if freq:
        return [freq[0].lower()]
    return list(_DEFAULT_MUSCLES)


def _matches_spec(row, muscle) -> bool:
    spec = MUSCLE_MAP[muscle]
    if row.get("category") != spec["category"]:
        return False
    targets = spec["targets"]
    return targets is None or (row.get("target_muscle") in targets)


def suggest_exercises(session_muscles=None, exclude_names=None, limit=12) -> dict:
    """Rank catalogue exercises by relevance to the current session.

    session_muscles: gym muscle groups already being trained today.
    exclude_names:   exercise names already in today's session (dropped).

    Score: novelty (never logged) > staleness (>30d) > recently trained, all
    within the muscle-matched pool. Returns {muscles, fallback, exercises:[...]}.
    """
    muscles = _target_muscles(session_muscles)
    fallback = not [m for m in (session_muscles or []) if m and m.lower() in MUSCLE_MAP]

    categories = list({MUSCLE_MAP[m]["category"] for m in muscles})
    pool = db.get_catalogue_by_categories(categories)

    excluded = {normalise_name(n) for n in (exclude_names or [])}
    history = {normalise_name(h["name"]): _days_since(h.get("last_date"))
               for h in db.get_gym_logged_history()}

    scored = []
    for row in pool:
        norm = normalise_name(row.get("name"))
        if norm in excluded:
            continue
        hit = next((m for m in muscles if _matches_spec(row, m)), None)
        if not hit:
            continue
        days = history.get(norm)
        if days is None:                       # never logged → the money case
            score, reason = 100, "new to you"
        elif days >= _STALE_DAYS:              # stale — worth revisiting
            score, reason = 60, f"not in {days}d"
        else:
            score, reason = 20, "recent"
        # Small boost so the exact group the user is on ranks above co-mapped
        # groups (e.g. training quads shouldn't surface only hamstrings).
        if session_muscles and hit == muscles[0]:
            score += 5
        # Stretches / mobility drills are lower-value as a "new exercise to try":
        # penalise by more than a novelty tier (40) so one never outranks a real
        # movement of the same freshness.
        if "stretch" in norm:
            score -= 50
        scored.append((score, row.get("name") or "", {
            "id": row.get("id"),
            "name": row.get("name"),
            "muscle": hit,
            "category": row.get("category"),
            "target_muscle": row.get("target_muscle"),
            "equipment": row.get("equipment"),
            "gif_url": row.get("gif_url"),
            "image_url": row.get("image_url"),
            "instructions": row.get("instructions"),
            "is_home_friendly": bool(row.get("is_home_friendly")),
            "reason": reason,
        }))

    # Highest score first; name as a stable, deterministic tiebreak.
    scored.sort(key=lambda t: (-t[0], t[1].lower()))
    return {
        "muscles": muscles,
        "fallback": fallback,
        "exercises": [item for _, _, item in scored[:limit]],
    }
