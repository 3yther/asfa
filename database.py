import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")

# Use PostgreSQL on Railway if DATABASE_URL set, else SQLite
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    USE_POSTGRES = True
else:
    USE_POSTGRES = False
    SQLITE_PATH = os.path.join(os.path.dirname(__file__), "asfa.db")


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
            """CREATE TABLE IF NOT EXISTS ideas (
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


def get_habits(days: int = 7):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT * FROM habits WHERE date >= NOW() - INTERVAL '%s days' ORDER BY date DESC", (days,))
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
                "SELECT * FROM spending WHERE date >= NOW() - INTERVAL '%s days' ORDER BY date DESC",
                (days,))
        else:
            cur.execute(
                "SELECT * FROM spending WHERE date >= date('now', ?) ORDER BY date DESC",
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
                "SELECT * FROM daily_scores WHERE date >= NOW() - INTERVAL '%s days' ORDER BY date",
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


# ── Voice notes & ideas ────────────────────────────────────────────────────────

def save_voice_note(content):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO voice_notes (content) VALUES ({ph})", (content,))


def save_idea(content):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"INSERT INTO ideas (content) VALUES ({ph})", (content,))


def get_ideas(limit=20):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM ideas ORDER BY created_at DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


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
                "SELECT * FROM body_weight WHERE date >= NOW() - INTERVAL '%s days' ORDER BY date",
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
