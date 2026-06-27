"""ASFA — AI Software For Amir. JARVIS-style life command centre."""
import base64
import hmac
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

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import database as db
from services import ai
from services.bots import get_bots_health, get_bots_status, get_trading_activity
from services.briefing import build_briefing
from services.gcal import add_event, get_todays_events, get_tomorrow_events
from services.gmail import (get_email_by_id, get_flow, get_unread_emails,
                            is_authenticated, save_credentials)
from services.news import get_finance_news, get_top_news
from services.obsidian_sync import OBSIDIAN_VAULT_PATH, sync_to_obsidian
from services import spotify
from services.weather import get_forecast, get_weather

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("asfa")

app = Flask(__name__)

# Session signing key. Never fall back to a hardcoded value — a predictable
# secret lets anyone forge session cookies. Use the env var if set; otherwise
# generate a random ephemeral key (single gunicorn worker, so it's stable for
# the process lifetime) and warn loudly so prod gets a persistent one set.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _secret = base64.urlsafe_b64encode(os.urandom(32)).decode()
    logger.warning(
        "SECRET_KEY not set — using a random ephemeral key. Sessions will reset "
        "on restart. Set SECRET_KEY in the environment for production.")
app.secret_key = _secret
app.config["PREFERRED_URL_SCHEME"] = "https"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("GOOGLE_REDIRECT_URI", "").startswith("https")

# ── App access gate ────────────────────────────────────────────────────────────
# The dashboard exposes personal Gmail/Calendar/finance data, so the whole app
# sits behind a single shared passphrase (APP_PASSWORD). Google/Spotify OAuth
# only authorises the *server* to reach those accounts — it does not gate users.
APP_PASSWORD = os.environ.get("APP_PASSWORD")
# Endpoints reachable without a session. Everything else requires login.
_PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if session.get("authed"):
        return None
    # Fail closed: if no passphrase is configured the app stays locked.
    if not APP_PASSWORD:
        logger.error("APP_PASSWORD not set — app is locked. Set it to enable access.")
        if request.path.startswith("/api/"):
            return jsonify({"error": "app auth not configured"}), 503
        return "ASFA is locked: set APP_PASSWORD in the environment.", 503
    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or "/"
    # Only allow same-site relative paths as the post-login redirect target.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if request.method == "POST":
        pw = request.form.get("password") or ""
        if APP_PASSWORD and hmac.compare_digest(pw, APP_PASSWORD):
            session["authed"] = True
            session.permanent = True
            return redirect(next_url)
        return render_template("login.html", error="Incorrect passphrase.", next_url=next_url), 401
    return render_template("login.html", error=None, next_url=next_url)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))

# Create tables if missing. Idempotent (CREATE TABLE IF NOT EXISTS) and handles
# the Postgres/SQLite difference, so it's safe to run on every boot. Critical on
# a fresh Railway Postgres where no tables exist yet.
db.init_db()
# Mission Control — create the agent ecosystem tables and seed the roster.
db.init_agents_db()


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        google_connected=is_authenticated(),
        spotify_connected=spotify.is_connected(),
    )


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
    # Validate the OAuth state to prevent CSRF / authorization-code injection:
    # the `state` Google returns must match the one we stored at /auth/google.
    expected = session.pop("oauth_state", None)
    returned = request.args.get("state")
    if not expected or not returned or not hmac.compare_digest(returned, expected):
        logger.warning("OAuth callback rejected: state mismatch.")
        return "Invalid OAuth state.", 400
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


# ── Spotify OAuth + playback ───────────────────────────────────────────────────

@app.route("/auth/spotify")
def auth_spotify():
    if not spotify.is_configured():
        return "Spotify is not configured on the server.", 400
    state = base64.urlsafe_b64encode(os.urandom(16)).decode()
    session["spotify_oauth_state"] = state
    return redirect(spotify.get_auth_url(state))


@app.route("/auth/spotify/callback")
def auth_spotify_callback():
    if request.args.get("error"):
        return redirect("/")
    state = request.args.get("state")
    if not state or state != session.get("spotify_oauth_state"):
        return "Invalid OAuth state.", 400
    code = request.args.get("code")
    if not code or not spotify.exchange_code(code):
        return "Spotify authorization failed.", 400
    logger.info("Spotify connected.")
    return redirect("/")


