"""The detail-page fetch, and the conditions it was approved under.

Each condition is a test here, because they are the terms of the approval
rather than preferences:

  1. only listings Scout has KEPT, never the whole search;
  2. 1s spacing, the identifying User-Agent, no login, no cookies, no account
     pages;
  3. cached — never re-fetched within 24 hours;
  4. back off on 429 or any 5xx and STOP the batch, no retry loop;
  5. store only the six panel fields, no bulk page HTML;
  6. personal use.

Every fixture is synthetic HTML. Nothing here touches the network — the
scraper's transport is stubbed throughout, and a test that accidentally made a
real request would be a bug in the test.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from services import scout_detail  # noqa: E402
from services.scrapers.gov_uk import (DETAIL_URL_RE, DetailFetchBackoff,  # noqa: E402
                                      GovUkScraper, ScraperError)

DETAIL_URL = ("https://www.findapprenticeship.service.gov.uk"
              "/apprenticeship/VAC9000001")


def _detail_html(wage="£22,000 a year", hours="37 hours a week",
                 start="1 September 2027", duration="48 months",
                 closing="14 November 2026",
                 description="Synthetic description of the working day. " * 3):
    """A detail page shaped like gov.uk's: a definition list plus prose."""
    return f"""<!doctype html>
<html><head><title>Apprentice Software Developer</title></head><body>
  <h1>Apprentice Software Developer</h1>
  <dl class="govuk-summary-list">
    <dt>Annual wage</dt><dd>{wage}</dd>
    <dt>Working week</dt><dd>{hours}</dd>
    <dt>Expected start date</dt><dd>{start}</dd>
    <dt>Expected duration</dt><dd>{duration}</dd>
    <dt>Closing date</dt><dd>{closing}</dd>
  </dl>
  <h2>What you will do in your working day</h2>
  <p>{description}</p>
</body></html>"""


def _stub(scraper, status=200, html=None, record=None):
    """Point the scraper's transport at canned HTML. No network, ever."""
    class _Resp:
        def __init__(self):
            self.status_code = status
            self.text = html if html is not None else _detail_html()

    def _get(url, **kwargs):
        if record is not None:
            record.append((url, time.monotonic(), dict(kwargs)))
        return _Resp()

    scraper.session.get = _get
    return scraper


@pytest.fixture(autouse=True)
def clean_detail_tables():
    db._ensure_scout_tables()
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM scout_job_details")
        cur.execute("DELETE FROM scout_jobs")
    yield


def _keep_listing(ref="VAC9000001", url=DETAIL_URL, status="open",
                  found="2026-10-01 00:00:00"):
    """Put a KEPT listing in the database, which is what makes it eligible."""
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_jobs (title, company, url, source, "
            f"listing_type, external_ref, status, found_date) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            ("Apprentice Software Developer", "Northwind Systems", url,
             "gov_uk", "apprenticeship", ref, status, found))


# ── Condition 1: kept listings only ────────────────────────────────────────

def test_only_listings_already_in_the_database_are_eligible():
    """The batch is driven off scout_jobs, so nothing can be fetched that Scout
    has not already decided to keep."""
    assert db.scout_details_needing_fetch() == []
    _keep_listing()
    pending = db.scout_details_needing_fetch()
    assert [r["external_ref"] for r in pending] == ["VAC9000001"]


def test_a_closed_listing_is_not_fetched():
    """Nothing to act on, so no reason to spend a request."""
    _keep_listing(status="closed")
    assert db.scout_details_needing_fetch() == []


def test_an_archived_listing_is_not_fetched():
    _keep_listing()
    with db.get_db() as conn:
        conn.cursor().execute(
            "UPDATE scout_jobs SET archived_at = '2026-10-02 00:00:00'")
    assert db.scout_details_needing_fetch() == []


def test_a_listing_with_no_url_is_not_fetched():
    _keep_listing(url="")
    assert db.scout_details_needing_fetch() == []


# ── Condition 2: politeness, and nothing that looks like a logged-in client ─

@pytest.mark.parametrize("url", [
    DETAIL_URL,
    DETAIL_URL + "/",
    DETAIL_URL + "?utm_source=x",
    DETAIL_URL + "#details",
    "https://findapprenticeship.service.gov.uk/apprenticeship/1000284512",
])
def test_a_vacancy_detail_url_is_accepted(url):
    assert DETAIL_URL_RE.match(url), url


