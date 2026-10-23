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

from services import scout_config  # noqa: E402
from services.scrapers.gov_uk import (GovUkScraper, ScraperError,  # noqa: E402
                                      SOURCE_FINDAPPRENTICESHIP)

# ── Classifying a listing as digital ─────────────────────────────────────────
# The lists and the keep/drop decision live in services/scout_config, shared
# with the production scan. If the two filtered differently these numbers would
# describe something the app does not do.
#
# CLASSIFIER_VERSION 5: filtering moved into scout_config and the report now
# carries exclusion counts. The digital signal is still the apprenticeship
# STANDARD rather than a title keyword match — scoring titles found BY tech
# keywords against those same keywords could only ever return ~100%.
CLASSIFIER_VERSION = 5

DIGITAL_STANDARDS = tuple(scout_config.settings()["digital_standards"])


def course_name(vacancy) -> str:
    return scout_config.course_name(vacancy.training_course)


def digital_route(vacancy) -> bool:
    """True when the listing's apprenticeship standard is a Digital one.

    Query-independent: reads only the standard the employer selected.
    """
    return scout_config.is_digital_standard(vacancy.training_course)


def route_unclassified(vacancy) -> bool:
    """No course text, so the standard cannot be read either way.

    Reported separately rather than counted as non-digital: these are the
    listings the metric genuinely cannot speak to.
    """
    return not course_name(vacancy)


def judge(vacancy):
    """The keep/drop verdict the production scan would reach for this listing."""
    return scout_config.judge(vacancy.title, vacancy.employer_name_raw,
                              vacancy.training_course)


# ── Measurement ──────────────────────────────────────────────────────────────

# The route sweep's label, used to attribute vacancies to it in `found_by`.
ROUTE_TERM = "[route sweep] digital, Level 4+"


def _searches(use_employers: bool, route_sweep: bool = True):
    """(kind, term) pairs this run will issue, from the live config.

    The route sweep goes first because it is the method with no dependency on
    wording, so it is the baseline the keyword searches get measured against
    rather than the other way round.
    """
    from services import apprenticeships

    out = []
    if route_sweep:
        out.append(("route", ROUTE_TERM))
    out += [("keyword", kw) for kw in apprenticeships.keyword_scans()]
    if use_employers:
        import app as app_module
        from models import Employer
        with app_module.app.app_context():
            for emp in Employer.query.filter_by(watching=True).all():
                for alias in emp.alias_list:
                    out.append(("employer", alias))
    return out


def measure(use_employers=False, min_level=1, max_pages=None, verbose=True,
            route_sweep=True):
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
    found_by = {}
    per_search = []
    errors = []

    searches = _searches(use_employers, route_sweep=route_sweep)
    for i, (kind, term) in enumerate(searches, 1):
        if verbose:
            print(f"  [{i}/{len(searches)}] {kind}: {term} ... ", end="", flush=True)
        stats = {}
        try:
            if kind == "route":
                # No search term: the route filter selects the listings. Also no
                # local level floor — gov.uk has already filtered via levelIds,
                # so re-filtering here would only discard listings whose level
                # we failed to parse.
                raws = scraper.by_route(min_level=1, stats=stats)
            elif kind == "employer":
                raws = scraper.by_employer(term, min_level=min_level, stats=stats)
            else:
                raws = scraper.by_keyword(term, min_level=min_level, stats=stats)
        except ScraperError as e:
            errors.append({"kind": kind, "term": term, "error": str(e)})
            if verbose:
                print("ERROR")
            continue
        fresh = sum(1 for r in raws if r.reference not in by_ref)
        for r in raws:
            by_ref.setdefault(r.reference, r)
            # Which terms found this vacancy, so "only the widened terms find
            # it" can be answered without a second run.
            found_by.setdefault(r.reference, []).append(term)
        row = {"kind": kind, "term": term,
               "returned": len(raws), "new_to_run": fresh,
               "site_reported_total": stats.get("reported_total"),
               "pages_fetched": stats.get("pages_fetched", 0),
               "cards_seen": stats.get("cards_seen", 0),
               "dropped_no_ref": stats.get("dropped_no_ref", 0),
               "dropped_below_level": stats.get("dropped_below_level", 0),
               "level_unparsed": stats.get("level_unparsed", 0),
               "duplicate_cards": stats.get("duplicate_cards", 0),
               "extra_locations": stats.get("extra_locations", 0),
               "stopped_because": stats.get("stopped_because"),
               "ref_kinds": stats.get("ref_kinds", {}),
               "unmatched_href_samples": stats.get("unmatched_href_samples", [])}
        per_search.append(row)
        if verbose:
            site = row["site_reported_total"]
            gap = "" if site is None else f"  site says {site}"
            drop = (f"  DROPPED {row['dropped_no_ref']} unparseable"
                    if row["dropped_no_ref"] else "")
            dup = (f"  {row['duplicate_cards']} dup card(s)"
                   if row["duplicate_cards"] else "")
            print(f"{len(raws)} ({fresh} new){gap}{dup}{drop}")
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

    return _summarise(by_ref, per_search, errors, searches, use_employers,
                      found_by=found_by)


