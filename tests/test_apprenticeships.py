"""Scout apprenticeships — the Apprenticeship Radar merge.

Covers the parts that are easy to break and expensive to notice: employer alias
matching, external_ref dedup, closure detection, the combined notifier, and the
?source= filter. The scraper's HTTP layer is stubbed throughout — these tests
never touch gov.uk.
"""
import os
import time
from datetime import date, datetime, timedelta

import pytest

import app as app_module
import database as db
from models import Employer, ScanLog, ScoutJob
from models import db as orm
from services import apprenticeships, scout_config, scout_notify
from services.scrapers.gov_uk import (SEARCH_URL, GovUkScraper, RawVacancy,
                                      ScraperError, _parse_uk_date, clean_title,
                                      reference_from_href)


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


@pytest.fixture
def watchlist(ctx):
    """A small watchlist mirroring the shapes the real seed produces."""
    for name, aliases in [
        ("Amazon", "Amazon UK,AMAZON UK SERVICES"),
        ("Capgemini", "CAPGEMINI UK,Capgemini"),
        ("BAE Systems", "BAE SYSTEMS PLC,BAE Systems Applied Intelligence"),
        ("Jaguar Land Rover", "JAGUAR - LAND ROVER LTD,JLR,Jaguar Land Rover"),
        ("JP Morgan", "JPMorgan Chase,JPMORGAN CHASE BANK,JP Morgan Chase"),
        ("EY", "Ernst & Young,EY LLP,ERNST YOUNG"),
    ]:
        if not Employer.query.filter_by(name=name).first():
            orm.session.add(Employer(name=name, aliases=aliases, watching=True))
    orm.session.commit()
    yield Employer.query.filter_by(watching=True).all()


def _raw(ref, employer, **kw):
    return RawVacancy(
        reference=ref,
        title=kw.get("title", "Apprentice Software Developer"),
        employer_name_raw=employer,
        location=kw.get("location", "London"),
        wage=kw.get("wage", "£18,000 a year"),
        training_course=kw.get("training_course", "Software developer (level 4)"),
        level=kw.get("level", 4),
        closing_text=kw.get("closing_text", "Closes in 10 days"),
        closing_date=kw.get("closing_date"),
        url=f"https://www.findapprenticeship.service.gov.uk/apprenticeship/{ref}",
    )


# ── Employer alias matching ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw_name,expected", [
    ("AMAZON UK SERVICES LTD.", "Amazon"),
    ("CAPGEMINI UK PLC", "Capgemini"),
    ("BAE SYSTEMS PLC", "BAE Systems"),
    ("JAGUAR - LAND ROVER LTD", "Jaguar Land Rover"),
    ("JPMorgan Chase & Co", "JP Morgan"),
    ("CTRL O LTD", None),
])
def test_alias_matching(watchlist, raw_name, expected):
    """The six required mappings, including gov.uk noise matching nothing."""
    matched = apprenticeships.match_employer(raw_name, watchlist)
    assert (matched.name if matched else None) == expected


@pytest.mark.parametrize("raw_name", ["SURREY COUNTY COUNCIL", "MODUS LTD"])
def test_short_aliases_need_a_word_boundary(watchlist, raw_name):
    """Plain substring matching makes `EY` match "SURREY…" and `MOD` match
    "MODUS LTD". A false employer match is an alert to the user's inbox, so
    aliases of <=3 chars must land on a word boundary."""
    assert apprenticeships.match_employer(raw_name, watchlist) is None


def test_short_alias_still_matches_as_a_whole_word(watchlist):
    matched = apprenticeships.match_employer("JLR HOLDINGS LTD", watchlist)
    assert matched is not None and matched.name == "Jaguar Land Rover"


def test_unwatched_employers_are_not_matched(watchlist):
    emp = Employer.query.filter_by(name="Amazon").first()
    emp.watching = False
    orm.session.commit()
    watched = Employer.query.filter_by(watching=True).all()
    assert apprenticeships.match_employer("AMAZON UK SERVICES LTD.", watched) is None
    emp.watching = True
    orm.session.commit()


# ── Persistence + dedup ──────────────────────────────────────────────────────

def test_dedup_by_external_ref(watchlist):
    """The same VAC ref across two scans inserts once and refreshes last_seen."""
    raws = [_raw("VAC900001", "AMAZON UK SERVICES LTD.")]
    bucket = []
    assert apprenticeships._persist_vacancies(raws, None, bucket, watchlist) == 1

    row = ScoutJob.query.filter_by(external_ref="VAC900001").one()
    first_seen = row.last_seen
    row.last_seen = datetime.utcnow() - timedelta(days=1)
    orm.session.commit()

    assert apprenticeships._persist_vacancies(raws, None, [], watchlist) == 0
    assert ScoutJob.query.filter_by(external_ref="VAC900001").count() == 1
    assert ScoutJob.query.filter_by(external_ref="VAC900001").one().last_seen > \
        datetime.utcnow() - timedelta(minutes=5)
    assert first_seen is not None


def test_both_watched_and_unwatched_employers_are_queued_for_alert(watchlist):
    """CHANGED in Phase 1: a Level 4+ digital role is alerted whoever posts it.

    This used to assert that only watchlist matches reached the bucket, which
    meant a genuine role from an employer not on the list was persisted and then
    never mentioned. The watchlist now earns a STAR instead of gatekeeping.
    """
    raws = [
        _raw("VAC900010", "AMAZON UK SERVICES LTD."),
        _raw("VAC900011", "CTRL O LTD"),
    ]
    bucket = []
    assert apprenticeships._persist_vacancies(raws, None, bucket, watchlist) == 2
    assert sorted(r.external_ref for r in bucket) == ["VAC900010", "VAC900011"]

    starred = {r.external_ref: r.starred for r in bucket}
    assert starred["VAC900010"] is True, "watchlist match should be starred"
    assert starred["VAC900011"] is False

    noise = ScoutJob.query.filter_by(external_ref="VAC900011").one()
    assert noise.employer_id is None
    assert noise.employer_name_raw == "CTRL O LTD"     # raw kept regardless
    assert noise.listing_type == "apprenticeship"


def test_persisted_fields_map_onto_scout_columns(watchlist):
    raws = [_raw("VAC900020", "CAPGEMINI UK PLC", level=6,
                 closing_date=date(2026, 8, 12))]
    apprenticeships._persist_vacancies(raws, None, [], watchlist)
    row = ScoutJob.query.filter_by(external_ref="VAC900020").one()

    assert row.listing_type == "apprenticeship"
    assert row.source == "gov_uk"          # provider, NOT the job/appr split
    assert row.job_type == "apprenticeship"
    assert row.salary == "£18,000 a year"  # wage maps onto Scout's salary
    assert row.company == "Capgemini"      # canonical name for display
    assert row.employer_name_raw == "CAPGEMINI UK PLC"
    assert row.level == 6
    assert row.closing_date == date(2026, 8, 12)
    assert row.status == "open"


def test_keyword_scan_links_employer_found_later(watchlist):
    """A keyword scan can see a posting before the employer scan identifies it."""
    apprenticeships._persist_vacancies(
        [_raw("VAC900030", "SOME UNKNOWN LTD")], None, [], watchlist)
    row = ScoutJob.query.filter_by(external_ref="VAC900030").one()
    assert row.employer_id is None

    amazon = Employer.query.filter_by(name="Amazon").first()
    apprenticeships._persist_vacancies(
        [_raw("VAC900030", "SOME UNKNOWN LTD")], amazon, [], watchlist)
    row = ScoutJob.query.filter_by(external_ref="VAC900030").one()
    assert row.employer_id == amazon.id


# ── Closure detection ────────────────────────────────────────────────────────

def test_stale_apprenticeships_close_but_are_not_deleted(watchlist):
    apprenticeships._persist_vacancies(
        [_raw("VAC900040", "AMAZON UK SERVICES LTD.")], None, [], watchlist)
    row = ScoutJob.query.filter_by(external_ref="VAC900040").one()
    row.last_seen = datetime.utcnow() - timedelta(
        minutes=apprenticeships.poll_minutes() * 4)
    orm.session.commit()

    assert apprenticeships._detect_closures() >= 1
    row = ScoutJob.query.filter_by(external_ref="VAC900040").one()
    assert row.status == "closed"          # historical window preserved


def test_recently_seen_rows_stay_open(watchlist):
    apprenticeships._persist_vacancies(
        [_raw("VAC900050", "AMAZON UK SERVICES LTD.")], None, [], watchlist)
    apprenticeships._detect_closures()
    assert ScoutJob.query.filter_by(external_ref="VAC900050").one().status == "open"


def test_closure_detection_ignores_jobs(watchlist):
    """Reed/SerpAPI jobs have no last_seen and must never be marked closed."""
    db.add_scout_job(title="Sales Assistant", company="TK Maxx", location="Erith",
                     salary="", job_type="part time",
                     url="https://example.com/closure-test", description="",
                     source="reed", posted_date="Today",
                     found_date="2026-08-01 00:00:00")
    apprenticeships._detect_closures()
    job = ScoutJob.query.filter_by(url="https://example.com/closure-test").one()
    assert job.listing_type == "job"
    assert job.status != "closed"


