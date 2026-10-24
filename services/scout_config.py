"""Scout's search and relevance configuration, and the filtering built on it.

One home for the lists, shared by the production scan
(`services/apprenticeships.py`) and the measurement tool
(`scripts/scout_coverage_baseline.py`). If the two filtered differently, the
before/after numbers would describe something the app doesn't do.

EDITING WITHOUT A CODE CHANGE
-----------------------------
The defaults below are overridden by a JSON file, so the lists can be tuned
without touching Python:

    config/scout_filters.json          (or $SCOUT_FILTERS_PATH)

    {
      "title_exclusions":    ["security guard", "commis chef"],
      "employer_exclusions": ["greggs"],
      "search_keywords":     ["cyber", "software"],
      "digital_standards":   ["software developer"],
      "strong_digital_terms": ["cyber"],
      "digital_terms":       ["security"]
    }

Any key may be omitted. By default a list REPLACES its built-in; prefix a key
with "+" ("+title_exclusions") to append to the built-in instead, which is
usually what you want for exclusions. The file is read once per process and
re-read if its mtime changes, so an edit takes effect on the next scan without
a redeploy.

THE BIAS IS DELIBERATE
----------------------
Missing a real apprenticeship costs more than showing noise, so every rule here
fails towards keeping a listing:

  * a recognised Digital apprenticeship standard always keeps the listing, and
    no exclusion can override it — the standard is chosen by the employer from
    a national catalogue and does not depend on what we searched for, which
    makes it the only signal here that isn't circular;
  * a title exclusion is overridden by an unambiguous technical word, so
    "Digital Project Manager" survives a "project manager" exclusion;
  * anything the rules can't speak to is kept, and every exclusion is logged
    with its reason so it can be audited rather than guessed at.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger("asfa.scout_config")

# ── Search terms sent to gov.uk ──────────────────────────────────────────────

# The six phrases Scout shipped with, kept separately so the measurement tool
# can attribute which vacancies only the widened terms find.
ORIGINAL_SEARCH_PHRASES = (
    "cyber security",
    "software developer",
    "data",
    "cloud",
    "network engineer",
    "devops",
)

# NARROW terms: specific enough that nearly everything they return is relevant.
# These run in the PRODUCTION scan alongside the route sweep, because the sweep
# cannot see a listing gov.uk has not tagged to the Digital route — notably the
# off-site Civil Service cross-posts, which carry no level and no course.
NARROW_SEARCH_KEYWORDS = (
    "cyber",
    "cyber security",
    "software developer",
    "developer",
    "devops",
    "cloud",
    "network engineer",
    "data",
    "computing",
    "software",
)

# BROAD terms: single words that return mostly noise. They stay in the
# measurement tool's audit mode and are deliberately OUT of the production scan.
# The 1D run showed the keyword passes found 34 vacancies, every one of them
# already inside the route sweep's 42 — so the broad terms were paying a large
# request budget and an exclusion-log tail for nothing the sweep did not have.
# Keeping them in the audit is what will show if that ever stops being true.
BROAD_SEARCH_KEYWORDS = (
    "engineer",
    "information",
    "IT",
    "technology",
    "digital",
    "systems",
    "security",
)

# Every term the ADDED keyword list introduced, narrow and broad together.
ADDED_SEARCH_KEYWORDS = tuple(
    t for t in NARROW_SEARCH_KEYWORDS + BROAD_SEARCH_KEYWORDS
    if t not in ORIGINAL_SEARCH_PHRASES)

# What the production scan searches: the narrow terms only.
DEFAULT_SEARCH_KEYWORDS = tuple(dict.fromkeys(NARROW_SEARCH_KEYWORDS))

# What the audit measures: narrow + broad + the original six, de-duplicated and
# in a stable order.
AUDIT_SEARCH_KEYWORDS = tuple(dict.fromkeys(
    ORIGINAL_SEARCH_PHRASES + NARROW_SEARCH_KEYWORDS + BROAD_SEARCH_KEYWORDS))

# ── Relevance signals ────────────────────────────────────────────────────────

# Apprenticeship standards on the Digital route, as they appear in gov.uk's
# "Training course" field (level suffix stripped, lower-cased). The strongest
# signal available and the only query-independent one.
#
# NOT verified against gov.uk's own route filter — that needs the live site's
# filter field names. `route_unclassified` in the baseline reports how much this
# list fails to cover, so its blind spots stay visible.
DEFAULT_DIGITAL_STANDARDS = (
    "cyber security technologist",
    "cyber security technician",
    "cyber security engineer",
    "cyber security technical professional",
    "software developer",
    "software development technician",
    "software engineer",
    "software tester",
    "data analyst",
    "data engineer",
    "data scientist",
    "data technician",
    "network engineer",
    "network cable installer",
    "digital and technology solutions professional",
    "digital and technology solutions specialist",
    "digital support technician",
    "digital service designer",
    "digital user experience professional",
    "digital product manager",
    "digital marketer",
    "information communications technician",
    "it solutions technician",
    "it support technician",
    "computer science",
    "unified communications technician",
    "unified communications trouble shooter",
    "devops engineer",
    "artificial intelligence data specialist",
    "machine learning engineer",
    "cloud engineer",
    "cloud network specialist",
    "games programmer",
    "games developer",
    # Added in Phase 1D. The route sweep returned Business Analyst at Level 4
    # as a Digital-route role while this list did not have it, so `judge` was
    # excluding it as a non-digital standard — the curated-list risk, realised.
    "business analyst",
    "data communications technician",
    "digital accessibility specialist",
)

# Words that mean "this is a technical role" on their own. Presence of one
# OVERRIDES a title exclusion, which is how "Digital Project Manager" survives
# a "project manager" exclusion.
DEFAULT_STRONG_DIGITAL_TERMS = (
    "cyber", "software", "digital", "computing", "computer", "developer",
    "development", "devops", "data", "cloud", "programmer", "programming",
    "it", "i.t.", "ict", "coding", "infrastructure", "database", "web",
    "automation", "ai", "machine learning",
)

# Ambiguous on their own: every one of these also appears in roles that are not
# digital at all ("security guard", "engineering operative", "information
# officer"). They count towards the digital flag but CANNOT rescue a listing
# from a title exclusion — that distinction is what stops "Security Guard
# Apprentice" being kept on the word "security".
DEFAULT_WEAK_DIGITAL_TERMS = (
    "security", "technology", "tech", "systems", "system", "information",
    "engineer", "engineering", "network", "networks", "analyst", "technician",
    "support", "solutions",
)

# ── Noise filtering ──────────────────────────────────────────────────────────

# Title phrases that mark a listing as not-digital. Phrases, not single words,
# so they are specific: "security guard" cannot match "Cyber Security", and
# "project manager" is overridden by a strong term (see above).
DEFAULT_TITLE_EXCLUSIONS = (
    # physical security — the known "Security Guard" false positive
    "security guard", "security officer", "security operative",
    "door supervisor", "close protection",
    # the known "Food Hub Project Manager" false positive
    "project manager", "project management",
    # catering and hospitality
    "commis chef", "chef de partie", "chef", "kitchen assistant",
    "kitchen porter", "hospitality", "barista", "food and beverage",
    "front of house", "waiting staff",
    # care, health, education
    "care assistant", "carer", "healthcare assistant", "dental nurse",
    "veterinary", "pharmacy", "teaching assistant", "early years",
    "nursery practitioner", "youth worker",
    # trades and construction
    "bricklayer", "bricklaying", "carpentry", "joinery", "plastering",
    "plumbing", "roofing", "scaffolding", "groundworker", "painter",
    "welding", "fabrication",
    # personal services
    "hairdressing", "barbering", "beauty therapist", "nail technician",
    # retail, warehouse, transport
    "retail assistant", "sales assistant", "customer service assistant",
    "warehouse operative", "forklift", "driver", "vehicle technician",
    "automotive", "bus driver", "train driver",
    # business admin and finance that aren't digital roles
    "business administrator", "business administration", "payroll",
    "accounts assistant", "accountancy", "bookkeeping", "human resources",
    "recruitment consultant",
    # land and animals
    "agriculture", "horticulture", "arboriculture", "gamekeeping",
    "animal care", "equine",
)

# Employers that never post a role Scout is looking for. Matched as a substring
# of the employer name, case-insensitively, so keep entries distinctive.
#
# Deliberately empty by default. An employer exclusion is a blunt instrument —
# a large employer posts across every route — so this is for the user to fill
# from what the exclusion log actually shows, not for guesses shipped in code.
DEFAULT_EMPLOYER_EXCLUSIONS = ()

# Employers whose listings must never be silently dropped by the standard rule.
# A defence employer posting against an odd standard is exactly the case worth
# a human glance, so those listings are KEPT and surfaced in a "review" group
# with the standard named, rather than excluded.
#
# This is the CONFIG watchlist, used by `judge`, which is a pure function and
# cannot query the database. The separate Employer table still drives the star
# on alerts. Replace this list with your own employers in
# config/scout_filters.json; matched as a case-insensitive substring, so keep
# entries distinctive.
# NOTE: once the spreadsheet is imported, the programme companies in
# scout_programmes take over as the watchlist (see
# database.get_scout_programme_companies and the `watchlist` argument to
# `judge`). This list is the fallback for a fresh install with no import yet.
DEFAULT_WATCHLIST_EMPLOYERS = (
    "thales",
    "bae systems",
    "babcock",
    "airbus",
    "leonardo",
    "gchq",
    "ncsc",
    "dstl",
    "cavendish nuclear",
)

# ── Reasons recorded in the exclusion log ────────────────────────────────────

REASON_TITLE = "title_excluded"
REASON_EMPLOYER = "employer_excluded"
REASON_NOT_DIGITAL = "no_digital_signal"
# The listing states an apprenticeship standard and it is not a Digital one.
# Stronger than a title guess, because the standard is what the apprentice
# actually trains towards.
REASON_NON_DIGITAL_STANDARD = "non_digital_standard"

KEEP_DIGITAL_STANDARD = "digital_standard"
KEEP_STRONG_TERM = "strong_digital_term"
KEEP_UNSURE = "kept_unsure"
# Kept despite a non-digital standard, because the employer is watched. Needs a
# human glance, so it is surfaced in its own group rather than mixed in.
KEEP_WATCHLIST_REVIEW = "watchlist_review"


# ── Loading the overrides ────────────────────────────────────────────────────

_LOCK = threading.Lock()
_CACHE: dict = {}
_CACHE_STAMP = None

# Pages per search. Raised from 5 to 20 once the widened keyword list made the
# cap bind: 5 of 17 searches stopped at the cap, leaving 201 of 605 result slots
# unfetched. At 1 request/second a 20-page search is at most 20s, and the loop
# stops early as soon as the site's own total is accounted for, so the cap is a
# ceiling for the pathological case rather than a budget to be spent.
DEFAULT_MAX_PAGES = 20

# ── gov.uk's own filters (field names verified against the live site) ────────

# routeIds: the apprenticeship route. 7 is Digital.
DIGITAL_ROUTE_ID = 7

# levelIds: repeated once per level. 4-7 is "Level 4 and above", i.e. higher and
# degree apprenticeships.
DEFAULT_LEVEL_IDS = (4, 5, 6, 7)

# There is deliberately NO distance or postcode setting here. Scout used to run
# a second sweep centred on a home postcode and tag what it found as near_home.
# That was removed: the apprenticeship is worth relocating for, so a listing
# being close to where the user lives today is not a reason to rank it higher,
# and a radius centred on home is a thumb on the scale towards staying put.
#
# The nationwide sweep is the only sweep. Location is shown on a card and can be
# searched for by name (see services/scout_roles.py), which is what you want
# when you are deciding where to move TO rather than filtering for near.

_SCALARS = {
    "max_pages": DEFAULT_MAX_PAGES,
}

_KEYS = {
    "search_keywords": DEFAULT_SEARCH_KEYWORDS,
    "audit_keywords": AUDIT_SEARCH_KEYWORDS,
    "digital_standards": DEFAULT_DIGITAL_STANDARDS,
    "strong_digital_terms": DEFAULT_STRONG_DIGITAL_TERMS,
    "weak_digital_terms": DEFAULT_WEAK_DIGITAL_TERMS,
    "title_exclusions": DEFAULT_TITLE_EXCLUSIONS,
    "employer_exclusions": DEFAULT_EMPLOYER_EXCLUSIONS,
    "watchlist_employers": DEFAULT_WATCHLIST_EMPLOYERS,
}


def config_path() -> str:
    return (os.environ.get("SCOUT_FILTERS_PATH")
            or os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "config", "scout_filters.json"))


def _load_overrides(path: str) -> dict:
    """Read the JSON overrides. A broken file must not stop a scan."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("scout_config: ignoring %s (%s)", path, e)
        return {}
    if not isinstance(raw, dict):
        logger.warning("scout_config: %s is not a JSON object — ignoring", path)
        return {}

    out = {}
    for key, default in _KEYS.items():
        if key in raw and isinstance(raw[key], list):
            out[key] = tuple(str(v) for v in raw[key] if str(v).strip())
        if f"+{key}" in raw and isinstance(raw[f"+{key}"], list):
            extra = tuple(str(v) for v in raw[f"+{key}"] if str(v).strip())
            out[key] = tuple(out.get(key, default)) + extra
    for key in _SCALARS:
        value = raw.get(key)
        if isinstance(value, bool):
            continue                      # bool is an int subclass; not a count
        if isinstance(value, int) and value > 0:
            out[key] = value
        elif value is not None:
            logger.warning("scout_config: %s must be a positive integer, "
                           "ignoring %r", key, value)
    return out


