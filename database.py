import json
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")

# Use PostgreSQL on Railway if DATABASE_URL set, else SQLite
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    USE_POSTGRES = True
else:
    USE_POSTGRES = False
    SQLITE_PATH = os.path.join(os.path.dirname(__file__), "asfa.db")

# Canonical daily supplements: (key, display label). Shared by the API,
# scheduler reminders, and briefing so they never drift.
SUPPLEMENTS = [
    ("creatine", "Creatine"),
    ("omega3", "Omega-3 Fish Oil"),
    ("magnesium", "Magnesium"),
]


@contextmanager
def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        stmts = [
            """CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                water_ml INTEGER DEFAULT 0,
                sleep_hours REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                exercise TEXT NOT NULL,
                weight_kg REAL,
                reps INTEGER,
                sets INTEGER,
                muscle_group TEXT,
                notes TEXT,
                is_pb INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS body_weight (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                weight_kg REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS spending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                score INTEGER,
                content TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                target TEXT,
                progress INTEGER DEFAULT 0,
                month TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS daily_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                score INTEGER NOT NULL,
                breakdown TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                plain_text TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS voice_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                kind TEXT DEFAULT 'info',
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS hydration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount_ml INTEGER NOT NULL,
                logged_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplements_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplement_name TEXT NOT NULL,
                taken_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT
            )""",
        ]
        # Postgres uses SERIAL not AUTOINCREMENT
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                stmt = stmt.replace("datetime('now')", "NOW()")
            cursor.execute(stmt)


# ── Habit helpers ──────────────────────────────────────────────────────────────

def log_water(date: str, ml: int):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO habits (date, water_ml, sleep_hours) VALUES (%s, %s, 0) "
                "ON CONFLICT DO NOTHING", (date, 0))
            cur.execute(
                "UPDATE habits SET water_ml = water_ml + %s WHERE date = %s", (ml, date))
        else:
            cur.execute("INSERT OR IGNORE INTO habits (date, water_ml, sleep_hours) VALUES (?, 0, 0)", (date,))
            cur.execute("UPDATE habits SET water_ml = water_ml + ? WHERE date = ?", (ml, date))


def log_sleep(date: str, hours: float):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO habits (date, water_ml, sleep_hours) VALUES (%s, 0, %s) "
                "ON CONFLICT DO NOTHING", (date, hours))
            cur.execute("UPDATE habits SET sleep_hours = %s WHERE date = %s", (hours, date))
        else:
            cur.execute("INSERT OR IGNORE INTO habits (date, water_ml, sleep_hours) VALUES (?, 0, 0)", (date,))
            cur.execute("UPDATE habits SET sleep_hours = ? WHERE date = ?", (hours, date))


def get_water_logged(date: str) -> int:
    """Fresh count of total water (ml) logged for a given day, read straight
    from the DB so alerts never see stale/cached habit rows."""
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT water_ml FROM habits WHERE date = %s", (date,))
        else:
            cur.execute("SELECT water_ml FROM habits WHERE date = ?", (date,))
        row = cur.fetchone()
        if not row:
            return 0
        return int(row["water_ml"] or 0)


def get_habits(days: int = 7):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM habits WHERE CAST(date AS TIMESTAMP) >= NOW() - INTERVAL '%s days' ORDER BY date DESC", (days,))
        else:
            cur.execute(
                "SELECT * FROM habits WHERE date >= date('now', ?) ORDER BY date DESC",
                (f"-{days} days",))
        return [dict(r) for r in cur.fetchall()]


def log_hydration(date: str, amount_ml: int, logged_at: str = None):
    """Append a hydration ledger entry. Keeps a per-event audit trail in
    addition to the rolled-up habits.water_ml total."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if logged_at:
            cur.execute(
                f"INSERT INTO hydration_log (date, amount_ml, logged_at) VALUES ({ph},{ph},{ph})",
                (date, amount_ml, logged_at))
        else:
            cur.execute(
                f"INSERT INTO hydration_log (date, amount_ml) VALUES ({ph},{ph})",
                (date, amount_ml))


def get_hydration_total(date: str) -> int:
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COALESCE(SUM(amount_ml), 0) AS total FROM hydration_log WHERE date = {ph}",
            (date,))
        row = cur.fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0


def get_hydration_count(date: str) -> int:
    """Number of separate hydration entries logged on `date`."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COUNT(*) AS n FROM hydration_log WHERE date = {ph}", (date,))
        row = cur.fetchone()
        return int(row["n"]) if row and row["n"] is not None else 0


def get_water_streak():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT date, water_ml FROM habits ORDER BY date DESC LIMIT 30")
        rows = cur.fetchall()
    streak = 0
    for r in rows:
        if r["water_ml"] >= 2000:
            streak += 1
        else:
            break
    return streak


def get_pbs():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT exercise, MAX(weight_kg) as best_weight, MAX(reps) as best_reps "
            "FROM workouts GROUP BY exercise ORDER BY exercise")
        return [dict(r) for r in cur.fetchall()]


# ── Spending helpers ───────────────────────────────────────────────────────────

def log_spend(date, amount, category, note=""):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO spending (date, amount, category, note) VALUES ({ph},{ph},{ph},{ph})",
            (date, amount, category, note))


def get_spending(days: int = 7):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM spending WHERE CAST(date AS TIMESTAMP) >= NOW() - INTERVAL '%s days' ORDER BY date DESC",
                (days,))
        else:
            cur.execute(
                "SELECT * FROM spending WHERE date >= date('now', ?) ORDER BY date DESC",
                (f"-{days} days",))
        return [dict(r) for r in cur.fetchall()]


def get_workouts(days: int = 7) -> list:
    """Get workout log entries for the last N days (most recent first)."""
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM workouts WHERE CAST(date AS TIMESTAMP) >= NOW() - (%s * INTERVAL '1 day') ORDER BY date DESC",
                (days,))
        else:
            cur.execute(
                "SELECT * FROM workouts WHERE date >= date('now', ?) ORDER BY date DESC",
                (f"-{days} days",))
        return [dict(r) for r in cur.fetchall()]


# ── Memory helpers ─────────────────────────────────────────────────────────────

def save_memory(content, tags=""):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO memories (content, tags) VALUES ({ph},{ph})", (content, tags))


def get_memories(limit: int = 10):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM memories ORDER BY created_at DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── Conversation helpers ───────────────────────────────────────────────────────

def save_message(role, content):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO conversations (role, content) VALUES ({ph},{ph})", (role, content))


def get_recent_conversation(limit: int = 20):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT role, content FROM conversations ORDER BY created_at DESC LIMIT {ph}", (limit,))
        rows = cur.fetchall()
    return list(reversed([dict(r) for r in rows]))


# ── Reflection helpers ─────────────────────────────────────────────────────────

def save_reflection(date, score, content):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO reflections (date, score, content) VALUES ({ph},{ph},{ph})",
            (date, score, content))


def get_reflections(limit: int = 7):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM reflections ORDER BY date DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── Daily score helpers ────────────────────────────────────────────────────────

def save_daily_score(date, score, breakdown=""):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO daily_scores (date, score, breakdown) VALUES (%s, %s, %s) "
                "ON CONFLICT (date) DO UPDATE SET score=%s, breakdown=%s",
                (date, score, breakdown, score, breakdown))
        else:
            cur.execute(
                "INSERT OR REPLACE INTO daily_scores (date, score, breakdown) VALUES (?, ?, ?)",
                (date, score, breakdown))


def get_daily_scores(days: int = 7):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM daily_scores WHERE CAST(date AS TIMESTAMP) >= NOW() - INTERVAL '%s days' ORDER BY date",
                (days,))
        else:
            cur.execute(
                "SELECT * FROM daily_scores WHERE date >= date('now', ?) ORDER BY date",
                (f"-{days} days",))
        return [dict(r) for r in cur.fetchall()]


# ── Goal helpers ───────────────────────────────────────────────────────────────

def get_goals(month: str = None):
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM goals WHERE month = {ph} ORDER BY id", (month,))
        return [dict(r) for r in cur.fetchall()]


def add_goal(title, target, month=None):
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO goals (title, target, month) VALUES ({ph},{ph},{ph})",
            (title, target, month))


def update_goal_progress(goal_id, progress):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE goals SET progress = {ph} WHERE id = {ph}", (progress, goal_id))


# ── Voice notes ────────────────────────────────────────────────────────────────

def save_voice_note(content):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO voice_notes (content) VALUES ({ph})", (content,))


def get_voice_notes(date: str):
    """Voice notes / quick captures created on `date` (oldest first)."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT content, created_at FROM voice_notes "
            f"WHERE substr(created_at,1,10) = {ph} ORDER BY created_at ASC",
            (date,))
        return [dict(r) for r in cur.fetchall()]


# ── Supplements ────────────────────────────────────────────────────────────────
# Self-initialising: init_db() isn't called at boot, so the table is created
# lazily (idempotent CREATE IF NOT EXISTS) on first use, for SQLite and Postgres.

_SUPPLEMENTS_READY = False


def _ensure_supplements_table():
    global _SUPPLEMENTS_READY
    if _SUPPLEMENTS_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS supplements_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplement_name TEXT NOT NULL,
        taken_at TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        stmt = stmt.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _SUPPLEMENTS_READY = True


def log_supplement(name: str, taken_at: str = None):
    """Record a supplement as taken (idempotent per day handled by callers)."""
    _ensure_supplements_table()
    taken_at = taken_at or datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO supplements_log (supplement_name, taken_at) VALUES ({ph},{ph})",
            (name, taken_at))


def remove_supplement_today(name: str, date: str):
    """Undo a same-day check (uncheck the box)."""
    _ensure_supplements_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"DELETE FROM supplements_log WHERE supplement_name = {ph} AND taken_at LIKE {ph}",
            (name, f"{date}%"))


def get_supplements_today(date: str) -> dict:
    """Return {name: earliest_taken_at} for supplements taken on `date`."""
    _ensure_supplements_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT supplement_name, MIN(taken_at) AS taken_at FROM supplements_log "
            f"WHERE taken_at LIKE {ph} GROUP BY supplement_name",
            (f"{date}%",))
        return {r["supplement_name"]: r["taken_at"] for r in cur.fetchall()}


def count_supplements_today(date: str) -> int:
    return len(get_supplements_today(date))


def _streak_from_complete(complete, today):
    """Count consecutive days ending today (or yesterday, if today is still
    pending) present in the `complete` set of 'YYYY-MM-DD' strings."""
    cur = today if today.isoformat() in complete else today - timedelta(days=1)
    streak = 0
    while cur.isoformat() in complete:
        streak += 1
        cur -= timedelta(days=1)
    return streak


def get_supplements_streak():
    """Consecutive days where ALL supplements were taken. Today counts once
    complete, but a still-pending today won't break the streak."""
    _ensure_supplements_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT substr(taken_at,1,10) AS d, COUNT(DISTINCT supplement_name) AS n "
            "FROM supplements_log GROUP BY substr(taken_at,1,10)")
        rows = cur.fetchall()
    total = len(SUPPLEMENTS)
    complete = {r["d"] for r in rows if (r["n"] or 0) >= total}
    return _streak_from_complete(complete, date.today())


