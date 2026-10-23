"""Scout's search/relevance config and the keep-or-drop decision.

The widened keyword list only works if the filtering is trustworthy, and the
filtering is only trustworthy if its bias is explicit. The rule is that missing
a real apprenticeship costs more than showing noise, so every test here is
written to catch over-eager exclusion as well as under-eager.

Config is shared with the production scan on purpose — the measurement tool and
the scan must not filter differently — so these tests cover the single
implementation both use.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import scout_config as sc  # noqa: E402


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the loader at a scratch file and clear the cache around the test."""
    path = tmp_path / "scout_filters.json"
    monkeypatch.setenv("SCOUT_FILTERS_PATH", str(path))
    sc.settings(refresh=True)
    yield path
    monkeypatch.delenv("SCOUT_FILTERS_PATH", raising=False)
    sc.settings(refresh=True)


def _write(path, obj):
    path.write_text(json.dumps(obj))
    sc.settings(refresh=True)


# ── Whole-word matching: the case the spec names ─────────────────────────────

@pytest.mark.parametrize("title", [
    "Kitchen Assistant Apprentice",
    "Kitchen Porter",
    "Kitchens and Bathrooms Apprentice",
    "Digital Marketing in Knitwear",
    "Legitimate Business Apprentice",
])
def test_the_term_it_does_not_match_inside_another_word(title):
    """`IT` as a substring hits "kitchen", "legitimate", "knitwear". Only a
    word-boundary match makes a two-letter search term usable at all."""
    assert "it" not in sc.strong_terms_in(title), \
        f"{title!r} matched the bare term 'it'"


@pytest.mark.parametrize("title,expected", [
    ("IT Support Technician Apprentice", True),
    ("IT Apprentice", True),
    ("Apprentice IT Technician", True),
    ("Apprentice (IT)", True),
    ("IT, Networks and Infrastructure", True),
    ("I.T. Technician Apprentice", True),
    ("Kitchen Assistant", False),
])
def test_it_matches_only_as_a_whole_word(title, expected):
    terms = sc.strong_terms_in(title)
    assert any(t in ("it", "i.t.") for t in terms) is expected


def test_multi_word_terms_are_matched_before_their_prefixes():
    """"machine learning" must not be reported as a bare "machine" match."""
    assert "machine learning" in sc.digital_terms_in(
        "Machine Learning Engineer Apprentice")


@pytest.mark.parametrize("term", sc.ADDED_SEARCH_KEYWORDS)
def test_every_added_keyword_is_in_the_search_list(term):
    assert term in sc.search_keywords()


def test_the_original_six_phrases_are_kept():
    """Widening must ADD to the search list, not replace it."""
    for phrase in sc.ORIGINAL_SEARCH_PHRASES:
        assert phrase in sc.search_keywords()


def test_keyword_scans_env_var_still_wins(monkeypatch):
    monkeypatch.setenv("KEYWORD_SCANS", "cyber security, devops ,")
    assert sc.search_keywords() == ["cyber security", "devops"]
    monkeypatch.delenv("KEYWORD_SCANS")


# ── The known false positives ────────────────────────────────────────────────

def test_security_guard_is_excluded():
    """The named example. It carries the weak term "security", which must not
    be enough to rescue it."""
    v = sc.judge("Security Guard Apprentice", "G4S", "Security operative (level 2)")
    assert v.keep is False
    assert v.reason == sc.REASON_TITLE
    assert v.detail == "security guard"


def test_food_hub_project_manager_is_excluded():
    v = sc.judge("Food Hub Project Manager", "Some Council",
                 "Project manager (level 4)")
    assert v.keep is False
    assert v.reason == sc.REASON_TITLE


def test_cyber_security_is_not_caught_by_the_security_guard_rule():
    """Exclusions are phrases so they stay specific."""
    assert sc.judge("Cyber Security Apprentice", "BT",
                    "Cyber security technologist (level 4)").keep is True


