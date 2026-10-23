"""The Phase 1 coverage measuring stick (scripts/scout_coverage_baseline.py).

This is the tool that produces the before/after numbers, so its counting has to
be trustworthy and stable: if the measuring stick drifts between two runs the
comparison is meaningless.

Note on scope: the classifier here is the *measuring stick*, not Scout's
production relevance rule. The production keyword/exclusion config is Phase 1
work and is not built yet (the live baseline it has to be measured against is
blocked — gov.uk is unreachable from the build environment). What is pinned
here is the word-boundary behaviour the spec calls out, because the same
requirement will apply to the production rule.
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


# ── Word boundaries: the case the spec calls out by name ─────────────────────

@pytest.mark.parametrize("title", [
    "Kitchen Assistant Apprentice",
    "Kitchen Porter",
    "Commis Chef Apprentice",
    "Hospitality Team Member",
    "Dental Nurse Apprentice",
])
def test_short_term_it_does_not_match_unrelated_words(title):
    """`IT` must not hit "kitchen" — a bare substring match would."""
    assert baseline.is_digital(_vac(title=title)) is False, \
        f"{title!r} was wrongly classified as digital"


@pytest.mark.parametrize("title", [
    "IT Support Technician Apprentice",
    "IT Apprentice",
    "Apprentice IT Technician",
    "IT, Networks and Infrastructure Apprentice",
])
def test_it_matches_as_a_whole_word(title):
    assert baseline.is_digital(_vac(title=title)) is True


def test_it_matches_when_punctuation_abuts_it():
    assert baseline.is_digital(_vac(title="Apprentice (IT)")) is True


# ── The genuine digital roles the widened net is meant to catch ──────────────

@pytest.mark.parametrize("title,course", [
    ("Cyber Security Apprentice", "Cyber security technologist (level 4)"),
    ("Software Developer Apprentice", "Software developer (level 4)"),
    ("Digital & Technology Solutions Apprentice",
     "Digital and technology solutions professional (level 6)"),
    ("Data Analyst Apprentice", "Data analyst (level 4)"),
    ("Network Engineer Degree Apprentice", "Network engineer (level 6)"),
    ("Cloud Engineer Apprentice", "Cloud engineer (level 4)"),
    ("Apprentice Systems Administrator", ""),
    ("Information Security Apprentice", ""),
    ("DevOps Apprentice", ""),
    ("Computing Apprentice", ""),
])
def test_digital_roles_are_classified_digital(title, course):
    assert baseline.is_digital(_vac(title=title, course=course)) is True


def test_course_text_alone_can_classify_a_vague_title():
    """gov.uk titles are often bare; the training course carries the signal."""
    assert baseline.is_digital(
        _vac(title="Apprentice", course="Software developer (level 4)")) is True


@pytest.mark.parametrize("title", [
    "Food Hub Project Manager",
    "Business Administration Apprentice",
    "Early Years Educator Apprentice",
    "Bricklaying Apprentice",
])
def test_clearly_unrelated_roles_are_not_digital(title):
    assert baseline.is_digital(_vac(title=title)) is False


def test_security_guard_is_a_known_false_positive():
    """Recorded, not accepted.

    A bare `security` term pulls in physical security. This is exactly the noise
    Phase 1's title/employer exclusion lists have to remove, and the spec's rule
    is that missing a real apprenticeship is worse than seeing noise — so the
    measuring stick stays inclusive and the exclusion happens downstream.
    """
    assert baseline.is_digital(_vac(title="Security Guard Apprentice")) is True


# ── Counting ─────────────────────────────────────────────────────────────────

def test_summary_counts_levels_and_digital():
    vacs = {
        "A": _vac("A", "Cyber Security Apprentice", level=4),
        "B": _vac("B", "Software Developer Apprentice", level=6),
        "C": _vac("C", "Kitchen Assistant", level=2),
        "D": _vac("D", "IT Technician", level=3),
        "E": _vac("E", "Apprentice", level=None),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    t = s["totals"]

    assert t["unique_results"] == 5
    assert t["level_4_plus"] == 2          # A, B
    assert t["level_6_plus"] == 1          # B
    assert t["digital"] == 3               # A, B, D
    assert t["digital_and_level_4_plus"] == 2
    assert t["level_unknown"] == 1
    assert s["by_level"] == {"2": 1, "3": 1, "4": 1, "6": 1, "unknown": 1}


def test_listings_are_sorted_highest_level_first():
    vacs = {
        "A": _vac("A", "Low", level=2),
        "B": _vac("B", "High", level=6),
        "C": _vac("C", "Unknown", level=None),
    }
    s = baseline._summarise(vacs, [], [], ["kw"], False)
    assert [x["level"] for x in s["listings"]] == [6, 2, None]


def test_snapshot_records_classifier_version():
    """A diff across a changed classifier is not a real comparison; the version
    is what makes that visible."""
    s = baseline._summarise({}, [], [], [], False)
    assert s["classifier_version"] == baseline.CLASSIFIER_VERSION
    assert isinstance(baseline.CLASSIFIER_VERSION, int)


def test_fixture_mode_dedupes_across_pages(tmp_path):
    """Two pages sharing a reference must count once — the same union dedup the
    live run relies on."""
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
    assert s["totals"]["level_4_plus"] == 2


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
