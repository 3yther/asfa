"""The Scout hub: Find roles / Upcoming / Tracker / Alerts, and what backs it.

PRIVACY: every fixture is invented — made-up companies, made-up programmes,
made-up notes. Nothing from the real workbook appears here, and no file is
written into the tree.

What each group pins:

  * ONE SOURCE OF TRUTH. A stage set from a role card and a stage set on the
    Tracker board are the same `scout_pipeline` row, matched on url OR on
    normalised title+company, so a role can never end up tracked twice with
    the user's stage stranded on the copy the screen does not read.
  * THE STAR IS COMPUTED. `services/apprenticeships.py` sets `starred` on a
    transient attribute of a model that has no such column, so the read model
    has to work it out from the same sources instead of reading a column that
    is always empty.
  * "NEVER EXCLUDE THIS" MEANS SOMETHING. Allowing a standard makes `judge()`
    keep it OUTRIGHT; allowing an employer removes the terms that would have
    matched them, rather than writing a name that was never on the list.
  * THE DETAIL FETCH STAYS POLITE. The single-listing path shares its
    eligibility clause and its 24-hour cache with the batch, refuses an
    off-site URL without a request, and stops dead on a backoff.
  * NOTHING LEAKS. The channel state is a boolean and a sentence — never a
    token, a chat id or an address.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db                                        # noqa: E402
from services import scout_config, scout_detail, scout_roles  # noqa: E402


@pytest.fixture(autouse=True)
def clean_scout():
    db._ensure_scout_tables()
    db.init_apprenticeships()
    with db.get_db() as conn:
        cur = conn.cursor()
        for table in ("scout_pipeline", "scout_jobs", "scout_programmes",
                      "scout_job_details", "scout_exclusions",
                      "scout_saved_searches", "scout_filter_overrides"):
            cur.execute(f"DELETE FROM {table}")
    yield


@pytest.fixture
def filters_file(tmp_path, monkeypatch):
    """A throwaway scout_filters.json. Never the repo's own."""
    path = tmp_path / "scout_filters.json"
    path.write_text(json.dumps({"home_postcode": ["ZZ99"]}), encoding="utf-8")
    monkeypatch.setenv("SCOUT_FILTERS_PATH", str(path))
    scout_config.settings(refresh=True)
    yield path
    monkeypatch.delenv("SCOUT_FILTERS_PATH", raising=False)
    scout_config.settings(refresh=True)


def _listing(ref="VAC5000001", title="Cyber Apprentice",
             employer="Northwind Defence", url=None, level=6,
             course="Cyber security technical professional (level 6)",
             status="open", found="2026-10-01"):
    url = url or ("https://www.findapprenticeship.service.gov.uk"
                  "/apprenticeship/" + ref)
    cols = ("title, company, location, url, source, found_date, listing_type, "
            "external_ref, employer_name_raw, level, training_course, status")
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"INSERT INTO scout_jobs ({cols}) VALUES ({','.join([ph] * 12)})",
            (title, employer, "Reading", url, "gov_uk", found,
             "apprenticeship", ref, employer, level, course, status))
    return ref, url


# ── One source of truth: the pipeline upsert ────────────────────────────────

def test_upsert_inserts_a_listing_that_is_not_yet_tracked():
    row = db.upsert_scout_pipeline_for_listing(
        job_title="Data Apprentice", company="Pelham Rail",
        job_url="https://example.invalid/a", stage="applied")
    assert row["stage"] == "applied"
    assert len(db.get_scout_pipeline()) == 1


def test_upsert_moves_a_row_matched_by_url_rather_than_duplicating_it():
    db.add_scout_pipeline_job("Data Apprentice", "Pelham Rail",
                              job_url="https://example.invalid/a",
                              stage="interested")
    row = db.upsert_scout_pipeline_for_listing(
        job_title="Data Apprentice (retitled upstream)", company="Pelham Rail",
        job_url="https://example.invalid/a", stage="offer")
    assert row["stage"] == "offer"
    assert len(db.get_scout_pipeline()) == 1, "the url is the identity"


