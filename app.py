"""ASFA — AI Software For Amir. JARVIS-style life command centre."""
import base64
import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ── Critical environment validation ─────────────────────────────────────────────
# Fail fast in production: a missing APP_PASSWORD (the only access gate) or
# DATABASE_URL (prod Postgres) should crash the boot loudly rather than silently
# degrade. We only hard-exit in prod — locally the app intentionally runs on
# SQLite with no APP_PASSWORD (it just stays locked), so we warn instead of
# exiting to keep `python app.py` working. POLYGON_API_KEY is optional (one
# graceful-fallback feature), so it's always a warning, never fatal.
_IS_PROD = bool(
    os.getenv("RAILWAY_ENVIRONMENT")
    or os.getenv("RAILWAY_PROJECT_ID")
    or os.getenv("RAILWAY_SERVICE_ID")
)
_REQUIRED_PROD = ["APP_PASSWORD", "DATABASE_URL"]
_WARN_OPTIONAL = ["POLYGON_API_KEY"]

_missing_required = [v for v in _REQUIRED_PROD if not os.getenv(v)]
_missing_optional = [v for v in _WARN_OPTIONAL if not os.getenv(v)]

if _IS_PROD and _missing_required:
    print(
        "FATAL: Missing required environment variables: "
        f"{', '.join(_missing_required)}",
        file=sys.stderr,
    )
    sys.exit(1)

if not _IS_PROD and _missing_required:
    print(
        "WARNING: Missing env vars (ok for local SQLite dev, REQUIRED in prod): "
        f"{', '.join(_missing_required)}",
        file=sys.stderr,
    )
if _missing_optional:
    print(
        "WARNING: Missing optional env vars (features degrade gracefully): "
        f"{', '.join(_missing_optional)}",
        file=sys.stderr,
    )

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
# Google often returns scopes in a different order / adds `openid`, which makes
# oauthlib raise "Scope has changed". Relaxing this is the standard fix and is
# safe — we still only ever request the SCOPES we ask for.
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

import database as db
from flask_limiter.util import get_remote_address

from services import ai
from services import telegram_bot
from services.bots import get_bots_health, get_bots_status, get_trading_activity
from services.briefing import build_briefing
from services.gcal import add_event, get_todays_events, get_tomorrow_events
from services.gmail import (get_email_by_id, get_flow, get_unread_emails,
                            is_authenticated, save_credentials)
from services.news import get_finance_news, get_top_news
from services.obsidian_sync import OBSIDIAN_VAULT_PATH, sync_to_obsidian
from services.security import init_rate_limiter
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
# Secure cookies whenever we're on Railway (TLS-terminated) or the OAuth
# redirect says we're serving https; stays False for local http dev.
app.config["SESSION_COOKIE_SECURE"] = (
    _IS_PROD or os.environ.get("GOOGLE_REDIRECT_URI", "").startswith("https")
)

# Jinja autoescaping guards against XSS in server-rendered templates. It's ON by
# default for .html in Flask; assert it so an accidental future override fails
# loudly at boot rather than silently opening an injection hole.
assert app.jinja_env.autoescape, "Jinja autoescape is OFF — XSS vulnerability!"

# Rate limiting, tiered per request: the owner's authenticated dashboard reads
# get a generous budget, while anonymous traffic and all writes keep the strict
# budget (see services/security.py). The /login route overrides these with its
# own tighter explicit limit below, and authenticated /api/gym/* is exempted via
# a request_filter further down. In-memory storage suits our single worker.
limiter = init_rate_limiter(app)

# ── App access gate ────────────────────────────────────────────────────────────
# The dashboard exposes personal Gmail/Calendar/finance data, so the whole app
# sits behind a single shared passphrase (APP_PASSWORD). Google/Spotify OAuth
# only authorises the *server* to reach those accounts — it does not gate users.
APP_PASSWORD = os.environ.get("APP_PASSWORD")
# Endpoints reachable without a session. Everything else requires login.
_PUBLIC_ENDPOINTS = {"login", "static", "mission_control_health", "api_system_health"}


@app.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if session.get("authed"):
        # Sessions created before CSRF protection shipped have no token yet;
        # mint one on their next (page-load) request so the meta tag and the
        # header check below agree.
        if not session.get("csrf_token"):
            session["csrf_token"] = secrets.token_hex(32)
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


# ── CSRF protection ────────────────────────────────────────────────────────────
# Every state-changing request must echo the per-session token in the
# X-CSRF-Token header (see templates/_csrf.html, which patches window.fetch so
# all existing frontend calls send it). Runs AFTER the auth gate (registration
# order), so unauthenticated writes are already 401 before this fires. /login
# is exempt — it's the request that creates the token. Background jobs
# (APScheduler) and the Telegram bot call Python functions directly, never
# HTTP, so they are unaffected.
_CSRF_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


@app.before_request
def _csrf_protect():
    if request.method not in _CSRF_METHODS:
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    expected = session.get("csrf_token") or ""
    provided = request.headers.get("X-CSRF-Token") or ""
    if not expected or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "CSRF"}), 403
    return None


@app.context_processor
def _inject_csrf_token():
    return {"csrf_token": session.get("csrf_token", "")}


# ── Login brute-force lockout ──────────────────────────────────────────────────
# Persistent (DB-backed) failure tracking on top of the 5/min limiter — the
# limiter's in-memory counters die on every Railway restart. 10 failures from
# one IP within an hour lock that IP out for an hour, even with the correct
# passphrase. The lockout response is deliberately generic (no thresholds).
_LOCKOUT_THRESHOLD = 10
_LOCKOUT_WINDOW_HOURS = 1


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    next_url = request.args.get("next") or "/"
    # Only allow same-site relative paths as the post-login redirect target.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if request.method == "POST":
        ip = get_remote_address()
        try:
            locked = db.count_auth_failures(ip, hours=_LOCKOUT_WINDOW_HOURS) >= _LOCKOUT_THRESHOLD
        except Exception as e:
            logger.error("auth failure lookup failed: %s", e)
            locked = False  # fail open on DB trouble; the 5/min limiter still applies
        if locked:
            return render_template("login.html", error="Too many attempts. Try again later.",
                                   next_url=next_url), 429
        pw = request.form.get("password") or ""
        if APP_PASSWORD and hmac.compare_digest(pw, APP_PASSWORD):
            try:
                db.clear_auth_failures(ip)
            except Exception as e:
                logger.error("auth failure clear failed: %s", e)
            # Rotate the session on login (anti-fixation) and mint the CSRF
            # token the frontend echoes back on every write.
            session.clear()
            session["authed"] = True
            session.permanent = True
            session["csrf_token"] = secrets.token_hex(32)
            return redirect(next_url)
        try:
            failures = db.record_auth_failure(ip)
            if failures == _LOCKOUT_THRESHOLD:
                # Sentinel's job: page the owner and remember the incident.
                telegram_bot.send_alert(
                    f"🚨 {_LOCKOUT_THRESHOLD} failed ASFA logins from {ip} in the last hour")
                db.log_episodic(
                    "sentinel", "security_alert",
                    f"Locked out IP {ip} after {_LOCKOUT_THRESHOLD} failed logins within an hour")
        except Exception as e:
            logger.error("auth failure tracking failed: %s", e)
        return render_template("login.html", error="Incorrect passphrase.", next_url=next_url), 401
    return render_template("login.html", error=None, next_url=next_url)