@app.route("/auth/spotify/disconnect", methods=["POST"])
def auth_spotify_disconnect():
    spotify.disconnect()
    return jsonify({"ok": True})


@app.route("/api/asfa/spotify/status")
def api_spotify_status():
    """Connection + current-playback snapshot for the dashboard indicator."""
    return jsonify(spotify.current_playback())


@app.route("/api/asfa/spotify/play")
def api_spotify_play():
    """Resume playback on the user's active/default device. Always 200 so the
    frontend can surface the friendly message regardless of outcome."""
    return jsonify(spotify.resume_playback())


@app.route("/api/asfa/spotify/focus")
def api_spotify_focus():
    """Start a mood playlist by search query (Think Mode ambient / Lock In focus)."""
    query = request.args.get("q", "deep focus")
    return jsonify(spotify.play_query(query))


# ── Focus: "What now?" line + Lock In sessions ─────────────────────────────────

@app.route("/api/asfa/focus-line")
def api_focus_line():
    """One prioritised sentence for the top of the dashboard.
    Priority: supplements (past 9am) > unreplied emails > water > all clear."""
    text, urgent = None, False

    # 1. Supplements not logged once it's past 09:00 local.
    if datetime.now().hour >= 9:
        try:
            if db.count_supplements_today(_today()) < len(db.SUPPLEMENTS):
                text, urgent = "You haven't logged your supplements yet.", True
        except Exception:
            pass

    # 2. Unread emails waiting on a reply.
    if not text and is_authenticated():
        try:
            emails = [e for e in get_unread_emails() if "error" not in e]
            n = len(emails)
            if n > 0:
                text = f"{n} email{'s' if n != 1 else ''} waiting on your reply."
                urgent = n >= 3
        except Exception:
            pass

    # 3. No water logged today. Read straight from the DB so logging via
    #    /api/asfa/water-intake is reflected immediately (no stale habits row).
    if not text:
        try:
            if db.get_water_logged(_today()) <= 0:
                text = "You haven't logged any water today."
        except Exception:
            pass

    # 4. Nothing pressing.
    if not text:
        text = "All clear. Nice work."

    return jsonify({"text": text, "urgent": urgent})


# ── Trading systems health + validation countdown ─────────────────────────────

@app.route("/api/asfa/bots-health")
def api_bots_health():
    """Per-bot alive/status/last-signal (cached ~60s server-side)."""
    return jsonify(get_bots_health())


# ── Obsidian sync (local markdown daily logs) ──────────────────────────────────

@app.route("/api/asfa/obsidian/sync-now")
def api_obsidian_sync():
    """Write the full ASFA vault tree to OBSIDIAN_VAULT_PATH. Only writes when
    ASFA runs on a machine with that local folder (i.e. the Mac, not Railway)."""
    result = sync_to_obsidian()
    result.setdefault("vault", OBSIDIAN_VAULT_PATH)
    return jsonify(result)


@app.route("/api/asfa/obsidian/open", methods=["POST"])
def api_obsidian_open():
    """Open the Obsidian vault in the desktop app. macOS/local only — fails
    gracefully on cloud (Railway) where there's no GUI / `open` binary."""
    import shutil
    import subprocess
    if not shutil.which("open"):
        return jsonify({"status": "error",
                        "message": "Open the vault from your Mac — no GUI here."}), 200
    try:
        subprocess.Popen(["open", "-a", "Obsidian", OBSIDIAN_VAULT_PATH])
        return jsonify({"status": "opened", "vault": OBSIDIAN_VAULT_PATH})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:120]}), 200


# Forward-validation window for both bots. Configurable; default targets the
# ~6-week (42-day) mark from mid-June 2026.
VALIDATION_START_DATE = os.environ.get("VALIDATION_START_DATE", "2026-06-16")
VALIDATION_DAYS = 42


@app.route("/api/asfa/validation")
def api_validation():
    try:
        start = datetime.strptime(VALIDATION_START_DATE, "%Y-%m-%d").date()
    except ValueError:
        start = datetime(2026, 6, 16).date()
    total = VALIDATION_DAYS
    today = datetime.now().date()
    raw_day = (today - start).days + 1          # day 1 on the start date itself
    complete = raw_day > total
    day = max(0, min(raw_day, total))
    pct = round((day / total) * 100) if total else 0
    return jsonify({
        "start": start.isoformat(),
        "end": (start + timedelta(days=total)).isoformat(),
        "day": day,
        "total": total,
        "pct": max(0, min(100, pct)),
        "complete": complete,
        "not_started": raw_day < 1,
    })


