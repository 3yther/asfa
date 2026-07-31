"""Guest / demo mode — the public portfolio and the read-only dashboard tour.

The invariant under test is simple to state and easy to break: a guest session
must reach every *screen* in the demo and no *row* in the database. So the
assertions come in three groups —

  1. entry + navigation (guest login, allowed pages, blocked pages),
  2. payload isolation (each demo endpoint answers empty for a guest and real
     for a logged-in user),
  3. shape parity — the guest stand-in has to look like the real response, or
     the frontend renders an error instead of an empty chart. That one is
     checked structurally against the live endpoints rather than by eyeballing
     _GUEST_EMPTY_SHAPES, so the map cannot silently drift from the API.

Database isolation and APP_PASSWORD come from the root conftest.
"""
import app as A
import database as db
import pytest


@pytest.fixture
def guest():
    c = A.app.test_client()
    r = c.post("/login/guest")
    assert r.status_code == 302
    return c


@pytest.fixture
def user():
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "test-token"
    return c


# ── 1. Entry + navigation ────────────────────────────────────────────────────

def test_login_page_offers_guest_entry():
    r = A.app.test_client().get("/login")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Login as Guest" in body
    assert 'action="/login/guest"' in body


def test_guest_login_creates_a_guest_not_an_authed_session():
    c = A.app.test_client()
    r = c.post("/login/guest")
    assert r.headers["Location"] in ("/", "http://localhost/")
    with c.session_transaction() as s:
        assert s.get("is_guest") is True
        assert not s.get("authed")          # never the real thing
        assert s.get("csrf_token")


def test_guest_lands_on_the_demo_dashboard(guest):
    r = guest.get("/")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "DEMO MODE" in body              # the watermark from nav.html
    assert "PORTFOLIO" in body


@pytest.mark.parametrize("path", ["/", "/gym", "/gym/plan", "/nutrition", "/portfolio"])
def test_guest_can_open_the_demo_screens(guest, path):
    assert guest.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/mission-control", "/agents", "/system",
                                  "/approvals", "/fragrances", "/interview/"])
def test_guest_is_bounced_off_non_demo_screens(guest, path):
    r = guest.get(path)
    assert r.status_code == 302
    assert "/portfolio" in r.headers["Location"]


def test_anonymous_visitor_still_hits_the_login_wall():
    c = A.app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/gym/sessions").status_code == 401


def test_guest_can_log_out(guest):
    r = guest.post("/logout", headers={"X-CSRF-Token": _token(guest)})
    assert r.status_code == 302
    with guest.session_transaction() as s:
        assert not s.get("is_guest")


def _token(client):
    with client.session_transaction() as s:
        return s["csrf_token"]


# ── 2. Payload isolation ─────────────────────────────────────────────────────

def test_portfolio_is_public_and_carries_the_cv():
    r = A.app.test_client().get("/portfolio")           # no session at all
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    for expected in ("AMIR SALAH", "Cloud Security Engineer", "Leigh UTC Dartford",
                     "Worldpay", "TK Maxx", "Financial Times", "NQ ORB Bot",
                     "ami.salax08@gmail.com"):
        assert expected in body


def test_cv_download_404s_until_the_pdf_is_added():
    if not __import__("os").path.exists(A.CV_PATH):
        assert A.app.test_client().get("/cv").status_code == 404


def test_guest_gym_history_is_empty_while_the_user_sees_real_sets(guest, user):
    session_id = db.create_session(None, db.today_str(), "18:00")
    ex = db.get_all_exercises()[0]
    db.log_set(session_id, ex["id"], 1, "working", 60.0, 8)

    assert guest.get("/api/gym/sessions").get_json() == []
    assert guest.get(f"/api/gym/history/{ex['id']}").get_json() == []
    assert len(user.get("/api/gym/sessions").get_json()) == 1
    assert len(user.get(f"/api/gym/history/{ex['id']}").get_json()) == 1


def test_guest_nutrition_is_zeroed_while_the_user_sees_the_meal(guest, user):
    db.log_meal(db.today_str(), "Chicken and rice", 55.0, 90.0, 12.0)

    demo = guest.get("/api/nutrition/today").get_json()
    assert demo["meals"] == []
    assert demo["total_calories"] == 0.0
    assert demo["total_protein"] == 0.0

    real = user.get("/api/nutrition/today").get_json()
    assert real["meal_count"] == 1
    assert real["total_protein"] == 55.0


def test_guest_steps_show_an_axis_but_no_series(guest, user):
    db.add_step_entry(db.today_str(), "manual", 8412)

    day = guest.get("/api/steps/date/today").get_json()
    assert day["total"] == 0 and day["entries"] == []
    week = guest.get("/api/steps/week").get_json()
    assert len(week["days"]) == 7                       # axis renders…
    assert {d["total"] for d in week["days"]} == {0}    # …with a flat, empty line

    assert user.get("/api/steps/date/today").get_json()["total"] == 8412


def test_guest_sleep_and_cardio_are_empty(guest, user):
    db.log_sleep_entry(db.today_str(), 7.5, 4)
    db.log_cardio_session(db.today_str(), "cycling", distance_miles=7.9,
                          duration_minutes=46)

    assert guest.get("/api/sleep/history").get_json() == []
    assert guest.get("/api/sleep/readiness").get_json()["readiness"] is None
    assert guest.get("/api/gym/cardio").get_json() == []

    assert len(user.get("/api/sleep/history").get_json()) == 1
    assert user.get("/api/sleep/readiness").get_json()["readiness"] is not None
    assert len(user.get("/api/gym/cardio").get_json()) == 1