def test_a_strong_term_overrides_a_title_exclusion():
    """"project manager" is excluded, but a digital project manager is a real
    role — this is the "when unsure, keep it" rule doing its job."""
    v = sc.judge("Digital Project Manager Apprentice", "Capgemini", "")
    assert v.keep is True
    assert v.reason == sc.KEEP_STRONG_TERM
    assert "digital" in v.detail


def test_a_weak_term_does_not_override_a_title_exclusion():
    """If it did, "security" would rescue every security guard posting."""
    assert sc.judge("Security Officer Apprentice", "ACME",
                    "Security operative (level 2)").keep is False


# ── The apprenticeship standard is the strongest signal ──────────────────────

def test_a_digital_standard_keeps_a_listing_whatever_its_title():
    """The standard is chosen by the employer from a national catalogue, so it
    beats a list of guessed title phrases."""
    v = sc.judge("Kitchen Assistant Apprentice", "Greggs",
                 "Software developer (level 4)")
    assert v.keep is True
    assert v.reason == sc.KEEP_DIGITAL_STANDARD


def test_a_digital_standard_beats_an_employer_exclusion(isolated_config):
    _write(isolated_config, {"employer_exclusions": ["greggs"]})
    v = sc.judge("Apprentice", "Greggs plc", "Cyber security technologist (level 4)")
    assert v.keep is True, "a stated digital standard must not be overridden"


def test_a_bare_title_with_a_digital_standard_is_kept():
    """gov.uk titles are often just "Apprentice"."""
    assert sc.judge("Apprentice", "IBM", "Software developer (level 4)").keep is True


@pytest.mark.parametrize("course", [
    "Software developer (level 4)",
    "Cyber security technologist (level 4)",
    "Data analyst (level 4)",
    "Network engineer (level 6)",
    "Digital and technology solutions professional (level 6)",
    "Information communications technician (level 3)",
])
def test_digital_standards_are_recognised(course):
    assert sc.is_digital_standard(course) is True


@pytest.mark.parametrize("course", [
    "Commis chef (level 2)", "Security operative (level 2)",
    "Business administrator (level 3)", "Dental nurse (level 3)", "",
])
def test_non_digital_standards_are_not(course):
    assert sc.is_digital_standard(course) is False


@pytest.mark.parametrize("raw,expected", [
    ("Software developer (level 4)", "software developer"),
    ("Software developer level 4", "software developer"),
    ("  SOFTWARE   DEVELOPER  [Level 4] ", "software developer"),
])
def test_course_names_are_normalised(raw, expected):
    assert sc.course_name(raw) == expected


# ── The bias towards keeping ─────────────────────────────────────────────────

def test_an_unrecognised_listing_is_kept():
    """Nothing matched either way, so it stays — and the reason records that it
    was a guess, not a judgement."""
    v = sc.judge("Widget Polishing Apprentice", "ACME Ltd", "Widget polishing")
    assert v.keep is True
    assert v.reason == sc.KEEP_UNSURE


def test_an_ambiguous_engineering_role_is_kept():
    v = sc.judge("Engineering Operative Apprentice", "Factory",
                 "Engineering operative (level 4)")
    assert v.keep is True


def test_an_empty_listing_is_kept_not_dropped():
    assert sc.judge("", "", "").keep is True


# ── Employer exclusions ──────────────────────────────────────────────────────

def test_employer_exclusions_are_empty_by_default():
    """A blunt instrument the user fills from the exclusion log, not a list of
    guesses shipped in code."""
    assert sc.settings()["employer_exclusions"] == ()


def test_an_excluded_employer_is_dropped(isolated_config):
    _write(isolated_config, {"employer_exclusions": ["greggs"]})
    v = sc.judge("Team Member Apprentice", "Greggs plc", "")
    assert v.keep is False
    assert v.reason == sc.REASON_EMPLOYER
    assert v.detail == "greggs"


