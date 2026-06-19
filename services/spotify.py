"""Spotify integration — OAuth (Authorization Code flow) + playback control.

ASFA is single-user, so the token set is persisted in the kv_store under the
key ``spotify_token`` as a JSON blob::

    {access_token, refresh_token, expires_at, scope}

Access tokens last ~1 hour; ``get_access_token`` transparently refreshes using
the long-lived refresh_token. All network calls degrade gracefully — callers
get a structured ``{ok|connected, message, reason}`` dict, never an exception.
"""
import base64
import json
import logging
import os
import time
from urllib.parse import urlencode

import requests

import database as db

logger = logging.getLogger("asfa.spotify")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

SCOPES = [
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-currently-playing",
]

_KV_KEY = "spotify_token"


# ── Config (env) ────────────────────────────────────────────────────────────────

def _client_id():
    return os.environ.get("SPOTIFY_CLIENT_ID", "")


def _client_secret():
    return os.environ.get("SPOTIFY_CLIENT_SECRET", "")


def _redirect_uri():
    return os.environ.get(
        "SPOTIFY_REDIRECT_URI",
        "https://asfa-production.up.railway.app/auth/spotify/callback",
    )


def is_configured():
    """True when server-side client credentials are present."""
    return bool(_client_id() and _client_secret())


# ── Token persistence (kv_store) ────────────────────────────────────────────────

def _store(tokens):
    db.kv_set(_KV_KEY, json.dumps(tokens))


def _load():
    # Read path runs on every page load (index renders the connect chip), so a
    # transient DB error must never break the page — just report "not connected".
    try:
        raw = db.kv_get(_KV_KEY)
    except Exception as e:
        logger.warning("Spotify token read failed: %s", e)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def is_connected():
    t = _load()
    return bool(t and t.get("refresh_token"))


def disconnect():
    db.kv_set(_KV_KEY, "")


# ── OAuth ───────────────────────────────────────────────────────────────────────

def get_auth_url(state):
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "scope": " ".join(SCOPES),
        "state": state,
        "show_dialog": "false",
    }
    return AUTH_URL + "?" + urlencode(params)


def _basic_auth_header():
    raw = (_client_id() + ":" + _client_secret()).encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(code):
    """Swap an authorization code for tokens and persist them. Returns bool."""
    try:
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": _redirect_uri()},
            headers={"Authorization": _basic_auth_header()},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Spotify token exchange failed: %s", e)
        return False
    _store({
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 60,
        "scope": data.get("scope", ""),
    })
    return True


