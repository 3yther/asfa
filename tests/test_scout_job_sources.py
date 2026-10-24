"""The part-time job sources, and what an unconfigured location means.

Scout's part-time search (Reed + Google Jobs) used to carry a committed list of
the towns around the user's home. That list is now SCOUT_JOB_LOCATIONS, and
unset means SKIP — not "search the whole country".

That distinction is the point of this file. A part-time retail shift is only
useful within travelling distance, so a nationwide sweep would not be wider
coverage, it would be a dashboard full of jobs that cannot be taken. The
apprenticeship search is the opposite case and is always nationwide; it has no
location setting at all.

Every test here asserts on whether a REQUEST WAS MADE, not just on the return
value: "returns 0" and "returns 0 without touching the network" are different
promises, and only the second one is the one being made.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import scout  # noqa: E402

JOB_VARS = ("SCOUT_JOB_LOCATIONS", "SCOUT_JOB_REGION")


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    """A developer's own .env must not decide what these tests measure."""
    for var in JOB_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REED_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-a-real-one")
    yield


@pytest.fixture
def no_network(monkeypatch):
    """Records any attempt to reach the network, and fails the test if one was
    made.

    It RECORDS rather than raises because both fetchers wrap their request in
    `except Exception` and log it — an AssertionError thrown from inside would
    be swallowed and the test would pass while the request really happened.
    The check runs on teardown, where nothing can catch it.
    """
    attempts = []

    def _record(*a, **k):                     # pragma: no cover - must not run
        attempts.append(a[0] if a else "?")
        raise RuntimeError("no network in this test")

    monkeypatch.setattr(scout.requests, "get", _record)
    monkeypatch.setattr(scout, "GoogleSearch", _record)
    yield attempts
    assert not attempts, (
        f"a request was made when the source should have been skipped: "
        f"{attempts}")


# ── The parser ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("", []),
    ("   ", []),
    (",, ,", []),
    ("Leeds", ["Leeds"]),
    ("Leeds,York", ["Leeds", "York"]),
    ("  Leeds , York  ", ["Leeds", "York"]),
    ("Leeds, , York,", ["Leeds", "York"]),
])
def test_the_location_list_is_parsed_from_the_environment(monkeypatch, raw,
                                                          expected):
    monkeypatch.setenv("SCOUT_JOB_LOCATIONS", raw)
    assert scout.locations() == expected


def test_there_is_no_built_in_location_list():
    """The committed list named the user's home towns. Nothing replaced it."""
    assert scout.locations() == []
    assert not hasattr(scout, "LOCATIONS")


def test_the_region_has_no_default_either():
    assert scout.search_region() == ""


# ── Unset means skip, for both sources ──────────────────────────────────────

def test_reed_is_skipped_when_no_location_is_configured(no_network, caplog):
    with caplog.at_level("INFO", logger="services.scout"):
        assert scout._fetch_reed("2026-10-24", set(), []) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert sum("skipping Reed source" in m for m in messages) == 1, (
        f"expected exactly one skip line, got {messages}")
    assert any("SCOUT_JOB_LOCATIONS" in m for m in messages), (
        "the line has to name the variable, or it is not actionable")


def test_google_jobs_is_skipped_when_no_location_is_configured(no_network,
                                                               caplog):
    with caplog.at_level("INFO", logger="services.scout"):
        assert scout._fetch_serpapi("2026-10-24", set(), []) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert sum("skipping Google Jobs source" in m for m in messages) == 1
    assert any("SCOUT_JOB_LOCATIONS" in m for m in messages)


def test_a_whitespace_only_value_counts_as_unset(monkeypatch, no_network):
    monkeypatch.setenv("SCOUT_JOB_LOCATIONS", "  ,  ,")
    assert scout._fetch_reed("2026-10-24", set(), []) == 0
    assert scout._fetch_serpapi("2026-10-24", set(), []) == 0


def test_a_full_scan_skips_both_sources_and_sends_no_email(monkeypatch,
                                                           no_network):
    def _no_email(jobs):                      # pragma: no cover - must not run
        raise AssertionError("nothing was found, so nothing should be emailed")

    monkeypatch.setattr(scout, "_send_email", _no_email)
    assert scout.scan() == 0


def test_the_missing_api_key_is_still_reported_first(monkeypatch, no_network,
                                                     caplog):
    """With neither a key nor a location, the key is the blocker to name —
    a location would not help while the source cannot be reached at all."""
    monkeypatch.delenv("REED_API_KEY", raising=False)
    with caplog.at_level("INFO", logger="services.scout"):
        assert scout._fetch_reed("2026-10-24", set(), []) == 0
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "REED_API_KEY not set" in messages
    assert "SCOUT_JOB_LOCATIONS" not in messages


# ── Configured means search, once per place ─────────────────────────────────

def test_reed_searches_each_configured_location(monkeypatch):
    monkeypatch.setenv("SCOUT_JOB_LOCATIONS", "Leeds, York")
    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": []}

    monkeypatch.setattr(scout.requests, "get",
                        lambda url, **kw: (calls.append(kw.get("params", {})),
                                           _Resp())[1])
    scout._fetch_reed("2026-10-24", set(), [])

    places = {c["locationName"] for c in calls}
    assert places == {"Leeds", "York"}
    assert len(calls) == 2 * len(scout.REED_KEYWORDS)
    for c in calls:
        assert c["distancefromLocation"] == 3, "still a local radius"


def test_google_jobs_searches_each_configured_location(monkeypatch):
    monkeypatch.setenv("SCOUT_JOB_LOCATIONS", "Leeds, York")
    queries = []

    class _Search:
        def __init__(self, params):
            queries.append(params)

        @staticmethod
        def get_dict():
            return {"jobs_results": []}

    monkeypatch.setattr(scout, "GoogleSearch", _Search)
    scout._fetch_serpapi("2026-10-24", set(), [])

    assert len(queries) == 2
    assert any("Leeds" in q["q"] for q in queries)
    assert any("York" in q["q"] for q in queries)


def test_the_serpapi_region_is_sent_only_when_configured(monkeypatch):
    monkeypatch.setenv("SCOUT_JOB_LOCATIONS", "Leeds")
    queries = []

    class _Search:
        def __init__(self, params):
            queries.append(params)

        @staticmethod
        def get_dict():
            return {"jobs_results": []}

    monkeypatch.setattr(scout, "GoogleSearch", _Search)

    scout._fetch_serpapi("2026-10-24", set(), [])
    assert "location" not in queries[0], "no hardcoded region to fall back to"

    queries.clear()
    monkeypatch.setenv("SCOUT_JOB_REGION", "Leeds, England")
    scout._fetch_serpapi("2026-10-24", set(), [])
    assert queries[0]["location"] == "Leeds, England"


# ── The on-demand skill ─────────────────────────────────────────────────────

def test_the_skill_still_honours_a_location_it_is_given(monkeypatch):
    """A caller asking for Manchester gets Manchester, configured or not."""
    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": []}

    monkeypatch.setattr(scout.requests, "get",
                        lambda url, **kw: (calls.append(kw.get("params", {})),
                                           _Resp())[1])
    scout.scan_jobs_reed("retail", location="Manchester")
    assert [c["locationName"] for c in calls] == ["Manchester"]


def test_the_skill_skips_when_given_nothing_and_nothing_is_configured(
        no_network, caplog):
    with caplog.at_level("INFO", logger="services.scout"):
        assert scout.scan_jobs_reed("retail") == []
    assert any("SCOUT_JOB_LOCATIONS" in r.getMessage() for r in caplog.records)
