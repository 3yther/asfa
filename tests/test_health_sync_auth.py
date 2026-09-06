"""Auth for the health-sync endpoint.

The iOS Shortcut has no session and no CSRF token, so POST /api/health/sync is
the first write in the app reachable with a bearer token. That is a hole if it
is even slightly wider than intended, so these tests pin the edges:

  * a 'health_sync' key can POST to /api/health/sync and NOTHING else;
  * a plain 'read' key cannot POST at all;
  * a revoked, absent or malformed token is refused;
  * the CSRF exemption applies only to key-authenticated requests — a browser
    session still needs its token.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_health_auth_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


def setup_module(module=None):
    db._ensure_health_metrics_table()
    db._ensure_api_keys_table()


def _app():
    import app as app_module
    return app_module


def _anon():
    """A client with no session at all — what the Shortcut looks like."""
    return _app().app.test_client()


def _authed():
    c = _app().app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


def _mint(scope="health_sync", name="Watch"):
    """Issue a key through the real endpoint, so scope validation is covered."""
    r = _authed().post("/api/keys/generate", json={"name": name, "scope": scope},
                       headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["key"]


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


_DAY = "2026-09-05"


# ── The intended path ───────────────────────────────────────────────────────

def test_1_health_sync_key_can_post_without_session_or_csrf():
    key = _mint()
    r = _anon().post("/api/health/sync",
                     json={"metric_date": _DAY, "hrv_ms": 41.0, "steps": 5000},
                     headers=_bearer(key))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["success"] is True
    assert db.get_health_metrics_for_date(_DAY)["hrv_ms"] == 41.0


def test_2_health_sync_key_can_read_back_what_it_wrote():
    key = _mint()
    c = _anon()
    c.post("/api/health/sync", json={"metric_date": _DAY, "steps": 77},
           headers=_bearer(key))
    r = c.get("/api/health/metrics/latest", headers=_bearer(key))
    assert r.status_code == 200
    assert r.get_json()["steps"] == 77


def test_3_using_a_key_records_last_used():
    key = _mint(name="Touch check")
    _anon().post("/api/health/sync", json={"metric_date": _DAY, "steps": 1},
                 headers=_bearer(key))
    row = next(k for k in db.list_api_keys() if k["name"] == "Touch check")
    assert row["last_used_at"], "last_used_at should be stamped on use"


# ── The edges that must stay shut ───────────────────────────────────────────

def test_4_read_scope_key_cannot_post():
    key = _mint(scope="read", name="Read only")
    r = _anon().post("/api/health/sync", json={"metric_date": _DAY, "steps": 1},
                     headers=_bearer(key))
    assert r.status_code == 401


def test_5_health_sync_key_cannot_post_anywhere_else():
    """The scope unlocks one endpoint, not 'writes' in general."""
    key = _mint()
    c = _anon()
    # Every path here must genuinely exist as a POST route, or the test proves
    # nothing: a 404 from a misspelled URL looks exactly like a refusal.
    targets = [
        ("/api/keys/generate", {"name": "escalate"}),
        ("/api/sleep/log", {"date": _DAY, "duration": 8, "quality": 5}),
        ("/api/steps/log", {"date": _DAY, "steps": 10000}),
        ("/api/steps/goal", {"steps_goal": 1}),
        ("/api/gym/sessions/start", {"routine_id": 1}),
    ]
    routes = {str(r.rule): r.methods for r in _app().app.url_map.iter_rules()}
    for path, payload in targets:
        assert "POST" in routes.get(path, set()), \
            f"{path} is not a POST route — this assertion would be vacuous"
        r = c.post(path, json=payload, headers=_bearer(key))
        assert r.status_code == 401, \
            f"health_sync key got {r.status_code} at {path}; expected 401"


def test_6_no_token_absent_and_malformed_headers_are_refused():
    c = _anon()
    body = {"metric_date": _DAY, "steps": 1}
    assert c.post("/api/health/sync", json=body).status_code == 401
    for hdr in ({"Authorization": "Bearer "}, {"Authorization": "asfa_nope"},
                {"Authorization": "Bearer not-a-real-key"},
                {"Authorization": "Basic YWJjOjEyMw=="}):
        assert c.post("/api/health/sync", json=body, headers=hdr).status_code == 401


def test_7_revoked_key_stops_working():
    key = _mint(name="To revoke")
    c = _anon()
    assert c.post("/api/health/sync", json={"metric_date": _DAY, "steps": 2},
                  headers=_bearer(key)).status_code == 200
    kid = next(k["id"] for k in db.list_api_keys() if k["name"] == "To revoke")
    db.revoke_api_key(kid)
    assert c.post("/api/health/sync", json={"metric_date": _DAY, "steps": 3},
                  headers=_bearer(key)).status_code == 401


def test_8_unknown_scope_is_rejected_at_mint_time():
    r = _authed().post("/api/keys/generate", json={"name": "x", "scope": "admin"},
                       headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400


def test_9_generate_still_defaults_to_read():
    r = _authed().post("/api/keys/generate", json={"name": "legacy"},
                       headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 201 and r.get_json()["scope"] == "read"


# ── CSRF exemption is scoped to key auth only ───────────────────────────────

def test_10_browser_session_still_needs_its_csrf_token():
    c = _app().app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    r = c.post("/api/health/sync", json={"metric_date": _DAY, "steps": 1})
    assert r.status_code == 403, "the API-key exemption must not disarm CSRF for cookies"


def test_11_session_with_token_works_as_before():
    c = _authed()
    r = c.post("/api/health/sync", json={"metric_date": _DAY, "steps": 9},
               headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200


if __name__ == "__main__":
    setup_module()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
