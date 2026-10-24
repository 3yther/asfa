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
def test_every_added_keyword_is_still_searched_somewhere(term):
    """CHANGED in Phase 1D: the broad terms moved from the production scan to
    the audit, so "is it searched" now has two possible homes."""
    assert term in sc.audit_keywords()


def test_the_original_six_phrases_are_still_audited():
    """Widening must ADD, not replace. Four of the six are narrow enough to
    stay in production; all six remain in the audit."""
    for phrase in sc.ORIGINAL_SEARCH_PHRASES:
        assert phrase in sc.audit_keywords(), f"{phrase!r} was dropped entirely"


def test_keyword_scans_env_var_still_wins(monkeypatch):
    monkeypatch.setenv("KEYWORD_SCANS", "cyber security, devops ,")
    assert sc.search_keywords() == ["cyber security", "devops"]
    monkeypatch.delenv("KEYWORD_SCANS")


# ── The known false positives ────────────────────────────────────────────────

def test_security_guard_is_excluded():
    """The named example. It carries the weak term "security", which must not
    be enough to rescue it.

    Its STATED standard is "Security operative", so the standard rule reports
    it first — a better reason than a title guess. The title rule still covers
    the no-course case below.
    """
    v = sc.judge("Security Guard Apprentice", "G4S", "Security operative (level 2)")
    assert v.keep is False
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD
    assert v.detail == "security operative"


def test_security_guard_without_a_course_is_caught_by_the_title_rule():
    """Off-site listings carry no training course, so the title list is the only
    thing standing between them and the inbox."""
    v = sc.judge("Security Guard Apprentice", "G4S", "")
    assert v.keep is False
    assert v.reason == sc.REASON_TITLE
    assert v.detail == "security guard"


def test_food_hub_project_manager_is_excluded():
    v = sc.judge("Food Hub Project Manager", "Some Council",
                 "Project manager (level 4)")
    assert v.keep is False
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD


def test_food_hub_project_manager_without_a_course_is_still_excluded():
    v = sc.judge("Food Hub Project Manager", "Some Council", "")
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

def test_an_unrecognised_listing_with_no_standard_is_kept():
    """Nothing matched either way, so it stays — and the reason records that it
    was a guess, not a judgement.

    No course text, so the standard rule cannot speak to it. A listing that DOES
    state a non-digital standard is a different case (see below): the standard
    is evidence, and absence of evidence is not.
    """
    v = sc.judge("Widget Polishing Apprentice", "ACME Ltd", "")
    assert v.keep is True
    assert v.reason == sc.KEEP_UNSURE


def test_an_unrecognised_non_digital_standard_is_excluded():
    """CHANGED in Phase 1B. The listing states what the apprentice will train
    as, and it is not digital, so the search term that found it is irrelevant.

    This narrows "when unsure, keep it" to "when there is NOTHING to go on,
    keep it". The risk it carries is that DIGITAL_STANDARDS is a curated list,
    so an omission excludes a real role — which is why the baseline report
    prints every excluded standard by frequency for review.
    """
    v = sc.judge("Widget Polishing Apprentice", "ACME Ltd", "Widget polishing")
    assert v.keep is False
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD
    assert v.detail == "widget polishing"


def test_an_ambiguous_engineering_role_with_no_standard_is_kept():
    v = sc.judge("Engineering Operative Apprentice", "Factory", "")
    assert v.keep is True


def test_a_stated_engineering_operative_standard_is_excluded():
    """"engineering" is a WEAK term, so it cannot override the standard."""
    v = sc.judge("Engineering Operative Apprentice", "Factory",
                 "Engineering operative (level 4)")
    assert v.keep is False
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD


# ── The non-digital-standard rule, with the examples from the data ──────────

@pytest.mark.parametrize("title,course", [
    ("Business Administrator Apprentice", "Business administrator (level 4)"),
    ("Marketing Apprentice", "Multi-channel marketer (level 3)"),
    ("Freight Apprentice", "International freight forwarding specialist (level 3)"),
    ("Installer Apprentice", "Interior systems installer (level 2)"),
    ("Warehouse Apprentice", "Supply chain warehouse operative (level 2)"),
])
def test_the_bulk_non_digital_standards_are_excluded(title, course):
    """The five standards that dominated the widened run's noise."""
    v = sc.judge(title, "Some Employer", course)
    assert v.keep is False, f"{course!r} should not survive"
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD


