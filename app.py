"""ASFA — AI Software For Amir. JARVIS-style life command centre."""
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
# Google often returns scopes in a different order / adds `openid`, which makes
# oauthlib raise "Scope has changed". Relaxing this is the standard fix and is
# safe — we still only ever request the SCOPES we ask for.
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import database as db
from services import ai
from services.bots import get_bots_status, get_trading_activity
from services.briefing import build_briefing
from services.gcal import add_event, get_todays_events, get_tomorrow_events
from services.gmail import (get_email_by_id, get_flow, get_unread_emails,
                            is_authenticated, save_credentials)
from services.news import get_finance_news, get_top_news
from services.weather import get_forecast, get_weather

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("asfa")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "asfa-dev-secret-change-me")
app.config["PREFERRED_URL_SCHEME"] = "https"

# db.init_db()


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
    (re.compile(r"\bspent\s+[£$]?(\d+(?:\.\d{1,2})?)\s+(?:on\s+)?([a-zA-Z][\w ]{1,40})", re.I),
     lambda m: _do_spend(float(m.group(1)), m.group(2).strip())),
    (re.compile(r"\bremember\s+(?:that\s+)?(.{4,})", re.I),
     lambda m: _do_memory(m.group(1).strip())),
]

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


# ── Body / gym (PBs + body weight only — workout logging removed) ──────────────

@app.route("/api/gym")
def api_gym():
    return jsonify({
        "pbs": db.get_pbs(),
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


# ── ASFA: water / hydration intake ────────────────────────────────────────────

@app.route("/api/asfa/water-intake", methods=["POST"])
def api_water_intake():
    """Log a hydration entry. Body: {"amount": <ml int>, "timestamp": <iso?>}.
    Writes the hydration_log ledger AND the rolled-up habits total so the gauge,
    daily score, and briefing all stay in sync. Returns the updated daily total."""
    d = request.get_json(force=True) or {}
    try:
        amount = int(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be an integer (ml)"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    when = datetime.now()
    ts_raw = d.get("timestamp")
    if ts_raw:
        try:
            when = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            pass  # fall back to now() on an unparseable timestamp
    date = when.strftime("%Y-%m-%d")

    db.log_hydration(date, amount, when.isoformat())
    db.log_water(date, amount)  # keep habits gauge / score / briefing consistent
    db.kv_set("last_water_ts", datetime.now().isoformat())

    return jsonify({
        "ok": True,
        "amount": amount,
        "total_ml": db.get_hydration_total(date),
        "target_ml": 2000,
        "streak": db.get_water_streak(),
    })


# ── ASFA: email draft generator ───────────────────────────────────────────────

@app.route("/api/asfa/draft-reply", methods=["POST"])
def api_draft_reply():
    """Compose (but never send) a professional reply to an email."""
    if not is_authenticated():
        return jsonify({"error": "Gmail not connected"}), 400
    d = request.get_json(force=True)
    email_id = d.get("email_id")
    if not email_id:
        return jsonify({"error": "email_id required"}), 400
    email = get_email_by_id(email_id)
    if "error" in email:
        return jsonify({"error": email["error"]}), 502
    draft = ai.draft_reply(email)
    return jsonify({
        "draft": draft,
        "subject": email.get("subject", ""),
        "to": email.get("from", ""),
    })


# ── ASFA: trading-bot status ──────────────────────────────────────────────────

@app.route("/api/asfa/bot-status")
def api_bot_status():
    """Live trading snapshot for the briefing card. Always returns dashboard
    links; adds live stats when the stock-scanner app is reachable."""
    return jsonify(get_trading_activity())


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


# Canonical callback path is /auth/google/callback. This MUST match exactly
# (scheme, host, path, no trailing slash) what's set in BOTH the Railway
# GOOGLE_REDIRECT_URI env var AND the Google Cloud Console "Authorized redirect
# URIs". The default below is the production URL.
_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "https://asfa-production.up.railway.app/auth/google/callback",
)


@app.route("/auth/google")
def auth_google():
    logger.info("OAuth start — redirect_uri=%s", _REDIRECT_URI)
    flow = get_flow(_REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true")
    session["oauth_state"] = state
    return redirect(auth_url)


# Primary callback + backward-compatible alias for the old /oauth/callback path.
@app.route("/auth/google/callback")
@app.route("/oauth/callback")
def oauth_callback():
    # Use the SAME configured redirect_uri that auth_google sent to Google — it
    # must match exactly at token exchange. (request.base_url can arrive as http
    # behind Railway's TLS proxy and would mismatch the https URI.)
    flow = get_flow(_REDIRECT_URI)
    # Behind the proxy the inbound URL may be http; force https so the `code`
    # exchange's redirect_uri comparison lines up with what Google issued.
    auth_response = request.url.replace("http://", "https://", 1)
    try:
        flow.fetch_token(authorization_response=auth_response)
        save_credentials(flow.credentials)
    except Exception as e:
        logger.error("OAuth callback failed (redirect_uri=%s): %s", _REDIRECT_URI, e)
        return f"OAuth error: {e}", 400
    logger.info("OAuth success — credentials saved.")
    return redirect("/")


@app.route("/auth/status")
def auth_status():
    return jsonify({"google_connected": is_authenticated()})


# ── Reflections, goals, memory ─────────────────────────────────────────────────

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


@app.route("/api/notes", methods=["POST"])
def api_notes():
    db.save_voice_note(request.get_json(force=True)["content"])
    return jsonify({"ok": True})


# ── Supplements ────────────────────────────────────────────────────────────────

def _supplements_status():
    taken = db.get_supplements_today(_today())
    items = [{"name": key, "label": label, "taken": key in taken, "taken_at": taken.get(key)}
             for key, label in db.SUPPLEMENTS]
    return {"items": items, "taken_count": len(taken), "total": len(db.SUPPLEMENTS)}


@app.route("/api/supplements", methods=["GET", "POST"])
def api_supplements():
    """GET → today's checklist status. POST {name, taken} → log/undo a supplement.
    Naturally resets each day since status is filtered by today's date."""
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        name = (d.get("name") or "").lower()
        if name not in {k for k, _ in db.SUPPLEMENTS}:
            return jsonify({"error": "unknown supplement"}), 400
        if d.get("taken", True):
            # One log per supplement per day.
            if name not in db.get_supplements_today(_today()):
                db.log_supplement(name)
        else:
            db.remove_supplement_today(name, _today())
    return jsonify(_supplements_status())


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

def _generate_startup_briefing():
    """Warm the morning briefing once at boot so the home page has it ready.
    Runs in a thread — a slow AI/Gmail call must not block app startup."""
    try:
        build_briefing(force=True)
        logger.info("Startup briefing generated.")
    except Exception as e:
        logger.error(f"startup briefing failed: {e}")


def _start_background():
    if os.environ.get("ASFA_BG_STARTED"):
        return
    os.environ["ASFA_BG_STARTED"] = "1"
    import threading

    from services.scheduler import start_scheduler
    from services.telegram_bot import start_bot
    start_scheduler()
    start_bot()
    threading.Thread(target=_generate_startup_briefing, daemon=True).start()


_start_background()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, use_reloader=False)
