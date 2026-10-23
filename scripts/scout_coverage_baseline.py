"""Measure Scout's gov.uk apprenticeship coverage. Read-only, writes no DB rows.

Phase 1 needs a *measured* before/after, not a code reading, and both sides have
to come from the same method. This script is that method: it runs the scraper's
configured searches against gov.uk, counts what comes back, and writes a JSON
snapshot so two runs can be diffed.

    # baseline, before any keyword changes (keyword scans only — cheap)
    python scripts/scout_coverage_baseline.py --out baseline.json

    # after the Phase 1 changes, same invocation
    python scripts/scout_coverage_baseline.py --out after.json

    # compare
    python scripts/scout_coverage_baseline.py --compare baseline.json after.json

By default it runs only the KEYWORD_SCANS searches. The employer sweep is
opt-in (`--employers`) because the watchlist is ~47 employers with several
aliases each, and at one request per second with up to 5 pages per search that
is a long, heavy crawl. `--estimate` prints the request budget and exits.

Offline validation: `--fixture DIR` reads saved HTML pages instead of calling
gov.uk, so the counting logic can be exercised without network access.

Nothing here writes to scout_jobs, scout_pipeline or the ScanLog — it is purely
a measurement, so running it never marks a listing as seen.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scrapers.gov_uk import GovUkScraper, ScraperError  # noqa: E402

# ── The digital/tech classifier used for measurement ─────────────────────────
# Deliberately explicit and in one place: the whole point is that the "before"
# and "after" numbers are produced by the SAME rule. Phase 1 will introduce the
# real relevance check in config; this is the measuring stick, not that rule, and
# changing it invalidates comparison against an older snapshot (the snapshot
# records CLASSIFIER_VERSION so a mismatched diff is obvious).
CLASSIFIER_VERSION = 3

# Whole-word terms. "it" must not match "kitchen"/"security guard" etc, so every
# term is matched on a word boundary rather than as a substring.
DIGITAL_TERMS = (
    "cyber", "software", "digital", "technology", "tech", "security", "it",
    "systems", "system", "developer", "development", "computing", "computer",
    "information", "engineer", "engineering", "data", "cloud", "network",
    "networks", "devops", "infrastructure", "programmer", "programming",
    "analyst", "automation", "ai", "machine learning",
)
_DIGITAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in DIGITAL_TERMS) + r")\b", re.I)


def is_digital(vacancy) -> bool:
    """Whole-word match over title + training course."""
    blob = f"{vacancy.title or ''} {vacancy.training_course or ''}"
    return bool(_DIGITAL_RE.search(blob))


def digital_terms_hit(vacancy):
    blob = f"{vacancy.title or ''} {vacancy.training_course or ''}"
    return sorted({m.group(1).lower() for m in _DIGITAL_RE.finditer(blob)})


# ── Measurement ──────────────────────────────────────────────────────────────

def _searches(use_employers: bool):
    """(kind, term) pairs this run will issue, from the live config."""
    from services import apprenticeships

    out = [("keyword", kw) for kw in apprenticeships.keyword_scans()]
    if use_employers:
        import app as app_module
        from models import Employer
        with app_module.app.app_context():
            for emp in Employer.query.filter_by(watching=True).all():
                for alias in emp.alias_list:
                    out.append(("employer", alias))
    return out


def measure(use_employers=False, min_level=1, max_pages=None, verbose=True):
    """Run every configured search and tally the union of what comes back.

    min_level defaults to 1, not the app's 4: the baseline has to see what the
    scraper *returns* before level filtering, or the "how many are Level 4+"
    number is just the filter restating itself.
    """
    scraper = GovUkScraper()
    if max_pages is not None:
        orig = scraper._search_all_pages
        scraper._search_all_pages = (
            lambda params, lvl, max_pages=max_pages: orig(params, lvl, max_pages))

    by_ref = {}
    per_search = []
    errors = []

    searches = _searches(use_employers)
    for i, (kind, term) in enumerate(searches, 1):
        if verbose:
            print(f"  [{i}/{len(searches)}] {kind}: {term} ... ", end="", flush=True)
        try:
            fn = scraper.by_employer if kind == "employer" else scraper.by_keyword
            raws = fn(term, min_level=min_level)
        except ScraperError as e:
            errors.append({"kind": kind, "term": term, "error": str(e)})
            if verbose:
                print("ERROR")
            continue
        fresh = sum(1 for r in raws if r.reference not in by_ref)
        for r in raws:
            by_ref.setdefault(r.reference, r)
        per_search.append({"kind": kind, "term": term,
                           "returned": len(raws), "new_to_run": fresh})
        if verbose:
            print(f"{len(raws)} ({fresh} new)")

    return _summarise(by_ref, per_search, errors, searches, use_employers)


def _summarise(by_ref, per_search, errors, searches, use_employers):
    vacs = list(by_ref.values())
    levels = Counter(v.level if v.level is not None else "unknown" for v in vacs)
    digital = [v for v in vacs if is_digital(v)]
    lvl4plus = [v for v in vacs if v.level is not None and v.level >= 4]
    lvl6plus = [v for v in vacs if v.level is not None and v.level >= 6]
    both = [v for v in vacs if is_digital(v) and v.level is not None and v.level >= 4]

    return {
        "classifier_version": CLASSIFIER_VERSION,
        "searches_issued": len(searches),
        "employer_sweep": use_employers,
        "errors": errors,
        "totals": {
            "unique_results": len(vacs),
            "level_4_plus": len(lvl4plus),
            "level_6_plus": len(lvl6plus),
            "digital": len(digital),
            "digital_and_level_4_plus": len(both),
            "level_unknown": levels.get("unknown", 0),
        },
        "by_level": {str(k): v for k, v in sorted(
            levels.items(), key=lambda kv: (kv[0] == "unknown", kv[0]))},
        "per_search": per_search,
        "listings": sorted(
            ({
                "ref": v.reference,
                "title": v.title,
                "employer": v.employer_name_raw,
                "location": v.location,
                "level": v.level,
                "course": v.training_course,
                "digital": is_digital(v),
                "digital_terms": digital_terms_hit(v),
                "url": v.url,
            } for v in vacs),
            key=lambda d: (d["level"] is None, -(d["level"] or 0), d["title"])),
    }


def measure_fixture(fixture_dir, min_level=1):
    """Count saved HTML pages instead of calling gov.uk (offline validation)."""
    scraper = GovUkScraper()
    by_ref = {}
    per_search = []
    names = sorted(n for n in os.listdir(fixture_dir) if n.endswith((".html", ".htm")))
    for name in names:
        with open(os.path.join(fixture_dir, name), encoding="utf-8") as fh:
            raws = scraper._parse_search_page(fh.read(), min_level)
        fresh = sum(1 for r in raws if r.reference not in by_ref)
        for r in raws:
            by_ref.setdefault(r.reference, r)
        per_search.append({"kind": "fixture", "term": name,
                           "returned": len(raws), "new_to_run": fresh})
    return _summarise(by_ref, per_search, [], names, False)


# ── Reporting ────────────────────────────────────────────────────────────────

def report(snap):
    t = snap["totals"]
    print()
    print(f"searches issued            : {snap['searches_issued']}"
          f"{'  (incl. employer sweep)' if snap['employer_sweep'] else '  (keyword scans only)'}")
    print(f"unique listings returned   : {t['unique_results']}")
    print(f"  Level 4+                 : {t['level_4_plus']}")
    print(f"  Level 6+                 : {t['level_6_plus']}")
    print(f"  digital/tech             : {t['digital']}")
    print(f"  digital AND Level 4+     : {t['digital_and_level_4_plus']}")
    print(f"  level not parsed         : {t['level_unknown']}")
    print()
    print("by level:")
    for lvl, n in snap["by_level"].items():
        print(f"    level {lvl:<8} {n:>5}")
    if snap["errors"]:
        print()
        print(f"errors ({len(snap['errors'])}):")
        for e in snap["errors"][:10]:
            print(f"    {e['kind']} {e['term']}: {e['error'][:110]}")


def compare(a_path, b_path):
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    if a.get("classifier_version") != b.get("classifier_version"):
        print(f"WARNING: classifier versions differ "
              f"({a.get('classifier_version')} vs {b.get('classifier_version')}) "
              f"— these numbers are not comparable.")
        print()
    keys = ("unique_results", "level_4_plus", "level_6_plus", "digital",
            "digital_and_level_4_plus")
    label = {"unique_results": "unique listings", "level_4_plus": "Level 4+",
             "level_6_plus": "Level 6+", "digital": "digital/tech",
             "digital_and_level_4_plus": "digital AND Level 4+"}
    print(f"{'metric':<26}{'before':>9}{'after':>9}{'delta':>9}")
    print("-" * 53)
    for k in keys:
        before, after = a["totals"][k], b["totals"][k]
        print(f"{label[k]:<26}{before:>9}{after:>9}{after - before:>+9}")
    a_refs = {x["ref"] for x in a["listings"]}
    b_refs = {x["ref"] for x in b["listings"]}
    print()
    print(f"newly found : {len(b_refs - a_refs)}")
    print(f"lost        : {len(a_refs - b_refs)}")
    for x in b["listings"]:
        if x["ref"] in (b_refs - a_refs):
            lvl = x["level"] if x["level"] is not None else "?"
            print(f"    + L{lvl} {x['title'][:58]}  ({x['employer'][:28]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--employers", action="store_true",
                    help="also sweep every watched employer alias (slow, heavy)")
    ap.add_argument("--min-level", type=int, default=1,
                    help="level floor passed to the scraper (default 1 = unfiltered)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="override the per-search page cap (default: scraper's 5)")
    ap.add_argument("--out", help="write the JSON snapshot here")
    ap.add_argument("--fixture", help="count saved HTML pages in this dir, no network")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="diff two snapshots and exit")
    ap.add_argument("--estimate", action="store_true",
                    help="print the request budget and exit")
    args = ap.parse_args()

    if args.compare:
        compare(*args.compare)
        return 0

    if args.estimate:
        searches = _searches(args.employers)
        pages = args.max_pages or 5
        print(f"searches        : {len(searches)}")
        print(f"max pages each  : {pages}")
        print(f"worst-case reqs : {len(searches) * pages}")
        print(f"min wall time   : ~{len(searches) * pages} s at 1 req/s")
        for kind, term in searches[:12]:
            print(f"    {kind}: {term}")
        if len(searches) > 12:
            print(f"    ... and {len(searches) - 12} more")
        return 0

    if args.fixture:
        print(f"Counting fixtures in {args.fixture} (no network)")
        snap = measure_fixture(args.fixture, min_level=args.min_level)
    else:
        searches = _searches(args.employers)
        print(f"Measuring gov.uk coverage — {len(searches)} searches, "
              f">=1s apart (worst case ~{len(searches) * (args.max_pages or 5)} requests)")
        snap = measure(use_employers=args.employers, min_level=args.min_level,
                       max_pages=args.max_pages)

    report(snap)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=2)
        print()
        print(f"snapshot written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