# ── Focus sessions (Lock In) ────────────────────────────────────────────────────
# Self-initialising, same as supplements: init_db() isn't called at boot, so the
# table is created lazily on first use (idempotent, SQLite + Postgres).

_FOCUS_READY = False


def _ensure_focus_table():
    global _FOCUS_READY
    if _FOCUS_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS focus_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        duration_seconds INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        stmt = stmt.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _FOCUS_READY = True


def log_focus_session(started_at, ended_at, duration_seconds):
    _ensure_focus_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO focus_sessions (started_at, ended_at, duration_seconds) "
            f"VALUES ({ph},{ph},{ph})",
            (started_at, ended_at, int(duration_seconds)))


def get_focus_seconds_today(date):
    """Total focused seconds for sessions that started on `date`."""
    _ensure_focus_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COALESCE(SUM(duration_seconds),0) AS s FROM focus_sessions "
            f"WHERE started_at LIKE {ph}",
            (f"{date}%",))
        row = cur.fetchone()
        return int((row["s"] if row else 0) or 0)


# ── Body weight ────────────────────────────────────────────────────────────────

def log_body_weight(date, weight_kg):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO body_weight (date, weight_kg) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", (date, weight_kg))
            cur.execute("UPDATE body_weight SET weight_kg = %s WHERE date = %s", (weight_kg, date))
        else:
            cur.execute(
                "INSERT OR REPLACE INTO body_weight (date, weight_kg) VALUES (?, ?)",
                (date, weight_kg))


def get_body_weight(days=30):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM body_weight WHERE CAST(date AS TIMESTAMP) >= NOW() - INTERVAL '%s days' ORDER BY date",
                (days,))
        else:
            cur.execute(
                "SELECT * FROM body_weight WHERE date >= date('now', ?) ORDER BY date",
                (f"-{days} days",))
        return [dict(r) for r in cur.fetchall()]


# ── Briefing cache ─────────────────────────────────────────────────────────────

def get_cached_briefing(date: str):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM briefings WHERE date = {ph}", (date,))
        row = cur.fetchone()
        return dict(row) if row else None


def save_briefing(date: str, content: str, plain_text: str):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO briefings (date, content, plain_text) VALUES (%s,%s,%s) "
                "ON CONFLICT (date) DO UPDATE SET content=%s, plain_text=%s",
                (date, content, plain_text, content, plain_text))
        else:
            cur.execute(
                "INSERT OR REPLACE INTO briefings (date, content, plain_text) VALUES (?,?,?)",
                (date, content, plain_text))


# ── Notifications ──────────────────────────────────────────────────────────────

def add_notification(message, kind="info"):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO notifications (message, kind) VALUES ({ph},{ph})", (message, kind))


def get_notifications(limit=20, unread_only=False):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        where = "WHERE is_read = 0" if unread_only else ""
        cur.execute(f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


def mark_notifications_read():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")


def ping():
    """Lightweight DB connectivity check (SELECT 1). Returns True on success,
    False on any failure. Used by the mission-control health endpoint."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


def count_recent_alerts(hours=24):
    """Count recent notification alerts by severity for the mission-control
    health check. Critical and warning are matched on the notifications.kind
    column within the last `hours` (compared against UTC created_at). Returns
    {"critical": int, "warning": int}."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    critical = warning = 0
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT LOWER(kind) AS k, COUNT(*) AS n FROM notifications "
            f"WHERE created_at >= {ph} GROUP BY LOWER(kind)",
            (cutoff,),
        )
        for r in cur.fetchall():
            kind = (r["k"] or "")
            n = r["n"]
            # Match only explicit severity kinds. The generic "alert" kind is a
            # catch-all for routine proactive notifications (bot/wellness nudges),
            # so it is deliberately NOT treated as a critical security alert.
            if kind in ("critical", "crit"):
                critical += n
            elif kind in ("warning", "warn"):
                warning += n
    return {"critical": critical, "warning": warning}


# ── Key-value store (scheduler state, snapshots) ───────────────────────────────

def kv_get(key, default=None):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT value FROM kv_store WHERE key = {ph}", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


def kv_set(key, value):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO kv_store (key, value) VALUES (%s,%s) "
                "ON CONFLICT (key) DO UPDATE SET value=%s",
                (key, value, value))
        else:
            cur.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?,?)", (key, value))


# ════════════════════════════════════════════════════════════════════════════
# MISSION CONTROL — gamified AI-agent ecosystem (agents, logs, battles, missions)
# Self-initialising, same pattern as supplements/focus: idempotent CREATE on
# first use plus a one-time seed, so it works on SQLite and a fresh Postgres.
# ════════════════════════════════════════════════════════════════════════════

_AGENTS_READY = False

# Seed roster. building_position is stored as a JSON blob. xp_max is derived from
# the level via _xp_max_for_level so the level-up maths stays consistent.
_AGENT_SEED = [
    ("nexus",    "Nexus",    "Multi-agent coordinator", "🛰️", 8, 850, "active",
     {"x": 40, "y": 28}, 142, 0,  12, 3),
    ("sentinel", "Sentinel", "Security auditor",         "🛡️", 7, 720, "active",
     {"x": 8,  "y": 8},  89,  34, 9,  2),
    ("axiom",    "Axiom",    "Code reviewer",            "🔍", 5, 430, "active",
     {"x": 60, "y": 8},  76,  21, 7,  4),
    ("pyro",     "Pyro",     "Python specialist",        "🐍", 4, 310, "idle",
     {"x": 6,  "y": 62}, 54,  8,  4,  5),
    ("quant",    "Quant",    "Trading analyst",          "📈", 6, 580, "idle",
     {"x": 60, "y": 62}, 63,  5,  8,  3),
    ("ghost",    "Ghost",    "Debugger",                 "👻", 3, 190, "idle",
     {"x": 82, "y": 62}, 28,  12, 2,  6),
    ("pixel",    "Pixel",    "Game developer",           "🎮", 3, 160, "idle",
     {"x": 82, "y": 8},  22,  3,  1,  2),
    ("forge",    "Forge",    "POD studio agent",         "⚒️", 0, 0,   "locked",
     {"x": 36, "y": 62}, 0,   0,  0,  0),
    # ── Real Claude Code agents (oracle / ledger / auto-docs / warden /
    #    incident-responder). xp_max is derived from level by _xp_max_for_level,
    #    so the level-up maths stays consistent. ──────────────────────────────
    ("oracle",   "Oracle",   "Research analyst",         "🔭", 2, 0,   "idle",
     {"x": 8,  "y": 36}, 0,   0,  0,  0),
    ("ledger",   "Ledger",   "Trading analyst",          "📊", 3, 0,   "idle",
     {"x": 82, "y": 36}, 0,   0,  0,  0),
    ("auto-docs","Auto-Docs","Documentation writer",     "📝", 1, 0,   "idle",
     {"x": 36, "y": 8},  0,   0,  0,  0),
    ("warden",   "Warden",   "Deployment monitor",       "🗼", 4, 0,   "active",
     {"x": 60, "y": 36}, 0,   0,  0,  0),
    ("incident-responder", "Incident Responder", "Crisis handler", "🚨", 5, 0, "idle",
     {"x": 36, "y": 62}, 0,   0,  0,  0),
]

# Daily mission pool. (title, description, xp_reward, target_agent_id)
# get_today_missions() draws 3 of these at random per day so they rotate.
_MISSION_TEMPLATES = [
    ("Run a security audit", "Have Sentinel sweep the codebase for vulnerabilities.",
     100, "sentinel"),
    ("Deploy 2 agents in parallel", "Coordinate a parallel multi-agent run via Nexus.",
     150, "nexus"),
    ("Win a battle", "Have any agent win a head-to-head battle.", 200, None),
    ("Research before you build", "Use Oracle to research a topic before writing code.",
     100, "oracle"),
    ("Check deployment health", "Use Warden to verify ASFA is online.",
     75, "warden"),
    ("Document a project", "Use Auto-Docs to generate docs for a project.",
     100, "auto-docs"),
]

# How many missions to surface per day (drawn from _MISSION_TEMPLATES).
DAILY_MISSION_COUNT = 3

BATTLE_XP = 75  # XP awarded to the winner of a battle


def _xp_max_for_level(level: int) -> int:
    """XP needed to clear a level. Grows linearly so higher levels take longer."""
    return (int(level) + 1) * 100


