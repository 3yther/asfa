"""The Scout hub's HTTP surface: /scout/roles and its API.

PRIVACY: invented companies and programmes only, in an isolated temp SQLite
file. Nothing is written into the tree.

What this pins:

  * every route sits behind the session auth gate, and every write behind CSRF;
  * the Tracker board carries BOTH programmes and the listings the user moved,
    plus a column for any retired stage that still holds a row — a column list
    built from SCOUT_STAGES alone would render those rows nowhere at all;
  * the stage set from a role card lands in `scout_pipeline`, which is the
    same table the board reads;
  * the alerts payload states a channel's availability and never its secrets.
"""
import json
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_scouthub_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402
import database as db      # noqa: E402

DETAIL_URL = ("https://www.findapprenticeship.service.gov.uk"
              "/apprenticeship/VAC7000001")


def setup_module(module):
    """Seed into THIS module's isolated DB (see test_api_approval_endpoints)."""
    db.init_apprenticeships()
    cols = ("title, company, location, url, source, found_date, listing_type, "
            "external_ref, employer_name_raw, level, training_course, status")
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"INSERT INTO scout_jobs ({cols}) VALUES ({','.join([ph] * 12)})",
            ("Cyber Apprentice", "Northwind Defence", "Reading", DETAIL_URL,
             "gov_uk", "2026-10-01", "apprenticeship", "VAC7000001",
             "Northwind Defence", 6,
             "Cyber security technical professional (level 6)", "open"))

    db.upsert_scout_programme("Orrery Bank", "Technology Apprenticeship",
                              {"open_months": "oct,nov", "level": "Level 6"})
    # A programme parked on the RETIRED stage. It must still reach the board.
    legacy = db.upsert_scout_programme("Marchmont Logistics",
                                       "IT Apprenticeship", {})
    rows = db.get_scout_programmes()
    for row in rows:
        if row["company"] == "Marchmont Logistics":
            db.set_scout_programme_stage(row["id"], "interview")
    assert legacy in ("inserted", "updated")

    db.log_scout_exclusions([{
        "external_ref": "VAC7100001", "title": "Security Guard Apprentice",
        "employer": "Bastion Facilities", "reason": "title_excluded",
        "detail": "security guard", "course": "Security first line responder",
        "url": "https://example.invalid/1", "listing_type": "apprenticeship"}])

    db.add_saved_search("Level 6 cyber", {"level": "6", "q": "cyber"},
                        ["bell", "telegram"])


def _client(login=True):
    c = app_module.app.test_client()
    if login:
        c.post("/login", data={"password": os.environ["APP_PASSWORD"]},
               follow_redirects=True)
    return c


def _csrf(c):
    with c.session_transaction() as s:
        return s.get("csrf_token", "")


def _post(c, url, payload=None, method="POST"):
    return c.open(url, method=method,
                  headers={"X-CSRF-Token": _csrf(c)},
                  json=payload if payload is not None else {})


# ── The gate ────────────────────────────────────────────────────────────────

def test_every_hub_route_is_behind_the_auth_gate():
    c = _client(login=False)
    for url in ("/api/scout/roles", "/api/scout/roles/VAC7000001",
                "/api/scout/excluded", "/api/scout/tracker",
                "/api/scout/upcoming", "/api/scout/alerts"):
        assert c.get(url).status_code == 401, url
    assert c.get("/scout/roles").status_code in (302, 401)


def test_a_write_without_a_csrf_token_is_refused():
    c = _client()
    r = c.post("/api/scout/roles/VAC7000001/stage", json={"stage": "applied"})
    assert r.status_code == 403


def test_the_page_renders_with_the_theme_stylesheet():
    r = _client().get("/scout/roles")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "css/themes/" in body, "the page must follow the active theme"
    assert "css/scout_roles.css" in body
    assert "js/scout_roles.js" in body


# ── Find roles ──────────────────────────────────────────────────────────────