# ── Closing soon ─────────────────────────────────────────────────────────────

def test_closing_soon_only_returns_watched_and_imminent(watchlist):
    apprenticeships._persist_vacancies([
        _raw("VAC900060", "AMAZON UK SERVICES LTD.",
             closing_date=date.today() + timedelta(days=2)),
        _raw("VAC900061", "AMAZON UK SERVICES LTD.",
             closing_date=date.today() + timedelta(days=30)),
        _raw("VAC900062", "CTRL O LTD",           # unmatched -> excluded
             closing_date=date.today() + timedelta(days=1)),
    ], None, [], watchlist)

    refs = {r.external_ref for r in apprenticeships.closing_soon(days=3)}
    assert "VAC900060" in refs
    assert "VAC900061" not in refs
    assert "VAC900062" not in refs


def test_closing_soon_sends_once_per_day(watchlist, monkeypatch):
    sent = []
    monkeypatch.setattr(scout_notify, "alert_closing_soon",
                        lambda rows, days=3: sent.append(len(rows)) or True)
    apprenticeships._persist_vacancies(
        [_raw("VAC900070", "AMAZON UK SERVICES LTD.",
              closing_date=date.today() + timedelta(days=1))], None, [], watchlist)
    db.kv_set(apprenticeships._CLOSING_SOON_KEY, "")

    assert apprenticeships._maybe_send_closing_soon() >= 1
    assert apprenticeships._maybe_send_closing_soon() == 0   # guarded
    assert len(sent) == 1


# ── ScanLog: the reserved-name bug ───────────────────────────────────────────

def test_scanlog_query_text_does_not_shadow_model_query(ctx):
    """Radar named this column `query`, which shadowed Model.query and raised
    AttributeError: 'Comparator' object has no attribute 'order_by'."""
    apprenticeships._log_scan("keyword", "cyber security", 3, 1, 120)
    # The failure mode was that .query.order_by() blew up entirely.
    rows = ScanLog.query.order_by(ScanLog.ran_at.desc()).all()
    assert rows and rows[0].query_text == "cyber security"
    assert not isinstance(ScanLog.query, ScanLog.query_text.__class__)


# ── Notifier ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n_jobs,n_appr,expected", [
    (3, 2, "🚨 3 new jobs, 2 new apprenticeships"),
    (1, 1, "🚨 1 new job, 1 new apprenticeship"),
    (0, 2, "🚨 2 new apprenticeships"),
    (3, 0, "🚨 3 new jobs"),
])
def test_subject_pluralises_and_omits_empty_categories(n_jobs, n_appr, expected):
    assert scout_notify.build_subject(n_jobs, n_appr) == expected


def test_combined_email_groups_by_source_with_badges(watchlist):
    apprenticeships._persist_vacancies(
        [_raw("VAC900080", "AMAZON UK SERVICES LTD.", level=6)], None, [], watchlist)
    appr = ScoutJob.query.filter_by(external_ref="VAC900080").all()
    jobs = [{"title": "Sales Assistant", "company": "TK Maxx",
             "location": "Erith", "url": "https://example.com/j1",
             "salary": "12.21", "posted_date": "Today"}]

    html = scout_notify.render_email(
        [scout_notify._as_row(j, scout_notify.JOB) for j in jobs],
        [scout_notify._as_row(a, scout_notify.APPRENTICESHIP) for a in appr])

    # The apprenticeship group is now split by level, so an unknown level is
    # labelled rather than implied (see _without_level).
    assert "Jobs (1)" in html
    assert "Apprenticeships \u00b7 Level 4+ (1)" in html
    assert ">JOB\n" in html or "JOB" in html
    assert "APPRENTICESHIP" in html
    # The fields the decision actually gets made on.
    assert "Level 6" in html
    assert "£18,000 a year" in html
    assert "Software developer (level 4)" in html
    assert "Closes in 10 days" in html


def test_email_escapes_html_in_listing_fields(watchlist):
    row = scout_notify._as_row(
        {"title": "<script>alert(1)</script>", "company": "A & B",
         "location": "", "url": "https://x", "salary": "", "posted_date": ""},
        scout_notify.JOB)
    html = scout_notify.render_email([row], [])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_alert_returns_false_when_nothing_new():
    assert scout_notify.alert_new_listings(jobs=[], apprenticeships=[]) is False


# ── Route filter ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(watchlist):
    apprenticeships._persist_vacancies(
        [_raw("VAC900090", "AMAZON UK SERVICES LTD.")], None, [], watchlist)
    db.add_scout_job(title="Sales Assistant", company="TK Maxx", location="Erith",
                     salary="", job_type="part time",
                     url="https://example.com/route-test", description="",
                     source="reed", posted_date="Today",
                     found_date="2026-08-01 00:00:00")
    c = app_module.app.test_client()
    c.post("/login", data={"password": os.environ["APP_PASSWORD"]},
           follow_redirects=True)
    return c


def test_source_filter_splits_the_two_kinds(client):
    everything = client.get("/api/scout/jobs").get_json()
    jobs = client.get("/api/scout/jobs?source=job").get_json()
    appr = client.get("/api/scout/jobs?source=apprenticeship").get_json()

    assert {r["listing_type"] for r in jobs} == {"job"}
    assert {r["listing_type"] for r in appr} == {"apprenticeship"}
    assert len(everything) == len(jobs) + len(appr)


def test_unknown_source_value_falls_back_to_all(client):
    assert len(client.get("/api/scout/jobs?source=banana").get_json()) == \
        len(client.get("/api/scout/jobs").get_json())


def test_days_to_close_is_computed_for_apprenticeships(watchlist, client):
    apprenticeships._persist_vacancies(
        [_raw("VAC900100", "AMAZON UK SERVICES LTD.",
              closing_date=date.today() + timedelta(days=2))], None, [], watchlist)
    rows = client.get("/api/scout/jobs?source=apprenticeship").get_json()
    row = next(r for r in rows if r["external_ref"] == "VAC900100")
    assert row["days_to_close"] == 2


def test_employers_route_lists_and_toggles(client):
    emps = client.get("/api/scout/employers").get_json()
    assert any(e["name"] == "Amazon" for e in emps)

    page = client.get("/scout/employers")
    assert page.status_code == 200
    token = page.get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]

    amazon = next(e for e in emps if e["name"] == "Amazon")
    r = client.put(f"/api/scout/employers/{amazon['id']}",
                   json={"watching": False}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.get_json()["employer"]["watching"] is False
    client.put(f"/api/scout/employers/{amazon['id']}",
               json={"watching": True}, headers={"X-CSRF-Token": token})


# ── Scraper helpers (no network) ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Closes in 10 days (Wednesday 12 August 2026)", date(2026, 8, 12)),
    ("Closes on Saturday 1 March 2025", date(2025, 3, 1)),
    ("", None),
    ("Closes soon", None),
])
def test_uk_date_parser(text, expected):
    assert _parse_uk_date(text) == expected


def test_multi_location_duplicate_cards_collapse_to_one():
    """gov.uk renders a multi-location posting as several identical cards; the
    scraper dedupes per page by reference."""
    card = """
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/VAC2000045313">Apprentice Software Developer</a>
      <p class="govuk-body">CTRL O LTD</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Software developer (level 4)</p>
      <p class="govuk-body"><b>Wage</b> £18,000 a year</p>
    </li>
    """
    html = f"<html><title>3 results found</title><ul>{card * 3}</ul></html>"
    parsed = GovUkScraper()._parse_search_page(html, min_level=4)
    assert len(parsed) == 1
    assert parsed[0].reference == "VAC2000045313"
    assert parsed[0].level == 4


def test_min_level_filters_out_lower_levels():
    card = """
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/VAC111">Apprentice</a>
      <p class="govuk-body">SOME LTD</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Digital support (level 3)</p>
    </li>
    """
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    assert GovUkScraper()._parse_search_page(html, min_level=4) == []
    assert len(GovUkScraper()._parse_search_page(html, min_level=3)) == 1


def test_zero_results_short_circuits():
    html = "<html><title>0 results found</title><ul></ul></html>"
    assert GovUkScraper()._parse_search_page(html, min_level=4) == []


# ── Result-count parsing (the coverage bug) ──────────────────────────────────

@pytest.mark.parametrize("total", [10, 20, 30, 40, 50, 60, 100, 110, 1000, 2340])
def test_zero_results_detection_is_not_a_substring_match(total):
    """The regression that was costing coverage.

    The check was `if "0 results found" in title`. That substring is also in
    "10 results found", "20", "50", "100"... so any total ending in a zero digit
    made the parser discard the whole page. Where the title carries the overall
    total (identical on every page) that zeroed the entire search silently.
    """
    card = """
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/VAC1">Cyber Apprentice</a>
      <p class="govuk-body">BT GROUP</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Cyber security technologist (level 4)</p>
    </li>
    """
    html = f"<html><title>{total} results found</title><ul>{card}</ul></html>"
    parsed = GovUkScraper()._parse_search_page(html, min_level=4)
    assert len(parsed) == 1, f"'{total} results found' was read as zero results"