# ── Likely cross-post detection (FLAG ONLY — Phase 2 owns dedupe) ────────────
# gov.uk lists some public-sector roles twice: once as a native apprenticeship
# with a VAC reference and a level, and once as an off-site cross-post to the
# employer's own system with neither. Those are the same job and should alert
# once. This only LABELS candidate groups so the scale of the problem is
# visible; nothing is merged, and the real matching (with a fuzzy fallback and
# near-miss tests) belongs to Phase 2.

_EMPLOYER_NOISE_RE = re.compile(
    r"\b(ltd|limited|plc|llp|llc|inc|group|uk|the)\b", re.I)

# Words that say nothing about WHICH role this is: grade/band markers, and the
# apprenticeship vocabulary every one of these listings shares.
_TITLE_NOISE = {
    "apprentice", "apprenticeship", "apprenticeships", "trainee", "graduate",
    "level", "band", "eo", "heo", "seo", "aa", "ao", "scheme", "programme",
    "program", "new", "tab", "opens", "in", "a", "the", "and", "of", "for",
}
_TITLE_BRACKET_RE = re.compile(r"\([^)]*\)")


def _norm_employer(name: str) -> str:
    n = _EMPLOYER_NOISE_RE.sub(" ", (name or "").casefold())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", n).split())


def _title_core(title: str) -> frozenset:
    """The tokens that distinguish one role from another."""
    t = _TITLE_BRACKET_RE.sub(" ", (title or "").casefold())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return frozenset(w for w in t.split()
                     if w not in _TITLE_NOISE and not w.isdigit() and len(w) > 2)


def likely_duplicate_groups(vacs):
    """Groups of listings that look like the same role seen twice.

    Grouped on normalised employer plus the distinguishing title tokens, and
    only when there are at least two such tokens — one generic token would pull
    unrelated roles together. Returns the groups with more than one reference.
    """
    buckets = {}
    for v in vacs:
        core = _title_core(v.title)
        if len(core) < 2:
            continue
        buckets.setdefault((_norm_employer(v.employer_name_raw), core), []).append(v)

    groups = []
    for (employer, core), members in buckets.items():
        refs = {m.reference for m in members}
        if len(refs) < 2:
            continue
        groups.append({
            "employer": employer,
            "title_tokens": sorted(core),
            "sources": sorted({m.source for m in members}),
            "cross_source": len({m.source for m in members}) > 1,
            "members": sorted(
                ({"ref": m.reference, "title": m.title, "source": m.source,
                  "level": m.level, "url": m.url} for m in members),
                key=lambda d: d["ref"]),
        })
    return sorted(groups, key=lambda g: (-len(g["members"]), g["employer"]))