def _ensure_agents_tables():
    global _AGENTS_READY
    if _AGENTS_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT,
            icon TEXT,
            level INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            xp_max INTEGER DEFAULT 100,
            tasks_run INTEGER DEFAULT 0,
            findings INTEGER DEFAULT 0,
            status TEXT DEFAULT 'idle',
            building_position TEXT,
            battles_won INTEGER DEFAULT 0,
            battles_lost INTEGER DEFAULT 0,
            last_active TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS agent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            message TEXT NOT NULL,
            xp_earned INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS agent_battles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent1_id TEXT,
            agent2_id TEXT,
            topic TEXT,
            winner_id TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS daily_missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            xp_reward INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            date TEXT NOT NULL,
            agent_id TEXT
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                stmt = stmt.replace("datetime('now')", "NOW()")
            cur.execute(stmt)
    _AGENTS_READY = True


def seed_agents():
    """Insert the seed roster once. Idempotent — existing agents are left alone."""
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        for (aid, name, role, icon, level, xp, status, pos,
             tasks, findings, won, lost) in _AGENT_SEED:
            xp_max = _xp_max_for_level(level)
            cols = ("id, name, role, icon, level, xp, xp_max, tasks_run, findings, "
                    "status, building_position, battles_won, battles_lost")
            vals = (aid, name, role, icon, level, xp, xp_max, tasks, findings,
                    status, json.dumps(pos), won, lost)
            placeholders = ",".join([ph] * 13)
            if USE_POSTGRES:
                cur.execute(
                    f"INSERT INTO agents ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO NOTHING", vals)
            else:
                cur.execute(
                    f"INSERT OR IGNORE INTO agents ({cols}) VALUES ({placeholders})", vals)


def init_agents_db():
    """Create the Mission Control tables and seed the roster. Safe on every boot."""
    _ensure_agents_tables()
    seed_agents()


# ── Agent reads ──────────────────────────────────────────────────────────────

def _agent_row_to_dict(r) -> dict:
    d = dict(r)
    pos = d.get("building_position")
    try:
        d["building_position"] = json.loads(pos) if pos else {"x": 50, "y": 50}
    except (TypeError, ValueError):
        d["building_position"] = {"x": 50, "y": 50}
    return d


# Stable display order matching the seed roster.
_AGENT_ORDER = {a[0]: i for i, a in enumerate(_AGENT_SEED)}


def get_agents() -> list:
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agents")
        agents = [_agent_row_to_dict(r) for r in cur.fetchall()]
    agents.sort(key=lambda a: _AGENT_ORDER.get(a["id"], 999))
    return agents


def get_agent(agent_id: str):
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM agents WHERE id = {ph}", (agent_id,))
        row = cur.fetchone()
        return _agent_row_to_dict(row) if row else None


# ── Agent writes (XP / status / logs) ────────────────────────────────────────

def add_agent_log(agent_id: str, message: str, xp_earned: int = 0):
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO agent_log (agent_id, message, xp_earned) VALUES ({ph},{ph},{ph})",
            (agent_id, message, int(xp_earned)))


def get_agent_log(agent_id: str, limit: int = 20) -> list:
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT id, agent_id, timestamp, message, xp_earned FROM agent_log "
            f"WHERE agent_id = {ph} ORDER BY id DESC LIMIT {ph}",
            (agent_id, limit))
        return [dict(r) for r in cur.fetchall()]


def award_agent_xp(agent_id: str, amount: int, message: str = None) -> dict:
    """Add XP to an agent, rolling over level-ups. Bumps last_active and writes a
    log line. Returns {agent, leveled_up, levels_gained} or {error}."""
    _ensure_agents_tables()
    agent = get_agent(agent_id)
    if not agent:
        return {"error": "unknown agent"}
    amount = int(amount)
    level = int(agent["level"])
    xp = int(agent["xp"]) + amount
    xp_max = int(agent["xp_max"]) or _xp_max_for_level(level)
    levels_gained = 0
    # Roll forward through as many level-ups as the XP covers.
    while xp >= xp_max:
        xp -= xp_max
        level += 1
        levels_gained += 1
        xp_max = _xp_max_for_level(level)
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE agents SET level={ph}, xp={ph}, xp_max={ph}, last_active={ph} "
            f"WHERE id={ph}",
            (level, xp, xp_max, now, agent_id))
    if message:
        add_agent_log(agent_id, message, amount)
    if levels_gained:
        add_agent_log(agent_id, f"⬆️ Leveled up to L{level}!", 0)
    return {"agent": get_agent(agent_id), "leveled_up": levels_gained > 0,
            "levels_gained": levels_gained, "xp_awarded": amount}


def set_agent_status(agent_id: str, status: str) -> dict:
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE agents SET status={ph}, last_active={ph} WHERE id={ph}",
            (status, datetime.now().isoformat(), agent_id))
    return get_agent(agent_id)


def toggle_agent_status(agent_id: str) -> dict:
    """Flip active⇄idle. Locked agents stay locked (must be unlocked elsewhere)."""
    agent = get_agent(agent_id)
    if not agent:
        return {"error": "unknown agent"}
    if agent["status"] == "locked":
        return agent
    new_status = "idle" if agent["status"] == "active" else "active"
    return set_agent_status(agent_id, new_status)


def increment_agent_tasks(agent_id: str, n: int = 1):
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE agents SET tasks_run = tasks_run + {ph} WHERE id = {ph}",
            (int(n), agent_id))


# ── Battles ──────────────────────────────────────────────────────────────────

def create_battle(agent1_id: str, agent2_id: str, topic: str, winner_id: str,
                  xp: int = BATTLE_XP) -> dict:
    """Record a battle, update win/loss records, and award XP to the winner."""
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO agent_battles (agent1_id, agent2_id, topic, winner_id) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (agent1_id, agent2_id, topic, winner_id))
        loser_id = agent2_id if winner_id == agent1_id else agent1_id
        cur.execute(f"UPDATE agents SET battles_won = battles_won + 1 WHERE id = {ph}",
                    (winner_id,))
        cur.execute(f"UPDATE agents SET battles_lost = battles_lost + 1 WHERE id = {ph}",
                    (loser_id,))
    award = award_agent_xp(
        winner_id, xp, f"⚔️ Won a battle over '{topic}'")
    return {"winner_id": winner_id, "loser_id": loser_id, "topic": topic,
            "xp_awarded": xp, "result": award}


def get_recent_battles(limit: int = 10) -> list:
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_battles ORDER BY id DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── Daily missions ───────────────────────────────────────────────────────────