@app.errorhandler(429)
def ratelimit_handler(e):
    """Rate-limit responses return clean JSON (login throttle, API limits)."""
    return jsonify({"error": "too many requests, try again shortly"}), 429


@app.route("/logout", methods=["POST"])
def logout():
    """CSRF-protected logout (POST-only so a cross-site GET can't kill the
    session). Audited, then the session is fully cleared."""
    try:
        db.log_audit("auth", "logout", "success", reason="user logout")
    except Exception as e:
        logger.error("logout audit failed: %s", e)
    session.clear()
    return redirect(url_for("login"))

# Create tables if missing. Idempotent (CREATE TABLE IF NOT EXISTS) and handles
# the Postgres/SQLite difference, so it's safe to run on every boot. Critical on
# a fresh Railway Postgres where no tables exist yet.
db.init_db()
# Mission Control — create the agent ecosystem tables and seed the roster.
db.init_agents_db()
# Agent data layer (Phase 3) — three-tier memory, audit trail, error budgets.
# Creates the new tables, seeds relationships, and inits per-agent error budgets.
db.init_agent_data()
# Skill executor (Phase 6) — register real skill implementations so approved
# plans invoke actual agent code instead of simulating.
from services.skill_executor import init_all_skills
init_all_skills()
# Gym tracker — create the gym_* tables and seed the exercise library + routines.
# Standalone; touches no existing tables.
db.init_gym_data()
# Scent Vault — fragrance shelf, body products, curated pairings + wear log.
db.init_fragrance_data()
# Scout pipeline — Kanban stage board; create table + backfill from scout_jobs.
db.init_scout_pipeline()
# Bottle photos live outside git (see .gitignore); recreate the dir on boot so
# a fresh clone/deploy can accept uploads immediately.
FRAGRANCE_UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "fragrances")
os.makedirs(FRAGRANCE_UPLOAD_DIR, exist_ok=True)
# Progress photos also live outside git (see .gitignore); same boot-time recreate.
GYM_PHOTO_UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "gym-photos")
os.makedirs(GYM_PHOTO_UPLOAD_DIR, exist_ok=True)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def command():
    return render_template(
        "command.html",
        active="command",
        google_connected=is_authenticated(),
        spotify_connected=spotify.is_connected(),
    )


@app.route("/agents")
def agents():
    return render_template("agents.html", active="agents")


@app.route("/approvals")
def approvals():
    return render_template("approvals.html", active="approvals")


@app.route("/system")
def system():
    return render_template("system.html", active="system")


@app.route("/gym")
def gym():
    return render_template("gym.html", active="gym", active_tab="gym")


@app.route("/gym/photos")
def gym_photos():
    return render_template("gym-photos.html", active="gym")


@app.route("/fragrances")
def fragrances():
    return render_template("fragrances.html", active="fragrances")


@app.route("/plans/<plan_id>")
def plan_view(plan_id):
    """Standalone page that opens the plan-approval modal for one plan."""
    return render_template("plan.html", active="command", plan_id=plan_id)


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


# ── Gym tracker ─────────────────────────────────────────────────────────────────
# Standalone gym-tracking API (exercise library, routines, logged sessions/sets,
# PRs, body stats, XP/ranks). Backed by the gym_* tables in database.py. All
# routes are auth-gated by the global before_request gate.

@app.route("/api/gym/exercises")
def api_gym_exercises():
    return jsonify(db.get_all_exercises())


@app.route("/api/gym/exercises/<int:exercise_id>")
def api_gym_exercise(exercise_id):
    ex = db.get_exercise(exercise_id)
    if not ex:
        return jsonify({"error": "exercise not found"}), 404
    return jsonify(ex)


@app.route("/api/gym/exercises/muscle/<group>")
def api_gym_exercises_by_muscle(group):
    return jsonify(db.get_exercises_by_muscle(group))


@app.route("/api/gym/routines")
def api_gym_routines():
    return jsonify(db.get_all_routines())


@app.route("/api/gym/routines/<int:routine_id>")
def api_gym_routine(routine_id):
    routine = db.get_routine(routine_id)
    if not routine:
        return jsonify({"error": "routine not found"}), 404
    return jsonify(routine)


@app.route("/api/gym/sessions/start", methods=["POST"])
def api_gym_session_start():
    d = request.get_json(force=True) or {}
    routine_id = d.get("routine_id")
    date = d.get("date") or _today()
    start_time = d.get("start_time") or datetime.now().isoformat()
    notes = d.get("notes", "")
    session_id = db.create_session(routine_id, date, start_time, notes)
    return jsonify({"ok": True, "session_id": session_id,
                    "session": db.get_session(session_id)})