def test_unmapped_api_endpoints_fall_back_to_an_empty_object(guest):
    # /api/briefing would otherwise call the Anthropic API — the gate answers
    # before dispatch, so a guest can neither read the briefing nor spend money.
    assert guest.get("/api/briefing").get_json() == {}


def test_guest_writes_are_refused(guest):
    # The module shares one database across tests, so compare against the total
    # standing before the write rather than against zero.
    before = db.get_steps_day_total(db.today_str())
    r = guest.post("/api/steps/log", json={"date": db.today_str(), "steps": 5000},
                   headers={"X-CSRF-Token": _token(guest)})
    assert r.status_code == 403
    assert r.get_json()["error"] == "guest session is read-only"
    assert db.get_steps_day_total(db.today_str()) == before


def test_guest_cannot_delete_or_export(guest):
    token = {"X-CSRF-Token": _token(guest)}
    assert guest.post("/api/export/all-data", headers=token).status_code == 403
    assert guest.delete("/api/gym/sessions/1", headers=token).status_code == 403


# ── 3. Shape parity ──────────────────────────────────────────────────────────
# Every guest stand-in is compared with what the endpoint actually returns, so
# the frontend gets the fields it destructures. Values differ (that is the
# point); the JSON type must match, and a guest object may only carry keys the
# real object also has — never an invented one.

_PARITY_URLS = {
    "api_gym": "/api/gym",
    "api_gym_photos": "/api/gym/photos",
    "api_gym_streak": "/api/gym/streak",
    "api_gym_xp": "/api/gym/xp",
    "api_gym_deload_check": "/api/gym/deload-check",
    "api_gym_plan": "/api/gym/plan",
    "api_gym_sessions": "/api/gym/sessions",
    "api_gym_cardio_list": "/api/gym/cardio",
    "api_gym_prs": "/api/gym/prs",
    "api_gym_body_stats": "/api/gym/body-stats",
    "api_gym_rest_days": "/api/gym/rest-days",
    "api_gym_weekly_volume": "/api/gym/volume/weekly",
    "api_gym_muscle_recovery": "/api/gym/muscle-recovery",
    "api_gym_ranks": "/api/gym/ranks",
    "api_gym_routines": "/api/gym/routines",
    "api_steps_date": "/api/steps/date/today",
    "api_steps_week": "/api/steps/week",
    "api_steps_goal_get": "/api/steps/goal",
    "api_sleep_readiness": "/api/sleep/readiness",
    "api_sleep_history": "/api/sleep/history",
    "api_nutrition_today": "/api/nutrition/today",
    "api_nutrition_history": "/api/nutrition/history",
    "api_nutrition_goals_get": "/api/nutrition/goals",
    "api_nutrition_trends": "/api/nutrition/trends",
    "api_nutrition_score": "/api/nutrition/score",
    "api_nutrition_favorites": "/api/nutrition/favorites",
    "api_nutrition_templates": "/api/nutrition/templates",
    "api_meal_prep_list": "/api/nutrition/meal-prep/list",
    "api_nutrition_insights": "/api/nutrition/insights",
    "api_habits": "/api/habits",
    "api_body_composition": "/api/body-composition",
    "api_supplements": "/api/supplements",
    "api_notifications": "/api/notifications",
    "api_score": "/api/score",
    "api_goals": "/api/goals",
    "api_finance_summary": "/api/finance/summary",
    "api_finance_pace": "/api/finance/pace",
    "api_finance_recent": "/api/finance/recent",
    "api_finance_accounts_summary": "/api/finance/accounts/summary",
}


@pytest.mark.parametrize("endpoint,url", sorted(_PARITY_URLS.items()))
def test_guest_payload_matches_the_real_shape(guest, user, endpoint, url):
    real = user.get(url)
    assert real.status_code == 200, url
    real_json = real.get_json()
    demo_json = guest.get(url).get_json()

    assert type(demo_json) is type(real_json), f"{url}: {type(demo_json)} vs {type(real_json)}"
    if isinstance(real_json, dict):
        unknown = set(demo_json) - set(real_json)
        assert not unknown, f"{url}: guest invents keys {unknown}"
        assert demo_json, f"{url}: guest payload is bare {{}} but the endpoint returns fields"


def test_every_mapped_endpoint_is_covered_by_parity():
    """A new entry in either guest map has to bring a parity URL with it."""
    mapped = set(A._GUEST_EMPTY_SHAPES) | set(A._GUEST_EMPTY_LISTS)
    # Endpoints the demo screens never call are exempt from needing a URL here.
    exempt = {"api_gym_session_sets", "api_gym_history", "api_gym_exercises",
              "api_gym_sessions_calendar", "api_nutrition_date",
              "api_nutrition_yesterday", "api_nutrition_previous_foods",
              "api_nutrition_frequent_at_hour", "api_reflection",
              "api_missions_today", "api_conversation", "api_audit",
              "api_agents_energy", "api_fragrances", "api_scout_jobs",
              "api_scout_pipeline_reminders"}
    assert mapped - exempt - set(_PARITY_URLS) == set()