# How long the database layer may be served from cache before it is re-checked.
# `settings()` is consulted several times per listing — thousands of times in a
# scan — so the overrides table cannot be read on every call. The stamp query
# is cheap but not free, so it runs at most this often; an explicit refresh
# after a write makes a correction visible immediately regardless.
_DB_STAMP_TTL = 30.0
_DB_STAMP = None
_DB_STAMP_AT = 0.0
_DB_STAMP_FOR = None


def _db_stamp(force: bool = False):
    """A fingerprint of the overrides table, re-read at most every TTL.

    The TTL bounds how long an out-of-band write (a direct SQL edit, a second
    process) stays invisible; a correction made through `allow_forever` is
    visible at once, because that refreshes explicitly.

    A change of DATABASE is never deferred, though: the identity check below is
    a string compare with no query behind it, and serving one database's
    overrides against another's rows is wrong rather than merely stale.

    Returns None when the database cannot be reached — deliberately not the
    (…, 0, 0) an empty table produces, so an unavailable database leaves the
    file layer in charge instead of looking like "every override was deleted".
    """
    global _DB_STAMP, _DB_STAMP_AT, _DB_STAMP_FOR
    try:
        import database as db
        db_id = db.current_database_id()
    except Exception as e:                              # pragma: no cover
        logger.debug("scout_config: no database module (%s)", e)
        return None

    now = time.monotonic()
    if (not force and _DB_STAMP_AT and db_id == _DB_STAMP_FOR
            and (now - _DB_STAMP_AT) < _DB_STAMP_TTL):
        return _DB_STAMP
    try:
        _DB_STAMP = db.scout_filter_overrides_stamp()
    except Exception as e:
        logger.debug("scout_config: overrides table unavailable (%s)", e)
        _DB_STAMP = None
    _DB_STAMP_AT, _DB_STAMP_FOR = now, db_id
    return _DB_STAMP