@app.route("/api/gym/sessions/<int:session_id>/end", methods=["POST"])
def api_gym_session_end(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    d = request.get_json(force=True) or {}
    end_time = d.get("end_time") or datetime.now().isoformat()

    # Derive totals from the sets actually logged this session.
    sets = db.get_session_sets(session_id)
    total_volume = round(sum((s.get("weight_kg") or 0) * (s.get("reps") or 0)
                             for s in sets), 2)
    total_sets = len(sets)

    duration = d.get("duration") or d.get("duration_minutes")
    if duration is None:
        try:
            start = datetime.fromisoformat(session.get("start_time"))
            duration = int((datetime.fromisoformat(end_time) - start).total_seconds() // 60)
        except (TypeError, ValueError):
            duration = 0

    # Completion bonus XP + streak update on finishing a workout.
    bonus = db.add_xp(100, "workout completed")
    streak = db.update_streak(session.get("date"))
    sets_xp = sum(db._xp_for_set(s.get("weight_kg"), s.get("reps"), bool(s.get("is_pr"))) for s in sets)
    session_xp = int(sets_xp) + 100
    db.end_session(session_id, end_time, duration, total_volume, total_sets, session_xp)

    # Session efficiency (kg volume per minute) + rolling average for this
    # routine. Cardio-only sessions have zero volume and skip efficiency.
    efficiency = round(total_volume / duration, 1) if (duration and total_volume) else None
    avg_efficiency = db.get_routine_efficiency_avg(
        session.get("routine_id"), exclude_session_id=session_id)

    return jsonify({"ok": True, "session": db.get_session(session_id),
                    "total_volume_kg": total_volume, "total_sets": total_sets,
                    "duration_minutes": duration, "streak": streak,
                    "efficiency": efficiency, "avg_efficiency": avg_efficiency,
                    "xp": bonus})


@app.route("/api/gym/sessions")
def api_gym_sessions():
    limit = int(request.args.get("limit", 10))
    return jsonify(db.get_recent_sessions(limit))


@app.route("/api/gym/sessions/calendar")
def api_gym_sessions_calendar():
    months = int(request.args.get("months", 3))
    return jsonify(db.get_streak_calendar(months))


@app.route("/api/gym/sessions/<int:session_id>")
def api_gym_session(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    session["sets"] = db.get_session_sets(session_id)
    return jsonify(session)


@app.route("/api/gym/sets", methods=["POST"])
def api_gym_log_set():
    d = request.get_json(force=True) or {}
    required = ("session_id", "exercise_id", "set_number")
    if any(d.get(k) is None for k in required):
        return jsonify({"error": "session_id, exercise_id and set_number are required"}), 400
    result = db.log_set(
        d["session_id"], d["exercise_id"], d["set_number"],
        d.get("set_type", "working"), d.get("weight_kg", 0), d.get("reps", 0),
        d.get("notes", ""), rpe=d.get("rpe"))
    return jsonify({"ok": True, **result})


@app.route("/api/gym/sets/session/<int:session_id>")
def api_gym_session_sets(session_id):
    return jsonify(db.get_session_sets(session_id))


@app.route("/api/gym/sets/<int:set_id>", methods=["DELETE"])
def api_gym_delete_set(set_id):
    ok = db.delete_set(set_id)
    if not ok:
        return jsonify({"error": "set not found"}), 404
    return jsonify({"ok": True})


def _csv_response(fieldnames, rows, filename):
    """Build a downloadable CSV response. csv.DictWriter handles quoting of
    commas, quotes and newlines inside fields; extrasaction='ignore' keeps
    unexpected keys out. Auth-gated like every non-public route."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/gym/export/csv")
def api_gym_export_csv():
    """Export logged gym sets as CSV. Optional ?start_date=&end_date= (inclusive
    ISO dates on the session date)."""
    start = request.args.get("start_date") or None
    end = request.args.get("end_date") or None
    rows = db.get_gym_sets_for_export(start, end)
    fields = ["date", "exercise", "weight_kg", "reps", "rpe",
              "duration_min", "pr", "xp_earned"]
    return _csv_response(fields, rows, f"asfa_gym_{_today()}.csv")


# ── Body composition (Renpho manual entry — see services/rephno.py seam) ────────

@app.route("/api/body-composition")
def api_body_composition():
    """Latest body-composition scans (default last 30 days), newest first."""
    days = request.args.get("days", default=30, type=int) or 30
    rows = db.get_body_composition(days=days)
    return jsonify({"scans": rows, "latest": db.latest_body_composition()})


@app.route("/api/body-composition/manual", methods=["POST"])
def api_body_composition_manual():
    """Manually log a body-composition scan. Upserts on the scan date (one row
    per day). Body: {date?, weight_kg, bmi, body_fat_percent, ffm_kg,
    body_water_percent, bmr, subcutaneous_fat_percent} — all metrics optional."""
    d = request.get_json(force=True) or {}
    date_scanned = (d.get("date") or d.get("date_scanned") or _today())[:10]
    metrics = {k: d.get(k) for k in (
        "weight_kg", "bmi", "body_fat_percent", "ffm_kg",
        "body_water_percent", "bmr", "subcutaneous_fat_percent")}
    if all(db._to_float(v) is None for v in metrics.values()):
        return jsonify({"error": "provide at least one metric"}), 400
    row = db.upsert_body_composition(date_scanned, metrics, source_id=None)
    return jsonify({"ok": True, "scan": row})


@app.route("/api/gym/photos")
def api_gym_photos():
    """Progress-photo gallery, newest first."""
    photos = db.get_gym_photos()
    for p in photos:
        p["url"] = f"/static/uploads/gym-photos/{p['filename']}"
    return jsonify({"photos": photos})


@app.route("/api/gym/photos", methods=["POST"])
def api_gym_photo_upload():
    """Upload a progress photo. Reuses the fragrance image pipeline (magic-byte
    sniff → Pillow decode/re-encode → EXIF strip, 5MB cap). Auto-tags today's
    date and that day's weight / body-fat if a scan or bodyweight entry exists."""
    f = request.files.get("image") or request.files.get("photo")
    if not f:
        return jsonify({"error": "no image file"}), 400
    data = f.read(_IMG_MAX_BYTES + 1)
    if len(data) > _IMG_MAX_BYTES:
        return jsonify({"error": "image too large (max 5MB)"}), 413
    ext = next((e for e, sniff in _IMG_MAGIC.items() if sniff(data)), None)
    if not ext:
        return jsonify({"error": "unsupported image type (jpg/png/webp only)"}), 415
    from io import BytesIO
    from PIL import Image, ImageOps
    try:
        img = Image.open(BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)   # bake orientation, then save w/o metadata
    except Exception:
        return jsonify({"error": "corrupt or unreadable image"}), 415
    today = _today()
    filename = f"{today}_{int(datetime.now().timestamp() * 1000)}.{ext}"
    path = os.path.join(GYM_PHOTO_UPLOAD_DIR, filename)
    if img.mode in ("P", "RGBA") and ext == "jpg":
        img = img.convert("RGB")
    img.save(path, format=_PIL_FORMATS[ext])

    # Auto-tag with today's body-fat (from a scan) and weight (scan → bodyweight log).
    scans = db.get_body_composition(days=1)
    today_scan = next((s for s in scans if str(s.get("date_scanned"))[:10] == today), None)
    body_fat = today_scan.get("body_fat_percent") if today_scan else None
    weight = today_scan.get("weight_kg") if today_scan else None
    if weight is None:
        try:
            bw = db.get_body_stats()  # existing bodyweight log (newest first)
            if bw and str(bw[0].get("date"))[:10] == today:
                weight = bw[0].get("weight_kg")
        except Exception:
            pass
    photo = db.add_gym_photo(today, filename, weight_kg=weight, body_fat_percent=body_fat)
    photo["url"] = f"/static/uploads/gym-photos/{filename}"
    return jsonify({"ok": True, "photo": photo})


@app.route("/api/gym/exercises/<int:exercise_id>/last-session")
def api_gym_last_session(exercise_id):
    exclude = request.args.get("exclude_session", type=int)
    return jsonify(db.get_last_session_for_exercise(exercise_id, exclude) or {})


@app.route("/api/gym/exercises/<int:exercise_id>/last-performance")
def api_gym_last_performance(exercise_id):
    """Feature A — prior-session sets for autofill. ?exclude_session=<id> skips
    the in-progress session. Cardio/first-timers → {"found": false}."""
    exclude = request.args.get("exclude_session", type=int)
    return jsonify(db.get_last_performance(exercise_id, exclude_session_id=exclude))


@app.route("/api/gym/exercises/<int:exercise_id>/recommendation")
def api_gym_recommendation(exercise_id):
    """Feature B — double-progression add-weight recommendation. Optional
    ?rep_min/?rep_max override the routine's rep range (the active session
    knows its own targets); ?exclude_session skips the in-progress session."""
    exclude = request.args.get("exclude_session", type=int)
    rep_min = request.args.get("rep_min", type=int)
    rep_max = request.args.get("rep_max", type=int)
    return jsonify(db.get_progression_recommendation(
        exercise_id, rep_min, rep_max, exclude_session_id=exclude))


@app.route("/api/gym/routines/<int:routine_id>/recommendations")
def api_gym_routine_recommendations(routine_id):
    """Per-exercise recommendations for a routine (dashboard Next Session
    Targets card). Read-only; cardio entries are skipped."""
    if not db.get_routine(routine_id):
        return jsonify({"error": "routine not found"}), 404
    return jsonify(db.get_routine_recommendations(routine_id))


@app.route("/api/gym/volume/weekly")
def api_gym_weekly_volume():
    return jsonify(db.get_weekly_volume())


@app.route("/api/gym/muscle-recovery")
def api_gym_muscle_recovery():
    return jsonify(db.get_muscle_recovery())


@app.route("/api/gym/sessions/active")
def api_gym_active_session():
    return jsonify(db.get_active_session() or {})


@app.route("/api/gym/sessions/<int:session_id>", methods=["DELETE"])
def api_gym_delete_session(session_id):
    ok = db.delete_session(session_id)
    if not ok:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/gym/sessions/<int:session_id>/duration", methods=["POST"])
def api_gym_session_duration(session_id):
    d = request.get_json(force=True) or {}
    minutes = int(d.get("minutes") or d.get("duration") or 0)
    if minutes <= 0:
        return jsonify({"error": "minutes must be > 0"}), 400
    if not db.update_session_duration(session_id, minutes):
        return jsonify({"error": "session not found"}), 404
    sess = db.get_session(session_id)
    vol = float(sess.get("total_volume_kg") or 0)
    efficiency = round(vol / minutes, 1) if (minutes and vol) else None
    avg_efficiency = db.get_routine_efficiency_avg(
        sess.get("routine_id"), exclude_session_id=session_id)
    return jsonify({"ok": True, "duration_minutes": minutes,
                    "efficiency": efficiency, "avg_efficiency": avg_efficiency})


@app.route("/api/gym/sessions/<int:session_id>/notes", methods=["POST"])
def api_gym_session_notes(session_id):
    d = request.get_json(force=True) or {}
    ok = db.save_session_notes(session_id, d.get("notes", ""))
    if not ok:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/gym/prs")
def api_gym_prs():
    return jsonify(db.get_all_prs())


@app.route("/api/gym/prs/<int:exercise_id>")
def api_gym_pr(exercise_id):
    pr = db.get_pr(exercise_id)
    if not pr:
        return jsonify({"error": "no PR for this exercise"}), 404
    return jsonify(pr)


@app.route("/api/gym/history/<int:exercise_id>")
def api_gym_history(exercise_id):
    limit = int(request.args.get("limit", 20))
    return jsonify(db.get_exercise_history(exercise_id, limit))


@app.route("/api/gym/body-stats", methods=["POST"])
def api_gym_log_body_stat():
    d = request.get_json(force=True) or {}
    kg = float(d.get("weight_kg", 0) or 0)
    if not 20 < kg < 300:
        return jsonify({"error": "invalid weight"}), 400
    date = d.get("date") or _today()
    db.log_body_stat(date, kg, d.get("notes", ""))
    return jsonify({"ok": True})


@app.route("/api/gym/body-stats")
def api_gym_body_stats():
    limit = int(request.args.get("limit", 30))
    return jsonify(db.get_body_stats(limit))


@app.route("/api/gym/ranks")
def api_gym_ranks():
    return jsonify(db.get_muscle_ranks())


@app.route("/api/gym/xp")
def api_gym_xp():
    xp = db.get_xp()
    xp["streak_days"] = db.get_streak()
    return jsonify(xp)


@app.route("/api/gym/streak")
def api_gym_streak():
    return jsonify({"streak": db.get_streak()})


@app.route("/api/gym/rest-day", methods=["POST"])
def api_gym_rest_day():
    d = request.get_json(force=True) or {}
    rest_date = d.get("date") or _today()
    db.add_rest_day(rest_date)
    return jsonify({"ok": True, "date": str(rest_date)[:10],
                    "streak": db.get_streak()})


@app.route("/api/gym/rest-days")
def api_gym_rest_days():
    return jsonify(db.get_rest_days())


@app.route("/api/gym/deload-check")
def api_gym_deload_check():
    return jsonify(db.get_deload_check())


@app.route("/api/gym/trainer", methods=["POST"])
def api_gym_trainer():
    """AI fitness-trainer chat. Reuses the shared Anthropic client in
    services/ai.py; fails gracefully (friendly message, HTTP 200) when no API
    key is configured so the client can render it inline. Gated client-side by
    the AI-Trainer settings toggle as well."""
    d = request.get_json(force=True) or {}
    message = (d.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    context = d.get("context") or {}
    reply = ai.gym_coach_reply(message, context)
    return jsonify({"reply": reply})


# The gym tracker is chatty by nature — a single logged workout fires one request
# per set (often 30+), plus dashboard/history reads. That easily exceeds the coarse
# app-wide "50/hour" abuse guard and would 429 the user mid-workout. Only an
# AUTHENTICATED session gets the exemption: anonymous hits on /api/gym/* stay
# rate-limited like everything else, so the exemption can't be used as an
# unthrottled probe surface.
@limiter.request_filter
def _exempt_gym_api():
    return request.path.startswith("/api/gym") and bool(session.get("authed"))


# ── Scent Vault ────────────────────────────────────────────────────────────────
# Fragrance shelf + smart daily routine recommendation. Reads are plain JSON;
# the wear/undo/image writes are covered by the global CSRF gate like every
# other POST/DELETE. Scoring is a pure function (injectable hour/temp) so the
# recommendation is testable without mocking the clock or the weather API.

_SCENT_SEASONS = {12: "winter", 1: "winter", 2: "winter",
                  3: "spring", 4: "spring", 5: "spring",
                  6: "summer", 7: "summer", 8: "summer",
                  9: "autumn", 10: "autumn", 11: "autumn"}
_SCENT_OCCASIONS = {"casual", "office", "date", "gym", "formal"}
_WARM_WEATHER_VIBES = {"fresh", "citrus", "clean", "aromatic"}
_COLD_WEATHER_VIBES = {"warm", "sweet", "spicy", "woody", "ambery"}


def _scent_time_bucket(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _csv_set(value) -> set:
    return {v.strip().lower() for v in str(value or "").split(",") if v.strip()}


def _score_fragrance(frag: dict, bucket: str, season: str, temp_c, occasion) -> tuple:
    """Score one bottle against the current context. Returns (score, specificity,
    factors): factors are the human fragments the reason string is built from;
    specificity counts EXACT season/time matches (vs catch-all "all") and only
    breaks ties — a bottle made for this exact season/hour beats an all-rounder."""
    score, specificity, factors = 0.0, 0, []
    times = _csv_set(frag.get("time_of_day"))
    if bucket in times or "all" in times:
        score += 3
        specificity += bucket in times
        factors.append(f"suits the {bucket}")
    seasons = _csv_set(frag.get("best_seasons"))
    if season in seasons or "all" in seasons:
        score += 3
        specificity += season in seasons
    vibes = _csv_set(frag.get("vibe"))
    if temp_c is not None:
        if temp_c >= 20 and vibes & _WARM_WEATHER_VIBES:
            score += 3
            factors.append(f"its {'/'.join(sorted(vibes & _WARM_WEATHER_VIBES))} character fits {round(temp_c)}°C")
        elif temp_c < 12 and vibes & _COLD_WEATHER_VIBES:
            score += 3
            factors.append(f"its {'/'.join(sorted(vibes & _COLD_WEATHER_VIBES))} character suits {round(temp_c)}°C")
    if occasion and occasion in _csv_set(frag.get("occasions")):
        score += 4
        factors.append(f"tagged for {occasion}")
    days = frag.get("days_since_worn")
    if days is None:
        days = 14  # never worn — max rotation nudge
    rot = min(days, 14) / 14 * 2
    score += rot
    if days == 0:
        score -= 5  # already worn today — rotate
    elif days >= 7 and rot >= 1:
        factors.append(f"you haven't worn it in {days} days")
    return score, specificity, factors


def _fragrance_recommendation(occasion=None, hour=None, temp_c=None, condition=None,
                              month=None, fragrances=None):
    """Pick tonight's scent. hour/temp_c/condition/month are injectable for
    tests; in normal use they come from the server clock and weather service."""
    if hour is None:
        hour = datetime.now().hour
    if temp_c is None:
        w = get_weather()
        if not w.get("error"):
            temp_c = w.get("temp")
            condition = w.get("description")
    bucket = _scent_time_bucket(hour)
    season = _SCENT_SEASONS[month or datetime.now().month]
    frags = fragrances if fragrances is not None else db.get_fragrances()
    if not frags:
        return None
    scored = [(f, *(_score_fragrance(f, bucket, season, temp_c, occasion))) for f in frags]
    # Best score wins; ties go to the least-worn bottle (fair rotation), then
    # to the most context-specific match (exact season/time beats "all").
    scored.sort(key=lambda t: (-t[1], t[0].get("wear_count") or 0, -t[2]))
    winner, score, _spec, factors = scored[0]

    ctx_bits = [bucket]
    if temp_c is not None:
        ctx_bits.append(f"{round(temp_c)}°C")
    ctx = " + ".join(ctx_bits)
    reason = f"{ctx[0].upper()}{ctx[1:]} → reach for {winner['name']}."
    if factors:
        joined = "; ".join(factors)
        reason += f" {joined[0].upper()}{joined[1:]}."
    pairing = db.get_fragrance_pairing(winner["id"]) or {}
    if pairing.get("layering_fragrance"):
        reason += f" Layer {pairing['layering_fragrance']['name']} — {pairing.get('layering_notes') or 'see routine'}"
    return {
        "fragrance": {k: winner.get(k) for k in
                      ("id", "name", "brand", "concentration", "vibe", "image_url",
                       "is_signature", "wear_count", "days_since_worn")},
        "routine": pairing,
        "context": {"time_bucket": bucket, "season": season,
                    "temp_c": temp_c, "condition": condition,
                    "occasion": occasion},
        "reason": reason,
    }


@app.route("/api/fragrances")
def api_fragrances():
    return jsonify(db.get_fragrances())


@app.route("/api/fragrances/stats")
def api_fragrance_stats():
    return jsonify(db.get_fragrance_stats())


@app.route("/api/fragrances/recommendation")
def api_fragrance_recommendation():
    occasion = (request.args.get("occasion") or "").strip().lower() or None
    if occasion and occasion not in _SCENT_OCCASIONS:
        return jsonify({"error": "unknown occasion"}), 400
    rec = _fragrance_recommendation(occasion=occasion)
    if not rec:
        return jsonify({"error": "no fragrances in collection"}), 404
    return jsonify(rec)


@app.route("/api/fragrances/<int:fragrance_id>")
def api_fragrance_detail(fragrance_id):
    frag = db.get_fragrance(fragrance_id)
    if not frag:
        return jsonify({"error": "not found"}), 404
    frag["pairing"] = db.get_fragrance_pairing(fragrance_id)
    frag["wears"] = db.get_fragrance_wears(fragrance_id, days=90)
    stats = db.get_fragrance_stats()
    ranked = sorted(stats["rotation"], key=lambda r: -r["wear_count"])
    frag["rotation_rank"] = next(
        (i + 1 for i, r in enumerate(ranked) if r["id"] == fragrance_id), None)
    frag["collection_size"] = len(ranked)
    return jsonify(frag)


@app.route("/api/fragrances/<int:fragrance_id>/wear", methods=["POST"])
def api_fragrance_wear(fragrance_id):
    d = request.get_json(silent=True) or {}
    time_of_day = (d.get("time_of_day") or "").strip().lower() or None
    occasion = (d.get("occasion") or "").strip().lower() or None
    if time_of_day and time_of_day not in {"morning", "day", "evening", "night"}:
        return jsonify({"error": "bad time_of_day"}), 400
    if occasion and occasion not in _SCENT_OCCASIONS:
        return jsonify({"error": "unknown occasion"}), 400
    updated = db.log_fragrance_wear(fragrance_id, time_of_day, occasion)
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify(updated)


@app.route("/api/fragrances/<int:fragrance_id>/wear/last", methods=["DELETE"])
def api_fragrance_undo_wear(fragrance_id):
    updated = db.undo_last_fragrance_wear(fragrance_id)
    if not updated:
        return jsonify({"error": "no wear to undo"}), 404
    return jsonify(updated)


# Accepted upload formats, validated by CONTENT not filename: magic-byte sniff
# first (cheap reject), then a full Pillow decode (catches polyglots/corrupt
# files) which also re-encodes the image — stripping EXIF/GPS metadata.
_IMG_MAGIC = {
    "jpg": lambda b: b[:3] == b"\xff\xd8\xff",
    "png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    "webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}
_IMG_MAX_BYTES = 5 * 1024 * 1024
_PIL_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


@app.route("/api/fragrances/<int:fragrance_id>/image", methods=["POST"])
def api_fragrance_image(fragrance_id):
    if not db.get_fragrance(fragrance_id):
        return jsonify({"error": "not found"}), 404
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "no image file"}), 400
    data = f.read(_IMG_MAX_BYTES + 1)
    if len(data) > _IMG_MAX_BYTES:
        return jsonify({"error": "image too large (max 5MB)"}), 413
    ext = next((e for e, sniff in _IMG_MAGIC.items() if sniff(data)), None)
    if not ext:
        return jsonify({"error": "unsupported image type (jpg/png/webp only)"}), 415
    from io import BytesIO
    from PIL import Image, ImageOps
    try:
        img = Image.open(BytesIO(data))
        img.load()
        # Bake in the EXIF orientation, then save WITHOUT metadata.
        img = ImageOps.exif_transpose(img)
    except Exception:
        return jsonify({"error": "corrupt or unreadable image"}), 415
    filename = f"{fragrance_id}.{ext}"
    path = os.path.join(FRAGRANCE_UPLOAD_DIR, filename)
    # Replacing a photo may change extension — drop stale siblings first.
    for old_ext in _IMG_MAGIC:
        if old_ext != ext:
            try:
                os.remove(os.path.join(FRAGRANCE_UPLOAD_DIR, f"{fragrance_id}.{old_ext}"))
            except FileNotFoundError:
                pass
    if img.mode in ("P", "RGBA") and ext == "jpg":
        img = img.convert("RGB")
    img.save(path, format=_PIL_FORMATS[ext])
    # Cache-buster so a replaced photo swaps immediately in the UI.
    image_url = f"/static/uploads/fragrances/{filename}?v={int(datetime.now().timestamp())}"
    db.set_fragrance_image(fragrance_id, image_url)
    return jsonify({"ok": True, "image_url": image_url})


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

    # Phase 3: record the intake as an episodic memory for the hydration agent.
    try:
        total = db.get_hydration_total(date)
        db.log_episodic("hydration", "water_logged",
                        f"Logged {amount}ml of water ({total}/2000ml today)",
                        payload={"amount_ml": amount, "total_ml": total, "date": date})
        # Phase 4: logging water energises the hydration agent.
        db.update_energy("hydration", 5)
    except Exception as e:
        logger.error("hydration episodic log failed: %s", e)

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


@app.route("/api/asfa/spotify/play", methods=["POST"])
def api_spotify_play():
    """Resume playback on the user's active/default device. POST-only so the
    global CSRF gate covers this external action. Always 200 so the frontend can
    surface the friendly message regardless of outcome."""
    return jsonify(spotify.resume_playback())


@app.route("/api/asfa/spotify/focus", methods=["POST"])
def api_spotify_focus():
    """Start a mood playlist by search query (Think Mode ambient / Lock In
    focus). POST-only so the global CSRF gate covers this external action.
    `q` may arrive as a query param or JSON body."""
    body = request.get_json(silent=True) or {}
    query = request.args.get("q") or body.get("q") or "deep focus"
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


# ── System monitoring (public health probe) ────────────────────────────────────

@app.route("/api/system/health", methods=["GET"])
def api_system_health():
    """Probe-safe health check. Public (in _PUBLIC_ENDPOINTS) so Railway can
    hit it without a session, so it returns ONLY coarse statuses — no error
    strings, file paths, job names, agent internals, or integration/env
    details (those leaked here before; they now live on the auth-gated /full
    variant below). Still pushes a Telegram alert if state is critical."""
    from services import monitor
    health = monitor.get_system_health()
    monitor.alert_if_critical(health)
    return jsonify({
        "status": health.get("status"),
        "db": (health.get("database") or {}).get("status"),
        "backup": (health.get("backups") or {}).get("status"),
    })


@app.route("/api/system/health/full", methods=["GET"])
def api_system_health_full():
    """Full per-subsystem health detail for the System screen. Auth-gated
    (NOT in _PUBLIC_ENDPOINTS) — this payload includes exception strings,
    scheduler job ids and integration configuration state."""
    from services import monitor
    return jsonify(monitor.get_system_health())


# ── Database backup (manual trigger) ───────────────────────────────────────────

@app.route("/api/asfa/backup/run-now", methods=["POST"])
def api_backup_run_now():
    """Manually trigger a production DB backup. Auth-required (not in
    _PUBLIC_ENDPOINTS). No-op on local SQLite; dumps + pushes on Railway/Postgres."""
    from services.backup import run_backup
    started = datetime.now()
    res = run_backup()
    # Phase 3: record the manual backup in the audit trail + error budget.
    try:
        dur_ms = int((datetime.now() - started).total_seconds() * 1000)
        ok = bool(res.get("ok"))
        db.log_audit("backup", "run_now", "success" if ok else "failure",
                     reason="manual backup trigger",
                     details=res, duration_ms=dur_ms)
        db.update_error_budget("backup", ok)
        # Phase 4: energy economy — reward a clean backup, penalise a failure.
        db.update_energy("backup", 5 if ok else -10)
    except Exception as e:
        logger.error("backup audit log failed: %s", e)
    return jsonify(res), (200 if res.get("ok") else 500)


# ── Obsidian sync (local markdown daily logs) ──────────────────────────────────

@app.route("/api/asfa/obsidian/sync-now", methods=["POST"])
def api_obsidian_sync():
    """Write the full ASFA vault tree to OBSIDIAN_VAULT_PATH. POST-only so the
    global CSRF gate covers this filesystem side effect. Only writes when ASFA
    runs on a machine with that local folder (i.e. the Mac, not Railway)."""
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
    return render_template("mission_control.html", active="mission")


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

    # Public endpoint (unauthenticated health checks): expose only the coarse
    # per-pillar statuses. The details block (probe internals, alert counts)
    # is included for logged-in sessions only.
    payload = {"power": "OK", "connectivity": connectivity, "security": security}
    if session.get("authed"):
        payload["details"] = details
    return jsonify(payload)


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


# ── Phase 4: agent intelligence (heartbeat, diaries, energy) ───────────────────

@app.route("/api/agents/status")
def api_agents_status():
    """Heartbeat results for all agents (status, energy, budget health)."""
    from services.heartbeat import run_heartbeat
    return jsonify(run_heartbeat())


@app.route("/api/agents/energy")
def api_agents_energy():
    """Energy levels for all agents: [{agent_id, energy, last_updated}, ...]."""
    return jsonify(db.get_all_energy())


@app.route("/api/agents/diary/generate-all", methods=["POST"])
def api_agents_diary_generate_all():
    """Trigger diary generation for all 13 agents (uses the Claude API)."""
    from services.agent_intelligence import generate_all_diaries
    return jsonify(generate_all_diaries())


@app.route("/api/agents/<agent_id>/diary")
def api_agent_diary(agent_id):
    """Most recent reflective diary entry for an agent."""
    reflections = db.get_agent_reflections(agent_id, period="daily", limit=1)
    if not reflections:
        return jsonify({"error": "no diary entry yet", "agent_id": agent_id}), 404
    r = reflections[0]
    stats = r.get("stats")
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except (TypeError, ValueError):
            pass
    return jsonify({
        "agent_id": agent_id,
        "summary": r.get("summary"),
        "stats": stats,
        "created_at": r.get("created_at"),
    })


@app.route("/api/agents/<agent_id>/diary/generate", methods=["POST"])
def api_agent_diary_generate(agent_id):
    """Trigger immediate diary generation for one agent (uses the Claude API)."""
    from services.agent_intelligence import generate_diary_entry
    result = generate_diary_entry(agent_id)
    return jsonify(result), (200 if result.get("ok") else 500)


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


@app.route("/api/scout/scan", methods=["POST"])
def api_scout_scan():
    """Trigger a manual Indeed scrape. POST-only so the global CSRF gate covers
    this side effect. Always returns 200 with a count so the frontend gets valid
    JSON even if the scrape is blocked/empty."""
    from services import scout
    try:
        count = scout.scan()
    except Exception as e:
        logger.error("scout scan failed: %s", e)
        return jsonify({"new_jobs": 0, "error": str(e)[:200]})
    return jsonify({"new_jobs": count})


@app.route("/api/scout/export/csv")
def api_scout_export_csv():
    """Export the Scout pipeline as CSV. Includes cv_match_score once the Part 4
    CV-match column exists."""
    rows = db.get_scout_pipeline_for_export()
    fields = ["date_saved", "job_title", "company", "stage", "date_applied",
              "date_stage_changed", "source", "notes"]
    if rows and "cv_match_score" in rows[0]:
        fields.append("cv_match_score")
    return _csv_response(fields, rows, f"asfa_scout_{_today()}.csv")


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


# ── Scout pipeline (Kanban board) ──────────────────────────────────────────────
# CRUD + follow-up reminders. Writes ride the global CSRF gate (POST/PUT/DELETE)
# and the auth gate like every other endpoint.
@app.route("/api/scout/pipeline", methods=["GET", "POST"])
def api_scout_pipeline():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        job_title = (d.get("job_title") or "").strip()
        company = (d.get("company") or "").strip()
        if not job_title or not company:
            return jsonify({"error": "job_title and company required"}), 400
        stage = (d.get("stage") or "saved").strip()
        if stage not in db.SCOUT_STAGES:
            return jsonify({"error": f"stage must be one of {', '.join(db.SCOUT_STAGES)}"}), 400
        pid = db.add_scout_pipeline_job(
            job_title, company, job_url=d.get("job_url") or None,
            location=d.get("location") or None, source=d.get("source") or None,
            stage=stage, notes=d.get("notes") or None, cv_version=d.get("cv_version") or None)
        return jsonify({"ok": True, "id": pid, "job": db.get_scout_pipeline_job(pid)})
    stage = request.args.get("stage")
    if stage and stage not in db.SCOUT_STAGES:
        return jsonify({"error": "unknown stage"}), 400
    return jsonify(db.get_scout_pipeline(stage=stage))


@app.route("/api/scout/pipeline/reminders")
def api_scout_pipeline_reminders():
    return jsonify(db.get_scout_pipeline_reminders())


@app.route("/api/scout/pipeline/<int:pid>", methods=["PUT", "DELETE"])
def api_scout_pipeline_item(pid):
    if request.method == "DELETE":
        ok = db.delete_scout_pipeline(pid)
        return (jsonify({"ok": True}) if ok else (jsonify({"error": "not found"}), 404))
    d = request.get_json(force=True) or {}
    stage = d.get("stage")
    if stage is not None and stage not in db.SCOUT_STAGES:
        return jsonify({"error": f"stage must be one of {', '.join(db.SCOUT_STAGES)}"}), 400
    job = db.update_scout_pipeline(pid, stage=stage, notes=d.get("notes"),
                                   cv_version=d.get("cv_version"))
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "job": job})


_CV_KV_KEY = "scout_cv_text"


@app.route("/api/scout/pipeline/<int:pid>/analyze-cv", methods=["POST"])
def api_scout_analyze_cv(pid):
    """Score a pipeline job against the CV and list missing required keywords.

    Body: {cv_text?, job_description?}. cv_text is cached in kv_store so later
    calls can omit it; job_description falls back to the scraped scout_jobs
    description matched by url or title+company. One Claude call (extract +
    compare). Stores score + missing_keywords (JSON) + timestamp on the row."""
    job = db.get_scout_pipeline_job(pid)
    if not job:
        return jsonify({"error": "not found"}), 404
    d = request.get_json(silent=True) or {}

    # CV text: body wins and is remembered; else fall back to the cached CV.
    cv_text = (d.get("cv_text") or "").strip()
    if cv_text:
        db.kv_set(_CV_KV_KEY, cv_text)
    else:
        cv_text = (db.kv_get(_CV_KV_KEY) or "").strip()
    if not cv_text:
        return jsonify({"error": "No CV on file — send cv_text once and it will "
                                 "be remembered for future analyses."}), 400

    # Job description: body, else the scraped description for this job.
    job_description = (d.get("job_description") or "").strip()
    if not job_description:
        job_description = (db.find_scout_job_description(
            job_url=job.get("job_url"), title=job.get("job_title"),
            company=job.get("company")) or "").strip()
    if not job_description:
        return jsonify({"error": "Need a description to analyze — paste the job "
                                 "description (no scraped text found for this job)."}), 400

    result = ai.analyze_cv_match(cv_text, job_description)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "analysis failed")}), 502

    analyzed_at = datetime.now().isoformat()
    updated = db.save_cv_match(pid, result["score"], result["missing"], analyzed_at)
    return jsonify({
        "ok": True,
        "id": pid,
        "cv_match_score": result["score"],
        "missing_keywords": result["missing"],
        "required": result["required"],
        "nice_to_have": result["nice_to_have"],
        "match_analysis_at": analyzed_at,
    })