@app.route("/api/asfa/focus/today")
def api_focus_today():
    return jsonify({"focus_seconds_today": db.get_focus_seconds_today(_today())})


@app.route("/api/asfa/focus/session", methods=["POST"])
def api_focus_session():
    """Log a completed Lock In session. Body: {duration_seconds}. The server
    timestamps it (now - duration → now) so the day-rollover stays consistent."""
    d = request.get_json(force=True) or {}
    try:
        dur = int(d.get("duration_seconds", 0))
    except (TypeError, ValueError):
        dur = 0
    if dur > 0:
        ended = datetime.now()
        started = ended - timedelta(seconds=dur)
        db.log_focus_session(started.isoformat(), ended.isoformat(), dur)
    return jsonify({"ok": True, "focus_seconds_today": db.get_focus_seconds_today(_today())})


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
    streak = 0
    try:
        streak = db.get_supplements_streak()
    except Exception:
        pass
    return {"items": items, "taken_count": len(taken), "total": len(db.SUPPLEMENTS),
            "streak": streak}


@app.route("/api/supplements", methods=["GET", "POST"])
def api_supplements():
    """GET → today's checklist status. POST {name, taken} → log/undo a supplement.
    Naturally resets each day since status is filtered by today's date."""
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        name = (d.get("name") or "").lower()
        if name not in {k for k, _ in db.SUPPLEMENTS}:
            return jsonify({"error": "unknown supplement",
                            "valid": [k for k, _ in db.SUPPLEMENTS]}), 400
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


# ── Mission Control — gamified AI-agent ecosystem ──────────────────────────────

def _mc_live_data() -> dict:
    """Real ASFA data surfaced on the Mission Control dashboard. Every source is
    best-effort; the last good trading P&L is cached so the panel still shows a
    value when the scanner is unreachable."""
    today = _today()

    # Water today
    water_ml = 0
    try:
        habits = db.get_habits(1)
        today_h = next((h for h in habits if h["date"] == today), {})
        water_ml = int(today_h.get("water_ml") or 0)
    except Exception as e:
        logger.warning("mc live water failed: %s", e)

    # Supplements
    supp_taken, supp_total = 0, len(db.SUPPLEMENTS)
    try:
        supp_taken = db.count_supplements_today(today)
    except Exception as e:
        logger.warning("mc live supplements failed: %s", e)

    # Trading P&L (live, with last-cached fallback)
    trading = {"online": False, "pnl": None, "pnl_pct": None, "equity": None,
               "signals": 0, "cached": False}
    try:
        activity = get_trading_activity()
        portfolio = (activity or {}).get("portfolio") or {}
        if activity.get("online") and portfolio:
            trading.update(
                online=True,
                pnl=portfolio.get("total_pnl"),
                pnl_pct=portfolio.get("total_pnl_pct"),
                equity=portfolio.get("equity"),
                signals=1 if activity.get("latest_signal") else 0,
            )
            db.kv_set("mc_last_trading", json.dumps({
                "pnl": trading["pnl"], "pnl_pct": trading["pnl_pct"],
                "equity": trading["equity"], "signals": trading["signals"],
            }))
        else:
            raise ValueError("trading offline")
    except Exception:
        cached = db.kv_get("mc_last_trading")
        if cached:
            try:
                c = json.loads(cached)
                trading.update(pnl=c.get("pnl"), pnl_pct=c.get("pnl_pct"),
                               equity=c.get("equity"), signals=c.get("signals", 0),
                               cached=True)
            except (TypeError, ValueError):
                pass

    return {
        "water_ml": water_ml,
        "water_target": 2000,
        "supplements_taken": supp_taken,
        "supplements_total": supp_total,
        "trading": trading,
        "uptime": "ONLINE",
        "time": datetime.now().isoformat(),
    }


