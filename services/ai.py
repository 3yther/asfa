import os
import json
from datetime import datetime

import anthropic

import database as db
from services.bots import get_bots_summary_text
from services.weather import get_weather

MODEL = "claude-sonnet-4-6"
client = None


def _get_client():
    global client
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key)
    return client


def build_context_block():
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%A, %d %B %Y %H:%M")

    habits = db.get_habits(7)
    today_habit = next((h for h in habits if h["date"] == today), {})
    workouts = db.get_workouts(7)
    spending = db.get_spending(7)
    memories = db.get_memories(8)
    reflections = db.get_reflections(5)
    pbs = db.get_pbs()
    goals = db.get_goals()
    scores = db.get_daily_scores(7)
    body_weights = db.get_body_weight(14)

    total_spend_week = sum(s["amount"] for s in spending)
    spend_by_cat = {}
    for s in spending:
        spend_by_cat[s["category"]] = spend_by_cat.get(s["category"], 0) + s["amount"]

    water_streak = db.get_water_streak()
    sleep_avg = (
        sum(h["sleep_hours"] for h in habits if h.get("sleep_hours"))
        / max(1, len([h for h in habits if h.get("sleep_hours")]))
    )

    muscle_groups = {}
    for w in workouts:
        g = w.get("muscle_group", "unknown")
        if g:
            muscle_groups[g] = muscle_groups.get(g, 0) + 1

    try:
        weather = get_weather()
        weather_str = f"{weather.get('temp')}°C, {weather.get('description')}"
    except Exception:
        weather_str = "unavailable"

    try:
        bots_str = get_bots_summary_text()
    except Exception:
        bots_str = "unavailable"

    ctx = f"""=== ASFA CONTEXT BLOCK ({now_str}) ===

TODAY'S HABITS:
- Water: {today_habit.get('water_ml', 0)}ml (target 2000ml)
- Sleep last night: {today_habit.get('sleep_hours', '?')}h
- 7-day sleep avg: {sleep_avg:.1f}h
- Water streak: {water_streak} days

THIS WEEK'S WORKOUTS ({len(workouts)} sessions):
{chr(10).join(f"- {w['date']}: {w['exercise']} {w.get('weight_kg','')}kg x{w.get('reps','')} [{w.get('muscle_group','')}]{' 🏆 PB!' if w.get('is_pb') else ''}" for w in workouts[:6]) or "None logged"}

MUSCLE GROUP BALANCE THIS WEEK: {json.dumps(muscle_groups)}

PERSONAL BESTS:
{chr(10).join(f"- {p['exercise']}: {p['best_weight']}kg x{p['best_reps']} reps" for p in pbs[:8]) or "None recorded"}

BODY WEIGHT (last 5):
{chr(10).join(f"- {bw['date']}: {bw['weight_kg']}kg" for bw in body_weights[-5:]) or "None logged"}

SPENDING THIS WEEK: £{total_spend_week:.2f}
By category: {json.dumps({k: f'£{v:.2f}' for k, v in spend_by_cat.items()})}

TRADING BOTS:
{bots_str}

WEATHER: {weather_str}

RECENT MEMORIES:
{chr(10).join(f"- {m['content']}" for m in memories) or "None"}

RECENT REFLECTIONS:
{chr(10).join(f"- {r['date']} (score {r['score']}/10): {r['content'][:120]}" for r in reflections) or "None"}

MONTHLY GOALS:
{chr(10).join(f"- {g['title']}: {g['progress']}% ({g['target']})" for g in goals) or "None set"}

DAILY SCORES (last 7 days):
{chr(10).join(f"- {s['date']}: {s['score']}/100" for s in scores) or "None"}
=== END CONTEXT ==="""
    return ctx


def chat(user_message: str, conversation_history: list = None) -> str:
    c = _get_client()
    if not c:
        return "ANTHROPIC_API_KEY not set. Please configure it in your .env file."

    context = build_context_block()

    system_prompt = f"""You are ASFA (AI Software For Amir) — a personal JARVIS-style AI assistant for Amir Salah.
You know everything about Amir's day, health, fitness, money, and trading through the context block below.
Be concise, direct, and genuinely helpful. Use the context to give informed, personalised answers.
When Amir asks "how am I doing", cross-reference habits, workouts, spending, and trading.
Detect commands in natural speech:
- "log Xml water" → call log_water
- "log X hours sleep" → call log_sleep
- "log [exercise] [weight]kg [reps] reps" → call log_workout
- "log weight Xkg" → call log_body_weight
- "spent £X on [category]" → call log_spend
- "remember [fact]" → call save_memory
- "add note: [text]" → call save_voice_note

{context}"""

    messages = []
    if conversation_history:
        messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = c.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
    except Exception as e:
        return f"AI error: {e}"