@pytest.mark.parametrize("url", [
    # the search itself — condition 1 says never the whole search
    "https://www.findapprenticeship.service.gov.uk/apprenticeships?searchTerm=x",
    # account and sign-in pages
    "https://www.findapprenticeship.service.gov.uk/account",
    "https://www.findapprenticeship.service.gov.uk/user/signin",
    "https://www.findapprenticeship.service.gov.uk/saved-vacancies",
    # someone else's site
    "https://www.civilservicejobs.service.gov.uk/csr/viewjob.cgi?jcode=2017911",
    "https://careers.example.test/roles/sw-dev",
    # not https
    "http://www.findapprenticeship.service.gov.uk/apprenticeship/VAC1",
    "",
])
def test_anything_that_is_not_a_vacancy_detail_page_is_refused(url):
    assert not DETAIL_URL_RE.match(url or ""), url


def test_a_refused_url_makes_no_request():
    """Refused BEFORE the request, not after."""
    calls = []
    scraper = _stub(GovUkScraper(min_interval=0), record=calls)
    with pytest.raises(ScraperError):
        scraper.fetch_detail(
            "https://www.findapprenticeship.service.gov.uk/account")
    assert calls == [], "a request was made for a refused URL"


def test_an_off_site_listing_is_refused_by_the_batch():
    """Civil Service Jobs is someone else's site and not ours to crawl."""
    _keep_listing(ref="jcode:2017911",
                  url="https://www.civilservicejobs.service.gov.uk"
                      "/csr/viewjob.cgi?jcode=2017911")
    calls = []
    summary = scout_detail.fetch_missing_details(
        scraper=_stub(GovUkScraper(min_interval=0), record=calls))

    assert summary["refused"] == 1
    assert summary["fetched"] == 0
    assert calls == []
    assert db.get_scout_job_detail("jcode:2017911")["fetch_status"] == \
        "refused_not_detail_url"


def test_the_identifying_user_agent_is_sent():
    scraper = GovUkScraper()
    ua = scraper.session.headers["User-Agent"]
    assert "ASFA-Scout" in ua
    assert "github.com/3yther/asfa" in ua


def test_a_cookie_from_a_response_is_not_stored():
    """A cookie jar is the first step towards looking like a signed-in client.

    Tested through the path requests actually uses — extract_cookies_to_jar,
    which is what `Session.send` calls — rather than `cookies.set()`, which
    inserts directly and bypasses the policy the same way a test can bypass a
    front door by climbing through the window.
    """
    from http.cookiejar import DefaultCookiePolicy
    from requests.cookies import extract_cookies_to_jar
    from urllib.request import Request

    scraper = GovUkScraper()
    policy = scraper.session.cookies.get_policy()
    assert isinstance(policy, DefaultCookiePolicy)

    class _Raw:
        def __init__(self):
            self._headers = {"Set-Cookie": "sid=value; Path=/"}

        def info(self):
            class _Info:
                @staticmethod
                def get_all(name, default=None):
                    return (["sid=value; Path=/"]
                            if name.lower() == "set-cookie" else default)
            return _Info()

    request = Request(DETAIL_URL)
    extract_cookies_to_jar(scraper.session.cookies, request, _Raw())

    assert len(scraper.session.cookies) == 0, \
        "a Set-Cookie from a response was stored"


def test_the_cookie_policy_refuses_every_domain():
    """`allowed_domains` is a method on DefaultCookiePolicy, and an empty tuple
    means no domain may set one."""
    policy = GovUkScraper().session.cookies.get_policy()
    assert policy.allowed_domains() == ()


def test_no_cookie_header_is_set_on_the_session():
    headers = {k.lower() for k in GovUkScraper().session.headers}
    assert "cookie" not in headers
    assert "authorization" not in headers, "no credentials are sent"


def test_requests_are_spaced_by_the_same_throttle_as_the_search():
    """The 1s floor is structural: the detail fetch goes through _throttle(),
    so a caller cannot forget it."""
    _keep_listing(ref="VAC1", url=DETAIL_URL)
    _keep_listing(ref="VAC2",
                  url="https://www.findapprenticeship.service.gov.uk"
                      "/apprenticeship/VAC2")
    _keep_listing(ref="VAC3",
                  url="https://www.findapprenticeship.service.gov.uk"
                      "/apprenticeship/VAC3")
    calls = []
    scraper = _stub(GovUkScraper(min_interval=0.05), record=calls)

    scout_detail.fetch_missing_details(scraper=scraper)

    assert len(calls) == 3
    gaps = [b[1] - a[1] for a, b in zip(calls, calls[1:])]
    assert min(gaps) >= 0.05, f"requests came too close together: {gaps}"


def test_the_production_interval_is_one_second():
    from services.scrapers.gov_uk import MIN_REQUEST_INTERVAL
    assert MIN_REQUEST_INTERVAL == 1.0
    assert GovUkScraper().min_interval == 1.0


# ── Condition 3: cached for 24 hours ───────────────────────────────────────