def _apply_db_overrides(merged: dict) -> dict:
    """Apply the stored corrections over the file layer.

    Replayed oldest-first so a later decision wins, and matched case-insensitively
    because the values come from scraped employer names and course titles, where
    the user's spelling and the site's rarely agree.
    """
    try:
        import database as db
        rows = db.list_scout_filter_overrides()
    except Exception as e:
        logger.debug("scout_config: could not read overrides (%s)", e)
        return merged

    for row in rows:
        key = row.get("list_key")
        value = " ".join((row.get("value") or "").split())
        if key not in _KEYS or not value:
            continue
        current = [str(v) for v in merged.get(key, _KEYS[key])]
        fold = value.casefold()
        kept = [v for v in current if v.strip().casefold() != fold]
        if (row.get("op") or "add") == "add":
            merged[key] = kept + [value]
        else:
            merged[key] = kept
    return merged


def settings(refresh: bool = False) -> dict:
    """The effective configuration, in three layers.

        1. the module defaults above
        2. config/scout_filters.json — the hand-editable file layer
        3. `scout_filter_overrides` — the corrections made from the Excluded
           screen, applied as a delta over both

    The database is last so a correction wins, and a delta rather than a
    replacement so the file stays meaningful: edit the file and the stored
    corrections still ride on top of the new value.
    """
    global _CACHE, _CACHE_STAMP
    path = config_path()
    try:
        file_stamp = os.path.getmtime(path)
    except OSError:
        file_stamp = None
    stamp = (path, file_stamp, _db_stamp(force=refresh))

    with _LOCK:
        if refresh or not _CACHE or _CACHE_STAMP != stamp:
            merged = dict(_KEYS)
            merged.update(_SCALARS)
            merged.update(_load_overrides(path))
            merged = _apply_db_overrides(merged)
            # Normalise the lists once so the matchers don't re-lower on every
            # listing; scalars are left as they are.
            _CACHE = {k: (v if k in _SCALARS else tuple(v))
                      for k, v in merged.items()}
            _CACHE_STAMP = stamp
        return _CACHE