def summarise_emails(emails: list) -> list:
    c = _get_client()
    if not c or not emails:
        return emails
    summaries = []
    for email in emails[:8]:
        if "error" in email:
            summaries.append(email)
            continue
        try:
            resp = c.messages.create(
                model=MODEL,
                max_tokens=100,
                messages=[{
                    "role": "user",
                    "content": f"Summarise this email in one sentence (max 15 words):\nFrom: {email['from']}\nSubject: {email['subject']}\n{email['snippet']}"
                }]
            )
            email["summary"] = resp.content[0].text.strip()
        except Exception:
            email["summary"] = email["snippet"][:100]
        summaries.append(email)
    return summaries


def detect_events_in_emails(emails: list) -> list:
    """Scan email subjects/snippets for dates & times → suggest calendar events."""
    c = _get_client()
    if not c or not emails:
        return []
    emails_text = "\n".join(
        f"[{i}] From: {e.get('from','')} | Subject: {e.get('subject','')} | {e.get('snippet','')}"
        for i, e in enumerate(emails[:8]) if "error" not in e
    )
    if not emails_text:
        return []
    now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Current date/time: {now} (Europe/London).
Scan these emails for anything that looks like an appointment, meeting, booking, or deadline with a date/time:

{emails_text}

Return ONLY a JSON array (no prose). Each item: {{"title": str, "start": "YYYY-MM-DDTHH:MM:00", "end": "YYYY-MM-DDTHH:MM:00", "source_subject": str}}.
Use 1-hour duration if no end time. Return [] if nothing found."""
            }]
        )
        text = resp.content[0].text.strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []
        return json.loads(text[start:end + 1])
    except Exception:
        return []


def generate_briefing(weather, events, emails, bot_status) -> dict:
    c = _get_client()
    today = datetime.now().strftime("%A, %d %B %Y")
    context = build_context_block()

    bots_text = get_bots_summary_text(bot_status)
    events_text = "\n".join(f"- {e.get('start','?')}: {e.get('title','?')}" for e in events) or "No events today"
    emails_text = "\n".join(f"- {e.get('from','?')}: {e.get('subject','?')}" for e in emails[:5]) or "No unread emails"

    prompt = f"""Generate a concise morning briefing for Amir for {today}.

Weather: {weather.get('temp','?')}°C, {weather.get('description','?')}
Calendar today:\n{events_text}
Unread emails:\n{emails_text}
Trading bots:\n{bots_text}

{context}

Write a warm, motivating briefing in 3-4 paragraphs:
1. Good morning + weather + day overview
2. Email highlights + calendar
3. Trading bots P&L + habit progress
4. One specific motivating focus for the day based on their goals/progress

Keep it personal, concise, and energising. Use "you" not "Amir"."""

    if not c:
        plain = f"Good morning! It's {today}. {weather.get('description','?')}, {weather.get('temp','?')}°C in London. You have {len(events)} events today."
        return {"content": plain, "plain_text": plain}

    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        return {"content": text, "plain_text": text}
    except Exception as e:
        plain = f"Good morning! It's {today}. {weather.get('description','?')}, {weather.get('temp','?')}°C in London."
        return {"content": plain, "plain_text": plain, "error": str(e)}


def compute_daily_score() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    habits = db.get_habits(1)
    today_h = next((h for h in habits if h["date"] == today), {})
    workouts = db.get_workouts(1)
    today_workouts = [w for w in workouts if w["date"] == today]
    spending = db.get_spending(7)
    today_spend = sum(s["amount"] for s in spending if s["date"] == today)
    week_avg_spend = sum(s["amount"] for s in spending) / 7 if spending else 0

    water = today_h.get("water_ml", 0)
    sleep = today_h.get("sleep_hours", 0)

    water_score = min(40, int((water / 2000) * 40))
    sleep_score = 20 if sleep >= 7 else int((sleep / 7) * 20)
    workout_score = 20 if today_workouts else 0
    spend_score = 20 if (week_avg_spend == 0 or today_spend <= week_avg_spend * 1.2) else max(0, 20 - int((today_spend - week_avg_spend) / week_avg_spend * 10))

    total = water_score + sleep_score + workout_score + spend_score
    breakdown = {
        "water": water_score,
        "sleep": sleep_score,
        "workout": workout_score,
        "spending": spend_score,
    }
    db.save_daily_score(today, total, json.dumps(breakdown))
    return {"score": total, "breakdown": breakdown}


def generate_weekly_review() -> str:
    c = _get_client()
    context = build_context_block()
    if not c:
        return "Weekly review unavailable — ANTHROPIC_API_KEY not set."
    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": f"Generate an honest, encouraging weekly review for Amir based on this data. Cover: habits, workouts, spending, trading bots P&L, sleep avg, and any reflection themes. Be specific about numbers.\n\n{context}"
            }]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"Weekly review error: {e}"


def analyse_photo(image_base64: str, mime_type: str = "image/jpeg") -> str:
    c = _get_client()
    if not c:
        return "ANTHROPIC_API_KEY not set."
    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Look at this image. If it's a meal, estimate calories and macros. If it's a receipt, extract total amount and merchant. If it's a gym screen (workout summary), extract exercise names, weights, and reps. Return a JSON object with type (meal/receipt/workout) and relevant fields."
                    }
                ],
            }]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"Vision error: {e}"
