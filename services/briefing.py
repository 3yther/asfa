"""Builds the morning briefing — combines weather, calendar, email, habits,
goals, and trading bots. Every data source degrades gracefully: if one fails,
that section is skipped and the rest of the briefing still renders."""
import logging
from datetime import datetime

import database as db
from services import ai, insights
from services.bots import get_trading_activity
from services.gcal import get_todays_events, get_tomorrow_events
from services.gmail import get_unread_emails
from services.news import get_top_news
from services.weather import get_weather

logger = logging.getLogger("asfa.briefing")

# kv_store key gating the opt-in AI briefing summary (Tier 5 Part 2). "1" = on.
AI_BRIEFING_SUMMARY_KEY = "ai_briefing_summary_enabled"


def _safe(label, fn, default):
    try:
        return fn()
    except Exception as e:
        logger.warning("briefing section '%s' failed: %s", label, e)
        return default


def _habits_avg():
    habits = db.get_habits(7)
    if not habits:
        return {"water_ml": 0, "sleep_hours": 0, "water_streak": db.get_water_streak()}
    water = sum(h.get("water_ml", 0) or 0 for h in habits) / len(habits)
    sleep_vals = [h["sleep_hours"] for h in habits if h.get("sleep_hours")]
    sleep = sum(sleep_vals) / len(sleep_vals) if sleep_vals else 0
    return {
        "water_ml": water,
        "sleep_hours": sleep,
        "water_streak": db.get_water_streak(),
    }


def build_briefing(force: bool = False) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    if not force:
        cached = db.get_cached_briefing(today)
        if cached:
            return {"date": today, "content": cached["content"],
                    "text": cached["plain_text"], "cached": True}

    # Gather every section independently so a single failure can't break the briefing.
    weather = _safe("weather", get_weather, {})
    events_today = _safe("calendar_today",
                         lambda: [e for e in get_todays_events() if "error" not in e], [])
    events_tomorrow = _safe("calendar_tomorrow",
                            lambda: [e for e in get_tomorrow_events() if "error" not in e], [])
    # Tier 5 Part 2: the AI enrichment (email one-liners + the narrative summary)
    # is opt-in and OFF by default to save Claude credits. When disabled we skip
    # every Claude call in the briefing build and simply omit the AI summary
    # block — the deterministic sections (patterns, headlines) still render.
    ai_summary_enabled = (db.kv_get(AI_BRIEFING_SUMMARY_KEY) or "0") == "1"

    raw_emails = _safe("gmail", get_unread_emails, [])
    emails = [e for e in raw_emails if "error" not in e]
    if ai_summary_enabled:
        emails = _safe("email_summaries", lambda: ai.summarise_emails(emails), emails)
    habits_avg = _safe("habits", _habits_avg, {})
    supplements = _safe("supplements", lambda: {
        "taken": db.count_supplements_today(today), "total": len(db.SUPPLEMENTS)}, {})
    goals = _safe("goals", db.get_goals, [])
    trading = _safe("trading", get_trading_activity, {})
    headlines = _safe("news", get_top_news, [])

    # Autonomous pattern detection — 1-2 insights woven into every briefing.
    metrics = _safe("metrics", insights.gather_metrics, {})
    detected = _safe("insights", lambda: insights.generate_insights(metrics), [])

    if ai_summary_enabled:
        result = ai.generate_briefing({
            "weather": weather,
            "events_today": events_today,
            "events_tomorrow": events_tomorrow,
            "emails": emails,
            "habits_avg": habits_avg,
            "supplements": supplements,
            "goals": goals,
            "trading": trading,
            "insights": detected,
        })
        narrative, plain_text = result["content"], result["plain_text"]
    else:
        narrative, plain_text = "", ""

    parts = [narrative] if narrative else []
    if detected:
        parts.append("🧠 Patterns:\n" + "\n".join(f"• {i}" for i in detected))
    if headlines:
        parts.append("📰 Headlines:\n" + "\n".join(
            f"• {h['title']}" for h in headlines[:4]))
    content = "\n\n".join(parts)

    db.save_briefing(today, content, plain_text or content)
    return {"date": today, "content": content, "text": plain_text or content,
            "cached": False, "ai_summary_enabled": ai_summary_enabled}
