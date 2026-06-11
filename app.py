"""ASFA — AI Software For Amir. JARVIS-style life command centre."""
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import database as db
from services import ai
from services.bots import get_bots_status
from services.briefing import build_briefing
from services.gcal import add_event, get_todays_events, get_tomorrow_events
from services.gmail import get_flow, get_unread_emails, is_authenticated, save_credentials
from services.news import get_finance_news, get_top_news
from services.weather import get_forecast, get_weather

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("asfa")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "asfa-dev-secret-change-me")
app.config["PREFERRED_URL_SCHEME"] = "https"

db.init_db()


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", google_connected=is_authenticated())


# ── Briefing ───────────────────────────────────────────────────────────────────

@app.route("/api/briefing")
def api_briefing():
    force = request.args.get("refresh") == "1"
    b = build_briefing(force=force)
    return jsonify(b)


# ── AI chat / voice — with natural-language command handling ──────────────────

COMMANDS = [
    # (regex, handler) — handlers return a confirmation string
    (re.compile(r"\blog\s+(\d{2,4})\s*ml\b(?:\s*(?:of\s+)?water)?", re.I),
     lambda m: _do_water(int(m.group(1)))),
    (re.compile(r"\blog\s+water\s+(\d{2,4})\s*ml\b", re.I),
     lambda m: _do_water(int(m.group(1)))),
    (re.compile(r"\blog\s+(?:sleep\s+)?(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b(?:\s*(?:of\s+)?sleep)?", re.I),
     lambda m: _do_sleep(float(m.group(1)))),
    (re.compile(r"\blog\s+weight\s+(\d{2,3}(?:\.\d+)?)\s*kg\b", re.I),
     lambda m: _do_body_weight(float(m.group(1)))),
    (re.compile(r"\blog\s+([a-zA-Z][a-zA-Z ]{2,30}?)\s+(\d{1,3}(?:\.\d+)?)\s*kg\s+(\d{1,2})\s*reps?\b", re.I),
     lambda m: _do_workout(m.group(1).strip(), float(m.group(2)), int(m.group(3)))),
    (re.compile(r"\bspent\s+[£$]?(\d+(?:\.\d{1,2})?)\s+(?:on\s+)?([a-zA-Z][\w ]{1,40})", re.I),
     lambda m: _do_spend(float(m.group(1)), m.group(2).strip())),
    (re.compile(r"\bremember\s+(?:that\s+)?(.{4,})", re.I),
     lambda m: _do_memory(m.group(1).strip())),
]

MUSCLE_MAP = {
    "bench": "chest", "press": "shoulders", "squat": "legs", "deadlift": "back",
    "row": "back", "curl": "arms", "pull": "back", "dip": "chest", "lunge": "legs",
    "fly": "chest", "extension": "arms", "raise": "shoulders", "shrug": "shoulders",
}


def _guess_muscle(exercise: str) -> str:
    low = exercise.lower()
    for key, group in MUSCLE_MAP.items():
        if key in low:
            return group
    return "other"


def _do_water(ml):
    db.log_water(_today(), ml)
    db.kv_set("last_water_ts", datetime.now().isoformat())
    return f"Logged {ml}ml of water."


def _do_sleep(hours):
    db.log_sleep(_today(), hours)
    return f"Logged {hours}h sleep."


def _do_body_weight(kg):
    db.log_body_weight(_today(), kg)
    return f"Logged body weight {kg}kg."


def _do_workout(exercise, kg, reps):
    is_pb = db.log_workout(_today(), exercise.title(), kg, reps, 1, _guess_muscle(exercise))
    return f"Logged {exercise.title()} {kg}kg x{reps}." + (" 🏆 NEW PB!" if is_pb else "")


def _do_spend(amount, note):
    category = note.split()[0].lower()
    db.log_spend(_today(), amount, category, note)
    return f"Logged £{amount:.2f} on {note}."


def _do_memory(content):
    db.save_memory(content)
    return "Got it — I'll remember that."


def _run_commands(message: str):
    actions = []
    for pattern, handler in COMMANDS:
        m = pattern.search(message)
        if m:
            try:
                actions.append(handler(m))
            except Exception as e:
                logger.error(f"command failed: {e}")
            break  # one command per message keeps parsing unambiguous
    return actions


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    actions = _run_commands(message)
    history = db.get_recent_conversation(10)
    ai_message = message
    if actions:
        ai_message += f"\n\n[System: actions already performed: {'; '.join(actions)} — confirm briefly and add any relevant insight.]"

    reply = ai.chat(ai_message, history)
    db.save_message("user", message)
    db.save_message("assistant", reply)
    return jsonify({"reply": reply, "actions": actions})


@app.route("/api/conversation")
def api_conversation():
    return jsonify(db.get_recent_conversation(30))


# ── Habits ─────────────────────────────────────────────────────────────────────

