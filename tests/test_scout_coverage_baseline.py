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
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    assert "classifier versions differ (3 vs 4)" in out


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