def route_sweep_params() -> dict:
    """The digital-route sweep's query, as verified against the live site.

        ?routeIds=7&levelIds=4&levelIds=5&levelIds=6&levelIds=7&sort=AgeAsc

    No searchTerm: the route filter is what selects the listings, so one pass
    covers the whole Digital Level 4+ route rather than depending on which words
    happen to appear in a title. levelIds is a repeated parameter.
    """
    return {
        "routeIds": DIGITAL_ROUTE_ID,
        "levelIds": list(DEFAULT_LEVEL_IDS),
    }


def max_pages() -> int:
    """Pages to walk per search. SCOUT_MAX_PAGES wins, then the JSON config."""
    env = os.environ.get("SCOUT_MAX_PAGES")
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
            logger.warning("scout_config: SCOUT_MAX_PAGES must be > 0, got %r", env)
        except ValueError:
            logger.warning("scout_config: SCOUT_MAX_PAGES is not an integer: %r", env)
    return int(settings()["max_pages"])


def search_keywords() -> list:
    """Terms the PRODUCTION scan searches gov.uk with: the narrow ones.

    KEYWORD_SCANS still wins if it is set, so an operator can pin the search
    list from the environment without editing anything.
    """
    env = os.environ.get("KEYWORD_SCANS")
    if env:
        return [k.strip() for k in env.split(",") if k.strip()]
    return list(settings()["search_keywords"])