@app.route("/api/habits")
def api_habits():
    habits = db.get_habits(7)
    today = next((h for h in habits if h["date"] == _today()), {"water_ml": 0, "sleep_hours": 0})
    return jsonify({
        "today": today,
        "history": habits,
        "water_streak": db.get_water_streak(),
    })


@app.route("/api/habits/water", methods=["POST"])
def api_log_water():
    ml = int(request.get_json(force=True).get("ml", 0))
    if ml <= 0:
        return jsonify({"error": "ml must be positive"}), 400
    msg = _do_water(ml)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/habits/sleep", methods=["POST"])
def api_log_sleep():
    hours = float(request.get_json(force=True).get("hours", 0))
    if not 0 < hours <= 24:
        return jsonify({"error": "invalid hours"}), 400
    return jsonify({"ok": True, "message": _do_sleep(hours)})


# ── Gym ────────────────────────────────────────────────────────────────────────

@app.route("/api/gym/workout", methods=["POST"])
def api_log_workout():
    d = request.get_json(force=True)
    is_pb = db.log_workout(
        _today(), d["exercise"].strip().title(), float(d.get("weight_kg") or 0),
        int(d.get("reps") or 0), int(d.get("sets") or 1),
        d.get("muscle_group") or _guess_muscle(d["exercise"]), d.get("notes", ""))
    return jsonify({"ok": True, "is_pb": bool(is_pb)})


@app.route("/api/gym")
def api_gym():
    workouts = db.get_workouts(7)
    balance = {}
    last_trained = {}
    for w in db.get_workouts(30):
        g = w.get("muscle_group") or "other"
        if w["date"] >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"):
            balance[g] = balance.get(g, 0) + 1
        last_trained[g] = max(last_trained.get(g, w["date"]), w["date"])
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    neglected = [g for g, d in last_trained.items() if d < cutoff]
    return jsonify({
        "workouts": workouts,
        "pbs": db.get_pbs(),
        "balance": balance,
        "neglected": neglected,
        "body_weight": db.get_body_weight(30),
    })


@app.route("/api/gym/weight", methods=["POST"])
def api_log_weight():
    kg = float(request.get_json(force=True).get("weight_kg", 0))
    if not 20 < kg < 300:
        return jsonify({"error": "invalid weight"}), 400
    return jsonify({"ok": True, "message": _do_body_weight(kg)})


# ── Money ──────────────────────────────────────────────────────────────────────

@app.route("/api/money")
def api_money():
    days = int(request.args.get("days", 7))
    spending = db.get_spending(days)
    by_cat = {}
    for s in spending:
        by_cat[s["category"]] = round(by_cat.get(s["category"], 0) + s["amount"], 2)
    monthly = db.get_spending(30)
    return jsonify({
        "spending": spending,
        "total": round(sum(s["amount"] for s in spending), 2),
        "monthly_total": round(sum(s["amount"] for s in monthly), 2),
        "by_category": by_cat,
    })


@app.route("/api/money", methods=["POST"])
def api_log_spend():
    d = request.get_json(force=True)
    amount = float(d.get("amount", 0))
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400
    db.log_spend(_today(), amount, (d.get("category") or "other").lower(), d.get("note", ""))
    return jsonify({"ok": True})


# ── Bots / news / weather ──────────────────────────────────────────────────────

@app.route("/api/bots")
def api_bots():
    return jsonify(get_bots_status())


@app.route("/api/news")
def api_news():
    return jsonify({"top": get_top_news(), "finance": get_finance_news()})


@app.route("/api/weather")
def api_weather():
    return jsonify({"current": get_weather(), "forecast": get_forecast()})


# ── Daily score ────────────────────────────────────────────────────────────────

@app.route("/api/score")
def api_score():
    result = ai.compute_daily_score()
    result["history"] = db.get_daily_scores(7)
    return jsonify(result)


# ── Gmail & Calendar ───────────────────────────────────────────────────────────

@app.route("/api/emails")
def api_emails():
    if not is_authenticated():
        return jsonify({"connected": False, "emails": [], "suggested_events": []})
    emails = get_unread_emails()
    emails = ai.summarise_emails(emails)
    suggestions = ai.detect_events_in_emails(emails)
    return jsonify({"connected": True, "emails": emails, "suggested_events": suggestions})


@app.route("/api/calendar")
def api_calendar():
    if not is_authenticated():
        return jsonify({"connected": False, "today": [], "tomorrow": []})
    return jsonify({
        "connected": True,
        "today": get_todays_events(),
        "tomorrow": get_tomorrow_events(),
    })


@app.route("/api/calendar", methods=["POST"])
def api_add_event():
    d = request.get_json(force=True)
    result = add_event(d["title"], d["start"], d["end"],
                       d.get("description", ""), d.get("location", ""))
    status = 400 if "error" in result else 200
    return jsonify(result), status