def _mc_alerts(agents: list, live: dict) -> list:
    """Up to 3 attention items pulled from real ASFA state + agent readiness."""
    alerts = []
    if live["supplements_taken"] < live["supplements_total"]:
        alerts.append({"level": "warn", "text": "Supplements not logged today"})
    if live["water_ml"] < 500:
        alerts.append({"level": "warn", "text": "Water intake low — log some water"})
    # Any agent within 10% of its next level → ready to level up.
    for a in agents:
        if a["status"] == "locked":
            continue
        xp_max = a.get("xp_max") or 1
        if xp_max and a["xp"] >= 0.9 * xp_max:
            alerts.append({"level": "info", "text": f"{a['name']} ready to level up"})
            break
    # Trading/deployment health — surfaced as a deployment issue when offline.
    if not live["trading"]["online"]:
        alerts.append({"level": "crit", "text": "Railway deployment issue detected"})
    return alerts[:3]


@app.route("/mission-control")
def mission_control():
    return render_template("mission_control.html")


@app.route("/api/mission-control/health")
def mission_control_health():
    """System health for the Mission Control facility view.

    power        — static placeholder, always OK for now.
    connectivity — OK if Polygon.io marketstatus returns 200; WARN if reachable
                   but non-200 (e.g. missing/invalid key, rate-limited); FAIL on
                   network error or if the database probe fails.
    security     — CRIT if any critical Sentinel alert in the last 24h, WARN if
                   only warnings, else OK.
    """
    details = {
        "polygon_api": "FAIL",
        "database": "FAIL",
        "sentinel_alerts": 0,
        "sentinel_critical": False,
    }

    # Database — SELECT 1
    db_ok = db.ping()
    details["database"] = "OK" if db_ok else "FAIL"

    # Connectivity — probe Polygon.io marketstatus
    connectivity = "FAIL"
    try:
        poly_key = os.environ.get("POLYGON_API_KEY", "")
        resp = requests.get(
            "https://api.polygon.io/v1/marketstatus/now",
            params={"apiKey": poly_key} if poly_key else None,
            timeout=5,
        )
        if resp.status_code == 200:
            details["polygon_api"] = "OK"
            connectivity = "OK"
        else:
            # Reached Polygon but the call didn't succeed (auth/rate-limit/etc.)
            details["polygon_api"] = "FAIL"
            connectivity = "WARN"
    except requests.RequestException as e:
        logger.warning("Polygon connectivity check failed: %s", e)
        details["polygon_api"] = "FAIL"
        connectivity = "FAIL"

    # A dead database means we can't trust connectivity either.
    if not db_ok:
        connectivity = "FAIL"

    # Security — recent Sentinel alerts
    security = "OK"
    if db_ok:
        try:
            alerts = db.count_recent_alerts(hours=24)
            details["sentinel_alerts"] = alerts["critical"] + alerts["warning"]
            if alerts["critical"] > 0:
                security = "CRIT"
                details["sentinel_critical"] = True
            elif alerts["warning"] > 0:
                security = "WARN"
        except Exception as e:
            logger.warning("Sentinel alert check failed: %s", e)

    return jsonify({
        "power": "OK",
        "connectivity": connectivity,
        "security": security,
        "details": details,
    })


@app.route("/api/agents")
def api_agents():
    agents = db.get_agents()
    live = _mc_live_data()
    return jsonify({
        "agents": agents,
        "live": live,
        "alerts": _mc_alerts(agents, live),
    })


@app.route("/api/agents/<agent_id>/xp", methods=["POST"])
def api_agent_xp(agent_id):
    d = request.get_json(force=True) or {}
    try:
        amount = int(d.get("amount", 50))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be an integer"}), 400
    message = d.get("message") or f"+{amount} XP awarded"
    result = db.award_agent_xp(agent_id, amount, message)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/agents/<agent_id>/status", methods=["POST"])
def api_agent_status(agent_id):
    """Toggle active⇄idle, or set an explicit status when one is provided."""
    d = request.get_json(silent=True) or {}
    status = d.get("status")
    if status in ("active", "idle", "locked"):
        agent = db.set_agent_status(agent_id, status)
    else:
        agent = db.toggle_agent_status(agent_id)
    if not agent or "error" in (agent or {}):
        return jsonify({"error": "unknown agent"}), 404
    return jsonify(agent)


