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


def test_fixture_mode_surfaces_dropped_references(tmp_path):
    """The offline path has to expose the same diagnostics as a live run, or it
    cannot be used to reproduce a coverage gap."""
    (tmp_path / "p.html").write_text(
        '<html><title>1 result found</title><ul>'
        '<li class="das-search-results__list-item">'
        '<a href="/apprenticeship/1234567">Data Apprentice</a>'
        '<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
        '<p class="govuk-body"><b>Training course</b> Data analyst (level 4)</p>'
        '</li></ul></html>')

    s = baseline.measure_fixture(str(tmp_path), min_level=1)
    assert s["totals"]["unique_results"] == 0
    assert s["coverage"]["total_dropped_no_ref"] == 1
    assert "/apprenticeship/1234567" in s["coverage"]["unmatched_href_samples"]


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