# ── Agent data layer: memory / audit / error budgets (Phase 3) ────────────────
# All auth-required (not in _PUBLIC_ENDPOINTS). Read endpoints for the three-tier
# memory, audit trail, and error budgets, plus episodic logging + budget init.

@app.route("/api/memory/episodic", methods=["GET", "POST"])
def api_memory_episodic():
    """GET: recent episodic memories (optional ?agent_id=&limit=).
    POST: log an episodic event {agent_id, event_type, summary, payload?}."""
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        agent_id = (d.get("agent_id") or "").strip()
        event_type = (d.get("event_type") or "").strip()
        summary = (d.get("summary") or "").strip()
        if not agent_id or not event_type or not summary:
            return jsonify({"error": "agent_id, event_type, summary required"}), 400
        db.log_episodic(agent_id, event_type, summary, d.get("payload"))
        return jsonify({"ok": True})
    agent_id = request.args.get("agent_id")
    limit = request.args.get("limit", 20, type=int)
    if agent_id:
        return jsonify(db.get_episodic(agent_id, limit))
    return jsonify(db.get_all_episodic(limit))


@app.route("/api/memory/reflective")
def api_memory_reflective():
    """Recent reflections for an agent (?agent_id= required, ?period=daily)."""
    agent_id = request.args.get("agent_id")
    if not agent_id:
        return jsonify({"error": "agent_id required"}), 400
    period = request.args.get("period", "daily")
    limit = request.args.get("limit", 7, type=int)
    return jsonify(db.get_agent_reflections(agent_id, period, limit))


