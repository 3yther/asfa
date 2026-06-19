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
        try:
            client = anthropic.Anthropic(api_key=api_key)
        except TypeError as e:
            if 'proxies' in str(e) or 'unexpected keyword argument' in str(e):
                raise RuntimeError(
                    f"Anthropic SDK incompatibility detected. "
                    f"Expected anthropic==0.109.1, but got incompatible version. "
                    f"Error: {e}. Update requirements.txt and redeploy."
                ) from e
            raise
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


def draft_reply(email: dict) -> str:
    """Compose a concise, professional reply to an email. Does NOT send."""
    c = _get_client()
    if not c:
        return "ANTHROPIC_API_KEY not set — can't draft a reply."
    content = (email.get("body") or email.get("snippet") or "").strip()
    context = (
        f"From: {email.get('from','')}\n"
        f"Subject: {email.get('subject','')}\n\n{content}"
    )
    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "Write a professional, concise reply to this email "
                    "(1-2 sentences max). Return only the reply body — no "
                    "subject line, no greeting placeholder like [Name], and no "
                    "commentary:\n\n" + context
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"Draft error: {e}"


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


def generate_briefing(data: dict) -> dict:
    """Build the morning briefing from already-gathered, fault-tolerant sections.

    `data` keys (any may be missing/empty — the briefing degrades gracefully):
      weather, events_today, events_tomorrow, emails, habits_avg, goals, trading
    """
    c = _get_client()
    today = datetime.now().strftime("%A, %d %B %Y")

    weather = data.get("weather") or {}
    events_today = data.get("events_today") or []
    events_tomorrow = data.get("events_tomorrow") or []
    emails = data.get("emails") or []
    habits_avg = data.get("habits_avg") or {}
    goals = data.get("goals") or []
    trading = data.get("trading") or {}
    insights = data.get("insights") or []
    supplements = data.get("supplements") or {}

    # --- Section text (each independently safe) ---------------------------------
    weather_text = (
        f"{weather.get('temp','?')}°C, {weather.get('description','?')}"
        if weather else "unavailable"
    )
    cal_today = "\n".join(
        f"- {e.get('start','?')}: {e.get('title','?')}" for e in events_today
    ) or "No events today"
    cal_tomorrow = "\n".join(
        f"- {e.get('start','?')}: {e.get('title','?')}" for e in events_tomorrow
    ) or "Nothing scheduled"
    emails_text = "\n".join(
        f"- {e.get('from','?')}: {e.get('subject','?')}"
        + (f" — {e['summary']}" if e.get("summary") else "")
        for e in emails[:5]
    ) or "No unread emails"
    habits_text = (
        f"7-day avg — water {habits_avg.get('water_ml', 0):.0f}ml, "
        f"sleep {habits_avg.get('sleep_hours', 0):.1f}h, "
        f"water streak {habits_avg.get('water_streak', 0)} days"
        if habits_avg else "No habit data"
    )
    goals_text = "\n".join(
        f"- {g.get('title','?')}: {g.get('progress',0)}%" for g in goals
    ) or "No goals set"
    supp_text = (
        f"{supplements.get('taken', 0)}/{supplements.get('total', 0)} taken today"
        if supplements else "No supplement data"
    )
    try:
        bots_text = get_bots_summary_text(trading)
    except Exception:
        bots_text = "unavailable"

    prompt = f"""Generate a concise morning briefing for Amir for {today}.

Cover these sections in this order, skipping any that have no data:
1. WEATHER: {weather_text}
2. CALENDAR — today:
{cal_today}
   tomorrow:
{cal_tomorrow}
3. EMAILS (top unread):
{emails_text}
4. HABITS: {habits_text}
5. SUPPLEMENTS: {supp_text}
6. GOALS:
{goals_text}
7. TRADING BOTS: {bots_text}

DETECTED PATTERNS (weave these in naturally, don't just list them):
{chr(10).join(f"- {i}" for i in insights) or "- none"}

Write a warm, motivating briefing in 3-4 short paragraphs that flows naturally
through the above. Be specific with numbers. Use "you" not "Amir". End with one
clear focus for the day based on the goals/progress."""

    # Plain fallback used when AI is unavailable — still useful & non-crashing.
    fallback = (
        f"Good morning! It's {today}. {weather_text} in London. "
        f"{len(events_today)} event(s) today, {len(emails)} unread email(s). "
        f"{habits_text}."
    )

    if not c:
        return {"content": fallback, "plain_text": fallback}

    try:
        resp = c.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        return {"content": text, "plain_text": text}
    except Exception as e:
        return {"content": fallback, "plain_text": fallback, "error": str(e)}


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