def test_upsert_matches_on_normalised_title_and_company_when_there_is_no_url():
    first = db.add_scout_pipeline_job("Cyber  Apprentice", "THALES",
                                      stage="applied")
    row = db.upsert_scout_pipeline_for_listing(
        job_title="cyber apprentice", company="  thales  ",
        job_url="https://example.invalid/vac1", stage="offer")
    assert row["id"] == first
    assert len(db.get_scout_pipeline()) == 1


def test_upsert_carries_the_url_onto_a_row_that_had_none():
    """Without this the stage is set on a row get_apprenticeship_rows()'s
    join — which is ON job_url — can never see, so the card snaps back."""
    db.add_scout_pipeline_job("Cyber Apprentice", "Thales", stage="applied")
    db.upsert_scout_pipeline_for_listing(
        job_title="Cyber Apprentice", company="Thales",
        job_url="https://example.invalid/vac1", stage="offer")
    assert db.get_scout_pipeline()[0]["job_url"] == "https://example.invalid/vac1"


def test_upsert_refuses_a_listing_with_nothing_to_identify_it_by():
    assert db.upsert_scout_pipeline_for_listing("", "Pelham Rail") is None
    assert db.upsert_scout_pipeline_for_listing("Role", "   ") is None
    assert db.get_scout_pipeline() == []


def test_upsert_coerces_an_unknown_stage_but_keeps_a_legacy_one():
    bad = db.upsert_scout_pipeline_for_listing(
        "A", "B", job_url="https://example.invalid/1", stage="nonsense")
    assert bad["stage"] == db.SCOUT_AUTO_STAGE
    legacy = db.upsert_scout_pipeline_for_listing(
        "C", "D", job_url="https://example.invalid/2", stage="interview")
    assert legacy["stage"] == "interview"


def test_a_stage_set_from_a_card_is_the_row_the_board_reads():
    _listing(ref="VAC5000009", title="Network Apprentice",
             employer="Thornbury Telecom")
    url = ("https://www.findapprenticeship.service.gov.uk"
           "/apprenticeship/VAC5000009")
    db.upsert_scout_pipeline_for_listing(
        job_title="Network Apprentice", company="Thornbury Telecom",
        job_url=url, stage="applied")
    rows = [r for r in db.get_apprenticeship_rows()
            if r["external_ref"] == "VAC5000009"]
    assert rows and rows[0]["stage"] == "applied"


# ── The star is computed, not read ──────────────────────────────────────────

def test_a_watchlist_star_comes_from_the_programme_list(filters_file):
    db.upsert_scout_programme("Northwind Defence", "Cyber Scheme", {})
    _listing(ref="VAC5000002", employer="Northwind Defence PLC")
    view = scout_roles.roles_view()
    card = view["groups"]["level_4_plus"][0]
    assert card["starred"] is True, (
        "scout_jobs has no `starred` column, so the read model must work it "
        "out rather than reading a column that is always empty")


def test_an_unwatched_employer_is_not_starred(filters_file):
    _listing(ref="VAC5000003", employer="Some Company Nobody Follows")
    card = scout_roles.roles_view()["groups"]["level_4_plus"][0]
    assert card["starred"] is False


def test_the_watchlist_only_filter_uses_the_computed_star(filters_file):
    db.upsert_scout_programme("Orrery Bank", "Tech Scheme", {})
    _listing(ref="VAC5000004", employer="Orrery Bank")
    _listing(ref="VAC5000005", employer="Unwatched Ltd")
    view = scout_roles.roles_view({"watchlist_only": True})
    refs = [c["ref"] for c in view["groups"]["level_4_plus"]]
    assert refs == ["VAC5000004"]


def test_watched_employer_names_survive_a_missing_employers_table():
    """A fresh install has not run create_all(). An empty watchlist is the
    honest answer; an exception would take the whole screen down."""
    assert isinstance(db.get_watched_employer_names(), list)


# ── Grouping, filters and badges ────────────────────────────────────────────

