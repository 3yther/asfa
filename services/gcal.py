import os
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from .gmail import load_credentials


def get_todays_events():
    creds = load_credentials()
    if not creds:
        return []
    try:
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end = now.replace(hour=23, minute=59, second=59).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        events = []
        for e in result.get("items", []):
            start_raw = e["start"].get("dateTime") or e["start"].get("date")
            end_raw = e["end"].get("dateTime") or e["end"].get("date")
            events.append({
                "id": e["id"],
                "title": e.get("summary", "(no title)"),
                "start": start_raw,
                "end": end_raw,
                "location": e.get("location", ""),
                "description": e.get("description", ""),
            })
        return events
    except Exception as e:
        return [{"error": str(e)}]


def get_tomorrow_events():
    creds = load_credentials()
    if not creds:
        return []
    try:
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end = tomorrow.replace(hour=23, minute=59, second=59).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        events = []
        for e in result.get("items", []):
            start_raw = e["start"].get("dateTime") or e["start"].get("date")
            end_raw = e["end"].get("dateTime") or e["end"].get("date")
            events.append({
                "id": e["id"],
                "title": e.get("summary", "(no title)"),
                "start": start_raw,
                "end": end_raw,
                "location": e.get("location", ""),
            })
        return events
    except Exception as e:
        return []


def add_event(title, start_datetime, end_datetime, description="", location=""):
    creds = load_credentials()
    if not creds:
        return {"error": "Not authenticated"}
    try:
        service = build("calendar", "v3", credentials=creds)
        event = {
            "summary": title,
            "location": location,
            "description": description,
            "start": {"dateTime": start_datetime, "timeZone": "Europe/London"},
            "end": {"dateTime": end_datetime, "timeZone": "Europe/London"},
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
        return {"id": created["id"], "link": created.get("htmlLink", "")}
    except Exception as e:
        return {"error": str(e)}
