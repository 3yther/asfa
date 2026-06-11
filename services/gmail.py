import os
import json
import base64
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

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


def get_unread_emails(hours=24):
    creds = load_credentials()
    if not creds:
        return []
    try:
        service = build("gmail", "v1", credentials=creds)
        after = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        results = service.users().messages().list(
            userId="me",
            q=f"is:unread after:{after}",
            maxResults=20
        ).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages[:10]:
            full = service.users().messages().get(userId="me", id=msg["id"], format="metadata",
                                                   metadataHeaders=["Subject", "From", "Date"]).execute()
            headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            snippet = full.get("snippet", "")
            emails.append({
                "id": msg["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": snippet[:200],
            })
        return emails
    except Exception as e:
        return [{"error": str(e)}]