def get_today_missions() -> list:
    """Return today's missions, auto-generating the 3 defaults on first call."""
    _ensure_agents_tables()
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT COUNT(*) AS n FROM daily_missions WHERE date = {ph}", (today,))
        row = cur.fetchone()
        count = (row["n"] if row else 0) or 0
        if count == 0:
            # Draw a fresh, stable set of missions for the day. Seeding by date
            # keeps the same 3 for the whole day across workers/restarts while
            # still rotating day-to-day.
            pool = list(_MISSION_TEMPLATES)
            rng = random.Random(today)
            picks = rng.sample(pool, min(DAILY_MISSION_COUNT, len(pool)))
            for title, desc, reward, agent_id in picks:
                cur.execute(
                    f"INSERT INTO daily_missions (title, description, xp_reward, date, agent_id) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                    (title, desc, reward, today, agent_id))
        cur.execute(
            f"SELECT * FROM daily_missions WHERE date = {ph} ORDER BY id", (today,))
        return [dict(r) for r in cur.fetchall()]


def complete_mission(mission_id: int) -> dict:
    """Mark a mission complete (idempotent) and award its XP to the target agent."""
    _ensure_agents_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM daily_missions WHERE id = {ph}", (mission_id,))
        row = cur.fetchone()
        if not row:
            return {"error": "unknown mission"}
        mission = dict(row)
        if mission.get("completed"):
            return {"mission": mission, "already_completed": True}
        cur.execute(f"UPDATE daily_missions SET completed = 1 WHERE id = {ph}", (mission_id,))
    award = None
    if mission.get("agent_id") and mission.get("xp_reward"):
        award = award_agent_xp(
            mission["agent_id"], mission["xp_reward"],
            f"🎯 Completed mission: {mission['title']}")
    mission["completed"] = 1
    return {"mission": mission, "award": award}


# ════════════════════════════════════════════════════════════════════════════
# SCOUT — part-time job hunting agent (scraped jobs + application tracker)
# Self-initialising, same pattern as Mission Control / supplements / focus:
# idempotent CREATE on first use, works on SQLite and a fresh Postgres.
# ════════════════════════════════════════════════════════════════════════════

_SCOUT_READY = False


def _ensure_scout_tables():
    global _SCOUT_READY
    if _SCOUT_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS scout_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT,
            salary TEXT, job_type TEXT, url TEXT,
            description TEXT, source TEXT,
            posted_date TEXT, found_date TEXT,
            is_new INTEGER DEFAULT 1,
            applied INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS scout_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT, role TEXT, location TEXT,
            method TEXT, applied_date TEXT,
            status TEXT DEFAULT 'pending',
            notes TEXT
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _SCOUT_READY = True


# ── Scout jobs ───────────────────────────────────────────────────────────────

def scout_job_exists(url: str) -> bool:
    """Dedup check — True if a job with this url is already stored."""
    if not url:
        return False
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT 1 FROM scout_jobs WHERE url = {ph} LIMIT 1", (url,))
        return cur.fetchone() is not None


def add_scout_job(title, company, location, salary, job_type, url, description,
                  source, posted_date, found_date, is_new=1) -> bool:
    """Insert a scraped job, skipping duplicates by url. Returns True if inserted."""
    _ensure_scout_tables()
    if scout_job_exists(url):
        return False
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = ("title, company, location, salary, job_type, url, description, "
                "source, posted_date, found_date, is_new")
        placeholders = ",".join([ph] * 11)
        cur.execute(
            f"INSERT INTO scout_jobs ({cols}) VALUES ({placeholders})",
            (title, company, location, salary, job_type, url, description,
             source, posted_date, found_date, int(is_new)))
    return True


def get_scout_jobs(location=None, new_only=False) -> list:
    """All stored jobs, newest first. Optional case-insensitive location filter
    and a new_only flag (is_new = 1)."""
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        clauses, params = [], []
        if location:
            clauses.append(f"LOWER(location) LIKE {ph}")
            params.append(f"%{location.lower()}%")
        if new_only:
            clauses.append("is_new = 1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(f"SELECT * FROM scout_jobs {where} ORDER BY id DESC", tuple(params))
        return [dict(r) for r in cur.fetchall()]


def mark_scout_job_applied(job_id) -> None:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE scout_jobs SET applied = 1 WHERE id = {ph}", (job_id,))


# ── Scout applications ───────────────────────────────────────────────────────

def get_scout_applications() -> list:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM scout_applications ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]


def add_scout_application(company, role, location, method, applied_date,
                          status="pending", notes="") -> None:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = "company, role, location, method, applied_date, status, notes"
        placeholders = ",".join([ph] * 7)
        cur.execute(
            f"INSERT INTO scout_applications ({cols}) VALUES ({placeholders})",
            (company, role, location, method, applied_date, status, notes))


def update_scout_application_status(app_id, status, notes=None) -> None:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if notes is not None:
            cur.execute(
                f"UPDATE scout_applications SET status = {ph}, notes = {ph} WHERE id = {ph}",
                (status, notes, app_id))
        else:
            cur.execute(
                f"UPDATE scout_applications SET status = {ph} WHERE id = {ph}",
                (status, app_id))


def delete_scout_application(app_id) -> None:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM scout_applications WHERE id = {ph}", (app_id,))


# ════════════════════════════════════════════════════════════════════════════
# AGENT DATA LAYER — three-tier memory, audit trail, error budgets
# Phase 3: the data layer that makes ASFA agents intelligent. Self-initialising,
# same pattern as Mission Control / scout / supplements: idempotent CREATE on
# first use plus a one-time seed, so it works on SQLite and a fresh Postgres.
# Nothing here touches existing tables.
# ════════════════════════════════════════════════════════════════════════════

_AGENT_DATA_READY = False

# Logical ASFA agents tracked by the data layer. These are the background jobs /
# features that act on the user's behalf. Error budgets are initialised for all
# of them at startup.
AGENT_IDS = [
    "scout",          # part-time job hunter (scout.scan)
    "sentinel",       # proactive / predictive alerts
    "quant_bot",      # trading bot poll
    "briefing",       # morning briefing
    "hydration",      # water intake + nudges
    "health",         # health endpoint monitor
    "obsidian",       # vault sync
    "backup",         # DB backup
    "summary",        # daily summary
    "supplement",     # supplement reminders
    "weekly_review",  # weekly review
    "reflection",     # end-of-day reflection prompt
    "insights",       # insight generation
]

# Known relationships between agents (directed). Seeded once at startup.
_RELATIONSHIP_SEED = [
    ("scout", "sentinel", "triggers", "Scout alerts Sentinel on new job matches", 0.8),
    ("sentinel", "briefing", "triggers", "Sentinel feeds alerts into Morning Briefing", 0.9),
    ("quant_bot", "sentinel", "triggers", "Quant bot alerts Sentinel on trade signals", 0.7),
    ("briefing", "obsidian", "collaborates", "Briefing data synced to Obsidian vault", 0.6),
    ("hydration", "sentinel", "monitors", "Sentinel monitors hydration compliance", 0.5),
    ("health", "sentinel", "monitors", "Sentinel monitors health endpoint", 0.9),
]


def _ensure_agent_data_tables():
    global _AGENT_DATA_READY
    if _AGENT_DATA_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS agent_memory_episodic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS agent_memory_reflective (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            period TEXT NOT NULL,
            summary TEXT NOT NULL,
            stats TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS agent_memory_relationship (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            related_agent_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            description TEXT,
            strength REAL DEFAULT 1.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(agent_id, related_agent_id, relationship_type)
        )""",
        """CREATE TABLE IF NOT EXISTS agent_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            outcome TEXT NOT NULL,
            details TEXT,
            duration_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS agent_error_budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL UNIQUE,
            target_success_rate REAL DEFAULT 0.95,
            window_days INTEGER DEFAULT 7,
            total_runs INTEGER DEFAULT 0,
            successful_runs INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS agent_energy (
            agent_id TEXT PRIMARY KEY,
            energy REAL DEFAULT 100.0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Phase 5: control surfaces — skill registry, plans, execution results.
        """CREATE TABLE IF NOT EXISTS agent_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            description TEXT NOT NULL,
            input_schema TEXT,
            output_schema TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(agent_id, skill_name)
        )""",
        """CREATE TABLE IF NOT EXISTS execution_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT UNIQUE NOT NULL,
            user_request TEXT NOT NULL,
            decomposition TEXT,
            status TEXT DEFAULT 'pending_approval',
            reasoning TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP,
            completed_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS plan_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            agent_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            input_params TEXT,
            output TEXT,
            status TEXT,
            error TEXT,
            duration_ms INTEGER,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _AGENT_DATA_READY = True


def _json_or_none(value):
    """Serialise a dict/list to JSON, pass through strings, else None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


# ── Episodic memory ────────────────────────────────────────────────────────────

def log_episodic(agent_id, event_type, summary, payload=None):
    """Log an episodic memory event for an agent."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO agent_memory_episodic (agent_id, event_type, summary, payload) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (agent_id, event_type, summary, _json_or_none(payload)))


def get_episodic(agent_id, limit=20):
    """Get recent episodic memories for an agent (newest first)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_memory_episodic WHERE agent_id = {ph} "
            f"ORDER BY id DESC LIMIT {ph}",
            (agent_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_all_episodic(limit=50):
    """Get recent episodic memories across all agents (newest first)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_memory_episodic ORDER BY id DESC LIMIT {ph}",
            (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── Reflective memory ──────────────────────────────────────────────────────────
# NOTE: named save_agent_reflection / get_agent_reflections to avoid colliding
# with the existing user-facing save_reflection / get_reflections helpers above.

def save_agent_reflection(agent_id, period, summary, stats=None):
    """Save a reflective summary for an agent."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO agent_memory_reflective (agent_id, period, summary, stats) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (agent_id, period, summary, _json_or_none(stats)))


def get_agent_reflections(agent_id, period='daily', limit=7):
    """Get recent reflections for an agent (newest first)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_memory_reflective WHERE agent_id = {ph} AND period = {ph} "
            f"ORDER BY id DESC LIMIT {ph}",
            (agent_id, period, limit))
        return [dict(r) for r in cur.fetchall()]


# ── Relationship memory ────────────────────────────────────────────────────────

def upsert_relationship(agent_id, related_agent_id, relationship_type,
                        description=None, strength=1.0):
    """Create or update a relationship between two agents (idempotent)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO agent_memory_relationship "
                "(agent_id, related_agent_id, relationship_type, description, strength) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (agent_id, related_agent_id, relationship_type) "
                "DO UPDATE SET description=%s, strength=%s, updated_at=NOW()",
                (agent_id, related_agent_id, relationship_type, description, strength,
                 description, strength))
        else:
            cur.execute(
                "UPDATE agent_memory_relationship "
                "SET description=?, strength=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE agent_id=? AND related_agent_id=? AND relationship_type=?",
                (description, strength, agent_id, related_agent_id, relationship_type))
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO agent_memory_relationship "
                    "(agent_id, related_agent_id, relationship_type, description, strength) "
                    "VALUES (?,?,?,?,?)",
                    (agent_id, related_agent_id, relationship_type, description, strength))


def get_relationships(agent_id):
    """Get all relationships originating from an agent."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_memory_relationship WHERE agent_id = {ph} ORDER BY id",
            (agent_id,))
        return [dict(r) for r in cur.fetchall()]


def get_all_relationships():
    """Get all agent relationships (for network visualization later)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_memory_relationship ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def seed_relationships():
    """Seed the known agent relationships once. Idempotent via upsert."""
    _ensure_agent_data_tables()
    for r in _RELATIONSHIP_SEED:
        upsert_relationship(*r)


# ── Audit trail ────────────────────────────────────────────────────────────────

def log_audit(agent_id, action, outcome, reason=None, details=None, duration_ms=None):
    """Log an agent action to the audit trail."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO agent_audit_log "
            f"(agent_id, action, reason, outcome, details, duration_ms) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
            (agent_id, action, reason, outcome, _json_or_none(details),
             int(duration_ms) if duration_ms is not None else None))


def get_audit_log(agent_id=None, limit=50):
    """Get audit log entries, optionally filtered by agent (newest first)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if agent_id:
            cur.execute(
                f"SELECT * FROM agent_audit_log WHERE agent_id = {ph} "
                f"ORDER BY id DESC LIMIT {ph}",
                (agent_id, limit))
        else:
            cur.execute(
                f"SELECT * FROM agent_audit_log ORDER BY id DESC LIMIT {ph}",
                (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── Error budgets ──────────────────────────────────────────────────────────────

def init_error_budget(agent_id, target_success_rate=0.95, window_days=7):
    """Initialize error budget for an agent (idempotent — leaves existing rows)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO agent_error_budgets (agent_id, target_success_rate, window_days) "
                "VALUES (%s,%s,%s) ON CONFLICT (agent_id) DO NOTHING",
                (agent_id, target_success_rate, window_days))
        else:
            cur.execute(
                "INSERT OR IGNORE INTO agent_error_budgets "
                "(agent_id, target_success_rate, window_days) VALUES (?,?,?)",
                (agent_id, target_success_rate, window_days))


def update_error_budget(agent_id, success: bool):
    """Record a run result and update the error budget. Auto-inits if missing."""
    _ensure_agent_data_tables()
    init_error_budget(agent_id)
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        inc = 1 if success else 0
        if USE_POSTGRES:
            cur.execute(
                "UPDATE agent_error_budgets SET total_runs = total_runs + 1, "
                "successful_runs = successful_runs + %s, last_updated = NOW() "
                "WHERE agent_id = %s",
                (inc, agent_id))
        else:
            cur.execute(
                "UPDATE agent_error_budgets SET total_runs = total_runs + 1, "
                "successful_runs = successful_runs + ?, last_updated = CURRENT_TIMESTAMP "
                "WHERE agent_id = ?",
                (inc, agent_id))


def _budget_with_health(row) -> dict:
    """Enrich an error-budget row with current_rate and health."""
    d = dict(row)
    total = int(d.get("total_runs") or 0)
    ok = int(d.get("successful_runs") or 0)
    target = float(d.get("target_success_rate") or 0.95)
    rate = (ok / total) if total else 1.0
    if rate >= target:
        health = "healthy"
    elif rate >= target - 0.10:
        health = "warning"
    else:
        health = "critical"
    d["current_rate"] = round(rate, 4)
    d["target"] = target
    d["health"] = health
    return d


def get_error_budget(agent_id):
    """Get current error budget status for an agent (with current_rate + health)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_error_budgets WHERE agent_id = {ph}", (agent_id,))
        row = cur.fetchone()
        return _budget_with_health(row) if row else None


def get_all_error_budgets():
    """Get error budget status for all agents (with current_rate + health)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_error_budgets ORDER BY agent_id")
        return [_budget_with_health(r) for r in cur.fetchall()]


def get_budget_health(agent_id):
    """Returns 'healthy', 'warning', or 'critical' for an agent's error budget.

    healthy  = current_rate >= target
    warning  = current_rate >= target - 0.10
    critical = current_rate <  target - 0.10
    An agent with no recorded runs is treated as healthy.
    """
    budget = get_error_budget(agent_id)
    return budget["health"] if budget else "healthy"


# ── Energy economy ─────────────────────────────────────────────────────────────
# Phase 4: each agent has an energy reserve (0-100) that rises on success and
# falls on failure/skip, giving a quick at-a-glance morale/health signal.

def init_energy(agent_id, starting_energy=100.0):
    """Initialize energy for an agent (idempotent — leaves existing rows)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO agent_energy (agent_id, energy) VALUES (%s,%s) "
                "ON CONFLICT (agent_id) DO NOTHING",
                (agent_id, float(starting_energy)))
        else:
            cur.execute(
                "INSERT OR IGNORE INTO agent_energy (agent_id, energy) VALUES (?,?)",
                (agent_id, float(starting_energy)))


def get_energy(agent_id):
    """Get current energy row for an agent ({agent_id, energy, last_updated}) or
    None if it has never been initialised."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM agent_energy WHERE agent_id = {ph}", (agent_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_energy(agent_id, delta: float):
    """Add or subtract energy from an agent (auto-inits if missing), clamped to
    0-100. Convention: +5 on success, -10 on failure, -2 on skip/timeout.
    Returns the new energy level."""
    _ensure_agent_data_tables()
    init_energy(agent_id)
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT energy FROM agent_energy WHERE agent_id = {ph}", (agent_id,))
        row = cur.fetchone()
        current = float(row["energy"]) if row and row["energy"] is not None else 100.0
        new_energy = max(0.0, min(100.0, current + float(delta)))
        if USE_POSTGRES:
            cur.execute(
                "UPDATE agent_energy SET energy = %s, last_updated = NOW() "
                "WHERE agent_id = %s",
                (new_energy, agent_id))
        else:
            cur.execute(
                "UPDATE agent_energy SET energy = ?, last_updated = CURRENT_TIMESTAMP "
                "WHERE agent_id = ?",
                (new_energy, agent_id))
    return new_energy


def get_all_energy():
    """Get energy levels for all agents (ordered by agent_id)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_energy ORDER BY agent_id")
        return [dict(r) for r in cur.fetchall()]


# ════════════════════════════════════════════════════════════════════════════
# CONTROL SURFACES — skill registry, execution plans, plan results
# Phase 5: gives ASFA agents a declared capability surface (skills), and a
# user-request → decomposed plan → approval → execution audit chain. Same
# self-initialising idempotent pattern as the rest of the agent data layer.
# ════════════════════════════════════════════════════════════════════════════

# ── Skill registry ─────────────────────────────────────────────────────────────

def register_skill(agent_id, skill_name, description, input_schema=None, output_schema=None):
    """Register a skill for an agent (idempotent on agent_id + skill_name)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO agent_skills "
                "(agent_id, skill_name, description, input_schema, output_schema) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (agent_id, skill_name) DO UPDATE SET "
                "description = EXCLUDED.description, "
                "input_schema = EXCLUDED.input_schema, "
                "output_schema = EXCLUDED.output_schema",
                (agent_id, skill_name, description,
                 _json_or_none(input_schema), _json_or_none(output_schema)))
        else:
            cur.execute(
                "INSERT INTO agent_skills "
                "(agent_id, skill_name, description, input_schema, output_schema) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(agent_id, skill_name) DO UPDATE SET "
                "description = excluded.description, "
                "input_schema = excluded.input_schema, "
                "output_schema = excluded.output_schema",
                (agent_id, skill_name, description,
                 _json_or_none(input_schema), _json_or_none(output_schema)))


def get_agent_skills(agent_id):
    """Get all skills for an agent (ordered by skill name)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM agent_skills WHERE agent_id = {ph} ORDER BY skill_name",
            (agent_id,))
        return [dict(r) for r in cur.fetchall()]


def get_all_skills():
    """Get all skills across all agents (ordered by agent then skill name)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_skills ORDER BY agent_id, skill_name")
        return [dict(r) for r in cur.fetchall()]


def skill_exists(agent_id, skill_name):
    """Check if a skill exists for an agent."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT 1 FROM agent_skills WHERE agent_id = {ph} AND skill_name = {ph}",
            (agent_id, skill_name))
        return cur.fetchone() is not None


# ── Execution plans ──────────────────────────────────────────────────────────────

def create_plan(plan_id, user_request, decomposition, reasoning):
    """Create a new execution plan (status defaults to pending_approval).
    decomposition/reasoning are stored as-is; pass a JSON string for decomposition."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO execution_plans "
            f"(plan_id, user_request, decomposition, reasoning) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (plan_id, user_request, _json_or_none(decomposition), reasoning))


def get_plan(plan_id):
    """Get plan details (decomposition is returned as the stored JSON string)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM execution_plans WHERE plan_id = {ph}", (plan_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def approve_plan(plan_id):
    """Mark plan as approved and stamp approved_at."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        ts = "NOW()" if USE_POSTGRES else "CURRENT_TIMESTAMP"
        cur.execute(
            f"UPDATE execution_plans SET status = 'approved', approved_at = {ts} "
            f"WHERE plan_id = {ph}",
            (plan_id,))


def reject_plan(plan_id):
    """Mark plan as rejected."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE execution_plans SET status = 'rejected' WHERE plan_id = {ph}",
            (plan_id,))