def _merge_counts(dicts):
    out = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _summarise(by_ref, per_search, errors, searches, use_employers,
               found_by=None):
    vacs = list(by_ref.values())
    found_by = found_by or {}
    levels = Counter(v.level if v.level is not None else "unknown" for v in vacs)

    # What the production relevance filter would drop, and why. Run over the
    # same listings the scan would see, so "excluded: N" is the real number.
    verdicts = {v.reference: judge(v) for v in vacs}
    kept = [v for v in vacs if verdicts[v.reference].keep]
    dropped = [v for v in vacs if not verdicts[v.reference].keep]
    exclusions = {
        "excluded": len(dropped),
        "kept": len(kept),
        "by_reason": dict(sorted(Counter(
            verdicts[v.reference].reason for v in dropped).items())),
        # Which standards/phrases did the dropping, with counts. This is the
        # safety net for the non_digital_standard rule: DIGITAL_STANDARDS is a
        # curated list and an omission from it silently excludes a real digital
        # role. Printing the excluded standards by frequency makes that
        # reviewable — a name that should be digital stands out and can be added
        # to config without a code change.
        "by_detail": {
            reason: dict(Counter(
                verdicts[v.reference].detail or "(none)"
                for v in dropped if verdicts[v.reference].reason == reason
            ).most_common())
            for reason in sorted({verdicts[v.reference].reason for v in dropped})
        },
        "kept_by_reason": dict(sorted(Counter(
            verdicts[v.reference].reason for v in kept).items())),
        # Every excluded listing, with the reason — the audit trail.
        "listings": sorted(
            ({"ref": v.reference, "title": v.title,
              "employer": v.employer_name_raw,
              "course": v.training_course,
              "reason": verdicts[v.reference].reason,
              "detail": verdicts[v.reference].detail,
              "url": v.url} for v in dropped),
            key=lambda d: (d["reason"], d["title"])),
    }

    # ── Route sweep vs keyword run ───────────────────────────────────────
    # Two different ways of finding the same thing, so the interesting number is
    # what each one MISSES. The sweep selects on what an apprenticeship IS; the
    # keywords select on what its advert SAYS. Neither is a superset of the
    # other, and the symmetric difference is the evidence for keeping both.
    sweep_refs = {ref for ref, terms in found_by.items() if ROUTE_TERM in terms}
    keyword_refs = {ref for ref, terms in found_by.items()
                    if any(t != ROUTE_TERM for t in terms)}
    # The keyword run's comparable subset: what it found that IS digital and
    # Level 4+, which is what the sweep is restricted to.
    keyword_digital_l4 = {
        v.reference for v in vacs
        if v.reference in keyword_refs and digital_route(v)
        and v.level is not None and v.level >= 4}

    sweep_row = next((r for r in per_search if r.get("term") == ROUTE_TERM), None)

    def _describe(refs):
        return sorted(
            ({"ref": r,
              "title": by_ref[r].title,
              "employer": by_ref[r].employer_name_raw,
              "level": by_ref[r].level,
              "course": by_ref[r].training_course,
              "source": by_ref[r].source,
              "url": by_ref[r].url} for r in refs if r in by_ref),
            key=lambda d: (d["level"] is None, -(d["level"] or 0), d["title"]))

    route_comparison = {
        "ran": sweep_row is not None,
        "sweep_slots_reported": (sweep_row or {}).get("site_reported_total"),
        "sweep_slots_fetched": (sweep_row or {}).get("cards_seen"),
        "sweep_duplicate_cards": (sweep_row or {}).get("duplicate_cards"),
        "sweep_stopped_because": (sweep_row or {}).get("stopped_because"),
        "sweep_distinct": len(sweep_refs),
        "keyword_digital_level_4_plus": len(keyword_digital_l4),
        "in_both": len(sweep_refs & keyword_digital_l4),
        "only_in_sweep": _describe(sweep_refs - keyword_digital_l4),
        "only_in_keywords": _describe(keyword_digital_l4 - sweep_refs),
    }

    # Per-keyword attribution: which terms found what, and which vacancies only
    # the widened terms reach. Computed from one run rather than by diffing two.
    original = set(scout_config.ORIGINAL_SEARCH_PHRASES)
    only_new_refs = {ref for ref, terms in found_by.items()
                     if not (set(terms) & original) and set(terms) != {ROUTE_TERM}}
    by_keyword = {}
    for ref, terms in found_by.items():
        for term in set(terms):
            if term == ROUTE_TERM:
                continue        # reported in its own section, not as a keyword
            entry = by_keyword.setdefault(term, {
                "distinct_vacancies": 0, "new_vs_original_six": 0,
                "is_original_phrase": term in original})
            entry["distinct_vacancies"] += 1
            if ref in only_new_refs:
                entry["new_vs_original_six"] += 1
    by_keyword = dict(sorted(
        by_keyword.items(),
        key=lambda kv: (-kv[1]["distinct_vacancies"], kv[0])))
    lvl4plus = [v for v in vacs if v.level is not None and v.level >= 4]
    lvl6plus = [v for v in vacs if v.level is not None and v.level >= 6]
    digital = [v for v in vacs if digital_route(v)]
    unclassified = [v for v in vacs if route_unclassified(v)]
    external = [v for v in vacs if v.source != SOURCE_FINDAPPRENTICESHIP]
    level_unknown = [v for v in vacs if v.level is None]
    both = [v for v in vacs
            if digital_route(v) and v.level is not None and v.level >= 4]

    # How far short of the site's own numbers the scrape came. The single most
    # useful figure for diagnosing coverage: if gov.uk reports far more than we
    # collected, the shortfall is in the scraper, not the corpus.
    # gov.uk's "N results found" counts RESULT SLOTS, not vacancies: a
    # multi-location vacancy is rendered once per location and each copy is
    # counted. So the site total must be compared against the CARDS we saw, and
    # the number of distinct vacancies is a separate, smaller figure. Comparing
    # distinct refs directly against the site total reads as a shortfall when
    # nothing is missing at all — which is exactly how "16 dropped" was
    # misdiagnosed. Confirmed by curl: jcode=2017911 appears 8 times on page 1
    # of "software developer".
    reported = [r["site_reported_total"] for r in per_search
                if r.get("site_reported_total") is not None]
    site_total = sum(reported) if reported else None
    cards_seen = sum(r.get("cards_seen", 0) for r in per_search)
    duplicate_cards = sum(r.get("duplicate_cards", 0) for r in per_search)
    coverage = {
        "searches_reporting_a_total": len(reported),
        # Slots the site says exist, and how many of those slots we fetched.
        "sum_site_reported_totals": site_total,
        "total_cards_seen": cards_seen,
        # Slots we never fetched: the only figure that is a REAL shortfall.
        "slots_not_fetched": (None if site_total is None
                              else site_total - cards_seen),
        # Slots that were the same vacancy again, under another location.
        "site_side_duplicate_cards": duplicate_cards,
        "extra_locations_merged": sum(
            r.get("extra_locations", 0) for r in per_search),
        "searches_with_duplicates": sum(
            1 for r in per_search if r.get("duplicate_cards", 0)),
        # Distinct vacancies, which is what a person actually wants to see.
        "distinct_listings": len(by_ref),
        "sum_returned": sum(r["returned"] for r in per_search),
        "total_dropped_no_ref": sum(r.get("dropped_no_ref", 0) for r in per_search),
        "total_dropped_below_level": sum(
            r.get("dropped_below_level", 0) for r in per_search),
        "searches_stopped_at_page_cap": sum(
            1 for r in per_search if r.get("stopped_because") == "page_cap"),
        # A capped search that also had duplicates is the crowding-out case:
        # location copies eat page slots that other vacancies could have used.
        "capped_searches_with_duplicates": sum(
            1 for r in per_search
            if r.get("stopped_because") == "page_cap" and r.get("duplicate_cards", 0)),
        "unmatched_href_samples": sorted({
            h for r in per_search for h in r.get("unmatched_href_samples", [])}),
        "ref_kinds": _merge_counts(r.get("ref_kinds", {}) for r in per_search),
    }

    return {
        "classifier_version": CLASSIFIER_VERSION,
        "searches_issued": len(searches),
        "employer_sweep": use_employers,
        "query_params_sent": GovUkScraper.search_params("<term>"),
        "route_sweep_params": GovUkScraper.route_sweep_params(),
        "errors": errors,
        "totals": {
            "unique_results": len(vacs),
            "level_4_plus": len(lvl4plus),
            "level_6_plus": len(lvl6plus),
            "digital_route": len(digital),
            "digital_route_and_level_4_plus": len(both),
            "route_unclassified": len(unclassified),
            "level_unknown": len(level_unknown),
            # Off-site cross-posts. They carry no level or training course, so
            # they are reported in their own right rather than counted as a
            # parsing failure or silently excluded by a level filter.
            "external_listings": len(external),
            "external_level_unknown": sum(
                1 for v in external if v.level is None),
            "onsite_level_unknown": sum(
                1 for v in level_unknown
                if v.source == SOURCE_FINDAPPRENTICESHIP),
            "multi_location_listings": sum(
                1 for v in vacs if v.location_count > 1),
        },
        "coverage": coverage,
        "exclusions": exclusions,
        "route_comparison": route_comparison,
        "by_keyword": by_keyword,
        "only_new_keywords_found": len(only_new_refs),
        "original_phrases": list(scout_config.ORIGINAL_SEARCH_PHRASES),
        "by_level": {str(k): v for k, v in sorted(
            levels.items(), key=lambda kv: (kv[0] == "unknown", kv[0]))},
        "by_source": {k: v for k, v in sorted(
            Counter(v.source for v in vacs).items())},
        "likely_duplicate_groups": likely_duplicate_groups(vacs),
        "by_course": {c: n for c, n in Counter(
            course_name(v) or "(no course)" for v in vacs).most_common()},
        "per_search": per_search,
        "listings": sorted(
            ({
                "ref": v.reference,
                "title": v.title,
                "employer": v.employer_name_raw,
                "location": v.location,
                "locations": list(v.locations),
                "location_count": v.location_count,
                "level": v.level,
                "course": v.training_course,
                "course_normalised": course_name(v),
                "digital_route": digital_route(v),
                "kept_by_filter": verdicts[v.reference].keep,
                "filter_reason": verdicts[v.reference].reason,
                "found_by": sorted(set(found_by.get(v.reference, []))),
                "source": v.source,
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
                           "duplicate_cards": stats.get("duplicate_cards", 0),
                           "extra_locations": stats.get("extra_locations", 0),
                           "stopped_because": None,
                           "ref_kinds": stats.get("ref_kinds", {}),
                           "unmatched_href_samples": stats.get("unmatched_href_samples", [])})
    return _summarise(by_ref, per_search, [], names, False)


# ── Reporting ────────────────────────────────────────────────────────────────

def report(snap):
    t = snap["totals"]
    print()
    print(f"keyword query              : {snap.get('query_params_sent')}")
    if snap.get("route_sweep_params"):
        print(f"route sweep query          : {snap['route_sweep_params']}")
    parts = []
    if (snap.get("route_comparison") or {}).get("ran"):
        parts.append("route sweep")
    parts.append("keyword scans")
    if snap["employer_sweep"]:
        parts.append("employer sweep")
    print(f"searches issued            : {snap['searches_issued']}"
          f"  ({' + '.join(parts)})")
    print(f"unique listings returned   : {t['unique_results']}")
    print(f"  Level 4+                 : {t['level_4_plus']}")
    print(f"  Level 6+                 : {t['level_6_plus']}")
    print(f"  digital route (standard) : {t['digital_route']}")
    print(f"  digital route AND L4+    : {t['digital_route_and_level_4_plus']}")
    print(f"  course text missing      : {t['route_unclassified']}"
          f"   (route cannot be read)")
    print()
    print("  level unknown — counted separately, NEVER dropped:")
    print(f"    total level unknown      : {t.get('level_unknown', 0)}")
    print(f"      of which off-site      : {t.get('external_level_unknown', 0)}"
          f"   (cross-posts carry no level)")
    print(f"      of which on gov.uk     : {t.get('onsite_level_unknown', 0)}"
          f"   (level not parsed — look at these)")
    print(f"    off-site listings        : {t.get('external_listings', 0)}")
    print(f"  multi-location roles       : {t.get('multi_location_listings', 0)}"
          f"   (merged from several cards)")

    if snap.get("by_source"):
        print()
        print("by source:")
        for src, n in snap["by_source"].items():
            print(f"    {src:<22} {n:>5}")

    c = snap.get("coverage") or {}
    print()
    print("coverage — gov.uk counts RESULT SLOTS, not vacancies:")
    print("  (a multi-location vacancy is one card per location, each counted)")
    site = c.get("sum_site_reported_totals")
    print(f"    searches reporting a total : {c.get('searches_reporting_a_total')}"
          f" / {snap['searches_issued']}")
    print(f"    result slots site reports  : "
          f"{site if site is not None else 'n/a (title not parsed)'}")
    print(f"    slots we fetched (cards)   : {c.get('total_cards_seen')}")
    not_fetched = c.get("slots_not_fetched")
    if not_fetched is not None:
        pct = f"  ({100 * not_fetched / site:.0f}% of slots)" if site else ""
        verdict = "  <-- TRUE shortfall" if not_fetched > 0 else "  (none missing)"
        print(f"    slots never fetched        : {not_fetched}{pct}{verdict}")
    print(f"    of fetched slots:")
    print(f"      site-side duplicates     : {c.get('site_side_duplicate_cards')}"
          f"   (same vacancy, another location)")
    print(f"      locations merged in      : {c.get('extra_locations_merged')}")
    print(f"      unparseable cards        : {c.get('total_dropped_no_ref')}")
    print(f"      below the level floor    : {c.get('total_dropped_below_level')}")
    print(f"    DISTINCT vacancies         : {c.get('distinct_listings')}"
          f"   <-- what you actually have")
    print(f"    searches hitting page cap  : {c.get('searches_stopped_at_page_cap')}"
          f" / {snap['searches_issued']}")
    capped_dup = c.get("capped_searches_with_duplicates") or 0
    if capped_dup:
        print(f"      ...of which ALSO had dups: {capped_dup}"
              f"   <-- location copies are crowding out other vacancies")
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
    print(f"    {'term':<26}{'uniq':>5}{'slots':>6}{'cards':>6}{'dup':>5}"
          f"{'nodrop':>7}{'pages':>6}  stopped")
    for r in snap["per_search"]:
        site_s = r.get("site_reported_total")
        print(f"    {r['term'][:25]:<26}{r['returned']:>5}"
              f"{(site_s if site_s is not None else '-'):>6}"
              f"{r.get('cards_seen', 0):>6}{r.get('duplicate_cards', 0):>5}"
              f"{r.get('dropped_no_ref', 0):>7}{r.get('pages_fetched', 0):>6}"
              f"  {r.get('stopped_because') or '-'}")
    print("    uniq = distinct vacancies | slots = site's count | "
          "dup = cards that repeated a vacancy")

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

    rc = snap.get("route_comparison") or {}
    if rc.get("ran"):
        print()
        print("route sweep vs keyword run")
        print("  (?routeIds=7&levelIds=4..7 — selects on what the "
              "apprenticeship IS,")
        print("   not on what its advert says, so neither method is a superset)")
        slots = rc.get("sweep_slots_reported")
        print(f"    sweep: result slots reported : "
              f"{slots if slots is not None else 'n/a'}")
        print(f"    sweep: slots fetched         : {rc.get('sweep_slots_fetched')}")
        print(f"    sweep: duplicate cards       : "
              f"{rc.get('sweep_duplicate_cards')}   (same vacancy, another location)")
        print(f"    sweep: DISTINCT vacancies    : {rc.get('sweep_distinct')}")
        print(f"    sweep: stopped because       : {rc.get('sweep_stopped_because')}")
        print(f"    keywords: digital AND L4+    : "
              f"{rc.get('keyword_digital_level_4_plus')}")
        print(f"    found by both                : {rc.get('in_both')}")
        print(f"    only the sweep found         : {len(rc.get('only_in_sweep') or [])}"
              f"   <-- the keywords MISS these")
        print(f"    only the keywords found      : "
              f"{len(rc.get('only_in_keywords') or [])}"
              f"   <-- outside the sweep's filter")

        for label, rows, note in (
            ("ONLY the route sweep found (keyword wording missed them)",
             rc.get("only_in_sweep") or [],
             "these are the argument for the sweep"),
            ("ONLY the keyword run found (not in the digital L4+ route)",
             rc.get("only_in_keywords") or [],
             "check whether each is really digital, or really Level 4+"),
        ):
            if not rows:
                continue
            print()
            print(f"  {label} — {len(rows)}:")
            print(f"  ({note})")
            for d in rows[:25]:
                lvl = d["level"] if d["level"] is not None else "?"
                print(f"    L{str(lvl):<3} {d['ref']:<18}{d['title'][:38]:<40}"
                      f"{d['employer'][:20]}")
                if d.get("course"):
                    print(f"         course: {d['course'][:60]}")
            if len(rows) > 25:
                print(f"    ... and {len(rows) - 25} more "
                      f"(full list in the JSON snapshot)")

    ex = snap.get("exclusions") or {}
    if ex:
        print()
        print(f"excluded: {ex.get('excluded', 0)}"
              f"   (kept {ex.get('kept', 0)} of "
              f"{ex.get('excluded', 0) + ex.get('kept', 0)})")
        for reason, n in (ex.get("by_reason") or {}).items():
            print(f"    {reason:<24}{n:>5}")
        details = ex.get("by_detail") or {}
        if details.get("non_digital_standard"):
            print("  excluded standards, most frequent first — CHECK THESE:")
            print("  (a name here that should count as digital means "
                  "DIGITAL_STANDARDS is")
            print("   missing it; add it to config/scout_filters.json)")
            for name, n in list(details["non_digital_standard"].items())[:20]:
                print(f"    {name[:52]:<54}{n:>5}")
            if len(details["non_digital_standard"]) > 20:
                print(f"    ... and {len(details['non_digital_standard']) - 20} more")
        if ex.get("kept_by_reason"):
            print("  kept because:")
            for reason, n in ex["kept_by_reason"].items():
                print(f"    {reason:<24}{n:>5}")
        if ex.get("listings"):
            print("  what was dropped (title | employer | reason):")
            for d in ex["listings"][:25]:
                print(f"    {d['title'][:38]:<40}{d['employer'][:22]:<24}"
                      f"{d['reason']}:{d['detail'][:18]}")
            if len(ex["listings"]) > 25:
                print(f"    ... and {len(ex['listings']) - 25} more "
                      f"(full list in the JSON snapshot)")

    bykw = snap.get("by_keyword") or {}
    if bykw:
        print()
        print(f"per keyword — {snap.get('only_new_keywords_found', 0)} vacancies "
              f"are reachable ONLY via the widened terms:")
        print(f"    {'term':<24}{'uniq':>6}{'new':>6}  origin")
        for term, d in bykw.items():
            origin = "original six" if d["is_original_phrase"] else "added"
            print(f"    {term[:23]:<24}{d['distinct_vacancies']:>6}"
                  f"{d['new_vs_original_six']:>6}  {origin}")
        print("    uniq = distinct vacancies this term found | "
              "new = of those, not found by any original phrase")

    groups = snap.get("likely_duplicate_groups") or []
    if groups:
        cross = sum(1 for g in groups if g["cross_source"])
        print()
        print(f"likely cross-posts — FLAGGED ONLY, nothing merged ({len(groups)} "
              f"group(s), {cross} spanning two sources):")
        for g in groups[:10]:
            tag = "  [cross-source]" if g["cross_source"] else ""
            print(f"    {g['employer'][:44]:<46}{'+'.join(g['title_tokens'])[:28]}{tag}")
            for m in g["members"]:
                lvl = m["level"] if m["level"] is not None else "?"
                print(f"        L{lvl:<3} {m['source']:<20}{m['ref']:<20}"
                      f"{m['title'][:38]}")
        if len(groups) > 10:
            print(f"    ... and {len(groups) - 10} more")
        print("    Phase 2 owns dedupe; these are reported so the scale is known.")

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

    keys = ("unique_results", "level_4_plus", "level_6_plus", "level_unknown",
            "external_listings", "multi_location_listings", "digital_route",
            "digital_route_and_level_4_plus", "route_unclassified")
    label = {"unique_results": "unique listings", "level_4_plus": "Level 4+",
             "level_6_plus": "Level 6+", "level_unknown": "level unknown",
             "external_listings": "off-site listings",
             "multi_location_listings": "multi-location roles",
             "digital_route": "digital route",
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
    for snap_, side in ((a, "before"), (b, "after")):
        exc = (snap_.get("exclusions") or {}).get("excluded")
        if exc is not None:
            print(f"{'  excluded (' + side + ')':<26}{exc:>9}{'':>9}{'n/c':>9}")

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
    ap.add_argument("--no-route-sweep", action="store_true",
                    help="skip the digital-route sweep (keyword searches only)")
    ap.add_argument("--employers", action="store_true",
                    help="also sweep every watched employer alias (slow, heavy)")
    ap.add_argument("--min-level", type=int, default=1,
                    help="level floor passed to the scraper (default 1 = unfiltered)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="override the per-search page cap "
                         "(default: config max_pages, currently 20)")
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
        searches = _searches(args.employers, route_sweep=not args.no_route_sweep)
        pages = args.max_pages or scout_config.max_pages()
        worst = len(searches) * pages
        print(f"searches            : {len(searches)}")
        print(f"max pages each      : {pages}"
              f"   (SCOUT_MAX_PAGES / config max_pages)")
        print(f"worst-case requests : {worst}")
        print(f"worst-case time     : ~{worst}s"
              f" (~{worst / 60:.1f} min) at 1 request/second")
        print()
        print("The cap is a ceiling, not a budget. A search stops as soon as the")
        print("site's own result total is accounted for, so the realistic cost is")
        print("far lower — only searches with more slots than the cap can reach")
        print("walk all the way. Run without --estimate and read 'pages' in the")
        print("per-search table for what it actually cost.")
        print()
        for kind, term in searches[:20]:
            print(f"    {kind}: {term}")
        if len(searches) > 20:
            print(f"    ... and {len(searches) - 20} more")
        return 0

    if args.fixture:
        print(f"Counting fixtures in {args.fixture} (no network)")
        snap = measure_fixture(args.fixture, min_level=args.min_level)
    else:
        searches = _searches(args.employers, route_sweep=not args.no_route_sweep)
        cap = args.max_pages or scout_config.max_pages()
        print(f"Measuring gov.uk coverage — {len(searches)} searches, "
              f">=1s apart, up to {cap} pages each "
              f"(worst case ~{len(searches) * cap} requests)")
        snap = measure(use_employers=args.employers, min_level=args.min_level,
                       max_pages=args.max_pages,
                       route_sweep=not args.no_route_sweep)

    report(snap)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=2)
        print()
        print(f"snapshot written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