@app.route("/api/memory/relationships")
def api_memory_relationships():
    """All agent relationships (for network visualisation)."""
    return jsonify(db.get_all_relationships())


@app.route("/api/audit")
def api_audit():
    """Audit log entries, optionally filtered by ?agent_id=&limit=."""
    agent_id = request.args.get("agent_id")
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.get_audit_log(agent_id, limit))


@app.route("/api/system/audit/verify")
@limiter.limit("10 per hour")
def api_system_audit_verify():
    """Verify the tamper-evident audit hash chain (Tier 3 Part 1). Read-only and
    occasional, so it carries an explicit strict limit on top of the default
    tiers. Returns {valid, total_entries, first_broken_id}."""
    return jsonify(db.verify_audit_chain())


@app.route("/api/error-budgets")
def api_error_budgets():
    """Error budget status for all agents (agent_id, target, current_rate,
    health, total_runs, successful_runs)."""
    return jsonify(db.get_all_error_budgets())


@app.route("/api/error-budgets/init", methods=["POST"])
def api_error_budgets_init():
    """Initialise an error budget for an agent. Body: {agent_id, target?, window_days?}."""
    d = request.get_json(force=True) or {}
    agent_id = (d.get("agent_id") or "").strip()
    if not agent_id:
        return jsonify({"error": "agent_id required"}), 400
    target = float(d.get("target", 0.95))
    window_days = int(d.get("window_days", 7))
    db.init_error_budget(agent_id, target, window_days)
    return jsonify({"ok": True, "budget": db.get_error_budget(agent_id)})