def test_a_genuine_zero_still_short_circuits():
    html = "<html><title>0 results found</title><ul></ul></html>"
    assert GovUkScraper()._parse_search_page(html, min_level=4) == []


@pytest.mark.parametrize("title,expected", [
    ("0 results found", 0),
    ("1 result found", 1),
    ("47 results found", 47),
    ("2,340 results found", 2340),
    ("Apprenticeship search", None),
])
def test_reported_total_reads_the_sites_own_count(title, expected):
    """The number to compare a scrape against, so a shortfall is visible."""
    html = f"<html><title>{title}</title></html>"
    assert GovUkScraper.reported_total(html) == expected


# ── Parse diagnostics ────────────────────────────────────────────────────────

def test_a_page_mixing_reference_formats_keeps_everything():
    """Was: the numeric card here was dropped and the VAC one kept. Both are
    kept now, and the format mix is reported so a new shape is visible as a
    count rather than as a quiet shortfall."""
    cards = """
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/1234567">Data Apprentice</a>
      <p class="govuk-body">LLOYDS</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Data analyst (level 4)</p>
    </li>
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/VAC9">Network Apprentice</a>
      <p class="govuk-body">CISCO</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Network engineer (level 6)</p>
    </li>
    """
    html = f"<html><title>2 results found</title><ul>{cards}</ul></html>"
    stats = {}
    parsed = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)

    assert len(parsed) == 2
    assert stats["cards_seen"] == 2
    assert stats.get("dropped_no_ref", 0) == 0
    assert stats["ref_kinds"] == {"id": 1, "vac": 1}
    assert {v.reference for v in parsed} == {"1234567", "VAC9"}


def test_stats_record_the_sites_total_and_why_pagination_stopped():
    html = "<html><title>0 results found</title><ul></ul></html>"
    stats = {}
    s = GovUkScraper(min_interval=0)
    s.session.get = lambda *a, **k: type(
        "R", (), {"status_code": 200, "text": html,
                  "raise_for_status": lambda self: None})()
    s.by_keyword("devops", stats=stats)

    assert stats["reported_total"] == 0
    assert stats["pages_fetched"] == 1
    assert stats["stopped_because"] == "empty_page"


def test_hitting_the_page_cap_is_reported():
    """So "we collected 50" can be told apart from "the site only had 50"."""
    def card(n):
        return (f'<li class="das-search-results__list-item">'
                f'<a href="/apprenticeship/VAC{n}">Apprentice</a>'
                f'<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
                f'<p class="govuk-body"><b>Training course</b> '
                f'Software developer (level 4)</p></li>')
    counter = {"i": 0}

    def get(*_a, **_k):
        counter["i"] += 1
        base = counter["i"] * 100
        body = "".join(card(base + k) for k in range(10))
        return type("R", (), {
            "status_code": 200,
            "text": f"<html><title>999 results found</title><ul>{body}</ul></html>",
            "raise_for_status": lambda self: None})()

    stats = {}
    s = GovUkScraper(min_interval=0, max_pages=5)
    s.session.get = get
    got = s.by_keyword("data", min_level=4, stats=stats)

    assert len(got) == 50
    assert stats["pages_fetched"] == 5
    assert stats["stopped_because"] == "page_cap"


# ── Reference parsing: the main source of the coverage gap ───────────────────
# Measured 2026-10-23: gov.uk reported 21 results for "software developer" and
# the scraper kept 5, dropping 16 whose detail links did not match
# /apprenticeship/VAC<digits>. A listing must not be lost over its link shape.

@pytest.mark.parametrize("href,expected_ref,expected_kind", [
    # The format the scraper was written against.
    ("/apprenticeship/VAC1000123", "VAC1000123", "vac"),
    ("/apprenticeship/VAC1000123?searchTerm=data&page=2", "VAC1000123", "vac"),
    ("/apprenticeship/VAC1000123#details", "VAC1000123", "vac"),
    ("https://www.findapprenticeship.service.gov.uk/apprenticeship/VAC1000123",
     "VAC1000123", "vac"),
    # Numeric ids, and other paths, which used to be dropped outright.
    ("/apprenticeship/1000284512", "1000284512", "id"),
    ("/vacancy/1000284512", "1000284512", "id"),
    ("/vacancies/1000284512", "1000284512", "id"),
    ("/find-an-apprenticeship/1234", "1234", "id"),
    ("/apprenticeships/VAC1000123", "VAC1000123", "id"),
    ("/apprenticeship/1000284512/", "1000284512", "id"),
    # No id-shaped segment: fall back to the path, which is still stable.
    ("/apprenticeship/some-slug", "/apprenticeship/some-slug", "path"),
])
def test_reference_is_derived_from_any_detail_link(href, expected_ref, expected_kind):
    ref, kind, source = reference_from_href(href)
    assert ref == expected_ref
    assert kind == expected_kind
    assert source == "findapprenticeship", \
        "these are all on-site links; see the external cases below"


@pytest.mark.parametrize("href", ["#", "", "   ", "mailto:a@b.c", "javascript:void(0)",
                                  "tel:+441234567890"])
def test_non_detail_hrefs_yield_no_reference(href):
    """A utility control must never become a listing's identity."""
    ref, kind, _source = reference_from_href(href)
    assert (ref, kind) == (None, None)


def _card(href, title="Apprentice Software Developer", emp="IBM UK",
          course="Software developer (level 4)"):
    return (f'<li class="das-search-results__list-item">'
            f'<h2 class="das-search-results__heading">'
            f'<a href="{href}">{title}</a></h2>'
            f'<p class="govuk-body">{emp}</p>'
            f'<p class="govuk-body">London</p>'
            f'<p class="govuk-body"><b>Training course</b> {course}</p></li>')


def test_the_measured_shortfall_is_recovered():
    """The exact shape reported on 2026-10-23: site says 21, 5 on the VAC
    format and 16 on another. Previously 5 were kept; all 21 must be now."""
    cards = [_card(f"/apprenticeship/VAC100{i}") for i in range(5)]
    cards += [_card(f"/vacancy/10002845{i:02d}") for i in range(16)]
    html = f"<html><title>21 results found</title><ul>{''.join(cards)}</ul></html>"

    stats = {}
    got = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)

    assert len(got) == 21, "listings are still being dropped on link shape"
    assert stats.get("dropped_no_ref", 0) == 0
    assert stats["ref_kinds"] == {"vac": 5, "id": 16}
    assert len({v.reference for v in got}) == 21, "references must stay unique"


def test_urls_stay_absolute_for_every_link_shape():
    """`url` is what the Apply link opens, so a relative href must be joined to
    the site root and an absolute one must not be doubled up."""
    for href in ("/vacancy/1000284512",
                 "/apprenticeship/VAC1",
                 "https://www.findapprenticeship.service.gov.uk/apprenticeship/VAC1"):
        html = f"<html><title>1 result found</title><ul>{_card(href)}</ul></html>"
        v = GovUkScraper()._parse_search_page(html, min_level=4)[0]
        assert v.url.startswith("https://www.findapprenticeship.service.gov.uk/")
        assert "https://www.findapprenticeship.service.gov.uk/https" not in v.url


def test_dropped_card_hrefs_are_captured_unfiltered():
    """The diagnostic's own bug: it only recorded hrefs containing
    "apprentice", filtering by a guess about the format it existed to find. A
    card linking to /vacancy/<id> was dropped AND unsampled, so the report read
    "16 dropped, 0 samples" and said nothing."""
    card = ('<li class="das-search-results__list-item">'
            '<a href="#save">Save</a>'
            '<p class="govuk-body">ACME</p></li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    stats = {}
    got = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)

    assert got == []
    assert stats["dropped_no_ref"] == 1
    assert "#save" in stats["unmatched_href_samples"], \
        "every href on a dropped card must be captured, not just matching ones"