def audit_keywords() -> list:
    """Every term, narrow and broad, for the coverage measurement only.

    Kept separate from `search_keywords` so the audit can keep asking whether
    the broad terms would find anything the route sweep misses, without the
    production scan paying for the answer on every poll.
    """
    env = os.environ.get("KEYWORD_SCANS")
    if env:
        return [k.strip() for k in env.split(",") if k.strip()]
    configured = list(settings()["search_keywords"])
    broad = [t for t in settings()["audit_keywords"] if t not in configured]
    return configured + broad


# ── Matching ─────────────────────────────────────────────────────────────────

def _word_pattern(terms) -> re.Pattern:
    """Whole-word alternation over `terms`.

    Word boundaries are the point: a substring match on the two-letter term
    "IT" hits "Kitchen", "security" and "digital", which would make the widened
    keyword list useless. Terms are sorted longest-first so "machine learning"
    is tried before "machine".

    `\\b` does not fire after a trailing "." (both are non-word characters), so
    a term like "i.t." gets an explicit lookahead instead.
    """
    parts = []
    for term in sorted({t.strip().lower() for t in terms if t.strip()},
                       key=len, reverse=True):
        escaped = re.escape(term)
        if term.endswith("."):
            parts.append(rf"\b{escaped}(?!\w)")
        else:
            parts.append(rf"\b{escaped}\b")
    if not parts:
        return re.compile(r"(?!)")          # matches nothing
    return re.compile("(" + "|".join(parts) + ")", re.I)