# ── Phase 5: control surfaces — skill registry + plan decomposition ────────────

@app.route("/api/skills")
def api_skills():
    """All registered skills, grouped by agent:
    {agent_id: [{skill_name, description, input_schema, output_schema}, ...]}."""
    grouped = {}
    for skill in db.get_all_skills():
        grouped.setdefault(skill["agent_id"], []).append({
            "skill_name": skill["skill_name"],
            "description": skill["description"],
            "input_schema": skill.get("input_schema"),
            "output_schema": skill.get("output_schema"),
        })
    return jsonify(grouped)


@app.route("/api/agents/<agent_id>/skills")
def api_agent_skills(agent_id):
    """Skills for one agent: [{skill_name, description, input_schema, output_schema}]."""
    return jsonify(db.get_agent_skills(agent_id))


@app.route("/api/plan/decompose", methods=["POST"])
def api_plan_decompose():
    """Decompose a user request into an agent-executable plan (uses Claude).
    Body: {"request": "..."}. The plan is saved as pending_approval."""
    d = request.get_json(force=True) or {}
    user_request = (d.get("request") or "").strip()
    if not user_request:
        return jsonify({"ok": False, "error": "request required"}), 400
    from services.planner import decompose_plan
    result = decompose_plan(user_request)
    return jsonify(result), (200 if result.get("ok") else 500)


