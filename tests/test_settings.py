"""Settings Phase 1+2 — notifications/alerts prefs, active sessions, data export,
password change.

DB isolation + schema init come from the root conftest.py (module-scoped autouse
fixture); this module just drives the Flask client. Writes carry the X-CSRF-Token
header the global CSRF gate expects.

    python -m pytest tests/test_settings.py -v
"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as app_module   # noqa: E402
import database as db       # noqa: E402

CSRF = "test-csrf-token"


@pytest.fixture()
def client():
    """A logged-in client with a known CSRF token. No server-side session row is
    forged: the auth gate lazily registers one on the first request (the
    backward-compat path for sessions that predate the registry)."""
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = CSRF
    return c


def _authed_client(token, ip=None, ua=None):
    """A logged-in client whose cookie sid matches a real user_sessions row, so
    the server-side session validation passes and is_current can be exercised."""
    db.create_user_session(token, db.DEFAULT_USER_ID, ip_address=ip, user_agent=ua)
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = CSRF
        s["sid"] = token
    return c


def _h():
    return {"X-CSRF-Token": CSRF}


# ── Phase 1: Notifications & Alerts ─────────────────────────────────────────────

def test_notifications_defaults():
    prefs = db.get_notification_prefs()
    assert prefs["email_frequency"] == "weekly"
    assert prefs["quiet_hours_start"] == "22:00"
    assert prefs["quiet_hours_end"] == "08:00"
    assert prefs["telegram_notifications"] is True
    assert prefs["alert_low_steps"] is True


def test_notifications_get(client):
    r = client.get("/api/settings/notifications")
    assert r.status_code == 200
    p = r.get_json()["preferences"]
    assert p["email_frequency"] == "weekly"
    assert p["alert_water_intake"] is True


def test_notifications_persist_toggles(client):
    payload = {
        "email_frequency": "daily",
        "telegram_notifications": False,
        "job_alerts_enabled": False,
        "alert_low_steps": False,
        "alert_missed_meal": True,
        "quiet_hours_start": "23:30",
        "quiet_hours_end": "07:15",
    }
    r = client.post("/api/settings/notifications", json=payload, headers=_h())
    assert r.status_code == 200
    p = r.get_json()["preferences"]
    assert p["email_frequency"] == "daily"
    assert p["telegram_notifications"] is False
    assert p["job_alerts_enabled"] is False
    assert p["alert_low_steps"] is False
    assert p["alert_missed_meal"] is True
    assert p["quiet_hours_start"] == "23:30"
    # Persisted across a fresh read
    p2 = db.get_notification_prefs()
    assert p2["telegram_notifications"] is False
    assert p2["quiet_hours_end"] == "07:15"


def test_notifications_reject_bad_values(client):
    # Bad enum + bad time are ignored, leaving the prior values intact.
    db.update_notification_prefs(email_frequency="weekly", quiet_hours_start="22:00")
    r = client.post("/api/settings/notifications",
                    json={"email_frequency": "hourly", "quiet_hours_start": "99:99"},
                    headers=_h())
    p = r.get_json()["preferences"]
    assert p["email_frequency"] == "weekly"        # unchanged
    assert p["quiet_hours_start"] == "22:00"        # unchanged


def test_notifications_requires_csrf(client):
    r = client.post("/api/settings/notifications", json={"email_frequency": "never"})
    assert r.status_code == 403


def test_quiet_hours_wraps_midnight():
    from datetime import datetime
    prefs = {"quiet_hours_enabled": True, "quiet_hours_start": "22:00",
             "quiet_hours_end": "08:00"}
    inside = datetime(2026, 1, 1, 23, 30)   # 11:30pm
    early = datetime(2026, 1, 1, 3, 0)      # 3am
    outside = datetime(2026, 1, 1, 12, 0)   # noon
    assert db.is_within_quiet_hours(prefs, now=inside) is True
    assert db.is_within_quiet_hours(prefs, now=early) is True
    assert db.is_within_quiet_hours(prefs, now=outside) is False


def test_quiet_hours_disabled():
    from datetime import datetime
    prefs = {"quiet_hours_enabled": False, "quiet_hours_start": "22:00",
             "quiet_hours_end": "08:00"}
    assert db.is_within_quiet_hours(prefs, now=datetime(2026, 1, 1, 23, 30)) is False


def test_alert_enabled_respects_toggle_and_quiet_hours():
    from datetime import datetime
    import database
    db.update_notification_prefs(alert_water_intake=True, quiet_hours_enabled=True,
                                 quiet_hours_start="22:00", quiet_hours_end="08:00")
    # Toggle off → disabled regardless of time
    db.update_notification_prefs(alert_water_intake=False)
    assert db.alert_enabled("alert_water_intake") is False
    db.update_notification_prefs(alert_water_intake=True)
    # Unknown key → False
    assert db.alert_enabled("alert_nonexistent") is False


# ── Phase 2: Active Sessions ────────────────────────────────────────────────────

def test_active_sessions_lists_current():
    c = _authed_client("sid-list-current", ip="10.0.0.9", ua="pytest-UA")
    r = c.get("/api/settings/active-sessions")
    assert r.status_code == 200
    sessions = r.get_json()["sessions"]
    current = [s for s in sessions if s["is_current"]]
    assert len(current) == 1
    assert current[0]["ip"] == "10.0.0.9"
    assert current[0]["user_agent"] == "pytest-UA"


def test_revoke_other_session():
    c = _authed_client("sid-revoke-owner", ip="1.1.1.1")
    db.create_user_session("sid-revoke-target", db.DEFAULT_USER_ID, ip_address="2.2.2.2")
    other = [s for s in db.list_user_sessions()
             if s["session_token"] == "sid-revoke-target"][0]
    r = c.delete(f"/api/settings/active-sessions/{other['id']}", headers=_h())
    assert r.status_code == 200
    assert r.get_json()["is_current"] is False
    tokens = [s["session_token"] for s in db.list_user_sessions()]
    assert "sid-revoke-target" not in tokens
    assert "sid-revoke-owner" in tokens


def test_revoke_missing_session(client):
    r = client.delete("/api/settings/active-sessions/999999", headers=_h())
    assert r.status_code == 404


def test_revoke_current_session_signs_out():
    c = _authed_client("sid-self-revoke", ip="3.3.3.3")
    row = [s for s in db.list_user_sessions()
           if s["session_token"] == "sid-self-revoke"][0]
    r = c.delete(f"/api/settings/active-sessions/{row['id']}", headers=_h())
    assert r.status_code == 200
    assert r.get_json()["is_current"] is True
    # Session cookie cleared → next request is unauthorized.
    r2 = c.get("/api/settings/active-sessions")
    assert r2.status_code == 401


def test_remote_logout_invalidates_session():
    """A session whose row is deleted stops authenticating on its next request."""
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = CSRF
        s["sid"] = "live-token"
    db.create_user_session("live-token", db.DEFAULT_USER_ID, ip_address="4.4.4.4")
    assert c.get("/api/settings/notifications").status_code == 200
    db.delete_session_by_token("live-token")
    assert c.get("/api/settings/notifications").status_code == 401


# ── Phase 2: Data Export ────────────────────────────────────────────────────────

def _seed_export_data():
    db.log_meal("2026-08-01", "Chicken & rice", 40, 60, 10, calories=520, time="12:00")
    db.add_step_entry("2026-08-01", "manual", 8000, {"steps": 8000})


def test_export_json(client):
    _seed_export_data()
    r = client.get("/api/settings/export-data?format=json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    payload = json.loads(r.get_data(as_text=True))
    assert "data" in payload
    for key in ("gym_sessions", "gym_sets", "nutrition_meals", "steps", "sleep", "cardio"):
        assert key in payload["data"]
    meals = payload["data"]["nutrition_meals"]
    assert any(m["food_name"] == "Chicken & rice" for m in meals)


def test_export_csv_zip(client):
    _seed_export_data()
    r = client.get("/api/settings/export-data?format=csv")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.get_data()))
    names = set(zf.namelist())
    assert {"nutrition_meals.csv", "steps.csv", "gym_sessions.csv",
            "sleep.csv", "cardio.csv"}.issubset(names)
    meals_csv = zf.read("nutrition_meals.csv").decode()
    assert "food_name" in meals_csv.splitlines()[0]          # header present
    assert "Chicken & rice" in meals_csv


# ── Phase 2: Password change ────────────────────────────────────────────────────

def _reset_password_override():
    try:
        db.kv_set(app_module._PASSWORD_HASH_KEY, "")
    except Exception:
        pass


def test_change_password_success(client):
    _reset_password_override()
    r = client.post("/api/settings/change-password",
                    json={"old_password": os.environ["APP_PASSWORD"],
                          "new_password": "brand-new-pass-1",
                          "confirm_password": "brand-new-pass-1"},
                    headers=_h())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["success"] is True
    # The new passphrase now verifies; the old env one no longer does.
    assert app_module._verify_password("brand-new-pass-1") is True
    assert app_module._verify_password(os.environ["APP_PASSWORD"]) is False
    _reset_password_override()   # don't leak the override to later tests


def test_change_password_wrong_old(client):
    _reset_password_override()
    r = client.post("/api/settings/change-password",
                    json={"old_password": "definitely-wrong",
                          "new_password": "another-good-pass",
                          "confirm_password": "another-good-pass"},
                    headers=_h())
    assert r.status_code == 403
    assert "Incorrect" in r.get_json()["error"]


def test_change_password_mismatch(client):
    _reset_password_override()
    r = client.post("/api/settings/change-password",
                    json={"old_password": os.environ["APP_PASSWORD"],
                          "new_password": "good-pass-here",
                          "confirm_password": "different-pass"},
                    headers=_h())
    assert r.status_code == 400
    assert "match" in r.get_json()["error"].lower()


def test_change_password_too_short(client):
    _reset_password_override()
    r = client.post("/api/settings/change-password",
                    json={"old_password": os.environ["APP_PASSWORD"],
                          "new_password": "short",
                          "confirm_password": "short"},
                    headers=_h())
    assert r.status_code == 400
    assert "short" in r.get_json()["error"].lower()


def test_change_password_login_flow():
    """End-to-end: change the passphrase, then the login route accepts the new one
    and rejects the old."""
    _reset_password_override()
    c = _authed_client("pw-flow-sid")
    r = c.post("/api/settings/change-password",
               json={"old_password": os.environ["APP_PASSWORD"],
                     "new_password": "login-flow-pass-9",
                     "confirm_password": "login-flow-pass-9"},
               headers=_h())
    assert r.status_code == 200
    # New passphrase logs in.
    fresh = app_module.app.test_client()
    ok = fresh.post("/login", data={"password": "login-flow-pass-9"})
    assert ok.status_code in (301, 302, 303)
    # Old passphrase rejected.
    bad = app_module.app.test_client()
    no = bad.post("/login", data={"password": os.environ["APP_PASSWORD"]})
    assert no.status_code == 401
    _reset_password_override()