def test_a_card_with_no_anchor_at_all_is_dropped_cleanly():
    card = ('<li class="das-search-results__list-item">'
            '<p class="govuk-body">ACME</p>'
            '<p class="govuk-body"><b>Training course</b> Software developer (level 4)</p>'
            '</li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    stats = {}
    assert GovUkScraper()._parse_search_page(html, min_level=4, stats=stats) == []
    assert stats["dropped_no_ref"] == 1


def test_the_title_link_wins_over_a_utility_link():
    """Identity comes from the heading link, not whichever anchor is first."""
    card = ('<li class="das-search-results__list-item">'
            '<a href="/save/999">Save this vacancy</a>'
            '<h2 class="das-search-results__heading">'
            '<a href="/vacancy/1000284512">Apprentice Software Developer</a></h2>'
            '<p class="govuk-body">IBM UK</p>'
            '<p class="govuk-body">London</p>'
            '<p class="govuk-body"><b>Training course</b> Software developer (level 4)</p>'
            '</li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    v = GovUkScraper()._parse_search_page(html, min_level=4)[0]
    assert v.reference == "1000284512"
    assert v.title == "Apprentice Software Developer"


# ── External cross-posts: identity lives in the query string ─────────────────
# gov.uk lists some public-sector roles off-site. Civil Service Jobs puts every
# vacancy on the SAME path (/csr/viewjob.cgi) and distinguishes them only by
# ?jcode=. Stripping the query gave 16 cards one reference, and the in-page
# dedup then kept exactly one of them — the second half of the coverage gap.

# The real URL from after-reffix.json.
HSE_CSJOBS_URL = ("https://www.civilservicejobs.service.gov.uk"
                  "/csr/viewjob.cgi?jcode=2017911")
HSE_VAC_REF = "VAC2000060231"


def test_the_real_civil_service_url_keeps_its_jcode():
    ref, kind, source = reference_from_href(HSE_CSJOBS_URL)
    assert ref == "jcode:2017911"
    assert kind == "jcode"
    assert source == "civil_service"


def test_two_jcodes_on_the_same_path_stay_distinct():
    """The collapse itself: same host, same path, different vacancies."""
    a = reference_from_href(HSE_CSJOBS_URL)
    b = reference_from_href("https://www.civilservicejobs.service.gov.uk"
                            "/csr/viewjob.cgi?jcode=2017912")
    assert a.reference != b.reference
    assert {a.reference, b.reference} == {"jcode:2017911", "jcode:2017912"}


def test_a_page_of_civil_service_cross_posts_is_not_collapsed():
    cards = "".join(
        f'<li class="das-search-results__list-item">'
        f'<h2><a href="https://www.civilservicejobs.service.gov.uk'
        f'/csr/viewjob.cgi?jcode=20179{i:02d}">'
        f'Software Developer Apprentice (Band 5/EO) (opens in new tab)</a></h2>'
        f'<p class="govuk-body">Health and Safety Executive</p>'
        f'<p class="govuk-body">Bootle</p></li>'
        for i in range(16))
    html = f"<html><title>16 results found</title><ul>{cards}</ul></html>"

    stats = {}
    got = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)

    assert len(got) == 16, "cross-posts collapsed to one reference again"
    assert len({v.reference for v in got}) == 16
    assert stats["sources"] == {"civil_service": 16}


def test_external_reference_ignores_tracking_parameters():
    """Two links to ONE vacancy must not become two listings."""
    plain = reference_from_href(HSE_CSJOBS_URL)
    tracked = reference_from_href(HSE_CSJOBS_URL + "&utm_source=newsletter&gclid=xyz")
    assert plain.reference == tracked.reference


def test_external_reference_ignores_parameter_order_and_scheme():
    a = reference_from_href("https://example.test/jobs/view?b=2&a=1")
    b = reference_from_href("http://example.test/jobs/view?a=1&b=2")
    assert a.reference == b.reference
    assert a.source == "other"


def test_an_external_link_with_no_id_parameter_uses_the_whole_url():
    ref, kind, source = reference_from_href("https://careers.example.test/roles/sw-dev")
    assert kind == "url"
    assert ref == "careers.example.test/roles/sw-dev"
    assert source == "other"


def test_an_onsite_vac_reference_is_unaffected_by_a_query_string():
    """On-site identity stays the VAC reference, so search params in a link
    cannot split one listing into several."""
    a = reference_from_href(f"/apprenticeship/{HSE_VAC_REF}")
    b = reference_from_href(f"/apprenticeship/{HSE_VAC_REF}?searchTerm=data&PageNumber=2")
    assert a.reference == b.reference == HSE_VAC_REF
    assert a.source == "findapprenticeship"


def test_external_and_onsite_references_cannot_collide():
    """External refs are namespaced, so a jcode can't be read as a gov.uk id."""
    external = reference_from_href(
        "https://www.civilservicejobs.service.gov.uk/csr/viewjob.cgi?jcode=1000284512")
    onsite = reference_from_href("/apprenticeship/1000284512")
    assert external.reference != onsite.reference


# ── Multi-location vacancies: one listing, every location ───────────────────
# Confirmed by curl on 2026-10-23: on page 1 of "software developer",
# jcode=2017911 appears EIGHT times — gov.uk renders a multi-location Civil
# Service vacancy once per location and counts each copy in "N results found".
# So those extra cards are the site repeating itself, not listings we lost.
# They must merge into one listing that keeps the locations, not be discarded.

HSE_LOCATIONS = ["Bootle", "Birmingham", "Bristol", "Cardiff",
                 "Glasgow", "Leeds", "London", "Manchester"]


def _location_cards(url, locations, title="Software Developer Apprentice "
                                         "(Band 5/EO) (opens in new tab)",
                    employer="Health and Safety Executive"):
    return "".join(
        f'<li class="das-search-results__list-item">'
        f'<h2><a href="{url}">{title}</a></h2>'
        f'<p class="govuk-body">{employer}</p>'
        f'<p class="govuk-body">{loc}</p></li>'
        for loc in locations)


def test_eight_location_cards_become_one_listing():
    """The real case, exactly: 8 identical jcode cards -> 1 listing, 8 locations."""
    html = (f"<html><title>8 results found</title><ul>"
            f"{_location_cards(HSE_CSJOBS_URL, HSE_LOCATIONS)}</ul></html>")
    stats = {}
    got = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)

    assert len(got) == 1, f"expected one merged listing, got {len(got)}"
    v = got[0]
    assert v.reference == "jcode:2017911"
    assert v.location_count == 8
    assert list(v.locations) == HSE_LOCATIONS
    # The site counted all 8, so 7 were repeats of a vacancy we already had.
    assert stats["cards_seen"] == 8
    assert stats["duplicate_cards"] == 7
    assert stats["extra_locations"] == 7


def test_the_merged_listing_keeps_its_first_location_in_location():
    """`location` stays a single string so existing callers and the scout_jobs
    column are unaffected; the full set lives in `locations`."""
    html = (f"<html><title>8 results found</title><ul>"
            f"{_location_cards(HSE_CSJOBS_URL, HSE_LOCATIONS)}</ul></html>")
    v = GovUkScraper()._parse_search_page(html, min_level=4)[0]
    assert v.location == "Bootle"


def test_a_single_location_vacancy_still_reports_one_location():
    html = (f"<html><title>1 result found</title><ul>"
            f"{_location_cards(HSE_CSJOBS_URL, ['Bootle'])}</ul></html>")
    v = GovUkScraper()._parse_search_page(html, min_level=4)[0]
    assert v.location_count == 1
    assert list(v.locations) == ["Bootle"]


def test_repeated_identical_locations_are_not_counted_twice():
    """Defensive: the same location twice is one location, not two."""
    html = (f"<html><title>3 results found</title><ul>"
            f"{_location_cards(HSE_CSJOBS_URL, ['Bootle', 'Bootle', 'Leeds'])}"
            f"</ul></html>")
    stats = {}
    v = GovUkScraper()._parse_search_page(html, min_level=4, stats=stats)[0]
    assert list(v.locations) == ["Bootle", "Leeds"]
    assert stats["duplicate_cards"] == 2      # both repeat cards consumed slots
    assert stats["extra_locations"] == 1      # only one added a new location


def test_two_different_vacancies_are_not_merged_by_location():
    """Only a shared REFERENCE merges. Same employer, same title, same town,
    different vacancies must stay separate."""
    html = ("<html><title>2 results found</title><ul>"
            + _location_cards(HSE_CSJOBS_URL, ["Bootle"])
            + _location_cards(
                "https://www.civilservicejobs.service.gov.uk"
                "/csr/viewjob.cgi?jcode=2017912", ["Bootle"])
            + "</ul></html>")
    got = GovUkScraper()._parse_search_page(html, min_level=4)
    assert len(got) == 2
    assert {v.location_count for v in got} == {1}


def test_locations_merge_across_a_page_boundary():
    """A multi-location vacancy can straddle pages, so the merge cannot live
    only inside one page."""
    pages = [
        f"<html><title>8 results found</title><ul>"
        f"{_location_cards(HSE_CSJOBS_URL, HSE_LOCATIONS[:4])}</ul></html>",
        f"<html><title>8 results found</title><ul>"
        f"{_location_cards(HSE_CSJOBS_URL, HSE_LOCATIONS[4:])}</ul></html>",
        "<html><title>8 results found</title><ul></ul></html>",
    ]
    calls = {"i": 0}

    def get(*_a, **_k):
        i = calls["i"]
        calls["i"] += 1
        text = pages[min(i, len(pages) - 1)]
        return type("R", (), {"status_code": 200, "text": text,
                              "raise_for_status": lambda self: None})()

    s = GovUkScraper(min_interval=0)
    s.session.get = get
    stats = {}
    got = s.by_keyword("software developer", min_level=4, stats=stats)

    assert len(got) == 1, "the same vacancy on two pages became two listings"
    assert got[0].location_count == 8
    assert sorted(got[0].locations) == sorted(HSE_LOCATIONS)


def test_as_dict_exposes_locations_and_count():
    v = RawVacancy(reference="jcode:1", title="t", employer_name_raw="e",
                   location="Bootle")
    v.add_location("Leeds")
    d = v.as_dict()
    assert d["locations"] == ["Bootle", "Leeds"]
    assert d["location_count"] == 2


def test_add_location_reports_whether_it_was_new():
    v = RawVacancy(reference="jcode:1", title="t", employer_name_raw="e",
                   location="Bootle")
    assert v.add_location("Leeds") is True
    assert v.add_location("Leeds") is False
    assert v.add_location("") is False
    assert v.location_count == 2