def test_a_freshly_fetched_page_is_not_re_fetched():
    _keep_listing()
    scraper = _stub(GovUkScraper(min_interval=0))
    assert scout_detail.fetch_missing_details(scraper=scraper)["fetched"] == 1

    calls = []
    again = _stub(GovUkScraper(min_interval=0), record=calls)
    summary = scout_detail.fetch_missing_details(scraper=again)

    assert summary["considered"] == 0
    assert calls == [], "a cached page was fetched again"


def test_a_page_older_than_the_cache_window_is_re_fetched():
    _keep_listing()
    db.save_scout_job_detail("VAC9000001", {"wage": "old"}, status="ok")
    with db.get_db() as conn:
        conn.cursor().execute(
            "UPDATE scout_job_details SET fetched_at = '2020-01-01 00:00:00'")

    pending = db.scout_details_needing_fetch()
    assert [r["external_ref"] for r in pending] == ["VAC9000001"]


def test_the_cache_window_is_twenty_four_hours():
    assert db.DETAIL_CACHE_HOURS == 24


def test_a_page_that_parsed_nothing_is_still_cached():
    """Otherwise an unparseable page is re-fetched on every single run."""
    _keep_listing()
    scraper = _stub(GovUkScraper(min_interval=0),
                    html="<html><body><p>nothing useful</p></body></html>")
    scout_detail.fetch_missing_details(scraper=scraper)

    row = db.get_scout_job_detail("VAC9000001")
    assert row["fetch_status"] == "ok_empty"
    assert db.scout_details_needing_fetch() == []


def test_a_failed_fetch_is_cached_so_it_is_not_retried_in_a_loop():
    _keep_listing()
    scraper = _stub(GovUkScraper(min_interval=0), status=404)
    summary = scout_detail.fetch_missing_details(scraper=scraper)

    assert summary["failed"] == 1
    assert db.get_scout_job_detail("VAC9000001")["fetch_status"] == "error"
    assert db.scout_details_needing_fetch() == []


# ── Condition 4: back off and STOP, never retry in a loop ──────────────────

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_a_rate_limit_or_server_error_raises_backoff(status):
    scraper = _stub(GovUkScraper(min_interval=0), status=status)
    with pytest.raises(DetailFetchBackoff):
        scraper.fetch_detail(DETAIL_URL)


def test_backoff_stops_the_whole_batch_on_the_first_one():
    """Not "skip and continue": stop. Hammering a 429 is the one behaviour that
    would make this impolite."""
    for i in range(1, 6):
        _keep_listing(ref=f"VAC{i}",
                      url="https://www.findapprenticeship.service.gov.uk"
                          f"/apprenticeship/VAC{i}")
    calls = []
    scraper = _stub(GovUkScraper(min_interval=0), status=429, record=calls)

    summary = scout_detail.fetch_missing_details(scraper=scraper)

    assert summary["stopped_early"] is True
    assert "429" in summary["stop_reason"]
    assert len(calls) == 1, f"kept going after a 429: {len(calls)} requests"
    assert summary["fetched"] == 0


def test_a_backoff_does_not_poison_the_cache():
    """The listing must still be eligible next run — it was never fetched."""
    _keep_listing()
    scout_detail.fetch_missing_details(
        scraper=_stub(GovUkScraper(min_interval=0), status=503))

    assert db.get_scout_job_detail("VAC9000001") is None
    assert len(db.scout_details_needing_fetch()) == 1


def test_one_bad_page_does_not_stop_the_batch():
    """A 404 is this listing's problem; a 429 is the site's. Only the second
    stops everything."""
    _keep_listing(ref="VAC1", url=DETAIL_URL)
    _keep_listing(ref="VAC2",
                  url="https://www.findapprenticeship.service.gov.uk"
                      "/apprenticeship/VAC2")
    calls = []

    class _Resp:
        def __init__(self, url):
            self.status_code = 404 if url.endswith("VAC9000001") else 200
            self.text = _detail_html()

    scraper = GovUkScraper(min_interval=0)
    scraper.session.get = lambda url, **kw: (calls.append(url), _Resp(url))[1]

    summary = scout_detail.fetch_missing_details(scraper=scraper)
    assert len(calls) == 2, "a single bad page stopped the batch"
    assert summary["failed"] == 1
    assert summary["fetched"] == 1
    assert summary["stopped_early"] is False


def test_backoff_is_a_distinct_type_from_an_ordinary_scraper_error():
    assert issubclass(DetailFetchBackoff, ScraperError)


# ── Condition 5: only the six fields, no page HTML ─────────────────────────

def test_the_six_panel_fields_are_parsed():
    got = GovUkScraper.parse_detail(_detail_html())
    assert got["wage"] == "£22,000 a year"
    assert got["hours"] == "37 hours a week"
    assert got["duration"] == "48 months"
    assert got["start_date"] == "2027-09-01", "dates are normalised"
    assert got["closing_date"] == "2026-11-14"
    assert got["description"].startswith("Synthetic description")