def set_plan_status(plan_id, status):
    """Set an arbitrary plan status (e.g. executing, complete, failed). Stamps
    completed_at when moving to a terminal state."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if status in ("complete", "failed"):
            ts = "NOW()" if USE_POSTGRES else "CURRENT_TIMESTAMP"
            cur.execute(
                f"UPDATE execution_plans SET status = {ph}, completed_at = {ts} "
                f"WHERE plan_id = {ph}",
                (status, plan_id))
        else:
            cur.execute(
                f"UPDATE execution_plans SET status = {ph} WHERE plan_id = {ph}",
                (status, plan_id))


# ── Plan execution results ──────────────────────────────────────────────────────

def log_plan_execution(plan_id, step_index, agent_id, skill_name, input_params,
                       output, status, error=None, duration_ms=None):
    """Log execution of a single plan step."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO plan_executions "
            f"(plan_id, step_index, agent_id, skill_name, input_params, output, "
            f"status, error, duration_ms) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (plan_id, step_index, agent_id, skill_name,
             _json_or_none(input_params), _json_or_none(output), status, error,
             int(duration_ms) if duration_ms is not None else None))


def get_plan_results(plan_id):
    """Get all execution results for a plan (ordered by step)."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM plan_executions WHERE plan_id = {ph} "
            f"ORDER BY step_index, id",
            (plan_id,))
        return [dict(r) for r in cur.fetchall()]


# ── Skill seed ────────────────────────────────────────────────────────────────
# Declared capability surface for each of the 13 agents in AGENT_IDS.
AGENT_SKILLS = {
    "scout": [
        ("scan_jobs", "Search for retail positions on Reed and SerpAPI",
         '{"keywords": "array", "location": "string", "limit": "integer"}',
         '{"matches": "array", "count": "integer"}'),
        ("filter_results", "Filter job matches by salary/location/type",
         '{"jobs": "array", "filters": "object"}',
         '{"filtered": "array"}'),
        ("apply_for_role", "Submit application to matched job",
         '{"job_id": "string", "cv_version": "string"}',
         '{"success": "boolean", "application_id": "string"}'),
    ],
    "sentinel": [
        ("monitor_alerts", "Watch for critical system events",
         None, '{"alert_count": "integer", "critical": "array"}'),
        ("escalate", "Escalate critical alerts to user",
         '{"alerts": "array", "severity": "string"}', '{"sent": "boolean"}'),
    ],
    "quant_bot": [
        ("scan_signals", "Run momentum strategy on S&P 500",
         None, '{"signals": "array", "trade_count": "integer"}'),
        ("execute_trade", "Place trade based on signal",
         '{"signal": "object", "size": "number"}', '{"order_id": "string", "status": "string"}'),
    ],
    "briefing": [
        ("generate_briefing", "Create morning briefing from overnight data",
         None, '{"briefing": "string", "items": "integer"}'),
    ],
    "hydration": [
        ("log_intake", "Log water intake event",
         '{"amount_ml": "number", "timestamp": "string"}', '{"total_today": "number"}'),
        ("get_status", "Get current hydration status",
         None, '{"logged_ml": "number", "target_ml": "number", "percent": "number"}'),
    ],
    "health": [
        ("check_endpoint", "Ping a system endpoint for health",
         '{"endpoint": "string"}', '{"up": "boolean", "latency_ms": "integer"}'),
    ],
    "obsidian": [
        ("sync_vault", "Push daily logs to Obsidian vault",
         None, '{"synced_files": "integer", "status": "string"}'),
    ],
    # Minimal skills for the background jobs
    "backup": [("backup_db", "Run database backup", None, '{"bytes": "integer"}')],
    "summary": [("summarize_day", "Create daily summary", None, '{"summary": "string"}')],
    "supplement": [("log_supplement", "Log supplement intake", '{"name": "string", "dose": "string"}', '{"logged": "boolean"}')],
    "weekly_review": [("generate_review", "Create weekly review", None, '{"review": "string"}')],
    "reflection": [("prompt_reflection", "Prompt for daily reflection", None, '{"prompt": "string"}')],
    "insights": [("generate_insights", "Extract patterns from logs", None, '{"insights": "array"}')],
}


def seed_skills():
    """Register the declared skills for every agent once. Idempotent."""
    _ensure_agent_data_tables()
    for agent_id, skills in AGENT_SKILLS.items():
        for skill_name, description, input_schema, output_schema in skills:
            register_skill(agent_id, skill_name, description, input_schema, output_schema)


def init_agent_data():
    """Create the agent data-layer tables, seed relationships + skills, and
    initialise an error budget and energy reserve for every known agent. Safe to
    call on every boot."""
    _ensure_agent_data_tables()
    seed_relationships()
    seed_skills()
    for aid in AGENT_IDS:
        init_error_budget(aid)
        init_energy(aid)


# ══════════════════════════════════════════════════════════════════════════════
# GYM TRACKER
# ══════════════════════════════════════════════════════════════════════════════
# Standalone gym-tracking module: exercise library, routine templates, logged
# sessions/sets, personal records, body stats, and XP/rank gamification. All
# tables are namespaced ``gym_*`` and never touch the existing schema. Tables are
# created + seeded lazily (idempotent) and also on boot via ``init_gym_data``.

import gym_seed

GYM_RANKS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
_RANK_SCORE = {name: i + 1 for i, name in enumerate(GYM_RANKS)}
# Overall (account-wide) rank tiers keyed off total XP, highest first.
_OVERALL_XP_TIERS = [
    (40000, "Diamond"),
    (15000, "Platinum"),
    (5000, "Gold"),
    (1000, "Silver"),
    (0, "Bronze"),
]

_GYM_READY = False


def _ensure_gym_tables():
    """Create every gym_* table if missing. Idempotent; handles SQLite/Postgres."""
    global _GYM_READY
    if _GYM_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS gym_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            muscle_group TEXT NOT NULL,
            secondary_muscles TEXT,
            equipment TEXT,
            exercise_type TEXT,
            youtube_url TEXT,
            instructions TEXT,
            tips TEXT,
            rank_bronze REAL,
            rank_silver REAL,
            rank_gold REAL,
            rank_platinum REAL,
            rank_diamond REAL
        )""",
        """CREATE TABLE IF NOT EXISTS gym_routines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            day_type TEXT NOT NULL,
            description TEXT,
            order_index INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS gym_routine_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            routine_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            sets INTEGER DEFAULT 3,
            rep_min INTEGER DEFAULT 8,
            rep_max INTEGER DEFAULT 12,
            rest_seconds INTEGER DEFAULT 90,
            order_index INTEGER DEFAULT 0,
            notes TEXT,
            is_cardio BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS gym_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            routine_id INTEGER,
            date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            duration_minutes INTEGER,
            notes TEXT,
            total_volume_kg REAL DEFAULT 0,
            total_sets INTEGER DEFAULT 0,
            xp_earned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS gym_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            set_number INTEGER NOT NULL,
            set_type TEXT DEFAULT 'working',
            weight_kg REAL,
            reps INTEGER,
            is_pr BOOLEAN DEFAULT FALSE,
            notes TEXT,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS gym_prs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL UNIQUE,
            weight_kg REAL NOT NULL,
            reps INTEGER NOT NULL,
            one_rep_max REAL,
            achieved_at TEXT NOT NULL,
            session_id INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS gym_body_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            weight_kg REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS gym_xp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_xp INTEGER DEFAULT 0,
            overall_rank TEXT DEFAULT 'Bronze',
            streak_days INTEGER DEFAULT 0,
            last_workout_date TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS gym_muscle_ranks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muscle_group TEXT NOT NULL UNIQUE,
            current_rank TEXT DEFAULT 'Bronze',
            best_weight_kg REAL DEFAULT 0,
            rank_score REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _GYM_READY = True


def _gym_insert(cur, table: str, cols: str, values: tuple):
    """INSERT a row and return its new id (Postgres RETURNING / SQLite lastrowid)."""
    ph = "%s" if USE_POSTGRES else "?"
    placeholders = ",".join([ph] * len(values))
    if USE_POSTGRES:
        cur.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id", values)
        return cur.fetchone()["id"]
    cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
    return cur.lastrowid


# ── Helpers ──────────────────────────────────────────────────────────────────

def calculate_one_rep_max(weight_kg: float, reps: int) -> float:
    """Estimated 1RM via the Epley formula: weight × (1 + reps/30)."""
    weight_kg = float(weight_kg or 0)
    reps = int(reps or 0)
    return round(weight_kg * (1 + reps / 30), 2)


def _rank_for_weight(ex: dict, weight_kg: float) -> str:
    """Highest rank whose threshold ``weight_kg`` meets, floored at Bronze."""
    weight_kg = float(weight_kg or 0)
    rank = "Bronze"
    for name in GYM_RANKS:
        threshold = ex.get(f"rank_{name.lower()}")
        if threshold is not None and weight_kg >= threshold:
            rank = name
    return rank


def get_exercise_rank(exercise_name: str, weight_kg: float) -> str:
    """Bronze/Silver/Gold/Platinum/Diamond for a weight on a named exercise."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM gym_exercises WHERE name = {ph}", (exercise_name,))
        row = cur.fetchone()
    if not row:
        return "Bronze"
    return _rank_for_weight(dict(row), weight_kg)