@pytest.mark.parametrize("title,course", [
    # a strong term in the title overrides a non-digital standard
    ("Digital Marketing Apprentice", "Multi-channel marketer (level 3)"),
    ("IT Apprentice", "Business administrator (level 4)"),
    ("Software Apprentice", "Supply chain warehouse operative (level 2)"),
    ("Data Apprentice", "Interior systems installer (level 2)"),
])
def test_a_strong_term_overrides_a_non_digital_standard(title, course):
    """The override that keeps the rule from eating real roles: a digital job
    advertised against an odd standard is still a digital job."""
    v = sc.judge(title, "Some Employer", course)
    assert v.keep is True, f"{title!r} was wrongly excluded"
    assert v.reason == sc.KEEP_STRONG_TERM


@pytest.mark.parametrize("course", [
    "Software developer (level 4)",
    "Cyber security technologist (level 4)",
    "Data analyst (level 4)",
])
def test_a_digital_standard_is_never_touched_by_the_rule(course):
    v = sc.judge("Apprentice", "Some Employer", course)
    assert v.keep is True
    assert v.reason == sc.KEEP_DIGITAL_STANDARD


def test_a_listing_with_no_course_is_not_hit_by_the_standard_rule():
    """Off-site cross-posts carry no course. They must not all be excluded for
    failing to state a standard they were never going to state."""
    v = sc.judge("Software Developer Apprentice (Band 5/EO)",
                 "Health and Safety Executive", "")
    assert v.keep is True


def test_a_whitespace_only_course_counts_as_no_course():
    assert sc.judge("Apprentice", "Emp", "   ").keep is True


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
    known = set(sc._KEYS) | set(sc._SCALARS) | {"distance"}
    for key in data:
        if key.startswith("_"):
            continue
        assert key.lstrip("+") in known, f"unknown config key: {key}"


# ── The verdict object ───────────────────────────────────────────────────────

def test_verdict_serialises_for_the_log():
    v = sc.judge("Security Guard Apprentice", "G4S", "")
    d = v.as_dict()
    assert d["keep"] is False
    assert d["reason"] == sc.REASON_TITLE
    assert "detail" in d and "terms" in d


# ── gov.uk's own filters (field names verified against the live site) ───────

def test_the_route_sweep_params_are_the_verified_ones():
    """?routeIds=7&levelIds=4&levelIds=5&levelIds=6&levelIds=7

    Pinned because these came from checking the live site, not from guessing,
    and a silent change here would alter what the sweep covers.
    """
    assert sc.DIGITAL_ROUTE_ID == 7
    assert sc.DEFAULT_LEVEL_IDS == (4, 5, 6, 7)
    assert sc.route_sweep_params() == {"routeIds": 7, "levelIds": [4, 5, 6, 7]}


def test_the_sweep_sends_no_search_term():
    """The whole point: the route filter selects the listings, so one pass
    covers the route instead of depending on wording."""
    assert "searchTerm" not in sc.route_sweep_params()


# ── Distance validation ─────────────────────────────────────────────────────

def test_the_allowed_distances_are_the_sites_own():
    assert sc.DISTANCE_VALUES == (2, 5, 10, 15, 20, 30, 40, "all")


def test_the_default_distance_is_twenty():
    assert sc.DEFAULT_DISTANCE == 20
    assert sc.search_distance() == 20


@pytest.mark.parametrize("value", [2, 5, 10, 15, 20, 30, 40, "all"])
def test_every_accepted_distance_validates(value):
    assert sc.validate_distance(value) == value


@pytest.mark.parametrize("raw,expected", [
    ("20", 20), (" 20 ", 20), ("ALL", "all"), ("All", "all"), (" all ", "all"),
])
def test_distance_strings_are_normalised(raw, expected):
    assert sc.validate_distance(raw) == expected


@pytest.mark.parametrize("bad", [25, 1, 0, -5, 100, 3.5, "near", "", None, True,
                                 [20], {"d": 20}])
def test_an_unaccepted_distance_is_rejected(bad):
    """Rejected, not coerced. Distance changes which listings a sweep can see,
    so quietly substituting a different radius would make the numbers a lie —
    and a wrong "nothing found" is worse than an error."""
    with pytest.raises(ValueError):
        sc.validate_distance(bad)


def test_the_rejection_message_names_the_allowed_values():
    with pytest.raises(ValueError) as e:
        sc.validate_distance(25)
    assert "2, 5, 10, 15, 20, 30, 40" in str(e.value)
    assert "all" in str(e.value)


def test_the_distance_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCOUT_DISTANCE", "40")
    assert sc.search_distance() == 40
    monkeypatch.setenv("SCOUT_DISTANCE", "all")
    assert sc.search_distance() == "all"