def get_access_token():
    """Return a valid access token, refreshing on expiry. None if not connected
    or the refresh was rejected (revoked token)."""
    t = _load()
    if not t or not t.get("refresh_token"):
        return None
    if t.get("access_token") and time.time() < t.get("expires_at", 0):
        return t["access_token"]
    try:
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"]},
            headers={"Authorization": _basic_auth_header()},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Spotify token refresh failed: %s", e)
        return None
    t["access_token"] = data.get("access_token")
    t["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    if data.get("refresh_token"):       # Spotify occasionally rotates it
        t["refresh_token"] = data["refresh_token"]
    _store(t)
    return t["access_token"]


def _auth_headers():
    tok = get_access_token()
    return {"Authorization": "Bearer " + tok} if tok else None


# ── Playback ────────────────────────────────────────────────────────────────────

def current_playback():
    """Player snapshot: {connected, configured, is_playing, track, artist, device}."""
    if not is_configured():
        return {"connected": False, "configured": False}
    if not is_connected():
        return {"connected": False, "configured": True}
    headers = _auth_headers()
    if not headers:
        return {"connected": False, "configured": True, "reason": "reauth"}
    try:
        r = requests.get(API_BASE + "/me/player", headers=headers, timeout=10)
        # 204 (or empty body) → connected but no active device / nothing playing.
        if r.status_code == 204 or not (r.text or "").strip():
            return {"connected": True, "configured": True, "is_playing": False, "device": None}
        r.raise_for_status()
        d = r.json()
        item = d.get("item") or {}
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
        return {
            "connected": True,
            "configured": True,
            "is_playing": bool(d.get("is_playing")),
            "track": item.get("name"),
            "artist": artists,
            "device": (d.get("device") or {}).get("name"),
        }
    except Exception as e:
        logger.warning("Spotify playback state failed: %s", e)
        return {"connected": True, "configured": True, "reason": "error"}


def _interpret_play(r):
    """Map a /play response to a structured {ok, reason, message} dict."""
    if r.status_code in (200, 202, 204):
        return {"ok": True, "message": "Playback started."}
    reason = None
    msg = None
    try:
        err = (r.json() or {}).get("error", {})
        reason = err.get("reason")
        msg = err.get("message")
    except Exception:
        pass
    no_device = "No Spotify device found. Open Spotify on your phone/computer and try again."
    if r.status_code == 404 or reason == "NO_ACTIVE_DEVICE":
        return {"ok": False, "reason": "no_device", "message": no_device}
    if r.status_code == 401:
        return {"ok": False, "reason": "reauth",
                "message": "Spotify session expired — please reconnect."}
    if r.status_code == 403:
        # 403 is often "already playing" or a Premium restriction.
        if msg and "already" in msg.lower():
            return {"ok": True, "message": "Already playing."}
        return {"ok": False, "reason": "restricted",
                "message": msg or "Spotify Premium is required to control playback."}
    return {"ok": False, "reason": "error", "message": "Spotify error (%s)." % r.status_code}


def _preflight():
    """Shared connection check → (headers, error_dict). Exactly one is non-None."""
    if not is_configured():
        return None, {"ok": False, "reason": "not_configured",
                      "message": "Spotify isn't configured on the server."}
    if not is_connected():
        return None, {"ok": False, "reason": "not_connected",
                      "message": "Connect your Spotify account to auto-play."}
    headers = _auth_headers()
    if not headers:
        return None, {"ok": False, "reason": "reauth",
                      "message": "Spotify session expired — please reconnect."}
    return headers, None


def resume_playback():
    """Resume playback on the active/default device. Returns {ok, message, reason}."""
    headers, err = _preflight()
    if err:
        return err
    try:
        r = requests.put(API_BASE + "/me/player/play", headers=headers, timeout=10)
    except Exception as e:
        logger.warning("Spotify play failed: %s", e)
        return {"ok": False, "reason": "error", "message": "Couldn't reach Spotify."}
    return _interpret_play(r)


def _search_playlist_uri(query, headers):
    """Return the first playlist URI matching `query`, or None."""
    try:
        r = requests.get(API_BASE + "/search", headers=headers,
                         params={"q": query, "type": "playlist", "limit": 1}, timeout=10)
        r.raise_for_status()
        items = (((r.json() or {}).get("playlists") or {}).get("items")) or []
        for it in items:
            if it and it.get("uri"):
                return it["uri"]
    except Exception as e:
        logger.warning("Spotify search failed: %s", e)
    return None


def play_query(query):
    """Start the first playlist matching `query` (e.g. 'deep focus', 'ambient
    focus'). Falls back to resuming current playback if search finds nothing.
    Same structured return shape as resume_playback()."""
    headers, err = _preflight()
    if err:
        return err
    uri = _search_playlist_uri(query, headers)
    body = {"context_uri": uri} if uri else None
    try:
        r = requests.put(API_BASE + "/me/player/play", headers=headers,
                         json=body, timeout=10)
    except Exception as e:
        logger.warning("Spotify play_query failed: %s", e)
        return {"ok": False, "reason": "error", "message": "Couldn't reach Spotify."}
    result = _interpret_play(r)
    if result.get("ok"):
        result["query"] = query
        result["found_playlist"] = bool(uri)
    return result
