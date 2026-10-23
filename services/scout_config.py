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

# The widened set. Single broad words pull in noise on purpose — the exclusion
# rules below are what make that affordable, and a wide net plus an auditable
# exclusion log beats a narrow net that silently misses roles.
ADDED_SEARCH_KEYWORDS = (
    "cyber",
    "software",
    "digital",
    "technology",
    "security",
    "IT",
    "systems",
    "developer",
    "computing",
    "information",
    "engineer",
)

DEFAULT_SEARCH_KEYWORDS = ORIGINAL_SEARCH_PHRASES + ADDED_SEARCH_KEYWORDS

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

# ── Reasons recorded in the exclusion log ────────────────────────────────────

REASON_TITLE = "title_excluded"
REASON_EMPLOYER = "employer_excluded"
REASON_NOT_DIGITAL = "no_digital_signal"

KEEP_DIGITAL_STANDARD = "digital_standard"
KEEP_STRONG_TERM = "strong_digital_term"
KEEP_UNSURE = "kept_unsure"


# ── Loading the overrides ────────────────────────────────────────────────────

_LOCK = threading.Lock()
_CACHE: dict = {}
_CACHE_STAMP = None

_KEYS = {
    "search_keywords": DEFAULT_SEARCH_KEYWORDS,
    "digital_standards": DEFAULT_DIGITAL_STANDARDS,
    "strong_digital_terms": DEFAULT_STRONG_DIGITAL_TERMS,
    "weak_digital_terms": DEFAULT_WEAK_DIGITAL_TERMS,
    "title_exclusions": DEFAULT_TITLE_EXCLUSIONS,
    "employer_exclusions": DEFAULT_EMPLOYER_EXCLUSIONS,
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
    return out


def settings(refresh: bool = False) -> dict:
    """The effective configuration: defaults merged with the JSON overrides."""
    global _CACHE, _CACHE_STAMP
    path = config_path()
    try:
        stamp = (path, os.path.getmtime(path))
    except OSError:
        stamp = (path, None)

    with _LOCK:
        if refresh or not _CACHE or _CACHE_STAMP != stamp:
            merged = dict(_KEYS)
            merged.update(_load_overrides(path))
            # Normalise once so the matchers don't re-lower on every listing.
            _CACHE = {k: tuple(v) for k, v in merged.items()}
            _CACHE_STAMP = stamp
        return _CACHE


def search_keywords() -> list:
    """Terms to search gov.uk with.

    KEYWORD_SCANS still wins if it is set, so an operator can pin the search
    list from the environment without editing anything.
    """
    env = os.environ.get("KEYWORD_SCANS")
    if env:
        return [k.strip() for k in env.split(",") if k.strip()]
    return list(settings()["search_keywords"])


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


def course_name(course: str) -> str:
    """A training-course string reduced to its standard name."""
    return " ".join(_COURSE_LEVEL_RE.sub("", (course or "").strip()).split()).casefold()


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

    __slots__ = ("keep", "reason", "detail", "digital", "terms")

    def __init__(self, keep, reason, detail="", digital=False, terms=()):
        self.keep = keep
        self.reason = reason
        self.detail = detail
        self.digital = digital
        self.terms = tuple(terms)

    def __repr__(self):
        return (f"Verdict(keep={self.keep}, reason={self.reason!r}, "
                f"detail={self.detail!r}, digital={self.digital})")

    def as_dict(self):
        return {"keep": self.keep, "reason": self.reason, "detail": self.detail,
                "digital": self.digital, "terms": list(self.terms)}


def judge(title: str, employer: str = "", course: str = "") -> Verdict:
    """Decide whether a listing is a digital apprenticeship worth surfacing.

    Order matters, and it is ordered to fail towards keeping:

      1. a recognised Digital STANDARD keeps the listing outright. The employer
         picked it from a national catalogue, so it is both the strongest and
         the only query-independent signal — and no exclusion list overrides it,
         because a list of guessed phrases should not beat a stated fact;
      2. an excluded EMPLOYER drops it. The user maintains that list from the
         exclusion log, so it is theirs to be blunt with;
      3. an excluded TITLE drops it, UNLESS an unambiguous technical word is
         present. "Security Guard" has only the weak term "security" and goes;
         "Digital Project Manager" has "digital" and stays;
      4. anything else is kept. The `digital` flag records whether any digital
         term was seen, so a caller can rank without the filter having guessed.
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

    title_hit = matched_title_exclusion(title)
    if title_hit and not strong:
        return Verdict(False, REASON_TITLE, detail=title_hit,
                       digital=bool(terms), terms=terms)

    if strong:
        return Verdict(True, KEEP_STRONG_TERM, detail=",".join(strong),
                       digital=True, terms=terms)

    return Verdict(True, KEEP_UNSURE, detail=",".join(terms),
                   digital=bool(terms), terms=terms)