# ── "(opens in new tab)" is not part of the job title ────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Software Developer Apprentice (Band 5/EO) (opens in new tab)",
     "Software Developer Apprentice (Band 5/EO)"),
    ("Cyber Apprentice (opens in new tab)", "Cyber Apprentice"),
    ("Cyber Apprentice (opens in a new tab)", "Cyber Apprentice"),
    ("Cyber Apprentice (opens in new window)", "Cyber Apprentice"),
    ("Cyber Apprentice (Opens In New Tab)", "Cyber Apprentice"),
    ("Data Apprentice", "Data Apprentice"),
    ("", ""),
])
def test_opens_in_new_tab_is_stripped(raw, expected):
    assert clean_title(raw) == expected


def test_parsed_titles_are_cleaned():
    card = (f'<li class="das-search-results__list-item">'
            f'<h2><a href="{HSE_CSJOBS_URL}">Software Developer Apprentice '
            f'(Band 5/EO) (opens in new tab)</a></h2>'
            f'<p class="govuk-body">Health and Safety Executive</p>'
            f'<p class="govuk-body">Bootle</p></li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    v = GovUkScraper()._parse_search_page(html, min_level=4)[0]
    assert v.title == "Software Developer Apprentice (Band 5/EO)"
    assert "opens in new tab" not in v.title.lower()


# ── A missing level is reported, never a reason to drop ──────────────────────

def test_external_listings_with_no_level_survive_a_level_filter():
    """Cross-posts carry no training course, so no level can be parsed. Dropping
    them would silently discard every off-site public-sector apprenticeship."""
    card = (f'<li class="das-search-results__list-item">'
            f'<h2><a href="{HSE_CSJOBS_URL}">Software Developer Apprentice</a></h2>'
            f'<p class="govuk-body">Health and Safety Executive</p>'
            f'<p class="govuk-body">Bootle</p></li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"

    for floor in (1, 4, 6, 7):
        stats = {}
        got = GovUkScraper()._parse_search_page(html, min_level=floor, stats=stats)
        assert len(got) == 1, f"dropped at min_level={floor}"
        assert got[0].level is None
        assert stats["level_unparsed"] == 1
        assert stats.get("dropped_below_level", 0) == 0


def test_a_known_level_below_the_floor_is_still_filtered():
    """The level filter must keep working for listings that DO state a level."""
    card = ('<li class="das-search-results__list-item">'
            '<h2><a href="/apprenticeship/VAC1">Apprentice</a></h2>'
            '<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
            '<p class="govuk-body"><b>Training course</b> '
            'Digital support technician (level 3)</p></li>')
    html = f"<html><title>1 result found</title><ul>{card}</ul></html>"
    stats = {}
    assert GovUkScraper()._parse_search_page(html, min_level=4, stats=stats) == []
    assert stats["dropped_below_level"] == 1


def test_source_defaults_to_findapprenticeship_for_relative_links():
    assert reference_from_href("/apprenticeship/VAC1").source == "findapprenticeship"
    assert RawVacancy(reference="VAC1", title="t",
                      employer_name_raw="e").source == "findapprenticeship"


# ── The query actually sent ──────────────────────────────────────────────────

def test_search_params_are_exactly_what_we_think():
    """Pins the query so a change to coverage is a deliberate, visible edit.

    Today: the search term and a fixed sort. No location, distance, level or
    route filter — which is why this is a bare national keyword search.
    """
    assert GovUkScraper.search_params("cyber security") == {
        "searchTerm": "cyber security",
        "sort": "AgeAsc",
    }


def test_page_one_sends_no_page_parameter_and_later_pages_do():
    seen = []

    def get(_url, params=None, **_k):
        seen.append(dict(params or {}))
        n = len(seen)
        if n > 2:
            return type("R", (), {
                "status_code": 200,
                "text": "<html><title>0 results found</title></html>",
                "raise_for_status": lambda self: None})()
        card = (f'<li class="das-search-results__list-item">'
                f'<a href="/apprenticeship/VAC{n}">Apprentice</a>'
                f'<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
                f'<p class="govuk-body"><b>Training course</b> '
                f'Software developer (level 4)</p></li>')
        return type("R", (), {
            "status_code": 200,
            "text": f"<html><title>99 results found</title><ul>{card}</ul></html>",
            "raise_for_status": lambda self: None})()

    s = GovUkScraper(min_interval=0)
    s.session.get = get
    s.by_keyword("data", min_level=4)

    assert "PageNumber" not in seen[0]
    assert seen[1]["PageNumber"] == 2
    assert seen[2]["PageNumber"] == 3


# ── Scraper politeness ───────────────────────────────────────────────────────
# A scan issues one search per employer alias plus one per keyword. The gap has
# to hold across those search boundaries, not just between pages of one search.

def _stub_requests(scraper, html, timestamps):
    """Point the scraper's session at canned HTML, recording request times."""
    class _Resp:
        status_code = 200
        text = html

        def raise_for_status(self):
            pass

    def _get(*_a, **_kw):
        timestamps.append(time.monotonic())
        return _Resp()

    scraper.session.get = _get


def test_identifying_user_agent_is_sent():
    """gov.uk must be able to tell who is calling, and how to reach them."""
    ua = GovUkScraper().session.headers["User-Agent"]
    assert "ASFA-Scout" in ua
    assert "github.com/3yther/asfa" in ua


def test_requests_are_spaced_within_a_search():
    card = """
    <li class="das-search-results__list-item">
      <a href="/apprenticeship/VAC{n}">Apprentice Software Developer</a>
      <p class="govuk-body">SOME LTD</p>
      <p class="govuk-body">London</p>
      <p class="govuk-body"><b>Training course</b> Software developer (level 4)</p>
    </li>
    """
    # Distinct refs per page would need per-call HTML; one page of real results
    # followed by the same refs makes pagination stop after page 2, which is
    # enough to prove two requests were spaced.
    html = f"<html><title>2 results found</title><ul>{card.format(n=1)}</ul></html>"
    stamps = []
    s = GovUkScraper(min_interval=0.05)
    _stub_requests(s, html, stamps)
    s.by_keyword("software developer")
    assert len(stamps) >= 2
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.05


def test_requests_are_spaced_across_separate_searches():
    """The regression this guards: a per-page sleep left consecutive searches
    firing their first request back-to-back, so a scan with many aliases sent a
    burst with no gap at all."""
    html = "<html><title>0 results found</title><ul></ul></html>"
    stamps = []
    s = GovUkScraper(min_interval=0.05)
    _stub_requests(s, html, stamps)
    for alias in ("Amazon", "Capgemini", "BAE Systems", "JLR"):
        s.by_employer(alias)
    assert len(stamps) == 4
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.05, f"searches fired too close together: {gaps}"


def test_first_request_is_not_delayed():
    """A fresh scraper shouldn't burn an interval before its first request."""
    html = "<html><title>0 results found</title><ul></ul></html>"
    stamps = []
    s = GovUkScraper(min_interval=5.0)
    _stub_requests(s, html, stamps)
    started = time.monotonic()
    s.by_keyword("data")
    assert stamps[0] - started < 1.0


def test_the_page_cap_is_configurable_and_defaults_to_twenty():
    """CHANGED in Phase 1B: the cap was 5 and bound on 5 of 17 widened searches,
    leaving 201 of 605 result slots unfetched. It is now config-driven."""
    assert scout_config.max_pages() == 20
    assert GovUkScraper().max_pages == 20
    assert GovUkScraper(max_pages=3).max_pages == 3


def test_the_page_cap_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCOUT_MAX_PAGES", "7")
    assert scout_config.max_pages() == 7
    assert GovUkScraper().max_pages == 7


@pytest.mark.parametrize("bad", ["0", "-3", "abc", ""])
def test_a_bad_page_cap_falls_back_to_the_default(monkeypatch, bad):
    """A mistyped env var must not disable pagination or crash a scan."""
    monkeypatch.setenv("SCOUT_MAX_PAGES", bad)
    assert scout_config.max_pages() == 20


# ── Config ───────────────────────────────────────────────────────────────────

def test_keyword_scans_default_and_override(monkeypatch):
    """CHANGED in Phase 1: the default list is the widened one from
    services/scout_config, which keeps the original six and adds to them."""
    monkeypatch.delenv("KEYWORD_SCANS", raising=False)
    terms = apprenticeships.keyword_scans()

    for phrase in scout_config.ORIGINAL_SEARCH_PHRASES:
        assert phrase in terms, f"the original phrase {phrase!r} was dropped"
    for added in scout_config.ADDED_SEARCH_KEYWORDS:
        assert added in terms, f"the added keyword {added!r} is missing"
    assert len(terms) == len(scout_config.ORIGINAL_SEARCH_PHRASES) + len(
        scout_config.ADDED_SEARCH_KEYWORDS)

    monkeypatch.setenv("KEYWORD_SCANS", "cyber security, devops ,")
    assert apprenticeships.keyword_scans() == ["cyber security", "devops"]


def test_poll_interval_defaults_to_six_hours(monkeypatch):
    monkeypatch.delenv("APPRENTICESHIP_POLL_MINUTES", raising=False)
    assert apprenticeships.poll_minutes() == 360
    monkeypatch.setenv("APPRENTICESHIP_POLL_MINUTES", "not-a-number")
    assert apprenticeships.poll_minutes() == 360


def test_existing_job_rows_default_to_listing_type_job(ctx):
    db.add_scout_job(title="Team Member", company="Greggs", location="Dartford",
                     salary="", job_type="part time",
                     url="https://example.com/legacy", description="",
                     source="reed", posted_date="Today",
                     found_date="2026-08-01 00:00:00")
    row = ScoutJob.query.filter_by(url="https://example.com/legacy").one()
    assert row.listing_type == "job"
    assert row.external_ref is None


# ── Off-site cross-posts reach the database ─────────────────────────────────
# A behaviour change worth pinning: these listings used to collapse to one row
# (shared reference) and now persist individually, with level NULL.

def test_external_cross_posts_persist_as_separate_rows(watchlist):
    emp = Employer.query.filter_by(name="BAE Systems").first()
    raws = [
        RawVacancy(reference=f"jcode:201791{i}",
                   title=f"Software Developer Apprentice (Band 5/EO)",
                   employer_name_raw="BAE SYSTEMS PLC",
                   location="Bootle", level=None, training_course="",
                   source="civil_service",
                   url=("https://www.civilservicejobs.service.gov.uk"
                        f"/csr/viewjob.cgi?jcode=201791{i}"))
        for i in range(3)
    ]
    bucket = []
    inserted = apprenticeships._persist_vacancies(raws, employer=emp,
                                                  new_bucket=bucket)

    assert inserted == 3, "cross-posts collapsed into one row"
    rows = ScoutJob.query.filter(
        ScoutJob.external_ref.like("jcode:%")).all()
    assert len({r.external_ref for r in rows}) == 3
    assert all(r.level is None for r in rows)
    # scout_jobs.source is the PROVIDER (where Scout found it), per CLAUDE.md.
    # Finding a role via gov.uk does not stop being gov.uk because the detail
    # page is hosted elsewhere.
    assert all(r.source == "gov_uk" for r in rows)


def test_persisting_an_external_listing_is_idempotent(watchlist):
    # A reference no other test in this module uses: _persist_vacancies dedups
    # on external_ref against the whole table, so a shared value here would
    # make the result depend on test order.
    ref = "jcode:4242424"
    raw = RawVacancy(reference=ref,
                     title="Software Developer Apprentice",
                     employer_name_raw="UNKNOWN DEPT",
                     level=None, training_course="", source="civil_service",
                     url=("https://www.civilservicejobs.service.gov.uk"
                          f"/csr/viewjob.cgi?jcode=4242424"))
    assert apprenticeships._persist_vacancies([raw], employer=None,
                                              new_bucket=[]) == 1
    assert apprenticeships._persist_vacancies([raw], employer=None,
                                              new_bucket=[]) == 0
    assert ScoutJob.query.filter_by(external_ref=ref).count() == 1


def test_a_merged_multi_location_vacancy_persists_as_one_row(watchlist):
    """It is one job, so it is one scout_jobs row and one alert.

    NOTE: scout_jobs has no locations column, so only `location` (the first one
    seen) is stored and location_count does not survive persistence. Flagged
    rather than changed — adding a column is a schema decision.
    """
    raw = RawVacancy(reference="jcode:5150001",
                     title="Software Developer Apprentice",
                     employer_name_raw="BAE SYSTEMS PLC",
                     location="Bootle", level=None, training_course="",
                     source="civil_service",
                     url=("https://www.civilservicejobs.service.gov.uk"
                          "/csr/viewjob.cgi?jcode=5150001"))
    for loc in ("Leeds", "London", "Manchester"):
        raw.add_location(loc)
    assert raw.location_count == 4

    emp = Employer.query.filter_by(name="BAE Systems").first()
    bucket = []
    assert apprenticeships._persist_vacancies([raw], employer=emp,
                                              new_bucket=bucket) == 1

    rows = ScoutJob.query.filter_by(external_ref="jcode:5150001").all()
    assert len(rows) == 1
    assert rows[0].location == "Bootle"
    assert len(bucket) == 1, "one vacancy must produce one alert, not four"


# ── Alerting on any Level 4+ digital role, not just the watchlist ───────────

def _appr_raw(ref, title, employer, course="Software developer (level 4)",
              level=4, location="London"):
    return RawVacancy(reference=ref, title=title, employer_name_raw=employer,
                      training_course=course, level=level, location=location,
                      url=f"https://example.test/{ref}")


def test_unwatched_employers_are_now_alerted_too(watchlist):
    """The widened rule: a Level 4+ digital role matters whoever posts it.
    Previously only watchlist matches reached the alert bucket."""
    raws = [_appr_raw("VAC-A1", "Software Developer Apprentice", "SOME RANDOM LTD")]
    bucket = []
    assert apprenticeships._persist_vacancies(
        raws, employer=None, new_bucket=bucket, excluded=[]) == 1
    assert len(bucket) == 1
    assert bucket[0].starred is False


def test_watchlist_matches_are_starred(watchlist):
    raws = [_appr_raw("VAC-A2", "Software Developer Apprentice", "AMAZON UK SERVICES")]
    bucket = []
    apprenticeships._persist_vacancies(raws, employer=None, new_bucket=bucket,
                                       employers=watchlist, excluded=[])
    assert len(bucket) == 1
    assert bucket[0].starred is True


def test_excluded_listings_are_not_persisted_and_are_logged(watchlist):
    raws = [
        _appr_raw("VAC-A3", "Security Guard Apprentice", "G4S",
                  course="Security operative (level 4)"),
        _appr_raw("VAC-A4", "Software Developer Apprentice", "IBM UK"),
    ]
    bucket, excluded = [], []
    inserted = apprenticeships._persist_vacancies(
        raws, employer=None, new_bucket=bucket, excluded=excluded)

    assert inserted == 1, "the excluded listing must not be persisted"
    assert ScoutJob.query.filter_by(external_ref="VAC-A3").count() == 0
    assert len(excluded) == 1
    row = excluded[0]
    assert row["title"] == "Security Guard Apprentice"
    assert row["employer"] == "G4S"
    # Its stated standard is "Security operative", so the standard rule reports
    # it — a better reason than a title guess.
    assert row["reason"] == "non_digital_standard"
    assert row["detail"] == "security operative"


def test_the_exclusion_log_records_title_employer_and_reason(ctx):
    db.log_scout_exclusions([{
        "external_ref": "VAC-LOG1", "title": "Security Guard Apprentice",
        "employer": "G4S", "reason": "title_excluded",
        "detail": "security guard", "course": "Security operative",
        "url": "https://example.test/x", "listing_type": "apprenticeship"}])

    rows = db.get_scout_exclusions(limit=50)
    row = next(r for r in rows if r["external_ref"] == "VAC-LOG1")
    assert row["title"] == "Security Guard Apprentice"
    assert row["employer"] == "G4S"
    assert row["reason"] == "title_excluded"


def test_the_exclusion_log_is_one_row_per_listing(ctx):
    """A listing excluded on every scan must bump a counter, not add a row —
    otherwise the log becomes a per-scan transcript."""
    entry = {"external_ref": "VAC-LOG2", "title": "Kitchen Assistant",
             "employer": "Greggs", "reason": "title_excluded",
             "detail": "kitchen assistant"}
    assert db.log_scout_exclusions([entry]) == 1
    assert db.log_scout_exclusions([entry]) == 0
    assert db.log_scout_exclusions([entry]) == 0

    rows = [r for r in db.get_scout_exclusions(limit=200)
            if r["external_ref"] == "VAC-LOG2"]
    assert len(rows) == 1
    assert rows[0]["times_seen"] == 3


def test_exclusion_counts_are_grouped_by_reason(ctx):
    db.log_scout_exclusions([
        {"external_ref": "VAC-C1", "title": "a", "employer": "e",
         "reason": "title_excluded", "detail": "d"},
        {"external_ref": "VAC-C2", "title": "b", "employer": "e",
         "reason": "title_excluded", "detail": "d"},
        {"external_ref": "VAC-C3", "title": "c", "employer": "e",
         "reason": "employer_excluded", "detail": "d"},
    ])
    counts = db.count_scout_exclusions()
    assert counts["total"] >= 3
    assert counts["by_reason"]["title_excluded"] >= 2
    assert counts["by_reason"]["employer_excluded"] >= 1


# ── One alert per vacancy, not per location or per search ───────────────────

def test_one_vacancy_found_by_several_searches_is_persisted_once(watchlist):
    raw = _appr_raw("VAC-DEDUP", "Software Developer Apprentice", "IBM UK")
    bucket = []
    assert apprenticeships._persist_vacancies(
        [raw], employer=None, new_bucket=bucket, excluded=[]) == 1
    # a later keyword scan returns the same vacancy
    assert apprenticeships._persist_vacancies(
        [raw], employer=None, new_bucket=bucket, excluded=[]) == 0
    assert len(bucket) == 1, "a re-found vacancy must not alert again"


def test_the_notifier_dedupes_by_reference():
    from services import scout_notify
    rows = [
        {"external_ref": "VAC9", "title": "Cyber Apprentice", "company": "BT",
         "location": "London", "level": 4, "starred": False, "url": "u"},
        {"external_ref": "VAC9", "title": "Cyber Apprentice", "company": "BT",
         "location": "Leeds", "level": 4, "starred": True, "url": "u"},
        {"external_ref": "VAC8", "title": "Data Apprentice", "company": "IBM",
         "location": "Leeds", "level": 6, "starred": False, "url": "u"},
    ]
    out = scout_notify._dedupe_by_reference(
        [scout_notify._as_row(r, scout_notify.APPRENTICESHIP) for r in rows])

    assert len(out) == 2
    vac9 = next(r for r in out if r["reference"] == "VAC9")
    assert vac9["starred"] is True, "the starred copy must win the merge"


def test_rows_without_a_reference_are_not_folded_together():
    from services import scout_notify
    rows = [{"title": "A", "company": "x", "url": "u"},
            {"title": "B", "company": "x", "url": "u"}]
    out = scout_notify._dedupe_by_reference(
        [scout_notify._as_row(r, scout_notify.JOB) for r in rows])
    assert len(out) == 2


# ── Level-unknown roles are grouped and labelled ────────────────────────────

def test_the_email_labels_the_level_unknown_group():
    from services import scout_notify
    rows = [
        {"external_ref": "VAC1", "title": "Cyber Apprentice", "company": "BT",
         "location": "London", "level": 4, "starred": True, "url": "u",
         "training_course": "Cyber security technologist"},
        {"external_ref": "jcode:2017911",
         "title": "Software Developer Apprentice (Band 5/EO)",
         "company": "Health and Safety Executive", "location": "Bootle",
         "level": None, "starred": False, "url": "u"},
    ]
    html = scout_notify.render_email(
        [], [scout_notify._as_row(r, scout_notify.APPRENTICESHIP) for r in rows],
        urgent=True)

    assert "Level 4+ (1)" in html
    assert "level unknown (off-site listing) (1)" in html
    assert "&amp;middot;" not in html, "the heading separator is double-escaped"


def test_the_star_is_rendered_for_watchlist_rows():
    from services import scout_notify
    starred = scout_notify._as_row(
        {"external_ref": "VAC1", "title": "Cyber Apprentice", "company": "BT",
         "location": "London", "level": 4, "starred": True, "url": "u"},
        scout_notify.APPRENTICESHIP)
    plain = dict(starred, starred=False)

    assert "&#9733;" in scout_notify._render_row(starred)
    assert "&#9733;" not in scout_notify._render_row(plain)


# ── The first run is a silent baseline ──────────────────────────────────────

def test_the_first_scan_sends_nothing_and_marks_everything_seen(watchlist, monkeypatch):
    """Widening the keywords makes every role already on the site look new.
    Mailing ~50 at once would teach the user to ignore the alert."""
    db.kv_set(apprenticeships._BASELINE_KEY, "")
    sent = []
    monkeypatch.setattr("services.scout_notify.alert_new_listings",
                        lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["software developer"])

    raws = [_appr_raw(f"VAC-B{i}", "Software Developer Apprentice", "IBM UK")
            for i in range(5)]
    monkeypatch.setattr(GovUkScraper, "by_keyword",
                        lambda self, kw, min_level=4, stats=None: raws)
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()

    assert summary["baseline_run"] is True
    assert summary["alerted"] == 0
    assert sent == [], "the first run must not send an alert"
    rows = ScoutJob.query.filter(ScoutJob.external_ref.like("VAC-B%")).all()
    assert len(rows) == 5
    assert all(r.alerted == 1 for r in rows), "baseline rows must be marked seen"
    assert db.kv_get(apprenticeships._BASELINE_KEY) == "1"


def test_the_second_scan_alerts_on_genuinely_new_roles(watchlist, monkeypatch):
    db.kv_set(apprenticeships._BASELINE_KEY, "1")
    sent = []
    monkeypatch.setattr("services.scout_notify.alert_new_listings",
                        lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["software developer"])

    raws = [_appr_raw("VAC-NEW1", "Software Developer Apprentice", "IBM UK")]
    monkeypatch.setattr(GovUkScraper, "by_keyword",
                        lambda self, kw, min_level=4, stats=None: raws)
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()

    assert summary["baseline_run"] is False
    assert summary["alerted"] == 1
    assert len(sent) == 1
    assert len(sent[0]["apprenticeships"]) == 1


def test_an_empty_first_scan_still_closes_the_baseline(watchlist, monkeypatch):
    """Otherwise the first genuinely-new role would be swallowed as baseline."""
    db.kv_set(apprenticeships._BASELINE_KEY, "")
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["devops"])
    monkeypatch.setattr(GovUkScraper, "by_keyword",
                        lambda self, kw, min_level=4, stats=None: [])
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()
    assert summary["baseline_run"] is True
    assert db.kv_get(apprenticeships._BASELINE_KEY) == "1"


def test_the_scan_reports_how_many_were_excluded(watchlist, monkeypatch):
    db.kv_set(apprenticeships._BASELINE_KEY, "1")
    monkeypatch.setattr("services.scout_notify.alert_new_listings", lambda **kw: True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["security"])

    raws = [
        _appr_raw("VAC-X1", "Security Guard Apprentice", "G4S",
                  course="Security operative (level 4)"),
        _appr_raw("VAC-X2", "Food Hub Project Manager", "Council",
                  course="Project manager (level 4)"),
        _appr_raw("VAC-X3", "Cyber Security Apprentice", "BT",
                  course="Cyber security technologist (level 4)"),
    ]
    monkeypatch.setattr(GovUkScraper, "by_keyword",
                        lambda self, kw, min_level=4, stats=None: raws)
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()
    assert summary["excluded"] == 2
    assert summary["new"] == 1


# ── Pagination stop conditions ──────────────────────────────────────────────
# Measured on the "software" search: the site reported 32 slots and the scraper
# stopped after 20 with reason "repeats", losing 12 vacancies. The old rule was
# "this page added no NEW REFERENCES", which is wrong for a page carrying
# further LOCATIONS of vacancies already seen — no new reference, but a
# different page with real slots behind it.

def _loc_card(ref, loc, title="Software Developer Apprentice"):
    return (f'<li class="das-search-results__list-item">'
            f'<h2><a href="/apprenticeship/{ref}">{title}</a></h2>'
            f'<p class="govuk-body">ACME</p><p class="govuk-body">{loc}</p>'
            f'<p class="govuk-body"><b>Training course</b> '
            f'Software developer (level 4)</p></li>')


def _paged_scraper(pages, site_total, cap=20):
    """A scraper served `pages` in order, repeating the last one after that —
    which is what gov.uk does for an out-of-range PageNumber."""
    calls = {"i": 0}

    def get(*_a, **_k):
        i = calls["i"]
        calls["i"] += 1
        body = pages[i] if i < len(pages) else pages[-1]
        return type("R", (), {
            "status_code": 200,
            "text": (f"<html><title>{site_total} results found</title>"
                     f"<ul>{body}</ul></html>"),
            "raise_for_status": lambda self: None})()

    s = GovUkScraper(min_interval=0, max_pages=cap)
    s.session.get = get
    return s


def test_a_page_of_extra_locations_does_not_stop_pagination():
    """The regression. Page 2 repeats page 1's references with new locations;
    pages 3 and 4 hold the rest of the vacancies."""
    pages = [
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "Leeds") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "Bristol") for i in range(10, 20)),
        "".join(_loc_card(f"VAC{i}", "Cardiff") for i in range(20, 22)),
    ]
    stats = {}
    got = _paged_scraper(pages, 32).by_keyword("software", min_level=4, stats=stats)

    assert len(got) == 22, "pagination stopped early and lost vacancies"
    assert stats["slots_fetched"] == 32
    assert stats["stopped_because"] == "reached_site_total"


