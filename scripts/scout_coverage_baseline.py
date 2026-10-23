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

# ── Classifying a listing as digital ─────────────────────────────────────────
# CLASSIFIER_VERSION 4 replaces a title-keyword match, which was circular: it
# scored titles found BY tech keywords against those same tech keywords, so it
# could only ever return ~100%. A baseline of "42 of 43 are digital" measured
# the search, not the corpus.
#
# The replacement classifies on the APPRENTICESHIP STANDARD (gov.uk's "Training
# course" field), which the employer picks from a fixed national catalogue when
# posting. It is not derived from the query, so the same listing classifies the
# same way however it was found — which is what makes a before/after comparison
# across different keyword sets meaningful.
#
# Snapshots record CLASSIFIER_VERSION, and --compare refuses to treat two
# versions as comparable.
CLASSIFIER_VERSION = 4

# Standards on the Digital route of the national catalogue, as they appear in
# the "Training course" field (lower-cased, level suffix stripped). Matched on
# the standard NAME, so a listing titled "Apprentice (Ref 8812)" still
# classifies correctly from its course.
#
# NOT VERIFIED against gov.uk's own route filter — that needs the live site, and
# a `routeIds`-style query parameter would give the authoritative answer. Until
# then this is a curated list and `route_unclassified` below reports how much it
# fails to cover, so its blind spots are visible instead of silent.
DIGITAL_STANDARDS = (
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

# Level suffixes and decoration gov.uk appends to a course name.
_COURSE_CLEAN_RE = re.compile(
    r"\s*[\(\[]?\s*(level|lvl)\s*\d+\s*[\)\]]?\s*$", re.I)


def course_name(vacancy) -> str:
    """The standard name, lower-cased with any trailing level suffix removed."""
    raw = (vacancy.training_course or "").strip()
    raw = _COURSE_CLEAN_RE.sub("", raw)
    return " ".join(raw.split()).casefold()


def digital_route(vacancy) -> bool:
    """True when the listing's apprenticeship standard is a Digital one.

    Query-independent: it reads only the standard the employer selected.
    """
    name = course_name(vacancy)
    if not name:
        return False
    return any(name == s or name.startswith(s) for s in DIGITAL_STANDARDS)


def route_unclassified(vacancy) -> bool:
    """No course text, so the standard cannot be read either way.

    Reported separately rather than counted as non-digital: these are the
    listings the metric genuinely cannot speak to, and lumping them in with
    "not digital" would overstate the classifier's reach.
    """
    return not course_name(vacancy)


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
            lambda params, lvl, mp=None, stats=None, _o=orig, _c=max_pages:
            _o(params, lvl, _c, stats=stats))

    by_ref = {}
    per_search = []
    errors = []

    searches = _searches(use_employers)
    for i, (kind, term) in enumerate(searches, 1):
        if verbose:
            print(f"  [{i}/{len(searches)}] {kind}: {term} ... ", end="", flush=True)
        stats = {}
        try:
            fn = scraper.by_employer if kind == "employer" else scraper.by_keyword
            raws = fn(term, min_level=min_level, stats=stats)
        except ScraperError as e:
            errors.append({"kind": kind, "term": term, "error": str(e)})
            if verbose:
                print("ERROR")
            continue
        fresh = sum(1 for r in raws if r.reference not in by_ref)
        for r in raws:
            by_ref.setdefault(r.reference, r)
        row = {"kind": kind, "term": term,
               "returned": len(raws), "new_to_run": fresh,
               "site_reported_total": stats.get("reported_total"),
               "pages_fetched": stats.get("pages_fetched", 0),
               "cards_seen": stats.get("cards_seen", 0),
               "dropped_no_ref": stats.get("dropped_no_ref", 0),
               "dropped_below_level": stats.get("dropped_below_level", 0),
               "level_unparsed": stats.get("level_unparsed", 0),
               "stopped_because": stats.get("stopped_because"),
               "ref_kinds": stats.get("ref_kinds", {}),
               "unmatched_href_samples": stats.get("unmatched_href_samples", [])}
        per_search.append(row)
        if verbose:
            site = row["site_reported_total"]
            gap = "" if site is None else f"  site says {site}"
            drop = (f"  DROPPED {row['dropped_no_ref']} unparseable"
                    if row["dropped_no_ref"] else "")
            print(f"{len(raws)} ({fresh} new){gap}{drop}")
            # Print the samples HERE, next to the search that produced them.
            # Burying them in a summary is how the last run reported "16
            # dropped" with no sign of what was dropped.
            if row["dropped_no_ref"]:
                if row["unmatched_href_samples"]:
                    print(f"      hrefs we could not parse "
                          f"({len(row['unmatched_href_samples'])} sampled):")
                    for h in row["unmatched_href_samples"]:
                        print(f"        {h}")
                else:
                    print("      (no hrefs captured on the dropped cards — the "
                          "cards may carry no anchor at all)")
            if row["ref_kinds"]:
                kinds = ", ".join(f"{k}={v}" for k, v in sorted(row["ref_kinds"].items()))
                if set(row["ref_kinds"]) - {"vac"}:
                    print(f"      reference formats: {kinds}"
                          f"   <-- non-VAC references present")

    return _summarise(by_ref, per_search, errors, searches, use_employers)


