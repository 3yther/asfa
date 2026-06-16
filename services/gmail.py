import os
import re
import json
import base64
import logging
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logger = logging.getLogger("asfa.gmail")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/oauth/callback")],
    }
}

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "google_token.json")


def get_flow(redirect_uri=None):
    config = dict(CLIENT_CONFIG)
    if redirect_uri:
        config["web"]["redirect_uris"] = [redirect_uri]
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri or config["web"]["redirect_uris"][0])


def save_credentials(creds):
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def load_credentials():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes", SCOPES),
        )
        return creds
    except Exception:
        return None


def is_authenticated():
    creds = load_credentials()
    return creds is not None and (creds.valid or creds.refresh_token)


def _b64url_decode(data: str) -> str:
    """Gmail returns bodies as base64url. Decode to UTF-8 text, lenient on errors."""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """Crude HTML → text so we can summarise HTML-only emails."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(payload: dict) -> str:
    """Walk a Gmail message payload and return the best plain-text body.

    Handles single-part text, multipart/alternative, and nested multipart.
    Prefers text/plain; falls back to stripped text/html.
    """
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    parts = payload.get("parts")

    # Leaf node with inline data
    if not parts:
        data = body.get("data", "")
        decoded = _b64url_decode(data)
        if mime == "text/html":
            return _strip_html(decoded)
        return decoded

    # Multipart — collect plain and html separately, prefer plain
    plain, html = "", ""
    for part in parts:
        pmime = part.get("mimeType", "")
        if pmime == "text/plain":
            plain += _b64url_decode(part.get("body", {}).get("data", ""))
        elif pmime == "text/html":
            html += _b64url_decode(part.get("body", {}).get("data", ""))
        elif pmime.startswith("multipart/"):
            nested = _extract_body(part)
            if nested and not plain:
                plain = nested
    if plain.strip():
        return plain.strip()
    if html.strip():
        return _strip_html(html)
    return ""


def _parse_message(full: dict) -> dict:
    headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
    body = _extract_body(full.get("payload", {})) or full.get("snippet", "")
    return {
        "id": full.get("id"),
        "thread_id": full.get("threadId"),
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": (full.get("snippet", "") or "")[:200],
        "body": body[:4000],
    }


def get_unread_emails(hours=24, max_results=10):
    """Fetch the most recent unread emails (last `max_results`, default 10).

    Returns a list of email dicts. On failure returns a single-item list
    [{"error": ...}] so callers can degrade gracefully without crashing.
    """
    creds = load_credentials()
    if not creds:
        return []
    try:
        service = build("gmail", "v1", credentials=creds)
        after = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        ids = []
        page_token = None
        # Paginate until we have max_results message ids (or run out).
        while len(ids) < max_results:
            results = service.users().messages().list(
                userId="me",
                q=f"is:unread after:{after}",
                maxResults=min(max_results - len(ids), 50),
                pageToken=page_token,
            ).execute()
            ids.extend(m["id"] for m in results.get("messages", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        emails = []
        for msg_id in ids[:max_results]:
            full = service.users().messages().get(
                userId="me", id=msg_id, format="full").execute()
            emails.append(_parse_message(full))
        return emails
    except Exception as e:
        logger.warning("Gmail fetch failed: %s", e)
        return [{"error": str(e)}]


def get_email_by_id(msg_id: str):
    """Fetch a single email (with decoded body) by id. Returns dict or
    {"error": ...} on failure."""
    creds = load_credentials()
    if not creds:
        return {"error": "Gmail not connected"}
    try:
        service = build("gmail", "v1", credentials=creds)
        full = service.users().messages().get(
            userId="me", id=msg_id, format="full").execute()
        return _parse_message(full)
    except Exception as e:
        logger.warning("Gmail get_email_by_id failed: %s", e)
        return {"error": str(e)}