def _exercise_row_to_dict(row) -> dict:
    d = dict(row)
    sec = d.get("secondary_muscles")
    try:
        d["secondary_muscles"] = json.loads(sec) if sec else []
    except (TypeError, ValueError):
        d["secondary_muscles"] = []
    return d


# ── Seeding ──────────────────────────────────────────────────────────────────

def seed_gym_exercises():
    """Insert the exercise library once. Idempotent (upsert on unique name)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = ("name, muscle_group, secondary_muscles, equipment, exercise_type, "
                "youtube_url, instructions, tips, rank_bronze, rank_silver, "
                "rank_gold, rank_platinum, rank_diamond")
        placeholders = ",".join([ph] * 13)
        for ex in gym_seed.EXERCISES:
            vals = (
                ex["name"], ex["muscle_group"],
                json.dumps(ex.get("secondary_muscles", [])),
                ex.get("equipment"), ex.get("exercise_type"), ex.get("youtube_url"),
                ex.get("instructions"), ex.get("tips"),
                ex.get("rank_bronze"), ex.get("rank_silver"), ex.get("rank_gold"),
                ex.get("rank_platinum"), ex.get("rank_diamond"),
            )
            if USE_POSTGRES:
                cur.execute(
                    f"INSERT INTO gym_exercises ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT (name) DO NOTHING", vals)
            else:
                cur.execute(
                    f"INSERT OR IGNORE INTO gym_exercises ({cols}) VALUES ({placeholders})",
                    vals)


def _exercise_id_by_name(cur, name: str):
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT id FROM gym_exercises WHERE name = {ph}", (name,))
    row = cur.fetchone()
    return row["id"] if row else None


def seed_gym_routines():
    """Insert routine templates + their exercise lists once. Idempotent — a
    routine is only populated if it doesn't already exist by name. Routine
    exercises referencing an unknown exercise name are skipped."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        for r in gym_seed.ROUTINES:
            cur.execute(f"SELECT id FROM gym_routines WHERE name = {ph}", (r["name"],))
            if cur.fetchone():
                continue  # already seeded
            routine_id = _gym_insert(
                cur, "gym_routines", "name, day_type, description, order_index",
                (r["name"], r["day_type"], r.get("description"), r.get("order_index", 0)))
            for idx, (ex_name, sets, rep_min, rep_max, rest) in enumerate(
                    gym_seed.ROUTINE_EXERCISES.get(r["name"], [])):
                ex_id = _exercise_id_by_name(cur, ex_name)
                if ex_id is None:
                    continue  # exercise not in library — skip gracefully
                is_cardio = ex_name == "Incline Walk"
                _gym_insert(
                    cur, "gym_routine_exercises",
                    "routine_id, exercise_id, sets, rep_min, rep_max, rest_seconds, "
                    "order_index, is_cardio",
                    (routine_id, ex_id, sets, rep_min, rep_max, rest, idx, is_cardio))


def init_gym_data():
    """Create the gym tables and seed the exercise library + routines. Also
    ensures the singleton XP row exists. Safe to call on every boot."""
    _ensure_gym_tables()
    seed_gym_exercises()
    seed_gym_routines()
    _ensure_gym_xp_row()


# ── Exercises ────────────────────────────────────────────────────────────────

def get_all_exercises() -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM gym_exercises ORDER BY muscle_group, name")
        return [_exercise_row_to_dict(r) for r in cur.fetchall()]


def get_exercise(exercise_id: int) -> dict:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM gym_exercises WHERE id = {ph}", (exercise_id,))
        row = cur.fetchone()
        return _exercise_row_to_dict(row) if row else None