_PATTERN_CACHE: dict = {}


def _pattern_for(key: str) -> re.Pattern:
    conf = settings()
    cached = _PATTERN_CACHE.get(key)
    terms = conf[key]
    if cached is None or cached[0] != terms:
        pattern = _word_pattern(terms)
        _PATTERN_CACHE[key] = (terms, pattern)
        return pattern
    return cached[1]


_COURSE_LEVEL_RE = re.compile(r"\s*[\(\[]?\s*(level|lvl)\s*\d+\s*[\)\]]?\s*$", re.I)

# Bracketed asides anywhere in a standard's name. gov.uk writes them mid-name as
# well as at the end — "Artificial intelligence (AI) data specialist",
# "Cyber security technical professional (integrated degree)" — so a trailing
# level strip alone left the middle ones in place and the match silently failed.
_COURSE_BRACKET_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")


def course_name(course: str) -> str:
    """A training-course string reduced to its standard name.

    Lower-cased, trailing level suffix removed, bracketed asides removed, inner
    whitespace collapsed.
    """
    text = _COURSE_LEVEL_RE.sub("", (course or "").strip())
    text = _COURSE_BRACKET_RE.sub(" ", text)
    return " ".join(text.split()).casefold()


def is_digital_standard(course: str) -> bool:
    """True when the apprenticeship standard is a recognised Digital one."""
    name = course_name(course)
    if not name:
        return False
    return any(name == s or name.startswith(s)
               for s in (x.lower() for x in settings()["digital_standards"]))


def strong_terms_in(text: str) -> list:
    return sorted({m.group(1).lower()
                   for m in _pattern_for("strong_digital_terms").finditer(text or "")})


def digital_terms_in(text: str) -> list:
    """Every digital term, strong or weak, appearing as a whole word."""
    found = set(strong_terms_in(text))
    found.update(m.group(1).lower()
                 for m in _pattern_for("weak_digital_terms").finditer(text or ""))
    return sorted(found)


def matched_title_exclusion(title: str):
    m = _pattern_for("title_exclusions").search(title or "")
    return m.group(1).lower() if m else None


def matched_watchlist_employer(employer: str, watchlist=None):
    """The watchlist entry this employer matches, or None.

    Substring, case-insensitive, for the same reason as the exclusion list: a
    company name is not a sentence, and "bae systems" should match
    "BAE SYSTEMS PLC".

    `watchlist` is the caller's own list, which is how the imported programmes
    take over from the placeholder in this module: `judge` stays a pure
    function of its arguments and cannot reach the database, so the scan passes
    the programme companies in. With nothing passed, the config list is used —
    which is what keeps this testable and what covers a fresh install before
    any import has run.
    """
    name = (employer or "").casefold()
    if not name:
        return None
    for term in (watchlist if watchlist is not None
                 else settings()["watchlist_employers"]):
        t = term.strip().casefold()
        if t and t in name:
            return t
    return None


def matched_employer_exclusion(employer: str):
    """Employer exclusions are substrings, not whole words.

    An employer name is not a sentence: "Greggs plc" should match "greggs", and
    requiring a word boundary around a company name is more surprising than
    helpful.
    """
    name = (employer or "").casefold()
    if not name:
        return None
    for term in settings()["employer_exclusions"]:
        t = term.strip().casefold()
        if t and t in name:
            return t
    return None


# ── The decision ─────────────────────────────────────────────────────────────