def test_a_bad_distance_in_the_environment_raises(monkeypatch):
    monkeypatch.setenv("SCOUT_DISTANCE", "25")
    with pytest.raises(ValueError):
        sc.search_distance()


def test_the_distance_can_be_set_in_config(isolated_config):
    _write(isolated_config, {"distance": 5})
    assert sc.search_distance() == 5


def test_a_bad_distance_in_config_falls_back_to_the_default(isolated_config):
    """A mistyped config value must not stop a scan; the warning is the signal."""
    _write(isolated_config, {"distance": 25})
    assert sc.search_distance() == sc.DEFAULT_DISTANCE


# ── Field names we do NOT have are not guessed ──────────────────────────────

def test_the_field_names_have_no_PYTHON_default(isolated_config, monkeypatch):
    """CHANGED in Phase 1D: the names are now in config/scout_filters.json,
    verified against the live site. There is still no default in Python —
    guessing a field name would send a query that looks local and is silently
    nationwide, a worse failure than not filtering at all."""
    monkeypatch.delenv("SCOUT_DISTANCE_PARAM", raising=False)
    monkeypatch.delenv("SCOUT_POSTCODE_PARAM", raising=False)
    monkeypatch.delenv("SCOUT_HOME_POSTCODE", raising=False)
    assert sc.distance_param() is None
    assert sc.postcode_param() is None


def test_the_home_postcode_is_never_hardcoded_in_python(isolated_config,
                                                        monkeypatch):
    """It comes from config. With config isolated there is no postcode at all,
    which is what "never hardcoded" has to mean."""
    monkeypatch.delenv("SCOUT_HOME_POSTCODE", raising=False)
    assert sc.home_postcode() == ""
    assert "DA17" not in str(sc.DEFAULT_SEARCH_KEYWORDS)


def test_the_home_postcode_can_be_configured(isolated_config):
    _write(isolated_config, {"home_postcode": ["DA17 6AB"]})
    assert sc.home_postcode() == "DA17 6AB"


def test_the_field_names_can_be_configured_once_known(isolated_config):
    _write(isolated_config, {"distance_param": ["distance"],
                             "postcode_param": ["location"]})
    assert sc.distance_param() == "distance"
    assert sc.postcode_param() == "location"


# ── Standards coverage (Phase 1D audit) ─────────────────────────────────────

@pytest.mark.parametrize("course", [
    # the Level 6/7 degree standards, named explicitly
    "Cyber security technical professional (integrated degree) (level 6)",
    "Digital and technology solutions professional (level 6)",
    "Digital and technology solutions specialist (level 7)",
    "Data scientist (integrated degree) (level 6)",
    "Network engineer (level 6)",
    "Software engineer (level 6)",
    "Artificial intelligence (AI) data specialist (level 7)",
    "Machine learning engineer (level 6)",
])
def test_the_level_six_and_seven_degree_standards_are_recognised(course):
    assert sc.is_digital_standard(course) is True, f"{course!r} not recognised"


def test_business_analyst_is_recognised():
    """The route sweep returned Business Analyst at Level 4 as a Digital-route
    role while this list did not have it, so `judge` was excluding it. The
    curated-list risk, realised — hence the report now prints every excluded
    standard."""
    assert sc.is_digital_standard("Business analyst (level 4)") is True


@pytest.mark.parametrize("raw,expected", [
    # brackets anywhere, not just a trailing level
    ("Artificial intelligence (AI) data specialist (level 7)",
     "artificial intelligence data specialist"),
    ("Cyber security technical professional (integrated degree) (level 6)",
     "cyber security technical professional"),
    ("Data scientist (integrated degree)", "data scientist"),
    ("Software developer (level 4)", "software developer"),
])
def test_bracketed_asides_are_stripped_from_a_standard_name(raw, expected):
    """A trailing-level strip alone left mid-name brackets in place, so
    "Artificial intelligence (AI) data specialist" silently failed to match."""
    assert sc.course_name(raw) == expected


def test_stripping_brackets_does_not_merge_distinct_standards():
    assert sc.course_name("Business administrator (level 4)") == "business administrator"
    assert sc.is_digital_standard("Business administrator (level 4)") is False


# ── Narrow production terms vs broad audit terms ────────────────────────────