def test_pagination_stops_on_an_identical_page():
    """gov.uk serves the same page for an out-of-range PageNumber rather than an
    empty one, so identity of the page is the signal that paging has stopped
    working."""
    pages = ["".join(_loc_card(f"VAC{i}", "London") for i in range(10))]
    stats = {}
    got = _paged_scraper(pages, 999).by_keyword("software", min_level=4, stats=stats)

    assert len(got) == 10
    assert stats["pages_fetched"] == 2, "it must notice on the first repeat"
    assert stats["stopped_because"] == "identical_page"


def test_pagination_stops_once_the_site_total_is_accounted_for():
    """Provably nothing left, so no further request is justified."""
    pages = [
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10, 20)),
    ]
    stats = {}
    got = _paged_scraper(pages, 20).by_keyword("software", min_level=4, stats=stats)

    assert len(got) == 20
    assert stats["pages_fetched"] == 2
    assert stats["stopped_because"] == "reached_site_total"


def test_pagination_stops_on_an_empty_page():
    pages = ["".join(_loc_card(f"VAC{i}", "London") for i in range(10)), ""]
    stats = {}
    got = _paged_scraper(pages, 999).by_keyword("software", min_level=4, stats=stats)

    assert len(got) == 10
    assert stats["stopped_because"] == "empty_page"