def test_off_site_listings_without_a_level_get_their_own_group(filters_file):
    _listing(ref="VAC5000006", level=6)
    _listing(ref="jcode:1", level=None, course="",
             url="https://www.civilservicejobs.service.gov.uk/csr/"
                 "viewjob.cgi?jcode=1")
    groups = scout_roles.roles_view()["groups"]
    assert [c["ref"] for c in groups["level_4_plus"]] == ["VAC5000006"]
    assert [c["ref"] for c in groups["level_unknown"]] == ["jcode:1"]


def test_a_review_listing_is_present_but_in_its_own_group(filters_file):
    ref, _ = _listing(ref="VAC5000007", course="Business administrator (level 3)")
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"UPDATE scout_jobs SET needs_review = 1, "
            f"review_standard = 'Business administrator' "
            f"WHERE external_ref = {ph}", (ref,))
    groups = scout_roles.roles_view()["groups"]
    assert [c["ref"] for c in groups["review"]] == [ref]
    assert groups["level_4_plus"] == [], "a review row is never also a result"


def test_a_filter_narrows_and_the_counts_say_by_how_much(filters_file):
    _listing(ref="VAC5000008", level=6)
    _listing(ref="VAC5000010", level=4)
    view = scout_roles.roles_view({"level": "6"})
    assert view["counts"]["total"] == 2
    assert view["counts"]["shown"] == 1


def test_the_view_carries_the_stage_list_and_the_auto_stage(filters_file):
    view = scout_roles.roles_view()
    assert [s["key"] for s in view["stages"]] == list(db.SCOUT_STAGES)
    assert view["auto_stage"] == db.SCOUT_AUTO_STAGE


# ── allow_forever: correcting a wrong exclusion ─────────────────────────────

def test_allowing_a_standard_makes_judge_keep_it_outright(filters_file):
    before = scout_config.judge("Food Hub Project Manager Apprentice",
                                "Larkspur Catering", "Business administrator")
    assert before.keep is False
    assert before.reason == scout_config.REASON_NON_DIGITAL_STANDARD

    changed = scout_config.allow_forever("standard", "business administrator")
    assert changed["added"]["digital_standards"] == ["business administrator"]

    after = scout_config.judge("Food Hub Project Manager Apprentice",
                               "Larkspur Catering", "Business administrator")
    assert after.keep is True
    assert after.reason == scout_config.KEEP_DIGITAL_STANDARD


def test_allowing_a_standard_appends_rather_than_replacing_the_defaults(filters_file):
    scout_config.allow_forever("standard", "business administrator")
    effective = scout_config.settings()["digital_standards"]
    for default in scout_config.DEFAULT_DIGITAL_STANDARDS:
        assert default in effective


def test_allowing_an_employer_removes_the_term_that_matched_them(filters_file):
    filters_file.write_text(json.dumps(
        {"+employer_exclusions": ["greggs", "unrelated"]}), encoding="utf-8")
    scout_config.settings(refresh=True)
    assert scout_config.matched_employer_exclusion("Greggs plc") == "greggs"

    changed = scout_config.allow_forever("employer", "Greggs plc")
    assert changed["removed"]["employer_exclusions"] == ["greggs"]
    assert scout_config.matched_employer_exclusion("Greggs plc") is None
    assert "unrelated" in scout_config.settings()["employer_exclusions"], (
        "only the terms that would have matched are removed")


def test_allowing_an_employer_also_watches_them(filters_file):
    scout_config.allow_forever("employer", "Castellan Systems")
    assert scout_config.matched_watchlist_employer("Castellan Systems Ltd")


def test_allow_forever_is_idempotent(filters_file):
    scout_config.allow_forever("standard", "business administrator")
    again = scout_config.allow_forever("standard", "BUSINESS ADMINISTRATOR")
    assert again == {"added": {}, "removed": {}}


def test_allow_forever_rejects_an_unknown_kind(filters_file):
    with pytest.raises(ValueError):
        scout_config.allow_forever("colour", "blue")
    with pytest.raises(ValueError):
        scout_config.allow_forever("standard", "   ")


def test_allow_forever_never_touches_the_config_file(filters_file):
    """Corrections go to the database, so the hand-editable file is left
    exactly as the user wrote it — including when it will not parse."""
    before = filters_file.read_text(encoding="utf-8")
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    assert filters_file.read_text(encoding="utf-8") == before