def test_employer_matching_is_a_substring(isolated_config):
    """A company name is not a sentence; "greggs" should match "Greggs plc"."""
    _write(isolated_config, {"employer_exclusions": ["greggs"]})
    for name in ("Greggs", "Greggs plc", "GREGGS UK LIMITED", "the greggs group"):
        assert sc.matched_employer_exclusion(name) == "greggs"


def test_an_unlisted_employer_is_not_excluded(isolated_config):
    _write(isolated_config, {"employer_exclusions": ["greggs"]})
    assert sc.matched_employer_exclusion("IBM UK") is None


# ── Config is editable without a code change ─────────────────────────────────

def test_a_list_can_be_replaced(isolated_config):
    _write(isolated_config, {"title_exclusions": ["only this"]})
    assert sc.settings()["title_exclusions"] == ("only this",)


def test_a_plus_key_appends_to_the_builtin(isolated_config):
    _write(isolated_config, {"+title_exclusions": ["widget polishing"]})
    conf = sc.settings()
    assert "widget polishing" in conf["title_exclusions"]
    assert "security guard" in conf["title_exclusions"], "built-ins must survive"
    assert len(conf["title_exclusions"]) == len(sc.DEFAULT_TITLE_EXCLUSIONS) + 1


def test_an_appended_exclusion_takes_effect(isolated_config):
    _write(isolated_config, {"+title_exclusions": ["widget polishing"]})
    assert sc.judge("Widget Polishing Apprentice", "ACME", "").keep is False


def test_search_keywords_can_be_extended_by_config(isolated_config):
    _write(isolated_config, {"+search_keywords": ["quantum"]})
    assert "quantum" in sc.search_keywords()


def test_a_missing_config_file_is_fine(isolated_config):
    assert not isolated_config.exists()
    assert len(sc.settings()["title_exclusions"]) == len(sc.DEFAULT_TITLE_EXCLUSIONS)


def test_a_broken_config_file_falls_back_to_defaults(isolated_config):
    """A scan must not die because the JSON was mistyped."""
    isolated_config.write_text("{ not json")
    sc.settings(refresh=True)
    assert sc.settings()["title_exclusions"] == sc.DEFAULT_TITLE_EXCLUSIONS


def test_a_non_object_config_file_is_ignored(isolated_config):
    isolated_config.write_text("[1, 2, 3]")
    sc.settings(refresh=True)
    assert sc.settings()["title_exclusions"] == sc.DEFAULT_TITLE_EXCLUSIONS


def test_a_non_list_value_is_ignored(isolated_config):
    _write(isolated_config, {"title_exclusions": "security guard"})
    assert sc.settings()["title_exclusions"] == sc.DEFAULT_TITLE_EXCLUSIONS


def test_blank_entries_are_dropped(isolated_config):
    _write(isolated_config, {"title_exclusions": ["security guard", "", "   "]})
    assert sc.settings()["title_exclusions"] == ("security guard",)


def test_an_empty_exclusion_list_excludes_nothing(isolated_config):
    _write(isolated_config, {"title_exclusions": []})
    assert sc.matched_title_exclusion("Security Guard Apprentice") is None
    assert sc.judge("Security Guard Apprentice", "G4S", "").keep is True


def test_the_shipped_config_file_is_valid_json():
    """It is committed, so a typo in it would break every scan."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "scout_filters.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict)
    for key in data:
        if key.startswith("_"):
            continue
        assert key.lstrip("+") in sc._KEYS, f"unknown config key: {key}"


# ── The verdict object ───────────────────────────────────────────────────────

def test_verdict_serialises_for_the_log():
    v = sc.judge("Security Guard Apprentice", "G4S", "")
    d = v.as_dict()
    assert d["keep"] is False
    assert d["reason"] == sc.REASON_TITLE
    assert "detail" in d and "terms" in d