def test_production_searches_the_narrow_terms_only():
    """The 1C run found every one of the 34 keyword hits already inside the
    route sweep's 42, so the broad single words were buying nothing on every
    poll. They stay in the audit, where the question can keep being asked."""
    assert sc.search_keywords() == list(sc.NARROW_SEARCH_KEYWORDS)
    assert sc.NARROW_SEARCH_KEYWORDS == (
        "cyber", "cyber security", "software developer", "developer", "devops",
        "cloud", "network engineer", "data", "computing", "software")


def test_the_broad_terms_are_out_of_production():
    assert sc.BROAD_SEARCH_KEYWORDS == (
        "engineer", "information", "IT", "technology", "digital", "systems",
        "security")
    for term in sc.BROAD_SEARCH_KEYWORDS:
        assert term not in sc.search_keywords()


def test_the_broad_terms_are_still_audited():
    audit = sc.audit_keywords()
    for term in sc.BROAD_SEARCH_KEYWORDS:
        assert term in audit
    for term in sc.NARROW_SEARCH_KEYWORDS:
        assert term in audit


def test_the_audit_list_has_no_duplicates():
    audit = sc.audit_keywords()
    assert len(audit) == len(set(audit))


def test_keyword_scans_env_var_overrides_both_lists(monkeypatch):
    monkeypatch.setenv("KEYWORD_SCANS", "cyber, devops")
    assert sc.search_keywords() == ["cyber", "devops"]
    assert sc.audit_keywords() == ["cyber", "devops"]
    monkeypatch.delenv("KEYWORD_SCANS")


# ── The watchlist protects against the standard rule ────────────────────────

def test_the_watchlist_ships_with_the_named_employers():
    wl = sc.settings()["watchlist_employers"]
    for name in ("thales", "bae systems", "babcock", "airbus", "leonardo",
                 "gchq", "ncsc", "dstl", "cavendish nuclear"):
        assert name in wl


@pytest.mark.parametrize("employer", [
    "BAE SYSTEMS PLC", "Thales UK", "Babcock International Group",
    "Airbus Operations Ltd", "Leonardo MW Ltd", "GCHQ", "NCSC",
    "DSTL", "Cavendish Nuclear Limited",
])
def test_a_watched_employer_is_kept_for_review_not_excluded(employer):
    """Never silently drop a watched employer's listing: a defence firm posting
    against an odd standard is the case most worth a look."""
    v = sc.judge("Project Manager Apprentice", employer,
                 "Business administrator (level 4)")
    assert v.keep is True
    assert v.reason == sc.KEEP_WATCHLIST_REVIEW
    assert v.review is True
    assert v.detail == "business administrator", "the standard must be named"
    assert v.watchlist


def test_an_unwatched_employer_with_the_same_listing_is_excluded():
    """The protection has to be specific, or it is not a filter at all."""
    v = sc.judge("Project Manager Apprentice", "Random Ltd",
                 "Business administrator (level 4)")
    assert v.keep is False
    assert v.reason == sc.REASON_NON_DIGITAL_STANDARD


def test_the_watchlist_does_not_resurrect_a_title_exclusion():
    """It guards against the STANDARD rule only. A watched employer's security
    guard vacancy is still a security guard vacancy."""
    v = sc.judge("Security Guard Apprentice", "BAE SYSTEMS PLC", "")
    assert v.keep is False
    assert v.reason == sc.REASON_TITLE


def test_a_watched_employer_with_a_digital_standard_needs_no_review():
    v = sc.judge("Apprentice", "BAE SYSTEMS PLC",
                 "Cyber security technologist (level 4)")
    assert v.keep is True
    assert v.reason == sc.KEEP_DIGITAL_STANDARD
    assert v.review is False


def test_the_watchlist_is_replaceable_from_config(isolated_config):
    """It is a starting point; the real list comes from the spreadsheet."""
    _write(isolated_config, {"watchlist_employers": ["acme defence"]})
    assert sc.judge("Project Manager Apprentice", "ACME Defence Ltd",
                    "Business administrator (level 4)").review is True
    assert sc.judge("Project Manager Apprentice", "BAE SYSTEMS PLC",
                    "Business administrator (level 4)").keep is False


def test_the_verdict_carries_the_review_flag_for_the_log():
    d = sc.judge("Project Manager Apprentice", "Thales UK",
                 "Business administrator (level 4)").as_dict()
    assert d["review"] is True
    assert d["watchlist"] == "thales"


# ── The verified distance/postcode field names ─────────────────────────────

def test_the_verified_field_names_are_configured():
    """location=DA17&distance=20 returned 2 results on the live site."""
    assert sc.distance_param() == "distance"
    assert sc.postcode_param() == "location"
    assert sc.home_postcode() == "DA17"
    assert sc.search_distance() == 20