class Verdict:
    """Why a listing was kept or dropped. Carries the reason for the log."""

    __slots__ = ("keep", "reason", "detail", "digital", "terms",
                 "watchlist", "review")

    def __init__(self, keep, reason, detail="", digital=False, terms=(),
                 watchlist=None, review=False):
        self.keep = keep
        self.reason = reason
        self.detail = detail
        self.digital = digital
        self.terms = tuple(terms)
        # The watchlist entry matched, when one was what saved the listing.
        self.watchlist = watchlist
        # True when a human should look at this rather than trust the filter.
        self.review = review

    def __repr__(self):
        return (f"Verdict(keep={self.keep}, reason={self.reason!r}, "
                f"detail={self.detail!r}, digital={self.digital})")

    def as_dict(self):
        return {"keep": self.keep, "reason": self.reason, "detail": self.detail,
                "digital": self.digital, "terms": list(self.terms),
                "watchlist": self.watchlist, "review": self.review}


def judge(title: str, employer: str = "", course: str = "",
          watchlist=None) -> Verdict:
    """Decide whether a listing is a digital apprenticeship worth surfacing.

    Order matters, and it is ordered to fail towards keeping:

      1. a recognised Digital STANDARD keeps the listing outright. The employer
         picked it from a national catalogue, so it is both the strongest and
         the only query-independent signal — and no exclusion list overrides it,
         because a list of guessed phrases should not beat a stated fact;
      2. an excluded EMPLOYER drops it. The user maintains that list from the
         exclusion log, so it is theirs to be blunt with;
      3. a STATED NON-DIGITAL STANDARD drops it, UNLESS an unambiguous technical
         word is present. If the listing says the apprentice will train as a
         business administrator or a multi-channel marketer, that is what the
         apprenticeship is, whatever the search term was. This catches the bulk
         of what the widened keywords pull in — business administrator,
         multi-channel marketer, international freight forwarding, interior
         systems installer, supply chain warehouse operative — without needing
         a title phrase for each one. Checked BEFORE the title list because a
         stated standard is better evidence than a guessed phrase, so it gives
         the more informative reason in the log;
      4. an excluded TITLE drops it, UNLESS an unambiguous technical word is
         present. "Security Guard" has only the weak term "security" and goes;
         "Digital Project Manager" has "digital" and stays;
      5. anything else is kept. The `digital` flag records whether any digital
         term was seen, so a caller can rank without the filter having guessed.

    Steps 3 and 4 share the strong-term override, which is what keeps the rule
    from eating real roles: a digital standard is never overridden, and neither
    is an unambiguous technical title.
    """
    blob = f"{title or ''} {course or ''}"
    terms = digital_terms_in(blob)
    strong = strong_terms_in(blob)

    if is_digital_standard(course):
        return Verdict(True, KEEP_DIGITAL_STANDARD,
                       detail=course_name(course), digital=True, terms=terms)

    employer_hit = matched_employer_exclusion(employer)
    if employer_hit:
        return Verdict(False, REASON_EMPLOYER, detail=employer_hit,
                       digital=bool(terms), terms=terms)

    stated = course_name(course)
    if stated and not strong:
        # The standard is known (there is course text) and step 1 already
        # established it is not a Digital one.
        watched = matched_watchlist_employer(employer, watchlist=watchlist)
        if watched:
            # Never silently drop a watched employer's listing. A defence firm
            # posting against an odd standard is the case most worth a look.
            return Verdict(True, KEEP_WATCHLIST_REVIEW, detail=stated,
                           digital=bool(terms), terms=terms,
                           watchlist=watched, review=True)
        return Verdict(False, REASON_NON_DIGITAL_STANDARD, detail=stated,
                       digital=bool(terms), terms=terms)

    title_hit = matched_title_exclusion(title)
    if title_hit and not strong:
        return Verdict(False, REASON_TITLE, detail=title_hit,
                       digital=bool(terms), terms=terms)

    if strong:
        return Verdict(True, KEEP_STRONG_TERM, detail=",".join(strong),
                       digital=True, terms=terms)

    return Verdict(True, KEEP_UNSURE, detail=",".join(terms),
                   digital=bool(terms), terms=terms)


# ── Correcting a wrong exclusion, from the Excluded audit view ───────────────
#
# The exclusion lists are guesses about words, and the audit log is where a bad
# guess shows up. These write the JSON overrides rather than Python, which is
# the whole point of the overrides existing: a filter the user cannot correct
# without a deploy is a filter they will stop trusting.

ALLOW_KINDS = ("standard", "employer")