@app.route("/api/plan/<plan_id>")
def api_plan_get(plan_id):
    """Plan details. decomposition is parsed back into a JSON array."""
    plan = db.get_plan(plan_id)
    if not plan:
        return jsonify({"error": "plan not found"}), 404
    decomposition = plan.get("decomposition")
    if isinstance(decomposition, str):
        try:
            decomposition = json.loads(decomposition)
        except (TypeError, ValueError):
            pass
    return jsonify({
        "plan_id": plan.get("plan_id"),
        "user_request": plan.get("user_request"),
        "decomposition": decomposition,
        "status": plan.get("status"),
        "reasoning": plan.get("reasoning"),
        "created_at": plan.get("created_at"),
        "approved_at": plan.get("approved_at"),
        "completed_at": plan.get("completed_at"),
    })


@app.route("/api/plan/<plan_id>/approve", methods=["POST"])
def api_plan_approve(plan_id):
    """Approve a plan, making it eligible for execution."""
    plan = db.get_plan(plan_id)
    if not plan:
        return jsonify({"ok": False, "error": "plan not found"}), 404
    db.approve_plan(plan_id)
    return jsonify({"ok": True, "plan_id": plan_id, "status": "approved"})


@app.route("/api/plan/<plan_id>/reject", methods=["POST"])
def api_plan_reject(plan_id):
    """Reject a plan."""
    plan = db.get_plan(plan_id)
    if not plan:
        return jsonify({"ok": False, "error": "plan not found"}), 404
    db.reject_plan(plan_id)
    return jsonify({"ok": True, "plan_id": plan_id, "status": "rejected"})


