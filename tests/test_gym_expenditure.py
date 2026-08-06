"""Caloric expenditure — back-calculated daily burn, 7-day trend, goal ETA.

The interesting cases are all about *refusing* to produce a number: an unlogged
day is not a zero-calorie day, a gap between weigh-ins must not dump its whole
delta onto one date, and a 5 kg overnight water swing is not metabolism.
"""
import datetime as dt

import pytest

import database as db
from app import app


def _day(offset=0):
    """`offset` days before today, in the app timezone."""
    return dt.date.fromisoformat(db.today_str()) - dt.timedelta(days=offset)


def _log(day, kcal=2200.0):
    db.log_meal(day.isoformat(), "test food", 150, 200, 60, calories=kcal)


def _weigh(day, kg):
    db.upsert_body_composition(day.isoformat(), {"weight_kg": kg})


@pytest.fixture(autouse=True)
def _clean():
    """Each test starts from an empty meals/weight state."""
    db._ensure_meals_table()
    db._ensure_body_tables()
    db._ensure_gym_tables()
    with db.get_db() as conn:
        cur = conn.cursor()
        for t in ("meals", "body_composition", "gym_body_stats"):
            cur.execute(f"DELETE FROM {t}")
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["authed"] = True
        yield c


# ── The core formula ─────────────────────────────────────────────────────────

def test_expenditure_is_intake_plus_energy_in_lost_tissue():
    _weigh(_day(1), 80.5)
    _weigh(_day(0), 80.4)          # 0.1 kg down
    _log(_day(0), 2000)
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["weight_change_kg"] == pytest.approx(0.1, abs=1e-6)
    # 2000 + 0.1 kg x 7700 kcal/kg
    assert r["expenditure"] == pytest.approx(2770.0, abs=0.5)
    assert r["reason"] is None


def test_weight_gain_lowers_the_burn_below_intake():
    _weigh(_day(1), 80.0)
    _weigh(_day(0), 80.1)          # gained
    _log(_day(0), 3000)
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["weight_change_kg"] < 0
    assert r["expenditure"] == pytest.approx(3000 - 770, abs=0.5)


def test_kcal_per_kg_constant_is_the_kg_conversion_not_the_pound_figure():
    # 3500 kcal is per pound; using it against a kg delta under-counts ~2.2x.
    assert db.KCAL_PER_KG_FAT == pytest.approx(3500 * 2.2046, rel=0.01)


# ── Edge cases: when NOT to produce a number ─────────────────────────────────

def test_unlogged_day_is_not_treated_as_zero_calories():
    _weigh(_day(1), 80.5)
    _weigh(_day(0), 80.4)          # weight present, no food logged
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["expenditure"] is None
    assert r["reason"] == "no-intake"


def test_no_weight_data_yields_no_expenditure():
    _log(_day(0), 2200)
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["expenditure"] is None
    assert r["reason"] == "no-weight"


def test_single_weigh_in_cannot_produce_a_delta():
    _weigh(_day(0), 80.0)
    _log(_day(0), 2200)
    assert db.get_daily_expenditure(_day(0).isoformat())["reason"] == "no-weight"


def test_implausible_overnight_swing_is_dropped_not_clamped():
    _weigh(_day(1), 85.0)
    _weigh(_day(0), 80.0)          # 5 kg of water, not 38,500 kcal burned
    _log(_day(0), 2200)
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["expenditure"] is None
    assert r["reason"] == "implausible"


def test_bad_date_string_is_handled():
    assert db.get_daily_expenditure("not-a-date")["reason"] == "bad-date"


def test_empty_database_returns_a_well_formed_shape():
    r = db.get_daily_expenditure()
    assert r["expenditure"] is None and r["calories_logged"] == 0.0
    t = db.get_expenditure_trend()
    assert t["average"] is None and t["valid_days"] == 0 and len(t["trend"]) == 7


# ── Gaps: weekends and weekly weigh-ins ──────────────────────────────────────

def test_weight_gap_is_spread_across_its_days_not_dumped_on_one():
    """Weigh in Monday and Friday only; the 0.4 kg is shared by all four days."""
    _weigh(_day(7), 80.0)
    _weigh(_day(3), 79.6)
    for i in range(3, 8):
        _log(_day(i), 2000)
    per_day = [db.get_daily_expenditure(_day(i).isoformat()) for i in (6, 5, 4, 3)]
    assert all(d["expenditure"] is not None for d in per_day), \
        "every day inside the gap should be computable"
    for d in per_day:
        assert d["weight_change_kg"] == pytest.approx(0.1, abs=1e-3)
        assert d["expenditure"] == pytest.approx(2770.0, abs=1.0)


def test_days_outside_the_scan_range_are_not_extrapolated():
    _weigh(_day(5), 80.0)
    _weigh(_day(3), 79.8)
    _log(_day(0), 2000)            # today is past the last weigh-in
    assert db.get_daily_expenditure(_day(0).isoformat())["reason"] == "no-weight"


def test_manual_gym_weights_count_and_scans_win_on_a_shared_day():
    db.log_body_stat(_day(1).isoformat(), 80.5)
    db.log_body_stat(_day(0).isoformat(), 79.0)   # manual, should be overridden
    _weigh(_day(0), 80.4)
    _log(_day(0), 2000)
    r = db.get_daily_expenditure(_day(0).isoformat())
    assert r["weight_kg"] == pytest.approx(80.4)
    assert r["expenditure"] == pytest.approx(2770.0, abs=0.5)


# ── Trend ────────────────────────────────────────────────────────────────────

def _seed_linear_month():
    """21 days of steady logging and a losing trend, with one unlogged day."""
    for i in range(21):
        if i == 3:
            continue
        _log(_day(i), 2000)
    for i in (0, 4, 11, 18, 20):
        _weigh(_day(i), 80.0 + i * 0.09)


