"""The Phase 1 coverage measuring stick (scripts/scout_coverage_baseline.py).

This is the tool that produces the before/after numbers, so its counting has to
be trustworthy and stable: if the measuring stick drifts between two runs the
comparison is meaningless.

The metric is deliberately NOT a title-keyword match. Scoring titles that were
found by tech keywords against those same tech keywords is circular — it can
only ever return ~100%, which is what made the first baseline read "42 of 43
digital". It now classifies on the apprenticeship standard (gov.uk's "Training
course"), which the employer picks from a fixed national catalogue and which is
therefore independent of the query. The non-circularity property itself is
pinned below.
"""
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import scout_config  # noqa: E402
from services.scrapers.gov_uk import RawVacancy  # noqa: E402

_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "scout_coverage_baseline.py")
_spec = importlib.util.spec_from_file_location("scout_coverage_baseline", _SPEC_PATH)
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)

def _vac(ref="VAC1", title="", course="", level=None, employer="ACME"):
    return RawVacancy(reference=ref, title=title, employer_name_raw=employer,
                      training_course=course, level=level)


# ── The metric does not depend on the search keyword ─────────────────────────

def test_classification_ignores_the_title_entirely():
    """The circularity fix, stated as a property.

    Same standard, wildly different titles -> same classification. A title-based
    metric could not do this, and a title-based metric is what made the search
    term leak into the measurement.
    """
    for title in ["Cyber Security Apprentice", "Apprentice", "",
                  "Trainee (Ref 8812)", "Kitchen Assistant"]:
        v = _vac(title=title, course="Cyber security technologist (level 4)")
        assert baseline.digital_route(v) is True, f"title changed the verdict: {title!r}"


def test_a_digital_sounding_title_is_not_enough():
    """"Security Guard" matched the OLD keyword classifier on `security`. Its
    standard is a physical-security one, so the route metric rejects it."""
    v = _vac(title="Security Guard Apprentice", course="Security operative (level 2)")
    assert baseline.digital_route(v) is False


def test_a_plain_title_with_a_digital_standard_still_counts():
    """The case a title-keyword rule misses: gov.uk titles are often bare."""
    v = _vac(title="Apprentice", course="Software developer (level 4)")
    assert baseline.digital_route(v) is True


@pytest.mark.parametrize("course", [
    "Cyber security technologist (level 4)",
    "Software developer (level 4)",
    "Data analyst (level 4)",
    "Network engineer (level 6)",
    "Digital and technology solutions professional (level 6)",
    "Information communications technician (level 3)",
    "DevOps engineer (level 4)",
    "Cloud engineer (level 4)",
])
def test_digital_standards_are_recognised(course):
    assert baseline.digital_route(_vac(course=course)) is True


@pytest.mark.parametrize("course", [
    "Commis chef (level 2)",
    "Project manager (level 4)",
    "Business administrator (level 3)",
    "Early years educator (level 3)",
    "Bricklayer (level 2)",
    "Security operative (level 2)",
    "Dental nurse (level 3)",
])
def test_non_digital_standards_are_rejected(course):
    assert baseline.digital_route(_vac(course=course)) is False


# ── Course-name normalisation ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Software developer (level 4)", "software developer"),
    ("Software developer level 4", "software developer"),
    ("Software developer [Level 4]", "software developer"),
    ("  Software   developer  (Level 4) ", "software developer"),
    ("SOFTWARE DEVELOPER (LEVEL 4)", "software developer"),
    ("", ""),
])
def test_course_name_normalisation(raw, expected):
    assert baseline.course_name(_vac(course=raw)) == expected


def test_missing_course_is_unclassified_not_non_digital():
    """These are listings the metric cannot speak to. Counting them as
    "not digital" would overstate how much of the corpus it actually covers."""
    v = _vac(title="Cyber Security Apprentice", course="")
    assert baseline.route_unclassified(v) is True
    assert baseline.digital_route(v) is False


# ── Counting ─────────────────────────────────────────────────────────────────