def test_a_correction_still_works_when_the_config_file_is_broken(filters_file):
    filters_file.write_text("{ this is not json", encoding="utf-8")
    scout_config.settings(refresh=True)
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    assert scout_config.is_digital_standard("Supply chain warehouse operative (level 2)")
    assert filters_file.read_text(encoding="utf-8") == "{ this is not json", (
        "and the file the user was mid-edit on is not rewritten")


# ── The three layers ────────────────────────────────────────────────────────

def test_the_database_layer_wins_over_the_file(filters_file):
    filters_file.write_text(json.dumps(
        {"+employer_exclusions": ["larkspur"]}), encoding="utf-8")
    scout_config.settings(refresh=True)
    assert scout_config.matched_employer_exclusion("Larkspur Catering")

    scout_config.allow_forever("employer", "Larkspur Catering")
    assert scout_config.matched_employer_exclusion("Larkspur Catering") is None


def test_the_database_layer_is_a_delta_not_a_replacement(filters_file):
    """Editing the file afterwards must still take effect, with the stored
    corrections riding on top — otherwise the file stops meaning anything."""
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    filters_file.write_text(json.dumps(
        {"+digital_standards": ["something else entirely"],
         "home_postcode": ["AB12"]}), encoding="utf-8")
    scout_config.settings(refresh=True)

    effective = scout_config.settings()["digital_standards"]
    assert "supply chain warehouse operative" in effective, "the stored correction survives"
    assert "something else entirely" in effective, "and so does the file edit"
    assert scout_config.home_postcode() == "AB12"


def test_a_correction_survives_a_restart(filters_file):
    """The whole point: nothing is kept in the process or on the container
    filesystem, so a redeploy cannot lose it."""
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    scout_config._CACHE = {}          # a fresh process has no cache
    scout_config._DB_STAMP_AT = 0.0
    scout_config._DB_STAMP_FOR = None
    assert scout_config.is_digital_standard("Supply chain warehouse operative (level 2)")


def test_a_correction_can_be_undone(filters_file):
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    stored = scout_config.allowed_overrides()
    row = next(r for r in stored if r["value"] == "supply chain warehouse operative")
    assert row["label"] == "Never exclude this standard"

    assert scout_config.undo_override(row["id"]) is True
    assert not scout_config.is_digital_standard("Supply chain warehouse operative (level 2)")
    assert scout_config.undo_override(row["id"]) is False, "already gone"


def test_undoing_an_employer_correction_restores_the_exclusion(filters_file):
    filters_file.write_text(json.dumps(
        {"+employer_exclusions": ["larkspur"]}), encoding="utf-8")
    scout_config.settings(refresh=True)
    scout_config.allow_forever("employer", "Larkspur Catering")
    assert scout_config.matched_employer_exclusion("Larkspur Catering") is None

    for row in scout_config.allowed_overrides():
        scout_config.undo_override(row["id"])
    assert scout_config.matched_employer_exclusion("Larkspur Catering") == "larkspur"


def test_a_later_decision_replaces_an_earlier_opposite_one(filters_file):
    """add and remove of the same value cannot both stand."""
    db.add_scout_filter_override("digital_standards", "supply chain warehouse operative", op="remove")
    db.add_scout_filter_override("digital_standards", "supply chain warehouse operative", op="add")
    rows = [r for r in db.list_scout_filter_overrides()
            if r["value"] == "supply chain warehouse operative"]
    assert len(rows) == 1 and rows[0]["op"] == "add"


def test_an_override_for_an_unknown_list_is_ignored_not_fatal(filters_file):
    db.add_scout_filter_override("not_a_real_list", "whatever", op="add")
    scout_config.settings(refresh=True)
    assert "not_a_real_list" not in scout_config.settings()


def test_the_cache_notices_a_correction_without_waiting_for_the_ttl(filters_file):
    assert not scout_config.is_digital_standard("Supply chain warehouse operative (level 2)")
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    # No refresh() call here: allow_forever has to have done it itself, or the
    # screen would show the old verdict for up to the stamp TTL.
    assert scout_config.is_digital_standard("Supply chain warehouse operative (level 2)")