def test_trend_returns_the_requested_window_with_rolling_averages():
    _seed_linear_month()
    t = db.get_expenditure_trend(7)
    assert len(t["trend"]) == 7
    assert t["trend"][-1]["date"] == _day(0).isoformat()
    assert all("rolling_avg" in d for d in t["trend"])
    assert t["average"] == pytest.approx(2693.0, abs=5.0)   # 2000 + 0.09x7700


def test_trend_keeps_gap_days_visible_but_out_of_the_average():
    _seed_linear_month()
    t = db.get_expenditure_trend(7)
    gap = [d for d in t["trend"] if d["date"] == _day(3).isoformat()]
    assert gap and gap[0]["expenditure"] is None and gap[0]["reason"] == "no-intake"
    assert t["valid_days"] == 6
    # A zero-counted gap day would drag the mean well below the daily figure.
    assert t["average"] > 2500


def test_rolling_average_uses_history_before_the_window():
    """The first displayed day averages over the preceding week, not just itself.

    Intake steps up sharply on the last 7 days, so if the earliest displayed day
    were averaging over the window alone its average would equal its own value.
    """
    for i in range(21):
        _log(_day(i), 2000 if i < 7 else 3000)
    for i in (0, 7, 14, 20):
        _weigh(_day(i), 80.0 + i * 0.05)
    t = db.get_expenditure_trend(7)
    first = t["trend"][0]
    assert first["expenditure"] is not None and first["rolling_avg"] is not None
    # Trailing week is the 3000-kcal era, so the average sits above today's day.
    assert first["rolling_avg"] > first["expenditure"]


def test_trend_days_are_clamped():
    assert db.get_expenditure_trend(999)["days"] == 90
    assert db.get_expenditure_trend(0)["days"] == 1
    assert db.get_expenditure_trend("junk")["days"] == 7


# ── Goal ETA and pace ────────────────────────────────────────────────────────

def test_eta_projects_the_target_from_the_regression_slope():
    for i in range(28):
        _weigh(_day(i), 80.0 + i * (0.5 / 7))     # losing 0.5 kg/week
    g = db.get_goal_eta(75.0)
    assert g["current_kg"] == pytest.approx(80.0, abs=0.05)
    assert g["pace_kg_week"] == pytest.approx(-0.5, abs=0.05)
    assert g["pace_status"] == "on-track"
    assert g["goal_eta_days"] == pytest.approx(70, abs=2)   # 5 kg / 0.5 per week
    assert g["goal_eta_date"] == (
        _day(0) + dt.timedelta(days=g["goal_eta_days"])).isoformat()


def test_pace_flags_a_crash_diet():
    for i in range(28):
        _weigh(_day(i), 80.0 + i * (2.0 / 7))     # 2 kg/week
    assert db.get_goal_eta(75.0)["pace_status"] == "too-fast"


def test_pace_flags_a_plateau():
    for i in range(28):
        _weigh(_day(i), 80.0)
    g = db.get_goal_eta(75.0)
    assert g["pace_status"] == "plateau"
    assert g["goal_eta_days"] is None             # flat never reaches the goal


def test_gaining_weight_has_no_eta():
    for i in range(28):
        _weigh(_day(i), 80.0 - i * 0.05)          # trending up toward today
    g = db.get_goal_eta(75.0)
    assert g["pace_kg_week"] > 0
    assert g["goal_eta_days"] is None


def test_target_already_reached():
    _weigh(_day(7), 76.0)
    _weigh(_day(0), 74.5)
    g = db.get_goal_eta(75.0)
    assert g["goal_eta_days"] == 0
    assert g["goal_eta_date"] == _day(0).isoformat()


def test_eta_without_weight_data_explains_itself():
    g = db.get_goal_eta(75.0)
    assert g["goal_eta_days"] is None and g["current_kg"] is None
    assert g["note"]


def test_eta_with_one_weigh_in_explains_itself():
    _weigh(_day(0), 80.0)
    g = db.get_goal_eta(75.0)
    assert g["current_kg"] == 80.0
    assert g["pace_kg_week"] is None and g["note"]


# ── Endpoint ─────────────────────────────────────────────────────────────────

def test_endpoint_returns_the_documented_shape(client):
    _seed_linear_month()
    r = client.get("/api/gym/expenditure-trend")
    assert r.status_code == 200
    body = r.get_json()
    for key in ("daily_burn", "trend", "goal_eta_date", "goal_eta_days",
                "pace_kg_week", "pace_status"):
        assert key in body
    assert isinstance(body["trend"], list) and len(body["trend"]) == 7
    assert body["pace_status"] in ("on-track", "too-fast", "plateau")
    assert isinstance(body["daily_burn"], float)


def test_endpoint_survives_an_empty_database(client):
    body = client.get("/api/gym/expenditure-trend").get_json()
    assert body["daily_burn"] is None
    assert body["goal_eta_days"] is None
    assert len(body["trend"]) == 7


def test_endpoint_rejects_a_nonsense_target(client):
    assert client.get("/api/gym/expenditure-trend?target_kg=5").status_code == 400
    assert client.get("/api/gym/expenditure-trend?target_kg=900").status_code == 400


def test_endpoint_honours_days_and_target(client):
    _seed_linear_month()
    body = client.get("/api/gym/expenditure-trend?days=14&target_kg=70").get_json()
    assert len(body["trend"]) == 14
    assert body["target_kg"] == 70.0


def test_endpoint_is_auth_gated():
    app.config["TESTING"] = True
    with app.test_client() as anon:
        r = anon.get("/api/gym/expenditure-trend")
    assert r.status_code in (302, 401)