def test_summary_counts_levels_and_digital_route():
    vacs = {
        "A": _vac("A", "x", "Cyber security technologist (level 4)", level=4),
        "B": _vac("B", "x", "Software developer (level 6)", level=6),
        "C": _vac("C", "x", "Commis chef (level 2)", level=2),
        "D": _vac("D", "x", "Data analyst (level 3)", level=3),
        "E": _vac("E", "x", "", level=None),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    t = s["totals"]

    assert t["unique_results"] == 5
    assert t["level_4_plus"] == 2            # A, B
    assert t["level_6_plus"] == 1            # B
    assert t["digital_route"] == 3           # A, B, D
    assert t["digital_route_and_level_4_plus"] == 2
    assert t["route_unclassified"] == 1      # E
    assert t["level_unknown"] == 1
    assert s["by_level"] == {"2": 1, "3": 1, "4": 1, "6": 1, "unknown": 1}


def test_summary_records_the_query_that_was_sent():
    """So a snapshot says what produced it, not just what came back."""
    s = baseline._summarise({}, [], [], [], False)
    assert s["query_params_sent"] == {"searchTerm": "<term>", "sort": "AgeAsc"}


def test_summary_reports_the_shortfall_against_the_site():
    per_search = [
        {"term": "data", "returned": 29, "site_reported_total": 312,
         "cards_seen": 30, "dropped_no_ref": 1, "dropped_below_level": 0,
         "stopped_because": "repeats", "unmatched_href_samples": ["/apprenticeship/999"]},
        {"term": "devops", "returned": 0, "site_reported_total": 20,
         "cards_seen": 0, "dropped_no_ref": 0, "dropped_below_level": 0,
         "stopped_because": "empty_page", "unmatched_href_samples": []},
    ]
    c = baseline._summarise({}, per_search, [], ["a", "b"], False)["coverage"]

    assert c["sum_site_reported_totals"] == 332
    assert c["sum_returned"] == 29
    assert c["total_cards_seen"] == 30
    assert c["total_dropped_no_ref"] == 1
    assert c["unmatched_href_samples"] == ["/apprenticeship/999"]


def test_summary_counts_searches_capped_by_pagination():
    per_search = [
        {"term": "a", "returned": 50, "site_reported_total": 900,
         "stopped_because": "page_cap"},
        {"term": "b", "returned": 3, "site_reported_total": 3,
         "stopped_because": "repeats"},
    ]
    c = baseline._summarise({}, per_search, [], ["a", "b"], False)["coverage"]
    assert c["searches_stopped_at_page_cap"] == 1


def test_listings_are_sorted_highest_level_first():
    vacs = {
        "A": _vac("A", "Low", level=2),
        "B": _vac("B", "High", level=6),
        "C": _vac("C", "Unknown", level=None),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    assert [x["level"] for x in s["listings"]] == [6, 2, None]


def test_snapshot_records_classifier_version():
    """A diff across a changed classifier is not a real comparison."""
    s = baseline._summarise({}, [], [], [], False)
    assert s["classifier_version"] == baseline.CLASSIFIER_VERSION
    assert baseline.CLASSIFIER_VERSION >= 4, \
        "bump the version when the classifier changes, or diffs lie"


def test_fixture_mode_dedupes_across_pages(tmp_path):
    def card(ref, title, course):
        return (f'<li class="das-search-results__list-item">'
                f'<a href="/apprenticeship/{ref}">{title}</a>'
                f'<p class="govuk-body">ACME</p>'
                f'<p class="govuk-body">London</p>'
                f'<p class="govuk-body"><b>Training course</b> {course}</p></li>')

    page1 = card("VAC1", "Cyber Apprentice", "Cyber security technologist (level 4)")
    page2 = (card("VAC1", "Cyber Apprentice", "Cyber security technologist (level 4)")
             + card("VAC2", "Cloud Apprentice", "Cloud engineer (level 4)"))
    (tmp_path / "p1.html").write_text(f"<html><title>1 result found</title><ul>{page1}</ul></html>")
    (tmp_path / "p2.html").write_text(f"<html><title>2 results found</title><ul>{page2}</ul></html>")

    s = baseline.measure_fixture(str(tmp_path), min_level=1)
    assert s["totals"]["unique_results"] == 2
    assert s["totals"]["digital_route"] == 2


def test_fixture_mode_keeps_a_numeric_reference(tmp_path):
    """A numeric reference used to be dropped here. The offline path has to
    reflect the fix, or it cannot be used to reproduce a coverage gap."""
    (tmp_path / "p.html").write_text(
        '<html><title>1 result found</title><ul>'
        '<li class="das-search-results__list-item">'
        '<a href="/apprenticeship/1234567">Data Apprentice</a>'
        '<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
        '<p class="govuk-body"><b>Training course</b> Data analyst (level 4)</p>'
        '</li></ul></html>')

    s = baseline.measure_fixture(str(tmp_path), min_level=1)
    assert s["totals"]["unique_results"] == 1
    assert s["coverage"]["total_dropped_no_ref"] == 0
    assert s["coverage"]["ref_kinds"] == {"id": 1}


def test_fixture_mode_surfaces_genuinely_unusable_cards(tmp_path):
    """A card with no usable detail link is still counted and sampled."""
    (tmp_path / "p.html").write_text(
        '<html><title>1 result found</title><ul>'
        '<li class="das-search-results__list-item">'
        '<a href="#save">Save</a>'
        '<p class="govuk-body">ACME</p></li></ul></html>')

    s = baseline.measure_fixture(str(tmp_path), min_level=1)
    assert s["totals"]["unique_results"] == 0
    assert s["coverage"]["total_dropped_no_ref"] == 1
    assert "#save" in s["coverage"]["unmatched_href_samples"]


def test_measurement_writes_no_database_rows(tmp_path):
    """The baseline must not mark anything as seen, or running it would suppress
    the first real alert."""
    import database as db
    db._ensure_scout_tables()
    with db.get_db() as conn:
        before = conn.cursor().execute("SELECT COUNT(*) FROM scout_jobs").fetchone()[0]

    (tmp_path / "p.html").write_text(
        '<html><title>1 result found</title><ul>'
        '<li class="das-search-results__list-item">'
        '<a href="/apprenticeship/VAC999">Cyber Apprentice</a>'
        '<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
        '<p class="govuk-body"><b>Training course</b> Cyber security technologist (level 4)</p>'
        '</li></ul></html>')
    baseline.measure_fixture(str(tmp_path), min_level=1)

    with db.get_db() as conn:
        after = conn.cursor().execute("SELECT COUNT(*) FROM scout_jobs").fetchone()[0]
    assert after == before


# ── --compare must tolerate older snapshots ──────────────────────────────────
# A v3 snapshot carries `digital` (the old circular title-keyword metric) and no
# `digital_route`, `coverage` or `query_params_sent`. Comparing one against a v4
# snapshot has to work: those files are the only record of the earlier runs.

def _v3_snapshot():
    return {
        "classifier_version": 3,
        "searches_issued": 6,
        "employer_sweep": False,
        "errors": [],
        "totals": {"unique_results": 43, "level_4_plus": 19, "level_6_plus": 3,
                   "digital": 42, "digital_and_level_4_plus": 19,
                   "level_unknown": 0},
        "by_level": {"4": 16},
        "per_search": [],
        "listings": [{"ref": "VAC1", "title": "Cyber Apprentice", "level": 4,
                      "employer": "BT"}],
    }


def _v4_snapshot():
    s = baseline._summarise({}, [], [], ["a"] * 6, False)
    s["totals"].update(unique_results=61, level_4_plus=28, level_6_plus=5,
                       digital_route=44, digital_route_and_level_4_plus=24,
                       route_unclassified=2)
    s["listings"] = [{"ref": "VAC1", "title": "Cyber Apprentice", "level": 4,
                      "employer": "BT"},
                     {"ref": "1000284512", "title": "SW Eng", "level": 4,
                      "employer": "ACME"}]
    return s


def _write(tmp_path, name, obj):
    import json
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_compare_handles_a_v3_snapshot_without_digital_route(tmp_path, capsys):
    a = _write(tmp_path, "v3.json", _v3_snapshot())
    b = _write(tmp_path, "v4.json", _v4_snapshot())

    baseline.compare(a, b)          # must not raise
    out = capsys.readouterr().out

    # Classifier-independent metrics still diff.
    assert "+18" in out, "unique listings delta should still be computed"
    assert "+9" in out, "Level 4+ delta should still be computed"
    # Classifier-dependent ones are marked, not diffed.
    assert "n/c" in out
    assert ("classifier versions differ (3 vs "
            f"{baseline.CLASSIFIER_VERSION})") in out


def test_compare_names_the_legacy_metric_rather_than_dropping_it(tmp_path, capsys):
    a = _write(tmp_path, "v3.json", _v3_snapshot())
    b = _write(tmp_path, "v4.json", _v4_snapshot())
    baseline.compare(a, b)
    out = capsys.readouterr().out
    assert "legacy title-keyword" in out
    assert "42" in out, "the old value should still be visible, just labelled"


def test_compare_still_diffs_digital_within_one_version(tmp_path, capsys):
    b = _v4_snapshot()
    a = _v4_snapshot()
    a["totals"]["digital_route"] = 30
    pa = _write(tmp_path, "a.json", a)
    pb = _write(tmp_path, "b.json", b)

    baseline.compare(pa, pb)
    out = capsys.readouterr().out
    assert "+14" in out, "same classifier version must diff the digital metric"
    assert "classifier versions differ" not in out


def test_compare_reports_newly_found_listings(tmp_path, capsys):
    a = _write(tmp_path, "v3.json", _v3_snapshot())
    b = _write(tmp_path, "v4.json", _v4_snapshot())
    baseline.compare(a, b)
    out = capsys.readouterr().out
    assert "newly found : 1" in out
    assert "SW Eng" in out


def test_compare_survives_a_snapshot_with_no_listings_key(tmp_path, capsys):
    a = _v3_snapshot(); del a["listings"]
    pa = _write(tmp_path, "a.json", a)
    pb = _write(tmp_path, "b.json", _v4_snapshot())
    baseline.compare(pa, pb)        # must not raise
    assert "newly found" in capsys.readouterr().out


# ── Dropped-href samples reach the screen ────────────────────────────────────

def test_dropped_href_samples_are_printed_next_to_their_search(capsys):
    """The last run reported "DROPPED 16" with no sign of what. The samples now
    print against the search that produced them, not only in a summary."""
    card = ('<li class="das-search-results__list-item">'
            '<a href="#save">Save</a><p class="govuk-body">ACME</p></li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"

    class _Resp:
        status_code = 200
        text = html

        def raise_for_status(self):
            pass

    import services.apprenticeships as appr
    from services.scrapers.gov_uk import GovUkScraper

    real_init = GovUkScraper.__init__

    def patched_init(self, *a, **k):
        real_init(self, *a, **k)
        self.min_interval = 0
        self.session.get = lambda *_a, **_k: _Resp()

    orig_keywords = appr.keyword_scans
    GovUkScraper.__init__ = patched_init
    appr.keyword_scans = lambda: ["software developer"]
    try:
        baseline.measure(use_employers=False, min_level=1, verbose=True)
    finally:
        GovUkScraper.__init__ = real_init
        appr.keyword_scans = orig_keywords

    out = capsys.readouterr().out
    assert "DROPPED 1" in out
    assert "could not parse" in out
    assert "#save" in out, "the actual href must be on screen"


def test_non_vac_reference_formats_are_flagged_on_screen(capsys):
    snap = baseline._summarise(
        {}, [{"term": "software developer", "returned": 21,
              "site_reported_total": 21, "pages_fetched": 1, "cards_seen": 21,
              "dropped_no_ref": 0, "dropped_below_level": 0,
              "stopped_because": "repeats", "ref_kinds": {"vac": 5, "id": 16},
              "unmatched_href_samples": []}],
        [], ["a"], False)
    baseline.report(snap)
    out = capsys.readouterr().out
    assert "reference formats seen" in out
    assert "id=16" in out and "vac=5" in out


def test_report_says_so_when_drops_had_no_captured_hrefs(capsys):
    """Distinguishes "nothing captured" from "nothing dropped" — the ambiguity
    that made the last report unreadable."""
    snap = baseline._summarise(
        {}, [{"term": "x", "returned": 0, "site_reported_total": 5,
              "pages_fetched": 1, "cards_seen": 5, "dropped_no_ref": 5,
              "dropped_below_level": 0, "stopped_because": "empty_page",
              "ref_kinds": {}, "unmatched_href_samples": []}],
        [], ["a"], False)
    baseline.report(snap)
    out = capsys.readouterr().out
    assert "none captured" in out


# ── Source field and off-site listings ───────────────────────────────────────

def _ext(ref, title, employer, jcode, level=None):
    return RawVacancy(
        reference=ref, title=title, employer_name_raw=employer,
        training_course="", level=level, source="civil_service",
        url=f"https://www.civilservicejobs.service.gov.uk/csr/viewjob.cgi?jcode={jcode}")


def _native(ref, title, employer, course="Software developer (level 4)", level=4):
    return RawVacancy(
        reference=ref, title=title, employer_name_raw=employer,
        training_course=course, level=level, source="findapprenticeship",
        url=f"https://www.findapprenticeship.service.gov.uk/apprenticeship/{ref}")


def test_summary_splits_level_unknown_by_source():
    """An off-site listing has no level because the system has none to give.
    An on-site listing with no level means the parse missed something. Those are
    different problems and must not share one number."""
    vacs = {
        "a": _native("VAC1", "Software Developer Apprentice", "IBM"),
        "b": _ext("jcode:2017911", "Software Developer Apprentice", "HSE", 2017911),
        "c": _native("VAC2", "Apprentice", "ACME", course="", level=None),
    }
    t = baseline._summarise(vacs, [], [], ["kw"], False)["totals"]

    assert t["level_unknown"] == 2
    assert t["external_level_unknown"] == 1
    assert t["onsite_level_unknown"] == 1
    assert t["external_listings"] == 1
    assert t["level_4_plus"] == 1


def test_summary_counts_by_source():
    vacs = {
        "a": _native("VAC1", "Software Developer Apprentice", "IBM"),
        "b": _ext("jcode:1", "Data Apprentice", "HSE", 1),
        "c": _ext("jcode:2", "Cyber Apprentice", "DWP", 2),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    assert s["by_source"] == {"civil_service": 2, "findapprenticeship": 1}


def test_listings_carry_their_source():
    vacs = {"b": _ext("jcode:2017911", "Software Developer Apprentice", "HSE", 2017911)}
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    assert s["listings"][0]["source"] == "civil_service"


def test_off_site_listings_are_never_counted_as_level_4_plus():
    """They must be visible, but a null level is not evidence of Level 4."""
    vacs = {"b": _ext("jcode:1", "Software Developer Apprentice", "HSE", 1)}
    t = baseline._summarise(vacs, [], [], ["kw"], False)["totals"]
    assert t["level_4_plus"] == 0
    assert t["unique_results"] == 1
    assert t["level_unknown"] == 1


# ── Likely cross-posts: FLAG ONLY. Phase 2 owns dedupe. ──────────────────────

def test_the_hse_pair_is_flagged_as_a_likely_duplicate():
    """The real cross-post: one HSE role listed natively with a VAC reference
    and a level, and again off-site with neither."""
    native = _native("VAC2000060231", "Software Developer Apprentice",
                     "Health and Safety Executive")
    external = _ext("jcode:2017911",
                    "Software Developer Apprentice (Band 5/EO)",
                    "Health and Safety Executive", 2017911)

    groups = baseline.likely_duplicate_groups([native, external])

    assert len(groups) == 1, f"expected one group, got {groups}"
    g = groups[0]
    assert g["cross_source"] is True
    assert sorted(g["sources"]) == ["civil_service", "findapprenticeship"]
    assert {m["ref"] for m in g["members"]} == {"VAC2000060231", "jcode:2017911"}


def test_flagging_does_not_merge_anything():
    """Both rows must survive into the snapshot — Phase 2 decides what merges."""
    vacs = {
        "a": _native("VAC2000060231", "Software Developer Apprentice",
                     "Health and Safety Executive"),
        "b": _ext("jcode:2017911", "Software Developer Apprentice (Band 5/EO)",
                  "Health and Safety Executive", 2017911),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)

    assert s["totals"]["unique_results"] == 2, "a flag must not collapse rows"
    assert len(s["listings"]) == 2
    assert len(s["likely_duplicate_groups"]) == 1


def test_two_different_roles_at_one_employer_do_not_group():
    """The near-miss that must not merge: same employer, different jobs."""
    a = _native("VAC1", "Software Developer Apprentice", "Health and Safety Executive")
    b = _native("VAC2", "Data Analyst Apprentice", "Health and Safety Executive")
    assert baseline.likely_duplicate_groups([a, b]) == []


def test_the_same_role_at_two_employers_does_not_group():
    a = _native("VAC1", "Software Developer Apprentice", "IBM UK")
    b = _native("VAC2", "Software Developer Apprentice", "Capgemini UK")
    assert baseline.likely_duplicate_groups([a, b]) == []


def test_grade_suffixes_do_not_prevent_a_match():
    """"(Band 5/EO)" is a grade, not a different job."""
    a = _native("VAC1", "Software Developer Apprentice", "HSE")
    b = _ext("jcode:1", "Software Developer Apprentice (Band 5/EO)", "HSE", 1)
    assert len(baseline.likely_duplicate_groups([a, b])) == 1


def test_employer_suffixes_do_not_prevent_a_match():
    a = _native("VAC1", "Software Developer Apprentice", "IBM UK Ltd")
    b = _native("VAC2", "Software Developer Apprentice", "IBM")
    assert len(baseline.likely_duplicate_groups([a, b])) == 1


def test_a_generic_title_alone_does_not_group():
    """Two bare "Apprentice" rows share no distinguishing token, so grouping on
    them would sweep unrelated roles together."""
    a = _native("VAC1", "Apprentice", "HSE")
    b = _ext("jcode:1", "Apprentice (Band 5/EO)", "HSE", 1)
    assert baseline.likely_duplicate_groups([a, b]) == []


def test_one_listing_is_never_a_group():
    a = _native("VAC1", "Software Developer Apprentice", "HSE")
    assert baseline.likely_duplicate_groups([a]) == []


def test_the_same_reference_twice_is_not_a_duplicate_group():
    """A group needs two distinct references, not one row seen twice."""
    a = _native("VAC1", "Software Developer Apprentice", "HSE")
    assert baseline.likely_duplicate_groups([a, a]) == []


def test_duplicate_groups_are_reported_on_screen(capsys):
    vacs = {
        "a": _native("VAC2000060231", "Software Developer Apprentice",
                     "Health and Safety Executive"),
        "b": _ext("jcode:2017911", "Software Developer Apprentice (Band 5/EO)",
                  "Health and Safety Executive", 2017911),
    }
    baseline.report(baseline._summarise(vacs, [], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "likely cross-posts" in out
    assert "nothing merged" in out
    assert "cross-source" in out
    assert "jcode:2017911" in out
    assert "VAC2000060231" in out
    assert "Phase 2 owns dedupe" in out


def test_report_shows_level_unknown_split_from_level_4_plus(capsys):
    vacs = {
        "a": _native("VAC1", "Software Developer Apprentice", "IBM"),
        "b": _ext("jcode:1", "Software Developer Apprentice", "HSE", 1),
    }
    baseline.report(baseline._summarise(vacs, [], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "level unknown — counted separately, NEVER dropped" in out
    assert "of which off-site" in out
    assert "by source:" in out
    assert "civil_service" in out


# ── Coverage metric: result slots vs distinct vacancies ──────────────────────
# gov.uk's "N results found" counts SLOTS, not vacancies: a multi-location
# vacancy is one card per location and each is counted. Comparing distinct refs
# straight against that total reads as a shortfall when nothing is missing —
# which is how "16 dropped" was misdiagnosed. Confirmed by curl: jcode=2017911
# appears 8 times on page 1 of "software developer".

def _search_row(term="software developer", returned=14, site=21, cards=21,
                dup=7, extra_locations=7, pages=1, no_ref=0, below=0,
                stopped=None):
    return {"kind": "keyword", "term": term, "returned": returned,
            "new_to_run": returned, "site_reported_total": site,
            "pages_fetched": pages, "cards_seen": cards,
            "dropped_no_ref": no_ref, "dropped_below_level": below,
            "level_unparsed": 0, "duplicate_cards": dup,
            "extra_locations": extra_locations, "stopped_because": stopped,
            "ref_kinds": {}, "unmatched_href_samples": []}


def test_site_side_duplicates_are_not_counted_as_a_shortfall():
    """The real case: 21 slots, 21 fetched, 7 of them repeats -> 14 vacancies
    and NOTHING missing."""
    vacs = {f"r{i}": _native(f"VAC{i}", f"Role {i} Apprentice", f"Emp {i}")
            for i in range(14)}
    c = baseline._summarise(vacs, [_search_row()], [], ["kw"], False)["coverage"]

    assert c["sum_site_reported_totals"] == 21
    assert c["total_cards_seen"] == 21
    assert c["slots_not_fetched"] == 0, "no slot was missed"
    assert c["site_side_duplicate_cards"] == 7
    assert c["distinct_listings"] == 14


def test_a_real_shortfall_is_still_reported():
    """Slots the site has that we never fetched — e.g. stopped by the page cap."""
    c = baseline._summarise(
        {}, [_search_row(site=312, cards=50, dup=46, extra_locations=46,
                         pages=5, stopped="page_cap")],
        [], ["kw"], False)["coverage"]

    assert c["slots_not_fetched"] == 262
    assert c["searches_stopped_at_page_cap"] == 1


def test_duplicates_under_the_page_cap_are_flagged_as_crowding_out():
    """A capped search whose slots went on location copies is the case where
    multi-location roles push other vacancies off the fetched pages."""
    c = baseline._summarise(
        {}, [_search_row(site=312, cards=50, dup=46, pages=5, stopped="page_cap"),
             _search_row(term="devops", site=3, cards=3, dup=0,
                         extra_locations=0, stopped="repeats")],
        [], ["a", "b"], False)["coverage"]

    assert c["capped_searches_with_duplicates"] == 1
    assert c["searches_with_duplicates"] == 1


def test_a_capped_search_without_duplicates_is_not_flagged():
    c = baseline._summarise(
        {}, [_search_row(site=312, cards=50, dup=0, extra_locations=0,
                         pages=5, stopped="page_cap")],
        [], ["kw"], False)["coverage"]
    assert c["capped_searches_with_duplicates"] == 0


def test_slots_not_fetched_is_none_when_the_site_total_is_unknown():
    """A page whose title didn't parse gives no denominator; say so rather than
    inventing a shortfall of zero."""
    c = baseline._summarise(
        {}, [_search_row(site=None, cards=10, dup=0, extra_locations=0)],
        [], ["kw"], False)["coverage"]
    assert c["sum_site_reported_totals"] is None
    assert c["slots_not_fetched"] is None


def test_multi_location_listings_are_counted():
    native = _native("VAC1", "Software Developer Apprentice", "IBM")
    multi = _ext("jcode:2017911", "Software Developer Apprentice", "HSE", 2017911)
    for loc in ("Bootle", "Leeds", "London"):
        multi.add_location(loc)

    t = baseline._summarise({"a": native, "b": multi}, [], [], ["kw"],
                            False)["totals"]
    assert t["multi_location_listings"] == 1


def test_listings_carry_locations_and_count():
    multi = _ext("jcode:2017911", "Software Developer Apprentice", "HSE", 2017911)
    for loc in ("Bootle", "Leeds"):
        multi.add_location(loc)

    listing = baseline._summarise({"b": multi}, [], [], ["kw"],
                                  False)["listings"][0]
    assert listing["locations"] == ["Bootle", "Leeds"]
    assert listing["location_count"] == 2


def test_report_separates_duplicates_from_true_shortfall(capsys):
    vacs = {f"r{i}": _native(f"VAC{i}", f"Role {i} Apprentice", f"Emp {i}")
            for i in range(14)}
    baseline.report(baseline._summarise(vacs, [_search_row()], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "counts RESULT SLOTS, not vacancies" in out
    assert "site-side duplicates" in out
    assert "none missing" in out
    assert "DISTINCT vacancies" in out


def test_report_calls_out_a_true_shortfall(capsys):
    baseline.report(baseline._summarise(
        {}, [_search_row(site=312, cards=50, dup=46, pages=5,
                         stopped="page_cap")],
        [], ["kw"], False))
    out = capsys.readouterr().out

    assert "TRUE shortfall" in out
    assert "crowding out" in out


def test_per_search_table_shows_slots_consumed_by_duplicates(capsys):
    baseline.report(baseline._summarise({}, [_search_row()], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "dup" in out
    assert "cards that repeated a vacancy" in out
    # uniq 14, slots 21, cards 21, dup 7 on the row
    assert "14" in out and "21" in out and "7" in out


# ── Exclusion counts and per-keyword attribution ────────────────────────────
# The measurement tool filters with services/scout_config, the same module the
# production scan uses, so "excluded: N" is the number the app would really
# drop rather than a second opinion.

def _v(ref, title, course="Software developer (level 4)", level=4, employer="Emp"):
    return RawVacancy(reference=ref, title=title, employer_name_raw=employer,
                      training_course=course, level=level, location="London",
                      url=f"https://example.test/{ref}")


def test_the_report_counts_exclusions_with_reasons():
    vacs = {
        "a": _v("A", "Cyber Security Apprentice",
                "Cyber security technologist (level 4)"),
        "b": _v("B", "Security Guard Apprentice", "Security operative (level 4)"),
        "c": _v("C", "Food Hub Project Manager", "Project manager (level 4)"),
    }
    ex = baseline._summarise(vacs, [], [], ["kw"], False)["exclusions"]

    assert ex["excluded"] == 2
    assert ex["kept"] == 1
    # Both state a non-digital standard, so the standard rule reports them.
    assert ex["by_reason"] == {"non_digital_standard": 2}
    assert ex["kept_by_reason"] == {"digital_standard": 1}


def test_every_excluded_listing_is_listed_with_title_employer_and_reason():
    """The audit trail the wide net depends on."""
    vacs = {"b": _v("B", "Security Guard Apprentice",
                    "Security operative (level 4)", employer="G4S")}
    ex = baseline._summarise(vacs, [], [], ["kw"], False)["exclusions"]

    assert len(ex["listings"]) == 1
    row = ex["listings"][0]
    assert row["title"] == "Security Guard Apprentice"
    assert row["employer"] == "G4S"
    assert row["reason"] == "non_digital_standard"
    assert row["detail"] == "security operative"
    assert row["url"].endswith("/B")


def test_a_strong_term_keeps_a_listing_the_title_list_would_drop():
    vacs = {"a": _v("A", "Digital Project Manager Apprentice", course="")}
    ex = baseline._summarise(vacs, [], [], ["kw"], False)["exclusions"]
    assert ex["excluded"] == 0
    assert ex["kept_by_reason"] == {"strong_digital_term": 1}


def test_listings_record_the_filter_verdict():
    vacs = {"b": _v("B", "Security Guard Apprentice", "Security operative (level 4)")}
    listing = baseline._summarise(vacs, [], [], ["kw"], False)["listings"][0]
    assert listing["kept_by_filter"] is False
    assert listing["filter_reason"] == "non_digital_standard"


def test_excluded_count_appears_on_screen(capsys):
    vacs = {"b": _v("B", "Security Guard Apprentice", "Security operative (level 4)")}
    baseline.report(baseline._summarise(vacs, [], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "excluded: 1" in out
    assert "non_digital_standard" in out
    assert "Security Guard Apprentice" in out


# ── Per-keyword attribution ─────────────────────────────────────────────────

def test_per_keyword_attribution_counts_distinct_vacancies():
    vacs = {"a": _v("A", "Cyber Security Apprentice",
                    "Cyber security technologist (level 4)"),
            "b": _v("B", "Software Developer Apprentice")}
    found_by = {"A": ["cyber security", "cyber"], "B": ["software"]}

    bykw = baseline._summarise(vacs, [], [], ["kw"], False,
                               found_by=found_by)["by_keyword"]

    assert bykw["cyber security"]["distinct_vacancies"] == 1
    assert bykw["cyber"]["distinct_vacancies"] == 1
    assert bykw["software"]["distinct_vacancies"] == 1


def test_vacancies_only_the_widened_terms_reach_are_counted():
    """The number the widening has to justify itself with."""
    vacs = {"a": _v("A", "Cyber Security Apprentice",
                    "Cyber security technologist (level 4)"),
            "b": _v("B", "Systems Engineer Apprentice", course=""),
            "c": _v("C", "IT Support Apprentice",
                    "Information communications technician (level 4)")}
    found_by = {
        "A": ["cyber security"],      # an original phrase found it
        "B": ["systems"],             # widened only
        "C": ["IT", "information"],   # widened only
    }
    snap = baseline._summarise(vacs, [], [], ["kw"], False, found_by=found_by)

    assert snap["only_new_keywords_found"] == 2
    assert snap["by_keyword"]["systems"]["new_vs_original_six"] == 1
    assert snap["by_keyword"]["cyber security"]["new_vs_original_six"] == 0


def test_original_phrases_are_marked_as_such():
    found_by = {"A": ["cyber security"], "B": ["cyber"]}
    vacs = {"a": _v("A", "a"), "b": _v("B", "b")}
    bykw = baseline._summarise(vacs, [], [], ["kw"], False,
                               found_by=found_by)["by_keyword"]

    assert bykw["cyber security"]["is_original_phrase"] is True
    assert bykw["cyber"]["is_original_phrase"] is False


def test_a_vacancy_found_by_both_an_original_and_a_new_term_is_not_new():
    """It was already reachable, so the widening gets no credit for it."""
    vacs = {"a": _v("A", "a")}
    snap = baseline._summarise(vacs, [], [], ["kw"], False,
                               found_by={"A": ["cyber security", "cyber"]})
    assert snap["only_new_keywords_found"] == 0


def test_listings_record_which_terms_found_them():
    vacs = {"a": _v("A", "a")}
    listing = baseline._summarise(vacs, [], [], ["kw"], False,
                                  found_by={"A": ["cyber", "cyber security",
                                                  "cyber"]})["listings"][0]
    assert listing["found_by"] == ["cyber", "cyber security"]


def test_per_keyword_table_appears_on_screen(capsys):
    vacs = {"a": _v("A", "a"), "b": _v("B", "b")}
    baseline.report(baseline._summarise(
        vacs, [], [], ["kw"], False,
        found_by={"A": ["cyber security"], "B": ["systems"]}))
    out = capsys.readouterr().out

    assert "per keyword" in out
    assert "original six" in out
    assert "added" in out
    assert "not found by any original phrase" in out


def test_the_script_searches_the_widened_list():
    """The tool must measure what the app would actually search."""
    terms = [t for kind, t in baseline._searches(use_employers=False)]
    for phrase in scout_config.ORIGINAL_SEARCH_PHRASES:
        assert phrase in terms
    for added in scout_config.ADDED_SEARCH_KEYWORDS:
        assert added in terms


def test_the_script_and_the_scan_share_one_filter():
    """If these diverged, the before/after numbers would describe something the
    app does not do."""
    from services import apprenticeships
    assert baseline.scout_config is scout_config
    assert apprenticeships.scout_config is scout_config


def test_classifier_version_was_bumped_for_the_filter_change():
    assert baseline.CLASSIFIER_VERSION >= 5


# ── Excluded standards are reviewable ───────────────────────────────────────
# The non_digital_standard rule leans on DIGITAL_STANDARDS, a curated list. An
# omission from it silently excludes a real digital role, so the report has to
# make the excluded standards readable rather than leave them as a count.

def test_excluded_standards_are_broken_down_by_frequency():
    vacs = {}
    for i in range(3):
        vacs[f"b{i}"] = _v(f"B{i}", "Business Administrator Apprentice",
                           "Business administrator (level 4)")
    for i in range(2):
        vacs[f"m{i}"] = _v(f"M{i}", "Marketing Apprentice",
                           "Multi-channel marketer (level 3)")
    vacs["f"] = _v("F", "Freight Apprentice",
                   "International freight forwarding specialist (level 3)")

    detail = baseline._summarise(vacs, [], [], ["kw"], False)[
        "exclusions"]["by_detail"]["non_digital_standard"]

    assert list(detail.items()) == [
        ("business administrator", 3),
        ("multi-channel marketer", 2),
        ("international freight forwarding specialist", 1),
    ], "most frequent first, so the biggest mistake is the most visible"


def test_excluded_standards_are_printed_for_review(capsys):
    vacs = {"b": _v("B", "Business Administrator Apprentice",
                    "Business administrator (level 4)")}
    baseline.report(baseline._summarise(vacs, [], [], ["kw"], False))
    out = capsys.readouterr().out

    assert "excluded standards" in out
    assert "CHECK THESE" in out
    assert "business administrator" in out
    assert "scout_filters.json" in out, "say how to fix a wrong exclusion"


def test_by_detail_covers_every_reason_present():
    vacs = {
        "a": _v("A", "Business Administrator Apprentice",
                "Business administrator (level 4)"),
        "b": _v("B", "Security Guard Apprentice", ""),
    }
    detail = baseline._summarise(vacs, [], [], ["kw"], False)[
        "exclusions"]["by_detail"]

    assert "non_digital_standard" in detail
    assert "title_excluded" in detail
    assert detail["title_excluded"] == {"security guard": 1}


def test_a_digital_standard_missing_from_config_can_be_added(tmp_path, monkeypatch):
    """The escape hatch for the curated-list risk: adding the standard to config
    reverses the exclusion without a code change."""
    vac = _v("Z", "Telecoms Apprentice", "Telecoms field operative (level 3)")

    assert baseline._summarise({"z": vac}, [], [], ["kw"], False)[
        "exclusions"]["excluded"] == 1

    path = tmp_path / "filters.json"
    path.write_text(json.dumps(
        {"+digital_standards": ["telecoms field operative"]}))
    monkeypatch.setenv("SCOUT_FILTERS_PATH", str(path))
    scout_config.settings(refresh=True)
    try:
        assert baseline._summarise({"z": vac}, [], [], ["kw"], False)[
            "exclusions"]["excluded"] == 0
    finally:
        monkeypatch.delenv("SCOUT_FILTERS_PATH", raising=False)
        scout_config.settings(refresh=True)


# ── The request budget ──────────────────────────────────────────────────────

def test_the_estimate_reports_the_new_budget(capsys, monkeypatch):
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, os.path.join(repo, "scripts",
                                      "scout_coverage_baseline.py"), "--estimate"],
        capture_output=True, text=True, cwd=repo)

    assert r.returncode == 0, r.stderr
    n = len(baseline._searches(use_employers=False))     # route sweep + keywords
    assert f"searches            : {n}" in r.stdout
    assert "max pages each      : 20" in r.stdout
    assert f"worst-case requests : {n * 20}" in r.stdout
    assert "1 request/second" in r.stdout
    assert "ceiling, not a budget" in r.stdout, \
        "the worst case overstates the real cost; say so"


def test_the_estimate_honours_a_page_cap_override():
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, SCOUT_MAX_PAGES="5")
    r = subprocess.run(
        [sys.executable, os.path.join(repo, "scripts",
                                      "scout_coverage_baseline.py"), "--estimate"],
        capture_output=True, text=True, cwd=repo, env=env)

    assert r.returncode == 0, r.stderr
    n = len(baseline._searches(use_employers=False))
    assert "max pages each      : 5" in r.stdout
    assert f"worst-case requests : {n * 5}" in r.stdout


# ── Route sweep vs keyword run ──────────────────────────────────────────────
# Two ways of finding the same thing, so the number that matters is what each
# one MISSES. The sweep selects on what the apprenticeship IS; the keywords
# select on what its advert SAYS. Neither is a superset of the other.

def _rv(ref, title, course, level=4, src="findapprenticeship"):
    return RawVacancy(reference=ref, title=title, employer_name_raw="Emp " + ref,
                      training_course=course, level=level, location="London",
                      source=src, url=f"https://example.test/{ref}")


def _sweep_row(slots=45, cards=45, dup=12, stopped="reached_site_total"):
    return {"kind": "route", "term": baseline.ROUTE_TERM, "returned": 33,
            "new_to_run": 33, "site_reported_total": slots, "pages_fetched": 5,
            "cards_seen": cards, "dropped_no_ref": 0, "dropped_below_level": 0,
            "level_unparsed": 0, "duplicate_cards": dup, "extra_locations": dup,
            "stopped_because": stopped, "ref_kinds": {},
            "unmatched_href_samples": []}


def _mixed_run():
    """28 found by both, 5 only by the sweep, 3 only by keywords, 1 noise."""
    vacs, found_by = {}, {}
    for i in range(28):
        r = f"BOTH{i}"
        vacs[r] = _rv(r, f"Software Developer Apprentice {i}",
                      "Software developer (level 4)")
        found_by[r] = [baseline.ROUTE_TERM, "software developer"]
    for i in range(5):
        r = f"SWEEP{i}"
        vacs[r] = _rv(r, f"Trainee Analyst Programme {i}",
                      "Data technician (level 4)")
        found_by[r] = [baseline.ROUTE_TERM]
    for i in range(3):
        r = f"KW{i}"
        vacs[r] = _rv(r, f"Cyber Apprentice {i}",
                      "Cyber security technologist (level 6)", level=6)
        found_by[r] = ["cyber security"]
    vacs["N1"] = _rv("N1", "Business Administrator Apprentice",
                     "Business administrator (level 4)")
    found_by["N1"] = ["information"]
    return vacs, found_by


def test_the_comparison_reports_both_sides_and_the_overlap():
    vacs, found_by = _mixed_run()
    rc = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                             found_by=found_by)["route_comparison"]

    assert rc["ran"] is True
    assert rc["sweep_slots_reported"] == 45
    assert rc["sweep_slots_fetched"] == 45
    assert rc["sweep_duplicate_cards"] == 12
    assert rc["sweep_distinct"] == 33
    assert rc["keyword_digital_level_4_plus"] == 31
    assert rc["in_both"] == 28


def test_vacancies_only_the_sweep_found_are_listed():
    """The argument for the sweep: titles no keyword would match."""
    vacs, found_by = _mixed_run()
    rc = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                             found_by=found_by)["route_comparison"]

    assert len(rc["only_in_sweep"]) == 5
    refs = {d["ref"] for d in rc["only_in_sweep"]}
    assert refs == {f"SWEEP{i}" for i in range(5)}
    row = rc["only_in_sweep"][0]
    for key in ("ref", "title", "employer", "level", "course", "url"):
        assert key in row


def test_vacancies_only_the_keywords_found_are_listed():
    vacs, found_by = _mixed_run()
    rc = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                             found_by=found_by)["route_comparison"]

    assert len(rc["only_in_keywords"]) == 3
    assert {d["ref"] for d in rc["only_in_keywords"]} == {"KW0", "KW1", "KW2"}


def test_keyword_noise_is_left_out_of_the_comparison():
    """The sweep is restricted to digital Level 4+, so the keyword side has to
    be the comparable subset or the difference is meaningless."""
    vacs, found_by = _mixed_run()
    rc = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                             found_by=found_by)["route_comparison"]

    assert "N1" not in {d["ref"] for d in rc["only_in_keywords"]}
    assert rc["keyword_digital_level_4_plus"] == 31


def test_a_level_3_digital_role_is_not_in_the_keyword_comparison():
    vacs = {"a": _rv("A", "Digital Support Apprentice",
                     "Digital support technician (level 3)", level=3)}
    rc = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                             found_by={"A": ["digital"]})["route_comparison"]
    assert rc["keyword_digital_level_4_plus"] == 0


def test_the_comparison_is_absent_when_the_sweep_did_not_run():
    rc = baseline._summarise({}, [], [], ["a"], False,
                             found_by={})["route_comparison"]
    assert rc["ran"] is False


def test_the_route_term_is_not_counted_as_a_keyword():
    """It has its own section; listing it among the keywords would double-count
    and make "only the widened terms find it" wrong."""
    vacs, found_by = _mixed_run()
    snap = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                               found_by=found_by)
    assert baseline.ROUTE_TERM not in snap["by_keyword"]


def test_a_sweep_only_vacancy_is_not_credited_to_the_widened_keywords():
    """It was not found by a keyword at all, so the widening gets no credit."""
    vacs, found_by = _mixed_run()
    snap = baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                               found_by=found_by)
    assert snap["only_new_keywords_found"] == 1      # just N1, via "information"


def test_the_comparison_is_printed_with_both_lists(capsys):
    vacs, found_by = _mixed_run()
    baseline.report(baseline._summarise(vacs, [_sweep_row()], [], ["a"], False,
                                        found_by=found_by))
    out = capsys.readouterr().out

    assert "route sweep vs keyword run" in out
    assert "routeIds=7" in out
    assert "only the sweep found         : 5" in out
    assert "only the keywords found      : 3" in out
    assert "keyword wording missed them" in out
    assert "Trainee Analyst Programme 0" in out
    assert "Cyber Apprentice 0" in out


def test_the_report_shows_both_queries(capsys):
    baseline.report(baseline._summarise({}, [_sweep_row()], [], ["a"], False,
                                        found_by={}))
    out = capsys.readouterr().out
    assert "keyword query" in out
    assert "route sweep query" in out
    assert "'routeIds': 7" in out
    assert "route sweep + keyword scans" in out


def test_the_sweep_is_the_first_search_issued():
    """It runs before the keywords so it is the baseline they are measured
    against, not the other way round."""
    searches = baseline._searches(use_employers=False)
    assert searches[0] == ("route", baseline.ROUTE_TERM)


def test_the_sweep_can_be_skipped():
    searches = baseline._searches(use_employers=False, route_sweep=False)
    assert all(kind != "route" for kind, _ in searches)