@app.route("/api/plan/<plan_id>/execute", methods=["POST"])
def api_plan_execute(plan_id):
    """Execute an approved plan (steps are simulated for now)."""
    from services.planner import execute_plan
    result = execute_plan(plan_id)
    if not result.get("ok"):
        # 404 when missing, 409 when not in an approved state.
        code = 404 if result.get("error") == "Plan not found" else 409
        return jsonify(result), code
    return jsonify(result)


@app.route("/api/plan/<plan_id>/results")
def api_plan_results(plan_id):
    """Execution results for a plan: [{step, agent, skill, input, output,
    status, duration}, ...]."""
    out = []
    for r in db.get_plan_results(plan_id):
        def _maybe_json(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except (TypeError, ValueError):
                    return v
            return v
        out.append({
            "step": r.get("step_index"),
            "agent": r.get("agent_id"),
            "skill": r.get("skill_name"),
            "input": _maybe_json(r.get("input_params")),
            "output": _maybe_json(r.get("output")),
            "status": r.get("status"),
            "error": r.get("error"),
            "duration": r.get("duration_ms"),
            "executed_at": r.get("executed_at"),
        })
    return jsonify(out)


def scout_daily_scan():
    """06:00 daily — scrape Indeed for new part-time roles and ping the bell."""
    started = datetime.now()
    outcome, count = "success", 0
    try:
        from services import scout
        count = scout.scan()
        logger.info("Scout daily scan: %d new jobs", count)
        if count:
            db.add_notification(
                f"🔎 Scout found {count} new job{'s' if count != 1 else ''}.", "scout")
    except Exception as e:
        outcome = "failure"
        logger.error("scout daily scan failed: %s", e)
    # Phase 3: record the run in the audit trail + error budget.
    try:
        dur_ms = int((datetime.now() - started).total_seconds() * 1000)
        db.log_audit("scout", "daily_scan", outcome,
                     reason="scheduled 06:00 job scan",
                     details={"new_jobs": count}, duration_ms=dur_ms)
        db.update_error_budget("scout", outcome == "success")
    except Exception as e:
        logger.error("scout audit log failed: %s", e)


# ── Security response headers ──────────────────────────────────────────────────
# CSP ships in Report-Only mode: the app relies on inline scripts on every
# page plus cdnjs/jsdelivr (Three.js, Chart.js, Phaser), Google Fonts, and
# YouTube embeds in the gym video modal. This policy allows all of those, but
# until it's verified against every screen in a real browser it observes
# rather than enforces (violations show in DevTools, nothing breaks).
_CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-src https://www.youtube.com; "
    "object-src 'none'; "
    "base-uri 'self'"
)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy-Report-Only", _CSP_REPORT_ONLY)
    return resp


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