def test_pagination_walks_to_the_cap_when_there_is_more():
    pages = ["".join(_loc_card(f"VAC{i * 10 + k}", "London") for k in range(10))
             for i in range(30)]
    stats = {}
    got = _paged_scraper(pages, 9999, cap=20).by_keyword(
        "software", min_level=4, stats=stats)

    assert len(got) == 200
    assert stats["pages_fetched"] == 20
    assert stats["stopped_because"] == "page_cap"


def test_the_repeats_stop_reason_is_gone():
    """It was the wrong question. Nothing should report it any more."""
    pages = [
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "Leeds") for i in range(10)),
    ]
    stats = {}
    _paged_scraper(pages, 20).by_keyword("software", min_level=4, stats=stats)
    assert stats["stopped_because"] != "repeats"


def test_slot_spacing_still_holds_across_many_pages():
    """A 20-page cap must not come at the cost of the 1 request/second floor."""
    pages = ["".join(_loc_card(f"VAC{i * 10 + k}", "London") for k in range(10))
             for i in range(5)]
    stamps = []
    s = _paged_scraper(pages, 9999, cap=5)
    inner = s.session.get

    def timed(*a, **k):
        stamps.append(time.monotonic())
        return inner(*a, **k)

    s.session.get = timed
    s.min_interval = 0.05
    s._last_request_at = None
    s.by_keyword("software", min_level=4)

    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 5
    assert min(gaps) >= 0.05, f"requests came too close together: {gaps}"