def _effective_matches(key: str, employer: str) -> list:
    """The `key` terms that would currently match this employer.

    `matched_employer_exclusion` is a substring test, so the term that caused
    the drop is often not the employer's full name — "greggs" excludes "Greggs
    plc". Removing exactly what WOULD match is what makes the button do what it
    says, rather than storing a name that was never on the list.
    """
    name = employer.casefold()
    return sorted({t.strip() for t in settings()[key]
                   if t.strip() and t.strip().casefold() in name})


def allow_forever(kind: str, value: str) -> dict:
    """"Never exclude this standard" / "...this employer" from the audit view.

    What each kind actually changes, because "never" has to mean something:

      * standard — the course joins `digital_standards`. `judge()` keeps a
        digital standard OUTRIGHT at step 1, before any exclusion runs, so this
        is a real guarantee for every listing training to that standard.
      * employer — every `employer_exclusions` term matching them is removed,
        and they join `watchlist_employers`. The removal is what lifts the drop;
        the watchlist entry is what turns a non-digital standard from them into
        a REVIEW row rather than a silent one. A title exclusion still applies
        — an "Apprentice Chef" at a watched employer is still an apprentice
        chef — so this is "stop excluding them for being them", not "keep
        everything they post".

    STORED IN THE DATABASE, as a delta over `config/scout_filters.json`. Railway's
    container filesystem is ephemeral, so a correction written into the file in
    production would hold until the next deploy and then silently revert — the
    user would have to notice the exclusion had come back. The file stays the
    hand-editable default layer; these ride on top of whatever it says.

    Returns what changed, per list, so a caller can report what happened rather
    than claim more than it did. Idempotent: a value already in effect stores
    nothing and comes back with both maps empty.
    """
    import database as db

    kind = (kind or "").strip().casefold()
    value = " ".join((value or "").split())
    if kind not in ALLOW_KINDS:
        raise ValueError(f"kind must be one of {', '.join(ALLOW_KINDS)}")
    if not value:
        raise ValueError("a value is required")

    changed = {"added": {}, "removed": {}}

    def _add(key, term):
        fold = term.casefold()
        if any(fold == str(v).strip().casefold() for v in settings()[key]):
            return
        if db.add_scout_filter_override(key, term, op="add"):
            changed["added"].setdefault(key, []).append(term)

    def _drop(key, terms):
        for term in terms:
            if db.add_scout_filter_override(key, term, op="remove"):
                changed["removed"].setdefault(key, []).append(term)

    if kind == "standard":
        _add("digital_standards", value)
    else:
        _drop("employer_exclusions",
              _effective_matches("employer_exclusions", value))
        _add("watchlist_employers", value)

    if changed["added"] or changed["removed"]:
        settings(refresh=True)        # the correction takes effect now, not in 30s
        logger.info("scout_config: allow_forever(%s) added=%s removed=%s",
                    kind, changed["added"], changed["removed"])
    return changed


def allowed_overrides() -> list:
    """Every correction currently stored, for the Excluded screen's undo list.

    Without this the only way back from a mis-click would be a SQL console —
    the file was at least hand-editable, so moving to the database has to bring
    its own way to reverse a decision.
    """
    import database as db

    labels = {
        ("digital_standards", "add"): "Never exclude this standard",
        ("watchlist_employers", "add"): "Never exclude this employer",
        ("employer_exclusions", "remove"): "Removed an employer exclusion",
    }
    out = []
    try:
        rows = db.list_scout_filter_overrides()
    except Exception as e:
        logger.warning("scout_config: could not list overrides: %s", e)
        return out
    for row in rows:
        key, op = row.get("list_key"), row.get("op") or "add"
        out.append({
            "id": row.get("id"),
            "value": row.get("value") or "",
            "list_key": key,
            "op": op,
            "label": labels.get((key, op),
                                f"{op} {str(key).replace('_', ' ')}"),
            "created_at": row.get("created_at") or "",
        })
    return out


def undo_override(override_id) -> bool:
    """Reverse one stored correction."""
    import database as db

    if db.delete_scout_filter_override(override_id):
        settings(refresh=True)
        return True
    return False