@app.route("/api/agents/<agent_id>/log", methods=["GET", "POST"])
def api_agent_log(agent_id):
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        message = (d.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message required"}), 400
        xp_earned = 0
        try:
            xp_earned = int(d.get("xp_earned", 0))
        except (TypeError, ValueError):
            xp_earned = 0
        db.add_agent_log(agent_id, message, xp_earned)
        return jsonify({"ok": True, "log": db.get_agent_log(agent_id, 20)})
    return jsonify(db.get_agent_log(agent_id, 20))


@app.route("/api/battles", methods=["POST"])
def api_battles():
    d = request.get_json(force=True) or {}
    a1, a2 = d.get("agent1_id"), d.get("agent2_id")
    winner = d.get("winner_id")
    topic = (d.get("topic") or "head-to-head").strip()
    if not a1 or not a2 or not winner:
        return jsonify({"error": "agent1_id, agent2_id and winner_id required"}), 400
    if winner not in (a1, a2):
        return jsonify({"error": "winner must be one of the two combatants"}), 400
    return jsonify(db.create_battle(a1, a2, topic, winner))


@app.route("/api/missions/today")
def api_missions_today():
    return jsonify(db.get_today_missions())


@app.route("/api/missions/<int:mission_id>/complete", methods=["POST"])
def api_mission_complete(mission_id):
    result = db.complete_mission(mission_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ── Scout — part-time job hunter ───────────────────────────────────────────────

@app.route("/scout")
def scout_page():
    return render_template("scout.html")


@app.route("/api/scout/jobs")
def api_scout_jobs():
    location = request.args.get("location") or None
    new_only = request.args.get("new_only") == "true"
    return jsonify(db.get_scout_jobs(location=location, new_only=new_only))


@app.route("/api/scout/scan")
def api_scout_scan():
    """Trigger a manual Indeed scrape. Always returns 200 with a count so the
    frontend gets valid JSON even if the scrape is blocked/empty."""
    from services import scout
    try:
        count = scout.scan()
    except Exception as e:
        logger.error("scout scan failed: %s", e)
        return jsonify({"new_jobs": 0, "error": str(e)[:200]})
    return jsonify({"new_jobs": count})


@app.route("/api/scout/apply", methods=["POST"])
def api_scout_apply():
    d = request.get_json(force=True) or {}
    job_id = d.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    db.mark_scout_job_applied(job_id)
    return jsonify({"ok": True})


@app.route("/api/scout/applications", methods=["GET", "POST"])
def api_scout_applications():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        company = (d.get("company") or "").strip()
        role = (d.get("role") or "").strip()
        if not company or not role:
            return jsonify({"error": "company and role required"}), 400
        db.add_scout_application(
            company=company,
            role=role,
            location=d.get("location", ""),
            method=d.get("method", ""),
            applied_date=d.get("applied_date") or _today(),
            status=d.get("status", "pending"),
            notes=d.get("notes", ""),
        )
        return jsonify({"ok": True})
    return jsonify(db.get_scout_applications())


@app.route("/api/scout/applications/<int:app_id>", methods=["PUT"])
def api_scout_application_update(app_id):
    d = request.get_json(force=True) or {}
    status = (d.get("status") or "").strip()
    if not status:
        return jsonify({"error": "status required"}), 400
    db.update_scout_application_status(app_id, status, d.get("notes"))
    return jsonify({"ok": True})


@app.route("/api/scout/applications/<int:app_id>", methods=["DELETE"])
def api_scout_application_delete(app_id):
    db.delete_scout_application(app_id)
    return jsonify({"ok": True})


def scout_daily_scan():
    """06:00 daily — scrape Indeed for new part-time roles and ping the bell."""
    try:
        from services import scout
        count = scout.scan()
        logger.info("Scout daily scan: %d new jobs", count)
        if count:
            db.add_notification(
                f"🔎 Scout found {count} new job{'s' if count != 1 else ''}.", "scout")
    except Exception as e:
        logger.error("scout daily scan failed: %s", e)


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
    sched = start_scheduler()
    # Scout job scan, daily at 06:00 (registered here to keep the scout feature
    # self-contained without editing services/scheduler.py).
    try:
        sched.add_job(scout_daily_scan, "cron", hour=6, minute=0, id="scout_daily_scan")
    except Exception as e:
        logger.error("failed to register scout daily scan: %s", e)
    start_bot()
    threading.Thread(target=_generate_startup_briefing, daemon=True).start()


_start_background()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True, use_reloader=False)