def test_allow_forever_keeps_the_settings_it_did_not_touch(filters_file):
    scout_config.allow_forever("standard", "supply chain warehouse operative")
    assert scout_config.home_postcode() == "ZZ99"


# ── The detail fetch, one listing at a time ─────────────────────────────────

class _Boom:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def fetch_detail(self, url):
        self.calls += 1
        raise self.exc


class _Fake:
    def __init__(self, fields):
        self.fields = fields
        self.calls = 0

    def fetch_detail(self, url):
        self.calls += 1
        return dict(self.fields)


def test_fetch_one_refuses_a_listing_that_is_not_stored():
    scraper = _Fake({})
    out = scout_detail.fetch_one("VAC_NOT_HERE", scraper=scraper)
    assert out["status"] == "not_found"
    assert scraper.calls == 0, "a listing Scout never kept is never fetched"


def test_fetch_one_refuses_an_off_site_url_without_a_request():
    _listing(ref="jcode:9", level=None, course="",
             url="https://www.civilservicejobs.service.gov.uk/csr/"
                 "viewjob.cgi?jcode=9")
    scraper = _Fake({"wage": "£1"})
    out = scout_detail.fetch_one("jcode:9", scraper=scraper)
    assert out["status"] == "refused_not_detail_url"
    assert scraper.calls == 0, "someone else's site is not ours to crawl"


def test_fetch_one_stores_the_panel_fields():
    ref, _ = _listing(ref="VAC5000011")
    scraper = _Fake({"wage": "£23,400 a year", "hours": "37 hours a week"})
    out = scout_detail.fetch_one(ref, scraper=scraper)
    assert out["status"] == "ok" and out["fields_filled"] == 2
    assert db.get_scout_job_detail(ref)["wage"] == "£23,400 a year"


def test_fetch_one_serves_the_cache_without_a_second_request():
    ref, _ = _listing(ref="VAC5000012")
    scout_detail.fetch_one(ref, scraper=_Fake({"wage": "£1"}))
    scraper = _Fake({"wage": "£2"})
    out = scout_detail.fetch_one(ref, scraper=scraper)
    assert out == {"status": "cached", "cached": True, "fields_filled": 0}
    assert scraper.calls == 0, "24 hours is a condition, not a suggestion"


def test_fetch_one_stops_on_a_backoff_and_does_not_retry():
    from services.scrapers.gov_uk import DetailFetchBackoff
    ref, _ = _listing(ref="VAC5000013")
    scraper = _Boom(DetailFetchBackoff("429 Too Many Requests"))
    out = scout_detail.fetch_one(ref, scraper=scraper)
    assert out["status"] == "backoff"
    assert scraper.calls == 1, "one request, then stop"


def test_fetch_one_skips_a_closed_listing():
    ref, _ = _listing(ref="VAC5000014", status="closed")
    scraper = _Fake({"wage": "£1"})
    assert scout_detail.fetch_one(ref, scraper=scraper)["status"] == "not_found"
    assert scraper.calls == 0


def test_the_single_and_batch_paths_agree_on_who_is_eligible():
    ref, _ = _listing(ref="VAC5000015")
    _listing(ref="VAC5000016", status="closed")
    batch = {r["external_ref"] for r in db.scout_details_needing_fetch(limit=50)}
    assert batch == {ref}
    assert db.scout_detail_candidate(ref)["eligible"] is True
    assert db.scout_detail_candidate("VAC5000016")["eligible"] is False


# ── Channels: a state, never a secret ───────────────────────────────────────

def test_channel_states_name_only_bell_and_telegram():
    assert [c["key"] for c in scout_roles.channel_states()] == ["bell", "telegram"]


def test_channel_states_never_return_a_token_a_chat_id_or_an_address(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    blob = json.dumps(scout_roles.channel_states())
    assert "SECRET-TOKEN-VALUE" not in blob
    assert "987654321" not in blob
    assert "@" not in blob, "no address either — this is rendered in a page"


def test_the_bell_is_always_available():
    bell = scout_roles.channel_states()[0]
    assert bell["configured"] is True