@app.route("/auth/google")
def auth_google():
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI") or url_for("oauth_callback", _external=True)
    print("EXACT REDIRECT URI:", redirect_uri, flush=True)
    if redirect_uri.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # local dev only
    flow = get_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true")
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    flow = get_flow(url_for("oauth_callback", _external=True))
    try:
        flow.fetch_token(authorization_response=request.url)
        save_credentials(flow.credentials)
    except Exception as e:
        return f"OAuth error: {e}", 400
    return redirect("/")


@app.route("/auth/status")
def auth_status():
    return jsonify({"google_connected": is_authenticated()})


# ── Reflections, goals, memory, ideas ─────────────────────────────────────────

@app.route("/api/reflection", methods=["GET", "POST"])
def api_reflection():
    if request.method == "POST":
        d = request.get_json(force=True)
        db.save_reflection(_today(), int(d.get("score", 5)), d.get("content", ""))
        return jsonify({"ok": True})
    return jsonify(db.get_reflections(7))


@app.route("/api/goals", methods=["GET", "POST"])
def api_goals():
    if request.method == "POST":
        d = request.get_json(force=True)
        db.add_goal(d["title"], d.get("target", ""))
        return jsonify({"ok": True})
    return jsonify(db.get_goals())


@app.route("/api/goals/<int:goal_id>", methods=["PATCH"])
def api_update_goal(goal_id):
    progress = int(request.get_json(force=True).get("progress", 0))
    db.update_goal_progress(goal_id, max(0, min(100, progress)))
    return jsonify({"ok": True})


@app.route("/api/memories", methods=["GET", "POST"])
def api_memories():
    if request.method == "POST":
        db.save_memory(request.get_json(force=True)["content"])
        return jsonify({"ok": True})
    return jsonify(db.get_memories(20))


@app.route("/api/ideas", methods=["GET", "POST"])
def api_ideas():
    if request.method == "POST":
        db.save_idea(request.get_json(force=True)["content"])
        return jsonify({"ok": True})
    return jsonify(db.get_ideas())


@app.route("/api/notes", methods=["POST"])
def api_notes():
    db.save_voice_note(request.get_json(force=True)["content"])
    return jsonify({"ok": True})


# ── Weekly review ──────────────────────────────────────────────────────────────

@app.route("/api/review")
def api_review():
    cached = db.kv_get("weekly_review")
    if cached and request.args.get("refresh") != "1":
        return jsonify(json.loads(cached))
    review = ai.generate_weekly_review()
    payload = {"date": _today(), "content": review}
    db.kv_set("weekly_review", json.dumps(payload))
    return jsonify(payload)


# ── Photo logging (vision) ─────────────────────────────────────────────────────

@app.route("/api/photo", methods=["POST"])
def api_photo():
    if "photo" in request.files:
        f = request.files["photo"]
        mime = f.mimetype or "image/jpeg"
        b64 = base64.standard_b64encode(f.read()).decode()
    else:
        d = request.get_json(force=True)
        b64 = d.get("image", "")
        mime = d.get("mime_type", "image/jpeg")
    if not b64:
        return jsonify({"error": "no image"}), 400
    analysis = ai.analyse_photo(b64, mime)
    return jsonify({"analysis": analysis})


@app.route("/api/photo/confirm", methods=["POST"])
def api_photo_confirm():
    d = request.get_json(force=True)
    kind = d.get("type")
    if kind == "receipt" and d.get("amount"):
        db.log_spend(_today(), float(d["amount"]), d.get("category", "other"), d.get("note", "from photo"))
        return jsonify({"ok": True, "message": f"Logged £{float(d['amount']):.2f} spend."})
    if kind == "workout" and d.get("exercise"):
        is_pb = db.log_workout(_today(), d["exercise"].title(), float(d.get("weight_kg") or 0),
                               int(d.get("reps") or 0), int(d.get("sets") or 1),
                               d.get("muscle_group") or _guess_muscle(d["exercise"]))
        return jsonify({"ok": True, "is_pb": bool(is_pb), "message": "Workout logged."})
    if kind == "meal":
        db.save_voice_note(f"Meal photo: {d.get('note', '')}")
        return jsonify({"ok": True, "message": "Meal noted."})
    return jsonify({"error": "unknown type"}), 400


# ── Notifications ──────────────────────────────────────────────────────────────

@app.route("/api/notifications")
def api_notifications():
    notifications = db.get_notifications(15)
    unread = len([n for n in notifications if not n["is_read"]])
    return jsonify({"notifications": notifications, "unread": unread})


@app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    db.mark_notifications_read()
    return jsonify({"ok": True})


# ── Background services (started once, even under gunicorn) ───────────────────

def _start_background():
    if os.environ.get("ASFA_BG_STARTED"):
        return
    os.environ["ASFA_BG_STARTED"] = "1"
    from services.scheduler import start_scheduler
    from services.telegram_bot import start_bot
    start_scheduler()
    start_bot()


_start_background()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, use_reloader=False)