def test_per_page_stats_are_merged_not_replaced():
    """Each page is parsed with its own stats dict so the page's slot count can
    be read; the totals must still add up across pages."""
    pages = [
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10, 20)),
    ]
    stats = {}
    _paged_scraper(pages, 20).by_keyword("software", min_level=4, stats=stats)

    assert stats["cards_seen"] == 20
    assert stats["kept"] == 20
    assert stats["ref_kinds"] == {"vac": 20}
    assert stats["reported_total"] == 20


# ── The digital-route sweep ─────────────────────────────────────────────────
# Verified against the live site: ?routeIds=7&levelIds=4&levelIds=5&levelIds=6
# &levelIds=7&sort=AgeAsc returns "45 results found" nationwide, with no
# searchTerm. One pass covers the whole Digital Level 4+ route.

def test_the_route_sweep_sends_the_verified_query():
    import requests
    params = GovUkScraper.route_sweep_params()
    url = requests.Request("GET", SEARCH_URL, params=params).prepare().url

    assert "routeIds=7" in url
    assert url.count("levelIds=") == 4
    for level in (4, 5, 6, 7):
        assert f"levelIds={level}" in url
    assert "sort=AgeAsc" in url
    assert "searchTerm" not in url, "the sweep must not send a search term"


def test_level_ids_are_sent_as_a_repeated_parameter():
    assert GovUkScraper.route_sweep_params()["levelIds"] == [4, 5, 6, 7]


def test_by_route_walks_pagination_like_any_search():
    pages = [
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10)),
        "".join(_loc_card(f"VAC{i}", "London") for i in range(10, 20)),
    ]
    stats = {}
    got = _paged_scraper(pages, 20).by_route(min_level=1, stats=stats)

    assert len(got) == 20
    assert stats["stopped_because"] == "reached_site_total"


def test_by_route_does_not_apply_a_local_level_floor():
    """gov.uk has already filtered to Level 4+ via levelIds. Re-filtering here
    would only discard the listings whose level we failed to parse — exactly the
    off-site ones worth keeping."""
    card = ('<li class="das-search-results__list-item">'
            '<h2><a href="/apprenticeship/VAC1">Apprentice</a></h2>'
            '<p class="govuk-body">ACME</p><p class="govuk-body">London</p>'
            '</li>')
    stats = {}
    got = _paged_scraper([card], 1).by_route(min_level=1, stats=stats)

    assert len(got) == 1
    assert got[0].level is None
    assert stats.get("dropped_below_level", 0) == 0


def test_the_sweep_query_is_requested_separately_from_the_keyword_query():
    """Two different queries; neither should contaminate the other."""
    assert "routeIds" not in GovUkScraper.search_params("cyber")
    assert "searchTerm" not in GovUkScraper.route_sweep_params()


# ── Distance: validated, but not sent until the field name is known ────────

def test_a_valid_distance_is_accepted_but_omitted_without_a_field_name(caplog):
    """Validated so a bad value is caught, omitted so the query cannot lie
    about being local when the field name is unknown."""
    with caplog.at_level("WARNING", logger="asfa.gov_uk"):
        params = GovUkScraper.route_sweep_params(distance=20)

    assert params == {"routeIds": 7, "levelIds": [4, 5, 6, 7], "sort": "AgeAsc"}
    messages = [r.getMessage() for r in caplog.records]
    assert any("no distance_param is configured" in m for m in messages), \
        f"a silently nationwide sweep must warn; got {messages}"


@pytest.mark.parametrize("bad", [25, 0, -1, "near"])
def test_an_invalid_distance_raises_before_any_request(bad):
    with pytest.raises(ValueError):
        GovUkScraper.route_sweep_params(distance=bad)


def test_a_distance_is_sent_once_the_field_name_is_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DISTANCE_PARAM", "distance")
    monkeypatch.setenv("SCOUT_POSTCODE_PARAM", "location")
    params = GovUkScraper.route_sweep_params(distance=20, postcode="DA17 6AB")

    assert params["distance"] == 20
    assert params["location"] == "DA17 6AB"
    assert params["routeIds"] == 7


def test_the_nationwide_sweep_sends_no_distance_at_all():
    assert GovUkScraper.route_sweep_params() == {
        "routeIds": 7, "levelIds": [4, 5, 6, 7], "sort": "AgeAsc"}


def test_the_scan_runs_the_route_sweep_before_the_keywords(watchlist, monkeypatch):
    """Production uses the sweep too, not just the measurement tool."""
    db.kv_set(apprenticeships._BASELINE_KEY, "1")
    monkeypatch.setattr("services.scout_notify.alert_new_listings", lambda **kw: True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["software developer"])

    order = []
    monkeypatch.setattr(
        GovUkScraper, "by_route",
        lambda self, min_level=1, distance=None, postcode="", stats=None:
            (order.append("route"),
             [_appr_raw("VAC-R1", "Trainee Analyst Programme", "IBM UK",
                        course="Data technician (level 4)")])[1])
    monkeypatch.setattr(
        GovUkScraper, "by_keyword",
        lambda self, kw, min_level=4, stats=None:
            (order.append("keyword"),
             [_appr_raw("VAC-R2", "Software Developer Apprentice", "IBM UK")])[1])
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()

    assert order[0] == "route", "the sweep must run before the keyword scans"
    assert summary["route_scanned"] == 1
    assert summary["route_found"] == 1
    assert summary["new"] == 2
    assert ScoutJob.query.filter_by(external_ref="VAC-R1").count() == 1


def test_a_failing_route_sweep_does_not_abort_the_scan(watchlist, monkeypatch):
    """Best-effort, like every other pass: one bad request must not cost the
    keyword scans."""
    db.kv_set(apprenticeships._BASELINE_KEY, "1")
    monkeypatch.setattr("services.scout_notify.alert_new_listings", lambda **kw: True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["software developer"])

    def boom(self, min_level=1, distance=None, postcode="", stats=None):
        raise ScraperError("gov.uk said no")

    monkeypatch.setattr(GovUkScraper, "by_route", boom)
    monkeypatch.setattr(
        GovUkScraper, "by_keyword",
        lambda self, kw, min_level=4, stats=None:
            [_appr_raw("VAC-R3", "Software Developer Apprentice", "IBM UK")])
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()

    assert summary["route_scanned"] == 0
    assert any("route" in e for e in summary["errors"])
    assert summary["new"] == 1, "the keyword scan must still have run"


def test_a_role_found_by_both_the_sweep_and_a_keyword_is_one_row(watchlist, monkeypatch):
    """Dedupe is by reference, so overlapping methods cost nothing."""
    db.kv_set(apprenticeships._BASELINE_KEY, "1")
    sent = []
    monkeypatch.setattr("services.scout_notify.alert_new_listings",
                        lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr(apprenticeships, "keyword_scans", lambda: ["software developer"])

    same = _appr_raw("VAC-R4", "Software Developer Apprentice", "IBM UK")
    monkeypatch.setattr(GovUkScraper, "by_route",
                        lambda self, min_level=1, distance=None, postcode="",
                        stats=None: [same])
    monkeypatch.setattr(GovUkScraper, "by_keyword",
                        lambda self, kw, min_level=4, stats=None: [same])
    monkeypatch.setattr(GovUkScraper, "by_employer",
                        lambda self, a, min_level=4, stats=None: [])

    summary = apprenticeships.scan_apprenticeships()

    assert summary["new"] == 1
    assert ScoutJob.query.filter_by(external_ref="VAC-R4").count() == 1
    assert len(sent[0]["apprenticeships"]) == 1, "one vacancy, one alert"