def test_only_the_six_fields_are_stored():
    _keep_listing()
    scout_detail.fetch_missing_details(
        scraper=_stub(GovUkScraper(min_interval=0)))

    row = db.get_scout_job_detail("VAC9000001")
    assert set(row) == set(db.DETAIL_FIELDS) | {
        "external_ref", "fetched_at", "fetch_status"}


def test_there_is_no_column_for_page_html():
    """So a bulk copy of the site cannot accumulate here even by accident."""
    with db.get_db() as conn:
        columns = [r[1] for r in conn.cursor().execute(
            "PRAGMA table_info(scout_job_details)").fetchall()]
    for forbidden in ("html", "body", "raw", "page", "content"):
        assert not any(forbidden in c.lower() for c in columns), \
            f"scout_job_details has a {forbidden!r}-ish column"


def test_the_description_is_capped():
    from services.scrapers.gov_uk import DETAIL_DESCRIPTION_CHARS
    long_text = "word " * 4000
    got = GovUkScraper.parse_detail(_detail_html(description=long_text))
    assert len(got["description"]) <= DETAIL_DESCRIPTION_CHARS


def test_a_missing_field_stays_none_and_is_never_invented():
    """The panel shows a muted dash for None. An invented wage is worse than a
    blank one."""
    html = """<html><body><h1>Apprentice</h1>
      <dl><dt>Annual wage</dt><dd>£20,000 a year</dd></dl></body></html>"""
    got = GovUkScraper.parse_detail(html)
    assert got["wage"] == "£20,000 a year"
    for field in ("hours", "start_date", "duration", "closing_date"):
        assert got[field] is None, f"{field} was invented"


def test_detail_for_returns_none_for_an_unfetched_listing():
    assert scout_detail.detail_for("VAC-NOT-FETCHED") == {
        k: None for k in db.DETAIL_FIELDS}


def test_a_field_is_never_carried_over_from_another_listing():
    _keep_listing(ref="VAC1", url=DETAIL_URL)
    db.save_scout_job_detail("VAC1", {"wage": "£22,000 a year"})
    assert scout_detail.detail_for("VAC2")["wage"] is None


def test_an_unparseable_date_keeps_its_raw_text():
    """Better a string the user can read than a dropped field."""
    got = GovUkScraper.parse_detail(_detail_html(closing="when filled"))
    assert got["closing_date"] == "when filled"


# ── The batch and the scheduled job ────────────────────────────────────────

def test_the_batch_is_limited():
    for i in range(1, 11):
        _keep_listing(ref=f"VAC{i}",
                      url="https://www.findapprenticeship.service.gov.uk"
                          f"/apprenticeship/VAC{i}")
    assert len(db.scout_details_needing_fetch(limit=4)) == 4


def test_the_batch_never_raises():
    """A scheduled job must not take the scheduler down."""
    _keep_listing()

    def boom(*_a, **_k):
        raise RuntimeError("synthetic transport failure")

    scraper = GovUkScraper(min_interval=0)
    scraper.session.get = boom
    summary = scout_detail.fetch_missing_details(scraper=scraper)
    assert summary["failed"] == 1


def test_the_detail_job_is_registered_with_safe_add(monkeypatch):
    """Standing rule: scheduler jobs go through _safe_add."""
    import app as app_module
    added = []

    class _Sched:
        def add_job(self, func, *a, **kw):
            added.append(kw.get("id") or getattr(func, "__name__", "?"))
            return object()

    from services import scheduler
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: _Sched())
    monkeypatch.setattr(app_module, "start_scheduler", lambda: _Sched(),
                        raising=False)
    # The registration block is inside the startup function; assert the job
    # function exists and is wired by name rather than re-running startup.
    assert callable(app_module.apprenticeship_detail_job)
    source = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app.py"), encoding="utf-8").read()
    assert "_safe_add(sched, apprenticeship_detail_job" in source
    assert 'id="apprenticeship_detail_fetch"' in source


def test_the_scan_and_detail_jobs_both_use_safe_add():
    source = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app.py"), encoding="utf-8").read()
    for job in ("scout_daily_scan", "apprenticeship_scan_job",
                "apprenticeship_detail_job"):
        assert f"_safe_add(sched, {job}" in source, f"{job} bypasses _safe_add"


def test_the_detail_job_swallows_its_own_failures(monkeypatch):
    import app as app_module
    from services import scout_detail as sd

    monkeypatch.setattr(sd, "fetch_missing_details",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    app_module.apprenticeship_detail_job()      # must not raise


def test_counts_are_reported_for_the_fetch():
    _keep_listing()
    scout_detail.fetch_missing_details(
        scraper=_stub(GovUkScraper(min_interval=0)))
    counts = db.count_scout_job_details()
    assert counts["total"] == 1
    assert counts["by_status"].get("ok") == 1