def get_exercises_by_muscle(muscle_group: str) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM gym_exercises WHERE muscle_group = {ph} ORDER BY name",
            (muscle_group,))
        return [_exercise_row_to_dict(r) for r in cur.fetchall()]


# ── Routines ─────────────────────────────────────────────────────────────────

def get_all_routines() -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM gym_routines ORDER BY order_index, id")
        return [dict(r) for r in cur.fetchall()]


def get_routine(routine_id: int) -> dict:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM gym_routines WHERE id = {ph}", (routine_id,))
        row = cur.fetchone()
        if not row:
            return None
        routine = dict(row)
    routine["exercises"] = get_routine_exercises(routine_id)
    return routine


def get_routine_exercises(routine_id: int) -> list:
    """Exercises in a routine, joined with the exercise library details."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT re.id AS routine_exercise_id, re.routine_id, re.exercise_id,
                       re.sets, re.rep_min, re.rep_max, re.rest_seconds,
                       re.order_index, re.notes, re.is_cardio,
                       e.name, e.muscle_group, e.secondary_muscles, e.equipment,
                       e.exercise_type, e.youtube_url, e.instructions, e.tips,
                       e.rank_bronze, e.rank_silver, e.rank_gold,
                       e.rank_platinum, e.rank_diamond
                FROM gym_routine_exercises re
                JOIN gym_exercises e ON e.id = re.exercise_id
                WHERE re.routine_id = {ph}
                ORDER BY re.order_index, re.id""",
            (routine_id,))
        out = []
        for r in cur.fetchall():
            d = dict(r)
            sec = d.get("secondary_muscles")
            try:
                d["secondary_muscles"] = json.loads(sec) if sec else []
            except (TypeError, ValueError):
                d["secondary_muscles"] = []
            out.append(d)
        return out


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(routine_id, date, start_time, notes="") -> int:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        return _gym_insert(
            cur, "gym_sessions", "routine_id, date, start_time, notes",
            (routine_id, date, start_time, notes))


def end_session(session_id, end_time, duration_minutes, total_volume,
                total_sets, xp_earned) -> None:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""UPDATE gym_sessions SET end_time = {ph}, duration_minutes = {ph},
                total_volume_kg = {ph}, total_sets = {ph}, xp_earned = {ph}
                WHERE id = {ph}""",
            (end_time, duration_minutes, total_volume, total_sets, xp_earned, session_id))


def get_session(session_id: int) -> dict:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM gym_sessions WHERE id = {ph}", (session_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_recent_sessions(limit=10) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT s.*, r.name AS routine_name, r.day_type
                FROM gym_sessions s
                LEFT JOIN gym_routines r ON r.id = s.routine_id
                ORDER BY s.date DESC, s.id DESC LIMIT {ph}""",
            (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_sessions_by_date_range(start_date, end_date) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT s.*, r.name AS routine_name, r.day_type
                FROM gym_sessions s
                LEFT JOIN gym_routines r ON r.id = s.routine_id
                WHERE s.date >= {ph} AND s.date <= {ph}
                ORDER BY s.date DESC, s.id DESC""",
            (start_date, end_date))
        return [dict(r) for r in cur.fetchall()]


def get_streak_calendar(months=3) -> dict:
    """Return {date_str: bool} for every day in the last ``months``, where the
    value is True if at least one session was logged that day."""
    _ensure_gym_tables()
    end = date.today()
    start = end - timedelta(days=months * 31)
    worked = set()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT DISTINCT date FROM gym_sessions WHERE date >= {ph} AND date <= {ph}",
            (start.isoformat(), end.isoformat()))
        for r in cur.fetchall():
            # normalise to YYYY-MM-DD in case of stored timestamps
            worked.add(str(r["date"])[:10])
    calendar = {}
    d = start
    while d <= end:
        key = d.isoformat()
        calendar[key] = key in worked
        d += timedelta(days=1)
    return calendar


# ── Sets ─────────────────────────────────────────────────────────────────────

def _xp_for_set(weight_kg: float, reps: int, is_pr: bool) -> int:
    """XP for a single logged set: volume-based, with a bonus for a new PR.
    Bodyweight/cardio sets (weight 0) still earn XP off reps/minutes."""
    weight_kg = float(weight_kg or 0)
    reps = int(reps or 0)
    xp = round(weight_kg * reps * 0.1)
    if xp < 1:
        xp = max(reps, 1)
    if is_pr:
        xp += 50
    return int(xp)


def log_set(session_id, exercise_id, set_number, set_type, weight_kg, reps) -> dict:
    """Log a single set. Detects a personal record (by estimated 1RM), updates
    the PR table, awards XP, and bumps the exercise's muscle-group rank.
    Returns {id, is_pr, one_rep_max, xp_earned, rank}."""
    _ensure_gym_tables()
    weight_kg = float(weight_kg or 0)
    reps = int(reps or 0)
    one_rm = calculate_one_rep_max(weight_kg, reps)

    existing = get_pr(exercise_id)
    is_pr = existing is None or one_rm > (existing.get("one_rep_max") or 0)

    with get_db() as conn:
        cur = conn.cursor()
        set_id = _gym_insert(
            cur, "gym_sets",
            "session_id, exercise_id, set_number, set_type, weight_kg, reps, is_pr",
            (session_id, exercise_id, set_number, set_type, weight_kg, reps, bool(is_pr)))

    ex = get_exercise(exercise_id)
    today = date.today().isoformat()
    if is_pr:
        update_pr(exercise_id, weight_kg, reps, one_rm, today, session_id)

    xp = _xp_for_set(weight_kg, reps, is_pr)
    add_xp(xp, f"set logged (exercise {exercise_id})")

    rank = None
    if ex:
        rank = _rank_for_weight(ex, weight_kg)
        update_muscle_rank(ex["muscle_group"], weight_kg, rank)

    return {"id": set_id, "is_pr": bool(is_pr), "one_rep_max": one_rm,
            "xp_earned": xp, "rank": rank}


def get_session_sets(session_id: int) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT st.*, e.name AS exercise_name, e.muscle_group
                FROM gym_sets st
                JOIN gym_exercises e ON e.id = st.exercise_id
                WHERE st.session_id = {ph}
                ORDER BY st.id""",
            (session_id,))
        return [dict(r) for r in cur.fetchall()]


def get_exercise_history(exercise_id: int, limit=20) -> list:
    """Best set (by estimated 1RM) per session for one exercise, newest first."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT st.session_id, st.weight_kg, st.reps, st.set_number,
                       s.date
                FROM gym_sets st
                JOIN gym_sessions s ON s.id = st.session_id
                WHERE st.exercise_id = {ph}
                ORDER BY s.date DESC, st.id DESC""",
            (exercise_id,))
        rows = [dict(r) for r in cur.fetchall()]
    best = {}
    for r in rows:
        one_rm = calculate_one_rep_max(r["weight_kg"], r["reps"])
        r["one_rep_max"] = one_rm
        cur_best = best.get(r["session_id"])
        if cur_best is None or one_rm > cur_best["one_rep_max"]:
            best[r["session_id"]] = r
    history = sorted(best.values(), key=lambda x: str(x["date"]), reverse=True)
    return history[:limit]


# ── Personal records ─────────────────────────────────────────────────────────

def get_pr(exercise_id: int) -> dict:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM gym_prs WHERE exercise_id = {ph}", (exercise_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_all_prs() -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT p.*, e.name AS exercise_name, e.muscle_group
               FROM gym_prs p
               JOIN gym_exercises e ON e.id = p.exercise_id
               ORDER BY e.muscle_group, e.name""")
        return [dict(r) for r in cur.fetchall()]


def update_pr(exercise_id, weight_kg, reps, one_rep_max, date, session_id) -> None:
    """Upsert the personal record for an exercise (one row per exercise)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if USE_POSTGRES:
            cur.execute(
                f"""INSERT INTO gym_prs
                    (exercise_id, weight_kg, reps, one_rep_max, achieved_at, session_id)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph})
                    ON CONFLICT (exercise_id) DO UPDATE SET
                        weight_kg = EXCLUDED.weight_kg, reps = EXCLUDED.reps,
                        one_rep_max = EXCLUDED.one_rep_max,
                        achieved_at = EXCLUDED.achieved_at,
                        session_id = EXCLUDED.session_id""",
                (exercise_id, weight_kg, reps, one_rep_max, date, session_id))
        else:
            cur.execute(
                f"""INSERT INTO gym_prs
                    (exercise_id, weight_kg, reps, one_rep_max, achieved_at, session_id)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph})
                    ON CONFLICT (exercise_id) DO UPDATE SET
                        weight_kg = excluded.weight_kg, reps = excluded.reps,
                        one_rep_max = excluded.one_rep_max,
                        achieved_at = excluded.achieved_at,
                        session_id = excluded.session_id""",
                (exercise_id, weight_kg, reps, one_rep_max, date, session_id))


# ── Body stats ───────────────────────────────────────────────────────────────

def log_body_stat(date, weight_kg, notes="") -> None:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO gym_body_stats (date, weight_kg, notes) VALUES ({ph},{ph},{ph})",
            (date, weight_kg, notes))


def get_body_stats(limit=30) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM gym_body_stats ORDER BY date DESC, id DESC LIMIT {ph}",
            (limit,))
        return [dict(r) for r in cur.fetchall()]


# ── XP and rankings ──────────────────────────────────────────────────────────

def _overall_rank_for_xp(total_xp: int) -> str:
    for threshold, name in _OVERALL_XP_TIERS:
        if total_xp >= threshold:
            return name
    return "Bronze"


