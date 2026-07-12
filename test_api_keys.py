"""Tests for the read-only API-key system (feat/api-keys).

Run: .venv/bin/pytest test_api_keys.py -v

Covers: key generation (returned once), stateless auth against the curated read
allowlist, rejection of invalid/missing/revoked keys, and the guarantees that a
key can NOT reach write endpoints, non-allowlisted reads, or the key-management
routes themselves.
"""
import os
import tempfile

import pytest

# Configure the environment BEFORE importing the app: skip the background
# scheduler/bot, use a throwaway SQLite file, and set a known passphrase.
os.environ["ASFA_BG_STARTED"] = "1"
os.environ.pop("DATABASE_URL", None)  # force SQLite
os.environ["APP_PASSWORD"] = "test-pass-123"
os.environ["SECRET_KEY"] = "test-secret-key"
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["ASFA_DB_PATH"] = _tmp.name

import app as asfa_app  # noqa: E402

asfa_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
# The 5/min login limiter would 429 across our many logins; off for tests.
asfa_app.limiter.enabled = False

PROTECTED_READ = "/api/finance/summary"  # in the API-key allowlist, hermetic (DB only)


@pytest.fixture()
def anon():
    """A client with no session — the MCP server's position."""
    return asfa_app.app.test_client()


@pytest.fixture()
def auth():
    """A logged-in client (session + CSRF token) for the management routes."""
    c = asfa_app.app.test_client()
    r = c.post("/login", data={"password": os.environ["APP_PASSWORD"]})
    assert r.status_code in (301, 302, 303), r.status_code
    with c.session_transaction() as sess:
        c.csrf = sess.get("csrf_token")
    return c


def _csrf(c):
    return {"X-CSRF-Token": getattr(c, "csrf", "")}


def _generate(auth, name="MCP Server"):
    r = auth.post("/api/keys/generate", json={"name": name}, headers=_csrf(auth))
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


# ── Generation ──────────────────────────────────────────────────────────────────

def test_generate_returns_key_once(auth):
    data = _generate(auth)
    assert data["key"].startswith("asfa_")
    assert len(data["key"]) > 40          # asfa_ + ~43 chars of base64url
    assert data["scope"] == "read"
    assert "once" in data["message"].lower()


def test_generate_requires_session(anon):
    # No session, no key: the management route is not publicly reachable.
    r = anon.post("/api/keys/generate", json={"name": "x"})
    assert r.status_code in (401, 403)     # 401 unauth (CSRF check runs after)


# ── Using a key ─────────────────────────────────────────────────────────────────

def test_key_unlocks_read_endpoint(anon, auth):
    key = _generate(auth)["key"]
    r = anon.get(PROTECTED_READ, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert isinstance(r.get_json(), dict)  # real payload, not an error envelope


def test_invalid_key_rejected(anon):
    r = anon.get(PROTECTED_READ, headers={"Authorization": "Bearer not-a-real-key"})
    assert r.status_code == 401


def test_missing_key_rejected(anon):
    r = anon.get(PROTECTED_READ)
    assert r.status_code == 401


def test_malformed_header_rejected(anon):
    r = anon.get(PROTECTED_READ, headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


# ── Revocation ──────────────────────────────────────────────────────────────────

def test_revoked_key_rejected(anon, auth):
    key = _generate(auth, name="to-revoke")["key"]
    # Sanity: it works first.
    assert anon.get(PROTECTED_READ,
                    headers={"Authorization": f"Bearer {key}"}).status_code == 200

    listing = auth.get("/api/keys/list").get_json()
    key_id = next(k["id"] for k in listing if k["name"] == "to-revoke")
    r = auth.post(f"/api/keys/{key_id}/revoke", headers=_csrf(auth))
    assert r.status_code == 200

    # Now it's dead.
    assert anon.get(PROTECTED_READ,
                    headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_revoke_unknown_key_404(auth):
    r = auth.post("/api/keys/999999/revoke", headers=_csrf(auth))
    assert r.status_code == 404


# ── Scope / surface guarantees ──────────────────────────────────────────────────

def test_list_shows_metadata_not_secret(auth):
    _generate(auth, name="listed-key")
    rows = auth.get("/api/keys/list").get_json()
    assert isinstance(rows, list) and rows
    row = rows[0]
    assert "key" not in row and "key_hash" not in row
    assert {"id", "name", "scope", "created_at"} <= set(row)


def test_key_cannot_reach_non_allowlisted_read(anon, auth):
    # /api/finance/recent is a GET but deliberately NOT in the allowlist.
    key = _generate(auth, name="scoped")["key"]
    r = anon.get("/api/finance/recent", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401


def test_key_cannot_mint_more_keys(anon, auth):
    # A key must not be able to reach the key-management routes (POST anyway).
    key = _generate(auth, name="no-escalation")["key"]
    r = anon.post("/api/keys/generate", json={"name": "evil"},
                  headers={"Authorization": f"Bearer {key}"})
    assert r.status_code in (401, 403)
