"""Scout apprenticeships — the Apprenticeship Radar merge.

Covers the parts that are easy to break and expensive to notice: employer alias
matching, external_ref dedup, closure detection, the combined notifier, and the
?source= filter. The scraper's HTTP layer is stubbed throughout — these tests
never touch gov.uk.
"""
import os
from datetime import date, datetime, timedelta

import pytest

import app as app_module
import database as db
from models import Employer, ScanLog, ScoutJob
from models import db as orm
from services import apprenticeships, scout_notify
from services.scrapers.gov_uk import GovUkScraper, RawVacancy, _parse_uk_date


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


def test_only_matched_employers_are_queued_for_alert(watchlist):
    """Unmatched gov.uk noise is persisted for the record but never alerts."""
    raws = [
        _raw("VAC900010", "AMAZON UK SERVICES LTD."),
        _raw("VAC900011", "CTRL O LTD"),
    ]
    bucket = []
    assert apprenticeships._persist_vacancies(raws, None, bucket, watchlist) == 2
    assert [r.external_ref for r in bucket] == ["VAC900010"]

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

    assert "Jobs (1)" in html and "Apprenticeships (1)" in html
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


# ── Config ───────────────────────────────────────────────────────────────────

def test_keyword_scans_default_and_override(monkeypatch):
    monkeypatch.delenv("KEYWORD_SCANS", raising=False)
    assert apprenticeships.keyword_scans() == [
        "cyber security", "software developer", "data", "cloud",
        "network engineer", "devops"]

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