def _merge_counts(dicts):
    out = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _summarise(by_ref, per_search, errors, searches, use_employers):
    vacs = list(by_ref.values())
    levels = Counter(v.level if v.level is not None else "unknown" for v in vacs)
    lvl4plus = [v for v in vacs if v.level is not None and v.level >= 4]
    lvl6plus = [v for v in vacs if v.level is not None and v.level >= 6]
    digital = [v for v in vacs if digital_route(v)]
    unclassified = [v for v in vacs if route_unclassified(v)]
    both = [v for v in vacs
            if digital_route(v) and v.level is not None and v.level >= 4]

    # How far short of the site's own numbers the scrape came. The single most
    # useful figure for diagnosing coverage: if gov.uk reports far more than we
    # collected, the shortfall is in the scraper, not the corpus.
    reported = [r["site_reported_total"] for r in per_search
                if r.get("site_reported_total") is not None]
    coverage = {
        "searches_reporting_a_total": len(reported),
        "sum_site_reported_totals": sum(reported) if reported else None,
        "sum_returned": sum(r["returned"] for r in per_search),
        "total_cards_seen": sum(r.get("cards_seen", 0) for r in per_search),
        "total_dropped_no_ref": sum(r.get("dropped_no_ref", 0) for r in per_search),
        "total_dropped_below_level": sum(
            r.get("dropped_below_level", 0) for r in per_search),
        "searches_stopped_at_page_cap": sum(
            1 for r in per_search if r.get("stopped_because") == "page_cap"),
        "unmatched_href_samples": sorted({
            h for r in per_search for h in r.get("unmatched_href_samples", [])}),
        "ref_kinds": _merge_counts(r.get("ref_kinds", {}) for r in per_search),
    }

    return {
        "classifier_version": CLASSIFIER_VERSION,
        "searches_issued": len(searches),
        "employer_sweep": use_employers,
        "query_params_sent": GovUkScraper.search_params("<term>"),
        "errors": errors,
        "totals": {
            "unique_results": len(vacs),
            "level_4_plus": len(lvl4plus),
            "level_6_plus": len(lvl6plus),
            "digital_route": len(digital),
            "digital_route_and_level_4_plus": len(both),
            "route_unclassified": len(unclassified),
            "level_unknown": levels.get("unknown", 0),
        },
        "coverage": coverage,
        "by_level": {str(k): v for k, v in sorted(
            levels.items(), key=lambda kv: (kv[0] == "unknown", kv[0]))},
        "by_course": {c: n for c, n in Counter(
            course_name(v) or "(no course)" for v in vacs).most_common()},
        "per_search": per_search,
        "listings": sorted(
            ({
                "ref": v.reference,
                "title": v.title,
                "employer": v.employer_name_raw,
                "location": v.location,
                "level": v.level,
                "course": v.training_course,
                "course_normalised": course_name(v),
                "digital_route": digital_route(v),
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
        stats = {}
        with open(os.path.join(fixture_dir, name), encoding="utf-8") as fh:
            raws = scraper._parse_search_page(fh.read(), min_level, stats=stats)
        fresh = sum(1 for r in raws if r.reference not in by_ref)
        for r in raws:
            by_ref.setdefault(r.reference, r)
        per_search.append({"kind": "fixture", "term": name,
                           "returned": len(raws), "new_to_run": fresh,
                           "site_reported_total": stats.get("reported_total"),
                           "pages_fetched": 1,
                           "cards_seen": stats.get("cards_seen", 0),
                           "dropped_no_ref": stats.get("dropped_no_ref", 0),
                           "dropped_below_level": stats.get("dropped_below_level", 0),
                           "level_unparsed": stats.get("level_unparsed", 0),
                           "stopped_because": None,
                           "ref_kinds": stats.get("ref_kinds", {}),
                           "unmatched_href_samples": stats.get("unmatched_href_samples", [])})
    return _summarise(by_ref, per_search, [], names, False)


# ── Reporting ────────────────────────────────────────────────────────────────

def report(snap):
    t = snap["totals"]
    print()
    print(f"query sent per search      : {snap.get('query_params_sent')}")
    print(f"searches issued            : {snap['searches_issued']}"
          f"{'  (incl. employer sweep)' if snap['employer_sweep'] else '  (keyword scans only)'}")
    print(f"unique listings returned   : {t['unique_results']}")
    print(f"  Level 4+                 : {t['level_4_plus']}")
    print(f"  Level 6+                 : {t['level_6_plus']}")
    print(f"  digital route (standard) : {t['digital_route']}")
    print(f"  digital route AND L4+    : {t['digital_route_and_level_4_plus']}")
    print(f"  course text missing      : {t['route_unclassified']}"
          f"   (route cannot be read)")
    print(f"  level not parsed         : {t['level_unknown']}")

    c = snap.get("coverage") or {}
    print()
    print("coverage vs the site's own numbers:")
    site = c.get("sum_site_reported_totals")
    print(f"    searches reporting a total : {c.get('searches_reporting_a_total')}"
          f" / {snap['searches_issued']}")
    print(f"    sum of site totals         : "
          f"{site if site is not None else 'n/a (title not parsed)'}")
    print(f"    sum returned by scraper    : {c.get('sum_returned')}")
    if site:
        shortfall = site - (c.get("sum_returned") or 0)
        print(f"    shortfall                  : {shortfall}"
              f"  ({100 * shortfall / site:.0f}% of what the site reports)")
    print(f"    cards seen on pages        : {c.get('total_cards_seen')}")
    print(f"    dropped, no VAC reference  : {c.get('total_dropped_no_ref')}"
          f"   <-- silently lost listings" if c.get("total_dropped_no_ref")
          else f"    dropped, no VAC reference  : 0")
    print(f"    dropped, below min level   : {c.get('total_dropped_below_level')}")
    print(f"    searches hitting page cap  : {c.get('searches_stopped_at_page_cap')}"
          f" / {snap['searches_issued']}")
    if c.get("ref_kinds"):
        kinds = ", ".join(f"{k}={v}" for k, v in c["ref_kinds"].items())
        print(f"    reference formats seen      : {kinds}")
    if c.get("unmatched_href_samples"):
        print("    hrefs we could NOT parse (all captured, unfiltered):")
        for h in c["unmatched_href_samples"]:
            print(f"        {h}")
    elif c.get("total_dropped_no_ref"):
        print("    hrefs we could NOT parse    : none captured — the dropped")
        print("                                  cards carry no anchor at all")

    print()
    print("per search:")
    print(f"    {'term':<26}{'got':>5}{'site':>6}{'pages':>6}{'cards':>6}"
          f"{'nodrop':>7}  stopped")
    for r in snap["per_search"]:
        site_s = r.get("site_reported_total")
        print(f"    {r['term'][:25]:<26}{r['returned']:>5}"
              f"{(site_s if site_s is not None else '-'):>6}"
              f"{r.get('pages_fetched', 0):>6}{r.get('cards_seen', 0):>6}"
              f"{r.get('dropped_no_ref', 0):>7}  {r.get('stopped_because') or '-'}")

    print()
    print("by level:")
    for lvl, n in snap["by_level"].items():
        print(f"    level {lvl:<8} {n:>5}")

    if snap.get("by_course"):
        print()
        print("by apprenticeship standard (top 15) — * = classified Digital:")
        digital_set = set(DIGITAL_STANDARDS)
        for course, n in list(snap["by_course"].items())[:15]:
            star = "*" if any(course == d or course.startswith(d)
                              for d in digital_set) else " "
            print(f"  {star} {course[:52]:<54}{n:>4}")

    if snap["errors"]:
        print()
        print(f"errors ({len(snap['errors'])}):")
        for e in snap["errors"][:10]:
            print(f"    {e['kind']} {e['term']}: {e['error'][:110]}")


# Metrics whose definition changed with CLASSIFIER_VERSION 4. A snapshot from
# before that carries the old circular `digital` key instead, and the two are
# not the same measurement, so they are shown as n/c rather than diffed.
_CLASSIFIER_DEPENDENT = ("digital_route", "digital_route_and_level_4_plus",
                         "route_unclassified")


def compare(a_path, b_path):
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    av, bv = a.get("classifier_version"), b.get("classifier_version")
    mixed = av != bv
    if mixed:
        print(f"NOTE: classifier versions differ ({av} vs {bv}).")
        print("      Level and count metrics are still comparable — they do not")
        print("      depend on the classifier. The digital metrics are not, and")
        print("      are shown as n/c. Version 4 replaced a title-keyword match")
        print("      with one based on the apprenticeship standard.")
        print()

    keys = ("unique_results", "level_4_plus", "level_6_plus", "digital_route",
            "digital_route_and_level_4_plus", "route_unclassified")
    label = {"unique_results": "unique listings", "level_4_plus": "Level 4+",
             "level_6_plus": "Level 6+", "digital_route": "digital route",
             "digital_route_and_level_4_plus": "digital route AND L4+",
             "route_unclassified": "course text missing"}

    print(f"{'metric':<26}{'before':>9}{'after':>9}{'delta':>9}")
    print("-" * 53)
    for k in keys:
        before = (a.get("totals") or {}).get(k)
        after = (b.get("totals") or {}).get(k)
        if before is None and after is None:
            continue            # neither snapshot has it; nothing to say
        if before is None or after is None or (mixed and k in _CLASSIFIER_DEPENDENT):
            b_s = "-" if before is None else str(before)
            a_s = "-" if after is None else str(after)
            print(f"{label[k]:<26}{b_s:>9}{a_s:>9}{'n/c':>9}")
        else:
            print(f"{label[k]:<26}{before:>9}{after:>9}{after - before:>+9}")

    # An older snapshot's circular metric, named so it is not mistaken for the
    # new one rather than quietly omitted.
    for snap, side in ((a, "before"), (b, "after")):
        legacy = (snap.get("totals") or {}).get("digital")
        if legacy is not None:
            print(f"{'  (legacy title-keyword ' + side + ')':<26}{legacy:>9}"
                  f"{'':>9}{'n/c':>9}")
    a_refs = {x["ref"] for x in a.get("listings", [])}
    b_refs = {x["ref"] for x in b.get("listings", [])}
    print()
    print(f"newly found : {len(b_refs - a_refs)}")
    print(f"lost        : {len(a_refs - b_refs)}")
    for x in b.get("listings", []):
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