def _ensure_gym_xp_row():
    """Guarantee the singleton XP row (id=1) exists."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM gym_xp ORDER BY id LIMIT 1")
        if cur.fetchone():
            return
        cur.execute(
            "INSERT INTO gym_xp (total_xp, overall_rank, streak_days) "
            "VALUES (0, 'Bronze', 0)")


def get_xp() -> dict:
    _ensure_gym_xp_row()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM gym_xp ORDER BY id LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None


def add_xp(amount: int, reason: str = "") -> dict:
    """Add XP to the account total, recompute overall rank, and return the new
    {total_xp, overall_rank, added, reason}."""
    _ensure_gym_xp_row()
    amount = int(amount or 0)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, total_xp FROM gym_xp ORDER BY id LIMIT 1")
        row = cur.fetchone()
        new_total = int(row["total_xp"] or 0) + amount
        new_rank = _overall_rank_for_xp(new_total)
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE gym_xp SET total_xp = {ph}, overall_rank = {ph} WHERE id = {ph}",
            (new_total, new_rank, row["id"]))
    return {"total_xp": new_total, "overall_rank": new_rank,
            "added": amount, "reason": reason}


def get_overall_rank() -> str:
    return get_xp().get("overall_rank", "Bronze")


def get_muscle_ranks() -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM gym_muscle_ranks ORDER BY muscle_group")
        return [dict(r) for r in cur.fetchall()]


def update_muscle_rank(muscle_group, weight_kg, rank_name=None) -> dict:
    """Recalculate a muscle group's rank. Tracks the best weight seen and the
    highest exercise rank achieved (never downgrades). ``rank_name`` is the
    exercise-derived rank for this lift; when omitted the current rank stands."""
    _ensure_gym_tables()
    weight_kg = float(weight_kg or 0)
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM gym_muscle_ranks WHERE muscle_group = {ph}", (muscle_group,))
        existing = cur.fetchone()

        if existing:
            best_weight = max(float(existing["best_weight_kg"] or 0), weight_kg)
            cur_score = float(existing["rank_score"] or 0)
        else:
            best_weight = weight_kg
            cur_score = 0

        new_score = cur_score
        if rank_name:
            new_score = max(cur_score, _RANK_SCORE.get(rank_name, 1))
        current_rank = GYM_RANKS[int(new_score) - 1] if new_score >= 1 else "Bronze"

        if existing:
            cur.execute(
                f"""UPDATE gym_muscle_ranks SET current_rank = {ph},
                    best_weight_kg = {ph}, rank_score = {ph} WHERE muscle_group = {ph}""",
                (current_rank, best_weight, new_score, muscle_group))
        else:
            cur.execute(
                f"""INSERT INTO gym_muscle_ranks
                    (muscle_group, current_rank, best_weight_kg, rank_score)
                    VALUES ({ph},{ph},{ph},{ph})""",
                (muscle_group, current_rank, best_weight, new_score))
    return {"muscle_group": muscle_group, "current_rank": current_rank,
            "best_weight_kg": best_weight, "rank_score": new_score}


def get_streak() -> int:
    return int(get_xp().get("streak_days", 0) or 0)


def update_streak(workout_date: str) -> int:
    """Update the workout streak given a completed workout date. Consecutive-day
    workouts extend the streak; a gap resets it to 1. Returns the new streak."""
    _ensure_gym_xp_row()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, streak_days, last_workout_date FROM gym_xp ORDER BY id LIMIT 1")
        row = cur.fetchone()
        last = row["last_workout_date"]
        streak = int(row["streak_days"] or 0)

        last_str = str(last)[:10] if last else None
        cur_str = str(workout_date)[:10]

        if last_str == cur_str:
            new_streak = streak if streak > 0 else 1
        else:
            new_streak = 1
            if last_str:
                try:
                    d_last = datetime.strptime(last_str, "%Y-%m-%d").date()
                    d_cur = datetime.strptime(cur_str, "%Y-%m-%d").date()
                    if (d_cur - d_last).days == 1:
                        new_streak = streak + 1
                except ValueError:
                    new_streak = 1

        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE gym_xp SET streak_days = {ph}, last_workout_date = {ph} WHERE id = {ph}",
            (new_streak, cur_str, row["id"]))
    return new_streak


# ── Frontend helpers (last-session, recovery, weekly volume, notes, active) ────

def get_last_session_for_exercise(exercise_id: int) -> dict:
    """Return the most recent *previous* session that contains this exercise,
    with every set logged for it plus the best set (by est. 1RM). Used to show
    the "LAST TIME" row and ghost placeholders in the workout screen.
    Shape: {session_id, date, sets: [...], best: {...}} or None."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        # newest session id that has a set for this exercise
        cur.execute(
            f"""SELECT st.session_id, s.date
                FROM gym_sets st JOIN gym_sessions s ON s.id = st.session_id
                WHERE st.exercise_id = {ph}
                ORDER BY s.date DESC, st.session_id DESC LIMIT 1""",
            (exercise_id,))
        row = cur.fetchone()
        if not row:
            return None
        session_id = row["session_id"]
        session_date = str(row["date"])[:10]
        cur.execute(
            f"""SELECT id, set_number, set_type, weight_kg, reps, is_pr
                FROM gym_sets
                WHERE session_id = {ph} AND exercise_id = {ph}
                ORDER BY set_number, id""",
            (session_id, exercise_id))
        sets = [dict(r) for r in cur.fetchall()]
    best = None
    for s in sets:
        s["one_rep_max"] = calculate_one_rep_max(s.get("weight_kg"), s.get("reps"))
        if best is None or s["one_rep_max"] > best["one_rep_max"]:
            best = s
    return {"session_id": session_id, "date": session_date,
            "sets": sets, "best": best}


def delete_set(set_id: int) -> bool:
    """Delete a single logged set. Returns True if a row was removed. Note: XP,
    PRs and ranks are not rolled back (they never downgrade by design)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM gym_sets WHERE id = {ph}", (set_id,))
        return (cur.rowcount or 0) > 0


def get_weekly_volume() -> list:
    """Total lifted volume (kg = Σ weight×reps) per muscle group for the last 7
    days vs the previous 7, with % change. Sorted by current volume desc."""
    _ensure_gym_tables()
    today = date.today()
    cur_start = (today - timedelta(days=6)).isoformat()          # last 7 days incl today
    prev_start = (today - timedelta(days=13)).isoformat()
    prev_end = (today - timedelta(days=7)).isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT e.muscle_group AS mg, s.date AS d,
                       st.weight_kg AS w, st.reps AS r
                FROM gym_sets st
                JOIN gym_sessions s ON s.id = st.session_id
                JOIN gym_exercises e ON e.id = st.exercise_id
                WHERE s.date >= {ph}""",
            (prev_start,))
        rows = [dict(r) for r in cur.fetchall()]
    agg = {}
    for r in rows:
        d = str(r["d"])[:10]
        vol = float(r["w"] or 0) * int(r["r"] or 0)
        mg = r["mg"]
        bucket = agg.setdefault(mg, {"current": 0.0, "previous": 0.0})
        if d >= cur_start:
            bucket["current"] += vol
        elif prev_start <= d <= prev_end:
            bucket["previous"] += vol
    out = []
    for mg, b in agg.items():
        cur_v = round(b["current"], 1)
        prev_v = round(b["previous"], 1)
        if prev_v > 0:
            change = round((cur_v - prev_v) / prev_v * 100)
        else:
            change = None  # no prior baseline
        out.append({"muscle_group": mg, "current": cur_v,
                    "previous": prev_v, "change_pct": change})
    out.sort(key=lambda x: x["current"], reverse=True)
    return out


def get_muscle_recovery() -> list:
    """Days since each muscle group was last trained. Covers every muscle group
    present in the exercise library (excluding cardio). last_date/days_since are
    None if never trained. Sorted by most-recently trained first."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        # all known muscle groups
        cur.execute(
            "SELECT DISTINCT muscle_group FROM gym_exercises "
            "WHERE muscle_group <> 'cardio' ORDER BY muscle_group")
        groups = [r["muscle_group"] for r in cur.fetchall()]
        # last trained date per muscle group
        cur.execute(
            """SELECT e.muscle_group AS mg, MAX(s.date) AS last_date
               FROM gym_sets st
               JOIN gym_sessions s ON s.id = st.session_id
               JOIN gym_exercises e ON e.id = st.exercise_id
               GROUP BY e.muscle_group""")
        last = {r["mg"]: str(r["last_date"])[:10] for r in cur.fetchall() if r["last_date"]}
    today = date.today()
    out = []
    for mg in groups:
        ld = last.get(mg)
        days = None
        if ld:
            try:
                days = (today - datetime.strptime(ld, "%Y-%m-%d").date()).days
            except ValueError:
                days = None
        out.append({"muscle_group": mg, "last_date": ld, "days_since": days})
    # trained ones first (fewest days), never-trained at the end
    out.sort(key=lambda x: (x["days_since"] is None, x["days_since"] if x["days_since"] is not None else 1e9))
    return out


def save_session_notes(session_id: int, notes: str) -> bool:
    """Persist free-text notes on a session. Returns True if the session exists."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE gym_sessions SET notes = {ph} WHERE id = {ph}",
                    (notes or "", session_id))
        return (cur.rowcount or 0) > 0


def delete_session(session_id: int) -> bool:
    """Abandon/delete a session and all its sets. Used by "Discard" on an
    in-progress workout. Returns True if the session existed."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM gym_sets WHERE session_id = {ph}", (session_id,))
        cur.execute(f"DELETE FROM gym_sessions WHERE id = {ph}", (session_id,))
        return (cur.rowcount or 0) > 0


def get_active_session() -> dict:
    """Return today's in-progress session (started, no end_time), with its sets
    and routine name, so a mid-workout refresh can resume. None if none open."""
    _ensure_gym_tables()
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT s.*, r.name AS routine_name, r.day_type
                FROM gym_sessions s
                LEFT JOIN gym_routines r ON r.id = s.routine_id
                WHERE s.date = {ph} AND (s.end_time IS NULL OR s.end_time = '')
                ORDER BY s.id DESC LIMIT 1""",
            (today,))
        row = cur.fetchone()
        if not row:
            return None
        session = dict(row)
    session["sets"] = get_session_sets(session["id"])
    return session
