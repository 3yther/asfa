"""Builds the morning briefing — combines weather, calendar, email, bots, news, habits."""
from datetime import datetime

import database as db
from services import ai
from services.bots import get_bots_status
from services.gcal import get_todays_events
from services.gmail import get_unread_emails
from services.news import get_top_news
from services.weather import get_weather


def build_briefing(force: bool = False) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    if not force:
        cached = db.get_cached_briefing(today)
        if cached:
            return {"date": today, "content": cached["content"],
                    "text": cached["plain_text"], "cached": True}

    weather = get_weather()
    events = [e for e in get_todays_events() if "error" not in e]
    emails = [e for e in get_unread_emails() if "error" not in e]
    bot_status = get_bots_status()
    headlines = get_top_news()

    result = ai.generate_briefing(weather, events, emails, bot_status)
    content = result["content"]
    if headlines:
        content += "\n\n📰 Headlines:\n" + "\n".join(f"• {h['title']}" for h in headlines[:4])

    db.save_briefing(today, content, result["plain_text"])
    return {"date": today, "content": content, "text": result["plain_text"], "cached": False}