def test_roles_returns_three_groups_and_the_stage_list():
    data = _client().get("/api/scout/roles").get_json()
    assert set(data["groups"]) == {"level_4_plus", "level_unknown", "review"}
    assert [s["key"] for s in data["stages"]] == list(db.SCOUT_STAGES)
    assert data["auto_stage"] == db.SCOUT_AUTO_STAGE
    assert data["counts"]["total"] >= 1


def test_a_role_detail_carries_the_panel_fields():
    card = _client().get("/api/scout/roles/VAC7000001").get_json()
    assert card["title"] == "Cyber Apprentice"
    for key in ("wage", "hours", "start_date", "duration", "description"):
        assert key in card, f"the panel renders {key} as a dash when unknown"


def test_an_unknown_ref_is_a_404_not_an_empty_card():
    assert _client().get("/api/scout/roles/NOPE").status_code == 404


def test_setting_a_stage_from_a_card_writes_the_pipeline_the_board_reads():
    c = _client()
    r = _post(c, "/api/scout/roles/VAC7000001/stage", {"stage": "applied"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    board = c.get("/api/scout/tracker").get_json()
    titles = [e["title"] for e in board["by_stage"]["applied"]]
    assert "Cyber Apprentice" in titles
    kinds = {e["kind"] for e in board["by_stage"]["applied"]}
    assert "listing" in kinds


def test_an_unknown_stage_is_rejected():
    c = _client()
    r = _post(c, "/api/scout/roles/VAC7000001/stage", {"stage": "promoted"})
    assert r.status_code == 400


# ── Tracker ─────────────────────────────────────────────────────────────────

def test_the_board_carries_programmes_and_listings():
    board = _client().get("/api/scout/tracker").get_json()
    kinds = {e["kind"] for rows in board["by_stage"].values() for e in rows}
    kinds |= {e["kind"] for e in board["not_started"]}
    assert "programme" in kinds


def test_a_retired_stage_still_gets_a_column():
    """"interview" was dropped from the offered list but never migrated. A
    column list built from SCOUT_STAGES alone renders those rows nowhere."""
    board = _client().get("/api/scout/tracker").get_json()
    keys = [s["key"] for s in board["stages"]]
    assert "interview" in keys
    assert [e["title"] for e in board["by_stage"]["interview"]] == \
        ["IT Apprenticeship"]


def test_the_board_reports_what_the_14_day_cleanup_would_take():
    board = _client().get("/api/scout/tracker").get_json()
    assert board["cleanup_days"] == 14
    assert board["cleanup_at_risk"] is not None


def test_moving_a_programme_and_a_listing_both_work():
    c = _client()
    board = c.get("/api/scout/tracker").get_json()
    prog = next(e for rows in board["by_stage"].values() for e in rows
                if e["kind"] == "programme")
    r = _post(c, f"/api/scout/tracker/programme/{prog['id']}/stage",
              {"stage": "offer"})
    assert r.status_code == 200 and r.get_json()["stage"] == "offer"

    listing = next(e for rows in
                   c.get("/api/scout/tracker").get_json()["by_stage"].values()
                   for e in rows if e["kind"] == "listing")
    r = _post(c, f"/api/scout/tracker/listing/{listing['id']}/stage",
              {"stage": "offer"})
    assert r.status_code == 200 and r.get_json()["stage"] == "offer"


def test_an_unknown_kind_is_rejected():
    c = _client()
    r = _post(c, "/api/scout/tracker/wombat/1/stage", {"stage": "offer"})
    assert r.status_code == 400


def test_clearing_a_listings_stage_returns_it_to_the_auto_stage():
    """A pipeline row always holds a stage, so "not tracked" means the auto
    stage — which is exactly the one the cleanup may take."""
    c = _client()
    listing = next(e for rows in
                   c.get("/api/scout/tracker").get_json()["by_stage"].values()
                   for e in rows if e["kind"] == "listing")
    r = _post(c, f"/api/scout/tracker/listing/{listing['id']}/stage",
              {"stage": ""})
    assert r.get_json()["stage"] == db.SCOUT_AUTO_STAGE
    board = c.get("/api/scout/tracker").get_json()
    assert all(e["kind"] != "listing"
               for rows in board["by_stage"].values() for e in rows), (
        "a listing back at the auto stage belongs on Find roles, not the board")


# ── Upcoming ────────────────────────────────────────────────────────────────

def test_upcoming_runs_september_to_august():
    data = _client().get("/api/scout/upcoming").get_json()
    assert data["months"][0] == "sep" and data["months"][-1] == "aug"
    assert len(data["months"]) == 12
    row = next(r for r in data["rows"] if r["company"] == "Orrery Bank")
    assert row["open_months"] == ["oct", "nov"]
    assert row["confirmed_open_date"] == "", "unconfirmed until you say so"


# ── Excluded ────────────────────────────────────────────────────────────────

def test_the_excluded_view_reports_counts_by_reason():
    data = _client().get("/api/scout/excluded").get_json()
    assert data["counts"]["by_reason"]["title_excluded"] == 1
    assert data["listings"][0]["employer"] == "Bastion Facilities"


def test_allow_forever_validates_its_input():
    c = _client()
    assert _post(c, "/api/scout/excluded/allow",
                 {"kind": "colour", "value": "blue"}).status_code == 400
    assert _post(c, "/api/scout/excluded/allow",
                 {"kind": "standard", "value": ""}).status_code == 400


def test_a_correction_round_trips_and_can_be_undone():
    c = _client()
    r = _post(c, "/api/scout/excluded/allow",
              {"kind": "standard", "value": "supply chain warehouse operative"})
    assert r.status_code == 200
    allowed = r.get_json()["allowed"]
    row = next(a for a in allowed
               if a["value"] == "supply chain warehouse operative")
    assert row["label"] == "Never exclude this standard"

    # It is visible on the next load of the screen, from the database.
    listed = c.get("/api/scout/excluded").get_json()["allowed"]
    assert any(a["id"] == row["id"] for a in listed)

    r = c.open(f"/api/scout/excluded/allow/{row['id']}", method="DELETE",
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/scout/excluded").get_json()["allowed"] == []


def test_undoing_a_correction_that_is_gone_is_a_404():
    c = _client()
    r = c.open("/api/scout/excluded/allow/999999", method="DELETE",
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 404


def test_a_stored_correction_changes_what_the_filter_does():
    """The point of the whole feature: the verdict actually moves."""
    from services import scout_config
    c = _client()
    before = scout_config.judge("Warehouse Systems Apprentice", "Marchmont",
                                "Supply chain warehouse operative")
    assert before.keep is False

    _post(c, "/api/scout/excluded/allow",
          {"kind": "standard", "value": "supply chain warehouse operative"})
    after = scout_config.judge("Warehouse Systems Apprentice", "Marchmont",
                               "Supply chain warehouse operative")
    assert after.keep is True and after.reason == scout_config.KEEP_DIGITAL_STANDARD


# ── Alerts ──────────────────────────────────────────────────────────────────

def test_alerts_report_each_channel_without_a_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    data = _client().get("/api/scout/alerts").get_json()
    assert [c["key"] for c in data["channels"]] == ["bell", "telegram"]
    blob = json.dumps(data)
    assert "SECRET-TOKEN-VALUE" not in blob and "987654321" not in blob


def test_a_saved_search_round_trips():
    c = _client()
    r = _post(c, "/api/scout/alerts",
              {"name": "Near home only", "filters": {"near_home": True},
               "channels": ["bell"]})
    assert r.status_code == 200
    new_id = r.get_json()["id"]

    rows = c.get("/api/scout/alerts").get_json()["searches"]
    row = next(s for s in rows if s["id"] == new_id)
    assert row["filters"] == {"near_home": True}
    assert "filters_json" not in row

    assert _post(c, f"/api/scout/alerts/{new_id}",
                 {"enabled": False}).get_json()["ok"] is True
    assert _post(c, f"/api/scout/alerts/{new_id}", None,
                 method="DELETE").get_json()["ok"] is True


def test_a_saved_search_needs_a_name():
    assert _post(_client(), "/api/scout/alerts", {"name": "  "}).status_code == 400
