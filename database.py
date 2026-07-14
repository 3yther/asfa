import calendar
import hashlib
import json
import os
import random
import re
import sqlite3
import threading
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
    # ASFA_DB_PATH lets a subprocess (e.g. the MCP server) or a test point at a
    # specific SQLite file; defaults to the repo-local asfa.db.
    SQLITE_PATH = os.environ.get("ASFA_DB_PATH") or os.path.join(
        os.path.dirname(__file__), "asfa.db")

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


def _column_exists(cur, table: str, column: str) -> bool:
    """True if `column` already exists on `table`. Works on SQLite + Postgres.
    Used to make ALTER TABLE ... ADD COLUMN migrations idempotent."""
    if USE_POSTGRES:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column))
        return cur.fetchone() is not None
    cur.execute(f"PRAGMA table_info({table})")
    return any((r["name"] if isinstance(r, sqlite3.Row) else r[1]) == column
               for r in cur.fetchall())


def _add_column(cur, table: str, column: str, coldef: str):
    """Idempotently add a column. No-op if it already exists."""
    if not _column_exists(cur, table, column):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


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
            """CREATE TABLE IF NOT EXISTS csp_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directive TEXT,
                blocked_uri TEXT,
                document_uri TEXT,
                raw TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
            # Tier 5 Part 1: cache CV-keyword-match results keyed by the CV text
            # hash + job-description hash, so re-analysing an unchanged (CV, job)
            # pair never spends another Claude call. Editing either side changes
            # the hash → new row (natural invalidation, no manual clear needed).
            """CREATE TABLE IF NOT EXISTS cv_match_cache (
                cv_hash TEXT NOT NULL,
                job_hash TEXT NOT NULL,
                score INTEGER NOT NULL,
                missing_keywords TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (cv_hash, job_hash)
            )""",
            # Tier 5 Part 4: per-call Claude API telemetry — one row per real call
            # (tokens from the response usage) plus one row per local cache hit
            # (cached_locally=1, NULL tokens) so spend + savings are visible by
            # endpoint over time.
            """CREATE TABLE IF NOT EXISTS claude_api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_creation_tokens INTEGER,
                cached_locally INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            # Interview Assistant (merged from the former standalone
            # interview_assistant app; backs the /interview page). Standalone
            # tables — no links to any other ASFA table. See the interview_*
            # helpers at the bottom of this module.
            """CREATE TABLE IF NOT EXISTS interview_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT,
                mode TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                ended_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS interview_qa (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                question TEXT,
                answer TEXT,
                rating INTEGER DEFAULT 0,
                ts TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""",
        ]
        # Postgres uses SERIAL not AUTOINCREMENT
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                stmt = stmt.replace("datetime('now')", "NOW()")
            cursor.execute(stmt)

        # Optional link from a hydration entry to the meal it was drunk with, so
        # a logged meal can show "💧 400ml with this meal". Nullable — standalone
        # water logs (the common case) leave it NULL. Added idempotently.
        _add_column(cursor, "hydration_log", "meal_id", "INTEGER")


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


def log_hydration(date: str, amount_ml: int, logged_at: str = None, meal_id: int = None):
    """Append a hydration ledger entry. Keeps a per-event audit trail in
    addition to the rolled-up habits.water_ml total. `meal_id` optionally links
    the entry to the meal it was drunk with (nullable; standalone logs pass None)."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = ["date", "amount_ml"]
        vals = [date, amount_ml]
        if logged_at:
            cols.append("logged_at")
            vals.append(logged_at)
        if meal_id is not None:
            cols.append("meal_id")
            vals.append(meal_id)
        placeholders = ",".join([ph] * len(vals))
        cur.execute(
            f"INSERT INTO hydration_log ({','.join(cols)}) VALUES ({placeholders})",
            tuple(vals))


def get_hydration_for_meal(meal_id: int) -> int:
    """Total water (ml) explicitly linked to `meal_id`. 0 if none."""
    if meal_id is None:
        return 0
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COALESCE(SUM(amount_ml), 0) AS total FROM hydration_log "
            f"WHERE meal_id = {ph}", (meal_id,))
        row = cur.fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0


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


# ── Finance / transactions (Tier 8) ─────────────────────────────────────────────
# Tier 8 EXTENDS the legacy `spending` table (Tier 1) rather than opening a
# parallel store: it adds `merchant` and `source` columns and layers richer
# helpers (month summary, spend pace, category history) on top. log_spend()/
# get_spending() and the /api/money card keep working and read the SAME rows.
# Amount sign convention: positive = spending, negative = income/refund. The
# legacy `note` column doubles as Tier 8's `notes`.

_SPENDING_READY = False


def _ensure_transactions_table():
    """Ensure the spending table exists and carries the Tier 8 columns. Safe to
    call before init_db() (e.g. isolated test DBs): creates the table if missing,
    then idempotently adds merchant/source."""
    global _SPENDING_READY
    if _SPENDING_READY:
        return
    create = """CREATE TABLE IF NOT EXISTS spending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        create = create.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        create = create.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(create)
        _add_column(cur, "spending", "merchant", "TEXT")
        _add_column(cur, "spending", "source", "TEXT DEFAULT 'manual'")
    _SPENDING_READY = True


def get_transaction(txn_id):
    """Fetch one transaction as a dict, or None if it doesn't exist."""
    _ensure_transactions_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM spending WHERE id = {ph}", (txn_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def log_transaction(date, amount, category, merchant=None, notes=None, source="manual"):
    """Insert one transaction. Positive amount = spending, negative = income/refund.
    Returns (row_dict, None) on success or (None, error_str) on a validation
    failure. Validates: date YYYY-MM-DD (real calendar date), amount a finite real
    number (bools rejected), category non-blank. category is lower-cased."""
    _ensure_transactions_table()
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        return (None, "date must be YYYY-MM-DD")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return (None, "date must be a real calendar date")
    if isinstance(amount, bool):
        return (None, "amount must be a number")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return (None, "amount must be a number")
    if amount != amount or amount in (float("inf"), float("-inf")):
        return (None, "amount must be a finite number")
    category = (category or "").strip().lower()
    if not category:
        return (None, "category is required")
    if source not in ("manual", "import"):
        return (None, "source must be 'manual' or 'import'")

    merchant = (merchant or "").strip() or None
    notes = (notes or "").strip() or None
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO spending (date, amount, category, merchant, note, source) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
            (date, amount, category, merchant, notes, source))
        if USE_POSTGRES:
            cur.execute("SELECT lastval() AS id")
        else:
            cur.execute("SELECT last_insert_rowid() AS id")
        new_id = cur.fetchone()["id"]
    return (get_transaction(new_id), None)


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def get_month_summary(month=None):
    """Spending/income rollup for a calendar month (YYYY-MM, defaults to the
    current month). An empty month yields zeros and an empty by_category.
    by_category sums SPENDING (positive amounts) per category; total_income is a
    positive figure; net = total_spent - total_income."""
    _ensure_transactions_table()
    if not month:
        month = _current_month()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT amount, category FROM spending WHERE date LIKE {ph}",
            (f"{month}-%",))
        rows = cur.fetchall()
    total_spent = 0.0
    total_income = 0.0
    by_category = {}
    for r in rows:
        amt = float(r["amount"])
        if amt >= 0:
            total_spent += amt
            by_category[r["category"]] = round(
                by_category.get(r["category"], 0.0) + amt, 2)
        else:
            total_income += -amt
    return {
        "month": month,
        "total_spent": round(total_spent, 2),
        "total_income": round(total_income, 2),
        "net": round(total_spent - total_income, 2),
        "by_category": by_category,
        "transaction_count": len(rows),
    }


def get_spending_pace(month=None):
    """Spend-rate projection for a month (YYYY-MM, defaults to current). For the
    current month days_elapsed is today's day-of-month; a past month counts as
    fully elapsed, a future month as not started. projected_month_total =
    daily_avg * days_in_month."""
    if not month:
        month = _current_month()
    spent_so_far = get_month_summary(month)["total_spent"]
    year, mon = (int(x) for x in month.split("-"))
    days_in_month = calendar.monthrange(year, mon)[1]
    now = datetime.now()
    cur_month = now.strftime("%Y-%m")
    if month == cur_month:
        days_elapsed = now.day
    elif month < cur_month:
        days_elapsed = days_in_month
    else:
        days_elapsed = 0
    daily_avg = round(spent_so_far / days_elapsed, 2) if days_elapsed else 0.0
    return {
        "month": month,
        "spent_so_far": spent_so_far,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "daily_avg": daily_avg,
        "projected_month_total": round(daily_avg * days_in_month, 2),
    }


def get_recent_transactions(limit=20):
    """Most recent transactions, newest first. Ordered by date then id so
    same-day inserts keep insertion order. limit is clamped to 1..200."""
    _ensure_transactions_table()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(200, limit))
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM spending ORDER BY date DESC, id DESC LIMIT {ph}",
            (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_category_history(months=6):
    """Per-month spending totals per category for the last `months`
    (oldest→newest), for trend charts. Only spending (positive amounts) counts.
    Every month in the window gets an entry (empty months as zeros). Shape:
    [{month, total_spent, by_category: {cat: sum}}, ...]. months clamped 1..36."""
    _ensure_transactions_table()
    try:
        months = int(months)
    except (TypeError, ValueError):
        months = 6
    months = max(1, min(36, months))
    now = datetime.now()
    buckets = []
    y, m = now.year, now.month
    for _ in range(months):
        buckets.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    buckets.reverse()
    start = buckets[0] + "-01"
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT date, amount, category FROM spending WHERE date >= {ph}",
            (start,))
        rows = cur.fetchall()
    out = {b: {"month": b, "total_spent": 0.0, "by_category": {}} for b in buckets}
    for r in rows:
        b = r["date"][:7]
        if b not in out:
            continue
        amt = float(r["amount"])
        if amt < 0:
            continue
        entry = out[b]
        entry["total_spent"] = round(entry["total_spent"] + amt, 2)
        entry["by_category"][r["category"]] = round(
            entry["by_category"].get(r["category"], 0.0) + amt, 2)
    return [out[b] for b in buckets]


# ── Account balances (dual-account net worth) ───────────────────────────────────
# Point-in-time balance snapshots per account (checking/savings). Independent of
# the `spending` transaction store above: spending tracks flows, this tracks the
# standing balance so the dashboard can show current balances and a 30-day trend.
_ACCOUNT_BALANCES_READY = False

_ACCOUNT_TYPES = ("checking", "savings")


def _ensure_account_balances_table():
    """Create the account_balances table if missing. Safe to call before
    init_db() (mirrors _ensure_transactions_table). No CHECK constraint — the
    account_type whitelist is enforced in add_account_balance() so the rule is
    identical on SQLite and Postgres."""
    global _ACCOUNT_BALANCES_READY
    if _ACCOUNT_BALANCES_READY:
        return
    create = """CREATE TABLE IF NOT EXISTS account_balances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_type TEXT NOT NULL,
        balance REAL NOT NULL,
        date TEXT NOT NULL,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        create = create.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        create = create.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        conn.cursor().execute(create)
    _ACCOUNT_BALANCES_READY = True


def add_account_balance(account_type, balance, date, notes=None):
    """Insert one balance snapshot. Returns (row_dict, None) on success or
    (None, error_str) on validation failure. Validates: account_type in the
    whitelist, balance a finite number >= 0 (bools rejected), date a real
    YYYY-MM-DD calendar date."""
    _ensure_account_balances_table()
    account_type = (account_type or "").strip().lower()
    if account_type not in _ACCOUNT_TYPES:
        return (None, "account_type must be 'checking' or 'savings'")
    if isinstance(balance, bool):
        return (None, "balance must be a number")
    try:
        balance = float(balance)
    except (TypeError, ValueError):
        return (None, "balance must be a number")
    if balance != balance or balance in (float("inf"), float("-inf")):
        return (None, "balance must be a finite number")
    if balance < 0:
        return (None, "balance must be >= 0")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        return (None, "date must be YYYY-MM-DD")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return (None, "date must be a real calendar date")
    notes = (notes or "").strip() or None
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO account_balances (account_type, balance, date, notes) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (account_type, balance, date, notes))
        if USE_POSTGRES:
            cur.execute("SELECT lastval() AS id")
        else:
            cur.execute("SELECT last_insert_rowid() AS id")
        new_id = cur.fetchone()["id"]
        cur.execute(f"SELECT * FROM account_balances WHERE id = {ph}", (new_id,))
        row = cur.fetchone()
    return (dict(row) if row else None, None)


def _balance_as_of(cur, account_type, on_or_before):
    """Latest recorded balance for an account on or before `on_or_before`
    (YYYY-MM-DD), or None if there is no such snapshot. Ordered by date then id
    so multiple same-day snapshots resolve to the last one entered."""
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"SELECT balance FROM account_balances "
        f"WHERE account_type = {ph} AND date <= {ph} "
        f"ORDER BY date DESC, id DESC LIMIT 1",
        (account_type, on_or_before))
    row = cur.fetchone()
    return float(row["balance"]) if row else None


def _earliest_balance(cur, account_type):
    """Oldest recorded balance for an account, or None if it has no snapshots.
    Used as the trend baseline when the account has no snapshot old enough to
    cover the full 30-day window (so a young account trends from inception
    rather than from a misleading zero)."""
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"SELECT balance FROM account_balances "
        f"WHERE account_type = {ph} ORDER BY date ASC, id ASC LIMIT 1",
        (account_type,))
    row = cur.fetchone()
    return float(row["balance"]) if row else None


def get_accounts_summary():
    """Current balance and 30-day trend per account, plus aggregate net worth.

    current = latest snapshot for the account. The trend baseline is the latest
    snapshot on or before 30 days ago; if the account has no snapshot that old,
    it falls back to the earliest snapshot on record (so an account younger than
    30 days trends from its first entry, not from a fictitious zero). trend =
    current - baseline. net_worth.current/trend sum the accounts. Accounts with
    no snapshots report current 0.0 and trend 0.0."""
    _ensure_account_balances_table()
    today = datetime.now().strftime("%Y-%m-%d")
    start_day = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    out = {}
    net_current = 0.0
    net_trend = 0.0
    with get_db() as conn:
        cur = conn.cursor()
        for acct in _ACCOUNT_TYPES:
            current = _balance_as_of(cur, acct, today)
            start = _balance_as_of(cur, acct, start_day)
            if start is None:
                start = _earliest_balance(cur, acct)
            cur_val = round(current if current is not None else 0.0, 2)
            trend = round((current or 0.0) - (start or 0.0), 2)
            out[acct] = {
                "current": cur_val,
                "start_30d": round(start if start is not None else 0.0, 2),
                "trend": trend,
                "has_data": current is not None,
            }
            net_current += cur_val
            net_trend += trend
    out["net_worth"] = {"current": round(net_current, 2), "trend": round(net_trend, 2)}
    return out


# ── CSP violation reports ───────────────────────────────────────────────────────
# Sink for Content-Security-Policy-Report-Only violations (Tier 4 Part 4). The
# policy is still observe-only; these rows accumulate real violation data so a
# flip to enforcement can be made against evidence rather than blind.

def log_csp_report(directive, blocked_uri, document_uri, raw):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO csp_reports (directive, blocked_uri, document_uri, raw) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (directive, blocked_uri, document_uri, raw))


def get_csp_reports(limit: int = 100) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM csp_reports ORDER BY id DESC LIMIT {ph}", (limit,))
        return [dict(r) for r in cur.fetchall()]


def purge_old_csp_reports(days: int = 7) -> int:
    """Delete CSP reports older than `days`. The report sink is unauthenticated
    and rate-limited but still unbounded over time; a daily cap keeps the table
    from growing without limit. Returns the number of rows removed."""
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "DELETE FROM csp_reports "
                "WHERE created_at::timestamptz < NOW() - make_interval(days => %s)",
                (days,))
        else:
            cur.execute(
                "DELETE FROM csp_reports "
                "WHERE created_at < datetime('now', ?)",
                (f"-{days} days",))
        return cur.rowcount


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


# ── Sleep tracking (Tier 6) ─────────────────────────────────────────────────────
# Structured sleep log, one row per night (UNIQUE date). Distinct from the
# habits.sleep_hours rollup written by log_sleep() above — this table also
# captures quality/wake-feeling and drives the readiness score.
# Self-initialising, same pattern as focus/supplements: idempotent CREATE on
# first use, works on SQLite and a fresh Postgres.

_SLEEP_READY = False


def _ensure_sleep_table():
    global _SLEEP_READY
    if _SLEEP_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS sleep (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        duration REAL NOT NULL,
        quality INTEGER NOT NULL,
        wake_feeling TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        stmt = stmt.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _SLEEP_READY = True


def score_readiness(duration, quality) -> int:
    """Pure readiness score in 0–100 from sleep duration (hours) and quality (1–5).
    Shared by log_sleep_entry, get_sleep_readiness, and get_sleep_history so the
    maths can never drift. Boundaries: 5.5 and 9.0 hours both incur no duration
    penalty; 9.0 is NOT oversleep.
      duration <5.5 → −20 · 5.5–9.0 → 0 · >9.0 → −10 (oversleep)
      quality ≤3 → −15 · ==4 → −5 · ==5 → 0
    Worked checks: (7.0,4)=95 · (8.0,5)=100 · (5.0,3)=65 · (10.0,2)=75."""
    score = 100
    d = float(duration)
    if d < 5.5:
        score -= 20
    elif d > 9.0:
        score -= 10
    q = int(quality)
    if q <= 3:
        score -= 15
    elif q == 4:
        score -= 5
    return max(0, min(100, score))


def log_sleep_entry(date, duration, quality, wake_feeling=None, notes=None):
    """Insert one night of sleep. Returns (row_dict, None) on success or
    (None, "duplicate") on a UNIQUE(date) violation — never overwrites an
    existing night (no update path yet)."""
    _ensure_sleep_table()
    IntegrityError = psycopg2.IntegrityError if USE_POSTGRES else sqlite3.IntegrityError
    ph = "%s" if USE_POSTGRES else "?"
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO sleep (date, duration, quality, wake_feeling, notes) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                (date, float(duration), int(quality), wake_feeling, notes))
    except IntegrityError:
        return (None, "duplicate")
    return (get_sleep(date), None)


def get_sleep(date):
    """Fetch one night as a dict, or None if nothing logged for that date."""
    _ensure_sleep_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM sleep WHERE date = {ph}", (date,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_sleep_readiness(date=None):
    """Readiness score for a given night (defaults to today, server-local).
    Returns None if no entry exists for that date."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    row = get_sleep(date)
    if not row:
        return None
    return score_readiness(row["duration"], row["quality"])


def get_sleep_history(days: int = 14) -> list:
    """Logged nights within the last `days`, oldest→newest for charting.
    Missing days are omitted. Shape: [{date, duration, quality, readiness}, ...]."""
    _ensure_sleep_table()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT date, duration, quality FROM sleep "
                "WHERE CAST(date AS TIMESTAMP) >= NOW() - (%s * INTERVAL '1 day') "
                "ORDER BY date ASC", (days,))
        else:
            cur.execute(
                "SELECT date, duration, quality FROM sleep "
                "WHERE date >= date('now', ?) ORDER BY date ASC",
                (f"-{days} days",))
        rows = cur.fetchall()
    return [{"date": r["date"], "duration": r["duration"], "quality": r["quality"],
             "readiness": score_readiness(r["duration"], r["quality"])}
            for r in rows]


# ── Nutrition / meal logging (Tier 7) ──────────────────────────────────────────
# One row per logged food item. Macros are stored in grams; calories are derived
# (protein*4 + carbs*4 + fat*9) unless the caller supplies their own. Meals are
# never deduplicated automatically — the barcode column is kept only so a caller
# can spot repeat scans of the same product.

_MEALS_READY = False


def _ensure_meals_table():
    global _MEALS_READY
    if _MEALS_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        time TEXT,
        food_name TEXT NOT NULL,
        protein REAL NOT NULL,
        carbs REAL NOT NULL,
        fat REAL NOT NULL,
        calories REAL,
        barcode TEXT,
        source TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(stmt)
        # `source` records HOW the food was entered (barcode/search/favorite…);
        # `food_source` records WHICH database the macros came from (usda /
        # usda_branded / restaurant / open_food_facts / manual) so the row can
        # show a provenance badge. Added idempotently for existing DBs.
        _add_column(cur, "meals", "food_source", "TEXT")
    _MEALS_READY = True


def compute_calories(protein, carbs, fat) -> float:
    """Atwater calorie estimate from macros in grams (4/4/9)."""
    return round(float(protein) * 4 + float(carbs) * 4 + float(fat) * 9, 1)


def get_meal(meal_id):
    """Fetch one logged meal as a dict, or None if it doesn't exist."""
    _ensure_meals_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM meals WHERE id = {ph}", (meal_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def log_meal(date, food_name, protein, carbs, fat, time=None, calories=None,
             barcode=None, source="manual", notes=None, food_source=None):
    """Insert one logged food item. `calories` is derived from the macros when
    not supplied. `food_source` is the provenance of the macros (which food
    database they came from); it is free-form and optional. Returns (row_dict,
    None) on success or (None, error_str) on a validation failure (bad source,
    negative macro, blank name)."""
    _ensure_meals_table()
    if source not in MEAL_SOURCES:
        return (None, f"source must be one of {', '.join(MEAL_SOURCES)}")
    if not (food_name or "").strip():
        return (None, "food_name is required")
    try:
        protein, carbs, fat = float(protein), float(carbs), float(fat)
    except (TypeError, ValueError):
        return (None, "protein, carbs and fat must be numbers")
    if protein < 0 or carbs < 0 or fat < 0:
        return (None, "protein, carbs and fat must be >= 0")
    if calories is None:
        calories = compute_calories(protein, carbs, fat)
    else:
        calories = float(calories)

    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO meals (date, time, food_name, protein, carbs, fat, "
            f"calories, barcode, source, notes, food_source) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (date, time, food_name.strip(), protein, carbs, fat, calories,
             barcode, source, notes, food_source))
        if USE_POSTGRES:
            cur.execute("SELECT lastval() AS id")
        else:
            cur.execute("SELECT last_insert_rowid() AS id")
        new_id = cur.fetchone()["id"]
    return (get_meal(new_id), None)


def get_daily_macros(date):
    """Totals across every meal logged on `date`. Always returns a dict; an empty
    day yields zeros with meal_count 0."""
    _ensure_meals_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COALESCE(SUM(protein),0) AS protein, "
            f"COALESCE(SUM(carbs),0) AS carbs, COALESCE(SUM(fat),0) AS fat, "
            f"COALESCE(SUM(calories),0) AS calories, COUNT(*) AS n "
            f"FROM meals WHERE date = {ph}", (date,))
        r = cur.fetchone()
    return {
        "date": date,
        "total_protein": round(float(r["protein"]), 1),
        "total_carbs": round(float(r["carbs"]), 1),
        "total_fat": round(float(r["fat"]), 1),
        "total_calories": round(float(r["calories"]), 1),
        "meal_count": int(r["n"]),
    }


def get_meals(date):
    """Every meal logged on `date`, in the order they were entered."""
    _ensure_meals_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM meals WHERE date = {ph} ORDER BY id ASC", (date,))
        return [dict(r) for r in cur.fetchall()]


def get_nutrition_history(days: int = 14) -> list:
    """Per-day macro totals for the last `days`, oldest→newest for charting and
    for the insights agent to correlate against sleep/gym. Days with no meals are
    omitted. Shape: [{date, protein, carbs, fat, calories}, ...]."""
    _ensure_meals_table()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT date, SUM(protein) AS protein, SUM(carbs) AS carbs, "
                "SUM(fat) AS fat, SUM(calories) AS calories FROM meals "
                "WHERE CAST(date AS TIMESTAMP) >= NOW() - (%s * INTERVAL '1 day') "
                "GROUP BY date ORDER BY date ASC", (days,))
        else:
            cur.execute(
                "SELECT date, SUM(protein) AS protein, SUM(carbs) AS carbs, "
                "SUM(fat) AS fat, SUM(calories) AS calories FROM meals "
                "WHERE date >= date('now', ?) GROUP BY date ORDER BY date ASC",
                (f"-{days} days",))
        rows = cur.fetchall()
    return [{"date": r["date"],
             "protein": round(float(r["protein"]), 1),
             "carbs": round(float(r["carbs"]), 1),
             "fat": round(float(r["fat"]), 1),
             "calories": round(float(r["calories"]), 1)}
            for r in rows]


# ── Nutrition hub (Tier 7 redesign) ─────────────────────────────────────────────
# Search-first logging built on the SAME meals table. No per-food table: previous
# foods and time-of-day suggestions are derived by aggregating meals.food_name.
# A single-row nutrition_goals table holds the user's daily macro targets.

# Sources a meal may be logged under. The original card allowed only barcode/manual;
# the hub adds search (picked from USDA/OFF/previous) and quick-add (macros typed
# straight in). Kept here so database + endpoint validation share one source list.
MEAL_SOURCES = ("barcode", "manual", "search", "quick-add", "template", "favorite",
                "meal-prep", "restaurant")

# Daily macro defaults surfaced until the user sets their own goals.
DEFAULT_NUTRITION_GOALS = {
    "protein_goal": 160, "carbs_goal": 200, "fat_goal": 70, "calorie_goal": 2500,
}

_NUTRITION_GOALS_READY = False


def _ensure_nutrition_goals_table():
    global _NUTRITION_GOALS_READY
    if _NUTRITION_GOALS_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS nutrition_goals (
        id INTEGER PRIMARY KEY,
        protein_goal REAL NOT NULL,
        carbs_goal REAL NOT NULL,
        fat_goal REAL NOT NULL,
        calorie_goal REAL NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _NUTRITION_GOALS_READY = True


def get_nutrition_goals() -> dict:
    """The user's daily macro targets, falling back to DEFAULT_NUTRITION_GOALS
    when none have been set. Always returns all four keys as numbers."""
    _ensure_nutrition_goals_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT protein_goal, carbs_goal, fat_goal, calorie_goal "
                    "FROM nutrition_goals WHERE id = 1")
        row = cur.fetchone()
    if not row:
        return dict(DEFAULT_NUTRITION_GOALS)
    return {
        "protein_goal": round(float(row["protein_goal"]), 1),
        "carbs_goal": round(float(row["carbs_goal"]), 1),
        "fat_goal": round(float(row["fat_goal"]), 1),
        "calorie_goal": round(float(row["calorie_goal"]), 1),
    }


def set_nutrition_goals(protein, carbs, fat, calories) -> dict:
    """Upsert the single goals row. Returns the stored goals dict. Raises
    ValueError on a non-numeric or negative target."""
    _ensure_nutrition_goals_table()
    try:
        protein, carbs, fat, calories = (
            float(protein), float(carbs), float(fat), float(calories))
    except (TypeError, ValueError):
        raise ValueError("goals must be numbers")
    if min(protein, carbs, fat, calories) < 0:
        raise ValueError("goals must be >= 0")

    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO nutrition_goals "
                "(id, protein_goal, carbs_goal, fat_goal, calorie_goal, updated_at) "
                f"VALUES (1,{ph},{ph},{ph},{ph},NOW()) "
                "ON CONFLICT (id) DO UPDATE SET "
                "protein_goal=EXCLUDED.protein_goal, carbs_goal=EXCLUDED.carbs_goal, "
                "fat_goal=EXCLUDED.fat_goal, calorie_goal=EXCLUDED.calorie_goal, "
                "updated_at=NOW()",
                (protein, carbs, fat, calories))
        else:
            cur.execute(
                "INSERT INTO nutrition_goals "
                "(id, protein_goal, carbs_goal, fat_goal, calorie_goal, updated_at) "
                f"VALUES (1,{ph},{ph},{ph},{ph},CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET "
                "protein_goal=excluded.protein_goal, carbs_goal=excluded.carbs_goal, "
                "fat_goal=excluded.fat_goal, calorie_goal=excluded.calorie_goal, "
                "updated_at=CURRENT_TIMESTAMP",
                (protein, carbs, fat, calories))
    return get_nutrition_goals()


def get_meals_for_date(date) -> list:
    """Full meal rows for `date` (used by the date picker and copy-yesterday).
    Thin wrapper over get_meals so callers read intent."""
    return get_meals(date)


def get_last_meal(date):
    """Most recently inserted meal on `date`, or None. Drives the undo action."""
    _ensure_meals_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM meals WHERE date = {ph} "
                    f"ORDER BY id DESC LIMIT 1", (date,))
        row = cur.fetchone()
        return dict(row) if row else None


def delete_meal(meal_id) -> bool:
    """Delete one meal by id. Returns True if a row was removed."""
    _ensure_meals_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM meals WHERE id = {ph}", (meal_id,))
        return cur.rowcount > 0


def get_frequent_foods_at_hour(hour: int, limit: int = 5) -> list:
    """Foods most often logged in the given clock hour over the last 30 days,
    sorted by frequency. `hour` is 0-23; meals with no time are ignored. Returns
    [{food_name, count}]. Powers time-of-day (breakfast/lunch/dinner) suggestions."""
    _ensure_meals_table()
    hour = max(0, min(23, int(hour)))
    hh = f"{hour:02d}"
    limit = max(1, min(20, int(limit)))
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "SELECT food_name, COUNT(*) AS n FROM meals "
                "WHERE time IS NOT NULL AND SUBSTR(time,1,2) = %s "
                "AND CAST(date AS TIMESTAMP) >= NOW() - INTERVAL '30 days' "
                "GROUP BY food_name ORDER BY n DESC, MAX(id) DESC LIMIT %s",
                (hh, limit))
        else:
            cur.execute(
                "SELECT food_name, COUNT(*) AS n FROM meals "
                "WHERE time IS NOT NULL AND SUBSTR(time,1,2) = ? "
                "AND date >= date('now','-30 days') "
                "GROUP BY food_name ORDER BY n DESC, MAX(id) DESC LIMIT ?",
                (hh, limit))
        rows = cur.fetchall()
    return [{"food_name": r["food_name"], "count": int(r["n"])} for r in rows]


def get_previous_foods(limit: int = 50) -> list:
    """Distinct foods the user has logged, most-frequent first (recency breaks
    ties), each carrying its most-recently logged macros so the UI can prefill a
    re-pick. Returns [{food_name, count, protein, carbs, fat, calories}]. This is
    the local "food cache" — no extra table, just an aggregate over meals."""
    _ensure_meals_table()
    limit = max(1, min(200, int(limit)))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT food_name, COUNT(*) AS n, MAX(id) AS last_id "
            "FROM meals GROUP BY food_name "
            "ORDER BY n DESC, last_id DESC LIMIT "
            + ("%s" if USE_POSTGRES else "?"),
            (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        out = []
        ph = "%s" if USE_POSTGRES else "?"
        for r in rows:
            cur.execute(
                f"SELECT protein, carbs, fat, calories FROM meals WHERE id = {ph}",
                (r["last_id"],))
            m = cur.fetchone()
            out.append({
                "food_name": r["food_name"],
                "count": int(r["n"]),
                "protein": round(float(m["protein"]), 1) if m else 0,
                "carbs": round(float(m["carbs"]), 1) if m else 0,
                "fat": round(float(m["fat"]), 1) if m else 0,
                "calories": round(float(m["calories"]), 1) if m and m["calories"] is not None else 0,
            })
    return out


def get_unique_foods(limit: int = 50) -> list:
    """Distinct food_name values ordered by frequency (thin projection of
    get_previous_foods, names only)."""
    return [f["food_name"] for f in get_previous_foods(limit)]


# ── Nutrition depth (Tier 9a): templates · trends · score · favorites · insights ─
# All built on the SAME meals + nutrition_goals stores — no duplicated truth. Meal
# templates are the one new table: named snapshots of item macros the user can
# re-log in one tap. Trends/score/favorites/insights are pure aggregates.

_MEAL_TEMPLATES_READY = False


def _ensure_meal_templates_table():
    global _MEAL_TEMPLATES_READY
    if _MEAL_TEMPLATES_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS meal_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        items TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _MEAL_TEMPLATES_READY = True


def _template_item(m: dict) -> dict:
    """Snapshot one meal row into a template item — copies the values so a later
    edit/delete of the source meal never mutates the template."""
    kcal = m.get("calories")
    if kcal is None:
        kcal = compute_calories(m.get("protein", 0), m.get("carbs", 0), m.get("fat", 0))
    return {
        "food_name": m.get("food_name") or "",
        "protein": round(float(m.get("protein") or 0), 1),
        "carbs": round(float(m.get("carbs") or 0), 1),
        "fat": round(float(m.get("fat") or 0), 1),
        "kcal": round(float(kcal), 1),
        "grams": m.get("grams"),   # meals don't store grams; kept for shape
    }


def _template_totals(items: list) -> dict:
    """Sum the macros across a template's items."""
    return {
        "protein": round(sum(float(i.get("protein") or 0) for i in items), 1),
        "carbs": round(sum(float(i.get("carbs") or 0) for i in items), 1),
        "fat": round(sum(float(i.get("fat") or 0) for i in items), 1),
        "kcal": round(sum(float(i.get("kcal") or 0) for i in items), 1),
    }


def _template_row(r: dict) -> dict:
    """Public projection of a meal_templates row with computed totals."""
    try:
        items = json.loads(r["items"]) if r.get("items") else []
    except (ValueError, TypeError):
        items = []
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "items": items,
        "item_count": len(items),
        "totals": _template_totals(items),
        "created_at": r.get("created_at"),
    }


def create_meal_template(name, meal_ids) -> tuple:
    """Snapshot the given meals into a named template. Copies each meal's macros
    into items JSON (values, not row references). Returns (template_dict, None) or
    (None, error_str) on a blank name or no valid meals."""
    _ensure_meal_templates_table()
    _ensure_meals_table()
    name = (name or "").strip()
    if not name:
        return (None, "name is required")
    ids = [i for i in (meal_ids or []) if i is not None]
    if not ids:
        return (None, "at least one meal is required")
    items = []
    for mid in ids:
        m = get_meal(mid)
        if m:
            items.append(_template_item(m))
    if not items:
        return (None, "no valid meals to snapshot")

    ph = "%s" if USE_POSTGRES else "?"
    payload = json.dumps(items)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO meal_templates (name, items) VALUES ({ph},{ph})",
            (name, payload))
        if USE_POSTGRES:
            cur.execute("SELECT lastval() AS id")
        else:
            cur.execute("SELECT last_insert_rowid() AS id")
        new_id = cur.fetchone()["id"]
    return (get_meal_template(new_id), None)


def get_meal_template(template_id) -> dict:
    """One template with computed totals, or None if it doesn't exist."""
    _ensure_meal_templates_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM meal_templates WHERE id = {ph}", (template_id,))
        row = cur.fetchone()
    return _template_row(dict(row)) if row else None


def get_meal_templates() -> list:
    """All saved templates, newest first, each with computed macro totals."""
    _ensure_meal_templates_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM meal_templates ORDER BY id DESC")
        return [_template_row(dict(r)) for r in cur.fetchall()]


def log_meal_template(template_id, date, time=None) -> tuple:
    """Log every item in a template as a meal on `date` (source="template").
    Returns (meals_logged, updated_totals) or (None, None) if the template is
    missing."""
    tpl = get_meal_template(template_id)
    if not tpl:
        return (None, None)
    logged = 0
    for it in tpl["items"]:
        meal, err = log_meal(
            date, it.get("food_name") or tpl["name"],
            it.get("protein") or 0, it.get("carbs") or 0, it.get("fat") or 0,
            time=time, calories=it.get("kcal"), source="template")
        if not err:
            logged += 1
    return (logged, get_daily_macros(date))


def delete_meal_template(template_id) -> bool:
    """Delete one template by id. Returns True if a row was removed."""
    _ensure_meal_templates_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM meal_templates WHERE id = {ph}", (template_id,))
        return cur.rowcount > 0


# ── Trends ───────────────────────────────────────────────────────────────────────

def get_nutrition_trends(days: int = 7, end_date: str = None) -> dict:
    """Per-day macro series for the last `days` ending at `end_date` (today by
    default), ZERO-FILLED for unlogged days so gaps stay visible on the chart.
    Shape: {dates:[], protein:[], carbs:[], fat:[], kcal:[], goals:{...}}. Same
    code path powers 7-day trends and the 30-day energy-balance view."""
    days = max(1, min(90, int(days)))
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = [(end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
             for i in range(days)]
    # Pull the logged totals for the window in one grouped query, index by date.
    _ensure_meals_table()
    start = dates[0]
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT date, SUM(protein) AS protein, SUM(carbs) AS carbs, "
            "SUM(fat) AS fat, SUM(calories) AS calories FROM meals "
            f"WHERE date >= {ph} AND date <= {ph} GROUP BY date",
            (start, end_date))
        by_date = {r["date"]: r for r in cur.fetchall()}
    protein, carbs, fat, kcal = [], [], [], []
    for d in dates:
        r = by_date.get(d)
        protein.append(round(float(r["protein"]), 1) if r else 0)
        carbs.append(round(float(r["carbs"]), 1) if r else 0)
        fat.append(round(float(r["fat"]), 1) if r else 0)
        kcal.append(round(float(r["calories"]), 1) if r and r["calories"] is not None else 0)
    return {
        "dates": dates,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "kcal": kcal,
        "goals": get_nutrition_goals(),
    }


# ── Daily score + streak ─────────────────────────────────────────────────────────

# Macro keys scored each day and how each is judged a "hit". Kept as one table so
# score + insights share the same rules and can never drift.
_SCORE_GRADES = ("A", "B", "C", "D")


def score_nutrition_day(date, totals=None, goals=None) -> dict:
    """Pure day grade from totals vs goals — mirrors score_readiness. Four checks:
      calories · within ±10% of goal
      protein  · >= 90% of goal (overshoot is fine)
      carbs    · within ±10% of goal
      fat      · within ±10% of goal
    A goal of 0 is treated as met (no target set). Grade: A=4/4 · B=3/4 · C=2/4 ·
    D=<2. Returns {date, grade, hits, misses, logged}. `misses` names the checks
    that failed, e.g. ["carbs"]. Worked checks (goals 160/200/70/2500):
      totals 160/200/70/2500 -> A (4/4) · 120/200/70/2500 -> B (protein short)."""
    if totals is None:
        totals = get_daily_macros(date)
    if goals is None:
        goals = get_nutrition_goals()
    checks = [
        ("calories", float(totals.get("total_calories") or 0), float(goals.get("calorie_goal") or 0), "band"),
        ("protein", float(totals.get("total_protein") or 0), float(goals.get("protein_goal") or 0), "floor"),
        ("carbs", float(totals.get("total_carbs") or 0), float(goals.get("carbs_goal") or 0), "band"),
        ("fat", float(totals.get("total_fat") or 0), float(goals.get("fat_goal") or 0), "band"),
    ]
    hits, misses = 0, []
    for name, got, goal, mode in checks:
        if goal <= 0:
            hit = True
        elif mode == "floor":
            hit = got >= 0.9 * goal
        else:  # band: within ±10%
            hit = 0.9 * goal <= got <= 1.1 * goal
        if hit:
            hits += 1
        else:
            misses.append(name)
    grade = "A" if hits == 4 else "B" if hits == 3 else "C" if hits == 2 else "D"
    return {
        "date": date,
        "grade": grade,
        "hits": hits,
        "misses": misses,
        "logged": int(totals.get("meal_count") or 0) > 0,
    }


def get_nutrition_streak(date=None) -> int:
    """Consecutive days graded A or B ending at `date` (today by default). Breaks
    on the first C/D or unlogged day. An unlogged end date yields 0."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    streak = 0
    cur = datetime.strptime(date, "%Y-%m-%d")
    # Cap the walk-back so a pathological call can't scan forever.
    for _ in range(366):
        ds = cur.strftime("%Y-%m-%d")
        s = score_nutrition_day(ds)
        if not s["logged"] or s["grade"] not in ("A", "B"):
            break
        streak += 1
        cur -= timedelta(days=1)
    return streak


# ── Favorites (averaged, for one-tap re-log) ─────────────────────────────────────

def get_favorite_foods(limit: int = 10) -> list:
    """Top foods by log count, each with its AVERAGE macros across all its logs —
    so a one-tap re-log uses the user's typical portion, not a per-100g figure.
    Returns [{food_name, count, protein, carbs, fat, calories}]."""
    _ensure_meals_table()
    limit = max(1, min(50, int(limit)))
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT food_name, COUNT(*) AS n, AVG(protein) AS protein, "
            "AVG(carbs) AS carbs, AVG(fat) AS fat, AVG(calories) AS calories, "
            "MAX(id) AS last_id FROM meals GROUP BY food_name "
            f"ORDER BY n DESC, last_id DESC LIMIT {ph}", (limit,))
        rows = cur.fetchall()
    return [{
        "food_name": r["food_name"],
        "count": int(r["n"]),
        "protein": round(float(r["protein"]), 1),
        "carbs": round(float(r["carbs"]), 1),
        "fat": round(float(r["fat"]), 1),
        "calories": round(float(r["calories"]), 1) if r["calories"] is not None else 0,
    } for r in rows]


def log_favorite_food(food_name, date, time=None) -> tuple:
    """Log one entry of `food_name` using its averaged macros (source="favorite").
    Returns (meal_dict, None) or (None, error_str) when the food has no history."""
    name = (food_name or "").strip()
    if not name:
        return (None, "food_name is required")
    match = None
    for f in get_favorite_foods(50):
        if f["food_name"].lower() == name.lower():
            match = f
            break
    if not match:
        return (None, "no history for this food")
    return log_meal(
        date, match["food_name"], match["protein"], match["carbs"], match["fat"],
        time=time, calories=match["calories"], source="favorite")


# ── Restaurant / chain food database (nutrition expansion v2) ────────────────────
# A local table of chain/restaurant menu items, searched alongside USDA. Left
# UNSEEDED in this pass by design — the search path (nutrition.search_foods) reads
# it, and curated seed rows can be added later (or via add_restaurant_item) without
# fabricating macros. `restaurants` groups items by brand for future filtering.

_RESTAURANTS_READY = False

RESTAURANT_CATEGORIES = ("fast_food", "casual", "chain")


def _ensure_restaurants_tables():
    global _RESTAURANTS_READY
    if _RESTAURANTS_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            country TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS restaurant_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER REFERENCES restaurants(id),
            item_name TEXT NOT NULL,
            kcal REAL,
            protein REAL,
            carbs REAL,
            fat REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _RESTAURANTS_READY = True


def add_restaurant(name, category=None, country=None) -> int:
    """Insert (or reuse) a restaurant by name, returning its id. Category is
    validated against RESTAURANT_CATEGORIES (ignored if unknown)."""
    _ensure_restaurants_tables()
    name = (name or "").strip()
    if not name:
        raise ValueError("restaurant name is required")
    if category not in RESTAURANT_CATEGORIES:
        category = None
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM restaurants WHERE LOWER(name) = LOWER({ph})", (name,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute(
            f"INSERT INTO restaurants (name, category, country) VALUES ({ph},{ph},{ph})",
            (name, category, country))
        cur.execute("SELECT lastval() AS id" if USE_POSTGRES
                    else "SELECT last_insert_rowid() AS id")
        return int(cur.fetchone()["id"])


def add_restaurant_item(restaurant_id, item_name, kcal, protein, carbs, fat,
                        notes=None) -> int:
    """Insert one menu item under a restaurant. Returns the new item id."""
    _ensure_restaurants_tables()
    item_name = (item_name or "").strip()
    if not item_name:
        raise ValueError("item_name is required")
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO restaurant_items "
            f"(restaurant_id, item_name, kcal, protein, carbs, fat, notes) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (restaurant_id, item_name, kcal, protein, carbs, fat, notes))
        cur.execute("SELECT lastval() AS id" if USE_POSTGRES
                    else "SELECT last_insert_rowid() AS id")
        return int(cur.fetchone()["id"])


def search_restaurant_items(query: str, limit: int = 10) -> list:
    """Search the local restaurant_items table by item name OR restaurant name.
    Returns absolute-per-serving foods shaped like the search API expects:
    [{food_name, protein, carbs, fat, kcal, source, food_source, restaurant}].
    Empty until the table is seeded — the search path just skips a no-hit tier."""
    _ensure_restaurants_tables()
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(25, int(limit)))
    like = f"%{q.lower()}%"
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT ri.item_name, ri.kcal, ri.protein, ri.carbs, ri.fat, "
            f"ri.notes, r.name AS restaurant "
            f"FROM restaurant_items ri "
            f"LEFT JOIN restaurants r ON r.id = ri.restaurant_id "
            f"WHERE LOWER(ri.item_name) LIKE {ph} OR LOWER(COALESCE(r.name,'')) LIKE {ph} "
            f"ORDER BY ri.item_name ASC LIMIT {ph}",
            (like, like, limit))
        rows = cur.fetchall()
    out = []
    for r in rows:
        name = r["item_name"]
        if r["restaurant"] and r["restaurant"].lower() not in name.lower():
            name = f"{r['restaurant']} {name}"
        out.append({
            "food_name": name,
            "protein": round(float(r["protein"] or 0), 1),
            "carbs": round(float(r["carbs"] or 0), 1),
            "fat": round(float(r["fat"] or 0), 1),
            "kcal": round(float(r["kcal"] or 0), 1),
            # Restaurant items are absolute per-serving (not per-100g); the UI
            # logs them as-is rather than scaling by grams.
            "per_serving": True,
            "source": "restaurant",
            "food_source": "restaurant",
            "restaurant": r["restaurant"],
        })
    return out


# ── Meal prep mode (nutrition expansion v2) ──────────────────────────────────────
# Log a batch cook once (ingredients → totals + portion count), then "use" a
# portion on any later day. Logging usage auto-adds a meal for that day with the
# per-portion macros (source="meal-prep") so daily totals stay honest without
# re-entering the food. Remaining portions = portions - Σ usage.portions_consumed.

_MEAL_PREP_READY = False


def _ensure_meal_prep_tables():
    global _MEAL_PREP_READY
    if _MEAL_PREP_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS meal_preps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            date_prepared TEXT,
            total_kcal REAL,
            total_protein REAL,
            total_carbs REAL,
            total_fat REAL,
            portions INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS meal_prep_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_prep_id INTEGER REFERENCES meal_preps(id),
            food_name TEXT,
            amount REAL,
            unit TEXT,
            kcal REAL,
            protein REAL,
            carbs REAL,
            fat REAL
        )""",
        """CREATE TABLE IF NOT EXISTS meal_prep_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_prep_id INTEGER REFERENCES meal_preps(id),
            date TEXT,
            portions_consumed INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _MEAL_PREP_READY = True


def _prep_per_portion(prep: dict) -> dict:
    """Per-portion macros for a prep row (total ÷ portions, floor 1 portion)."""
    portions = max(1, int(prep.get("portions") or 1))
    return {
        "kcal": round(float(prep.get("total_kcal") or 0) / portions, 1),
        "protein": round(float(prep.get("total_protein") or 0) / portions, 1),
        "carbs": round(float(prep.get("total_carbs") or 0) / portions, 1),
        "fat": round(float(prep.get("total_fat") or 0) / portions, 1),
    }


def create_meal_prep(name, items, portions=1, date_prepared=None, notes=None) -> tuple:
    """Create a meal-prep batch from a list of ingredient dicts
    ({food_name, amount, unit, kcal, protein, carbs, fat}). Totals are SUMMED
    from the items. Returns (prep_dict, None) or (None, error_str)."""
    _ensure_meal_prep_tables()
    name = (name or "").strip()
    if not name:
        return (None, "name is required")
    try:
        portions = int(portions)
    except (TypeError, ValueError):
        return (None, "portions must be an integer")
    if portions < 1:
        return (None, "portions must be >= 1")
    if not isinstance(items, list) or not items:
        return (None, "at least one ingredient is required")

    clean = []
    totals = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    for it in items:
        fname = (str(it.get("food_name") or "")).strip()
        if not fname:
            continue
        row = {
            "food_name": fname,
            "amount": _safe_float(it.get("amount")),
            "unit": (str(it.get("unit") or "")).strip() or None,
            "kcal": _safe_float(it.get("kcal")) or 0.0,
            "protein": _safe_float(it.get("protein")) or 0.0,
            "carbs": _safe_float(it.get("carbs")) or 0.0,
            "fat": _safe_float(it.get("fat")) or 0.0,
        }
        # kcal falls back to Atwater when the caller omits it.
        if not row["kcal"]:
            row["kcal"] = compute_calories(row["protein"], row["carbs"], row["fat"])
        for k in totals:
            totals[k] += row[k]
        clean.append(row)
    if not clean:
        return (None, "no valid ingredients")

    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO meal_preps "
            f"(name, date_prepared, total_kcal, total_protein, total_carbs, "
            f"total_fat, portions, notes) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (name, date_prepared, round(totals["kcal"], 1),
             round(totals["protein"], 1), round(totals["carbs"], 1),
             round(totals["fat"], 1), portions, notes))
        cur.execute("SELECT lastval() AS id" if USE_POSTGRES
                    else "SELECT last_insert_rowid() AS id")
        prep_id = int(cur.fetchone()["id"])
        for row in clean:
            cur.execute(
                f"INSERT INTO meal_prep_items "
                f"(meal_prep_id, food_name, amount, unit, kcal, protein, carbs, fat) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (prep_id, row["food_name"], row["amount"], row["unit"],
                 round(row["kcal"], 1), round(row["protein"], 1),
                 round(row["carbs"], 1), round(row["fat"], 1)))
    return (get_meal_prep(prep_id), None)


def get_meal_prep(prep_id) -> dict:
    """One prep with its items, per-portion macros, and remaining portions, or
    None if it doesn't exist."""
    _ensure_meal_prep_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM meal_preps WHERE id = {ph}", (prep_id,))
        row = cur.fetchone()
        if not row:
            return None
        prep = dict(row)
        cur.execute(
            f"SELECT * FROM meal_prep_items WHERE meal_prep_id = {ph} ORDER BY id ASC",
            (prep_id,))
        items = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"SELECT COALESCE(SUM(portions_consumed),0) AS used "
            f"FROM meal_prep_usage WHERE meal_prep_id = {ph}", (prep_id,))
        used = int(cur.fetchone()["used"] or 0)
    portions = max(1, int(prep.get("portions") or 1))
    prep["items"] = items
    prep["item_count"] = len(items)
    prep["per_portion"] = _prep_per_portion(prep)
    prep["portions_used"] = used
    prep["portions_remaining"] = portions - used
    return prep


def get_meal_preps(include_spent=True) -> list:
    """All preps, newest first, each with per-portion macros + remaining count.
    With include_spent=False, drops preps whose portions are fully consumed."""
    _ensure_meal_prep_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM meal_preps ORDER BY id DESC")
        ids = [r["id"] for r in cur.fetchall()]
    out = [get_meal_prep(i) for i in ids]
    out = [p for p in out if p]
    if not include_spent:
        out = [p for p in out if p["portions_remaining"] > 0]
    return out


def log_meal_prep_usage(prep_id, date, portions_consumed=1, notes=None) -> tuple:
    """Record consuming `portions_consumed` portions of a prep on `date`, and
    auto-add a matching meal (per-portion macros × portions, source="meal-prep")
    so daily totals reflect it. Returns (result_dict, None) or (None, error_str).
    Does not hard-block over-consumption but reports remaining (can go negative)."""
    _ensure_meal_prep_tables()
    prep = get_meal_prep(prep_id)
    if not prep:
        return (None, "meal prep not found")
    try:
        portions_consumed = int(portions_consumed)
    except (TypeError, ValueError):
        return (None, "portions_consumed must be an integer")
    if portions_consumed < 1:
        return (None, "portions_consumed must be >= 1")

    per = prep["per_portion"]
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO meal_prep_usage "
            f"(meal_prep_id, date, portions_consumed, notes) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (prep_id, date, portions_consumed, notes))
    # Auto-log a meal for the day with the consumed macros.
    meal, err = log_meal(
        date, prep["name"],
        round(per["protein"] * portions_consumed, 1),
        round(per["carbs"] * portions_consumed, 1),
        round(per["fat"] * portions_consumed, 1),
        calories=round(per["kcal"] * portions_consumed, 1),
        source="meal-prep", food_source="meal_prep",
        notes=f"{portions_consumed} portion(s) of meal prep")
    if err:
        return (None, err)
    return ({
        "meal": meal,
        "portions_consumed": portions_consumed,
        "prep": get_meal_prep(prep_id),
        "updated_totals": get_daily_macros(date),
    }, None)


def delete_meal_prep(prep_id) -> bool:
    """Delete a prep and its items + usage rows. Returns True if it existed.
    Does NOT delete meals already auto-logged from past usage (those are real
    consumed food and stay in the daily record)."""
    _ensure_meal_prep_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM meal_preps WHERE id = {ph}", (prep_id,))
        if not cur.fetchone():
            return False
        cur.execute(f"DELETE FROM meal_prep_items WHERE meal_prep_id = {ph}", (prep_id,))
        cur.execute(f"DELETE FROM meal_prep_usage WHERE meal_prep_id = {ph}", (prep_id,))
        cur.execute(f"DELETE FROM meal_preps WHERE id = {ph}", (prep_id,))
    return True


def add_manual_favorite(food_name, protein, carbs, fat, date=None, calories=None) -> tuple:
    """Log a food the user typed in as a favorite (source="favorite") so it
    immediately joins the aggregate favorites list. Thin wrapper over log_meal
    that just fixes the source. Returns (meal_dict, None) or (None, error_str)."""
    return log_meal(
        date or _server_today(), food_name, protein, carbs, fat,
        calories=calories, source="favorite", food_source="manual")


def _safe_float(value):
    """Float or None (rejects bools/blanks). Local mirror of the app-layer helper
    so DB functions don't depend on the route module."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _server_today() -> str:
    from datetime import datetime as _dt
    return _dt.now().strftime("%Y-%m-%d")


# ── Insights (rule-based, honest, no AI call) ────────────────────────────────────

def _fmt_int(n) -> str:
    """Thousands-separated integer for readouts ("2,340")."""
    return f"{int(round(n)):,}"


def get_nutrition_insights(end_date=None) -> list:
    """2–4 plain-English observations from the last 7 days — honest, not
    motivational. Returns a single "not enough data" line when fewer than 3 days
    have any meals logged."""
    trends = get_nutrition_trends(7, end_date=end_date)
    goals = trends["goals"]
    kcal = trends["kcal"]
    protein = trends["protein"]
    logged_flags = [k > 0 or p > 0 for k, p in zip(kcal, protein)]
    logged_days = sum(1 for f in logged_flags if f)
    if logged_days < 3:
        return ["Not enough data yet — log a few more days."]

    out = []
    p_goal = float(goals.get("protein_goal") or 0)
    if p_goal > 0:
        p_hits = sum(1 for i, p in enumerate(protein) if logged_flags[i] and p >= 0.9 * p_goal)
        out.append(f"Protein target hit {p_hits}/{logged_days} logged days.")

    logged_kcal = [k for i, k in enumerate(kcal) if logged_flags[i]]
    if logged_kcal:
        avg_kcal = sum(logged_kcal) / len(logged_kcal)
        c_goal = float(goals.get("calorie_goal") or 0)
        if c_goal > 0:
            out.append(f"Avg {_fmt_int(avg_kcal)} kcal vs {_fmt_int(c_goal)} goal.")
        else:
            out.append(f"Avg {_fmt_int(avg_kcal)} kcal over {len(logged_kcal)} days.")

    unlogged = 7 - logged_days
    if unlogged > 0:
        out.append(f"No meals logged {unlogged} of the last 7 days.")

    # Fill toward the 2–4 range with a grade-consistency line when there's room.
    if len(out) < 4:
        ab_days = 0
        for i, d in enumerate(trends["dates"]):
            if logged_flags[i] and score_nutrition_day(d)["grade"] in ("A", "B"):
                ab_days += 1
        out.append(f"Grade A/B on {ab_days}/{logged_days} logged days.")
    return out[:4]


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
        """CREATE TABLE IF NOT EXISTS scout_pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            job_url TEXT,
            location TEXT,
            stage TEXT NOT NULL DEFAULT 'saved',
            notes TEXT,
            cv_version TEXT,
            date_saved TEXT,
            date_applied TEXT,
            date_stage_changed TEXT,
            source TEXT,
            created_at TEXT,
            updated_at TEXT
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
        # CV keyword-match analysis (Part 4). All nullable.
        _add_column(cur, "scout_pipeline", "cv_match_score", "INTEGER")
        _add_column(cur, "scout_pipeline", "missing_keywords", "TEXT")
        _add_column(cur, "scout_pipeline", "match_analysis_at", "TEXT")
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


# ── Scout pipeline (CRM-style stage board) ──────────────────────────────────
SCOUT_STAGES = ("saved", "applied", "interview", "offer", "rejected")


def get_scout_pipeline(stage=None) -> list:
    """Pipeline rows, most-recently-touched first; optional stage filter."""
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        order = "ORDER BY COALESCE(updated_at, created_at) DESC, id DESC"
        if stage:
            cur.execute(f"SELECT * FROM scout_pipeline WHERE stage = {ph} {order}", (stage,))
        else:
            cur.execute(f"SELECT * FROM scout_pipeline {order}")
        return [dict(r) for r in cur.fetchall()]


def get_scout_pipeline_job(pid):
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM scout_pipeline WHERE id = {ph}", (pid,))
        r = cur.fetchone()
        return dict(r) if r else None


def find_scout_job_description(job_url=None, title=None, company=None):
    """Best-effort lookup of a scraped job's description text for CV analysis:
    match a scout_jobs row by url first, else by title+company. Returns the
    description string or None."""
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if job_url:
            cur.execute(f"SELECT description FROM scout_jobs WHERE url = {ph} "
                        f"AND description IS NOT NULL AND description <> '' LIMIT 1", (job_url,))
            r = cur.fetchone()
            if r and r["description"]:
                return r["description"]
        if title and company:
            cur.execute(f"SELECT description FROM scout_jobs WHERE title = {ph} AND company = {ph} "
                        f"AND description IS NOT NULL AND description <> '' LIMIT 1", (title, company))
            r = cur.fetchone()
            if r and r["description"]:
                return r["description"]
    return None


def save_cv_match(pid, score, missing_keywords, analyzed_at):
    """Persist a CV-match result on a pipeline row. missing_keywords is stored as
    a JSON array string. Returns the updated row, or None if the id is unknown."""
    _ensure_scout_tables()
    if not get_scout_pipeline_job(pid):
        return None
    missing_json = json.dumps(missing_keywords or [])
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE scout_pipeline SET cv_match_score = {ph}, missing_keywords = {ph}, "
            f"match_analysis_at = {ph} WHERE id = {ph}",
            (int(score), missing_json, analyzed_at, pid))
    return get_scout_pipeline_job(pid)


def cv_match_cache_get(cv_hash, job_hash):
    """Return a cached CV-match result for this (cv_hash, job_hash) pair, or None.
    Result shape: {"score": int, "missing": [..], "created_at": str}."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT score, missing_keywords, created_at FROM cv_match_cache "
            f"WHERE cv_hash = {ph} AND job_hash = {ph}", (cv_hash, job_hash))
        r = cur.fetchone()
        if not r:
            return None
        try:
            missing = json.loads(r["missing_keywords"] or "[]")
        except (ValueError, TypeError):
            missing = []
        return {"score": int(r["score"]), "missing": missing,
                "created_at": r["created_at"]}


def cv_match_cache_set(cv_hash, job_hash, score, missing_keywords):
    """Upsert a CV-match result into the cache. missing_keywords stored as JSON."""
    missing_json = json.dumps(missing_keywords or [])
    created_at = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO cv_match_cache (cv_hash, job_hash, score, "
                "missing_keywords, created_at) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (cv_hash, job_hash) DO UPDATE SET score = EXCLUDED.score, "
                "missing_keywords = EXCLUDED.missing_keywords, created_at = EXCLUDED.created_at",
                (cv_hash, job_hash, int(score), missing_json, created_at))
        else:
            cur.execute(
                "INSERT OR REPLACE INTO cv_match_cache (cv_hash, job_hash, score, "
                "missing_keywords, created_at) VALUES (?,?,?,?,?)",
                (cv_hash, job_hash, int(score), missing_json, created_at))


def log_claude_call(endpoint, input_tokens=None, output_tokens=None,
                    cache_read_tokens=None, cache_creation_tokens=None,
                    cached_locally=0):
    """Record one Claude API call for telemetry (Tier 5 Part 4). Pass NULL tokens
    for a local cache hit (cached_locally=1) or when usage is unavailable. Never
    raises into callers — telemetry must not break a feature."""
    created_at = datetime.now().isoformat()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            ph = "%s" if USE_POSTGRES else "?"
            cur.execute(
                f"INSERT INTO claude_api_calls (endpoint, input_tokens, output_tokens, "
                f"cache_read_tokens, cache_creation_tokens, cached_locally, created_at) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (endpoint, input_tokens, output_tokens, cache_read_tokens,
                 cache_creation_tokens, int(cached_locally or 0), created_at))
    except Exception:
        pass


def claude_usage_summary(days=30):
    """Per-endpoint Claude usage over the last `days`. Returns
    {days, per_endpoint: [{endpoint, calls, cached_hits, input_tokens_sum,
    output_tokens_sum}], total: {...}}. `calls` counts every logged row;
    `cached_hits` is the local-cache subset (so real calls = calls - cached_hits)."""
    since = (datetime.now() - timedelta(days=int(days))).isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT endpoint, COUNT(*) AS calls, "
            f"SUM(CASE WHEN cached_locally = 1 THEN 1 ELSE 0 END) AS cached_hits, "
            f"COALESCE(SUM(input_tokens), 0) AS input_tokens_sum, "
            f"COALESCE(SUM(output_tokens), 0) AS output_tokens_sum "
            f"FROM claude_api_calls WHERE created_at >= {ph} "
            f"GROUP BY endpoint ORDER BY calls DESC", (since,))
        rows = [dict(r) for r in cur.fetchall()]
    per, total = [], {"calls": 0, "cached_hits": 0,
                      "input_tokens_sum": 0, "output_tokens_sum": 0}
    for r in rows:
        item = {"endpoint": r["endpoint"],
                "calls": int(r["calls"] or 0),
                "cached_hits": int(r["cached_hits"] or 0),
                "input_tokens_sum": int(r["input_tokens_sum"] or 0),
                "output_tokens_sum": int(r["output_tokens_sum"] or 0)}
        for k in total:
            total[k] += item[k]
        per.append(item)
    return {"days": int(days), "per_endpoint": per, "total": total}


def add_scout_pipeline_job(job_title, company, job_url=None, location=None,
                           source=None, stage="saved", notes=None, cv_version=None):
    """Add a job to the pipeline. Returns the new row id."""
    _ensure_scout_tables()
    if stage not in SCOUT_STAGES:
        stage = "saved"
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    date_applied = today if stage == "applied" else None
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = ("job_title, company, job_url, location, stage, notes, cv_version, "
                "date_saved, date_applied, date_stage_changed, source, created_at, updated_at")
        values = (job_title, company, job_url, location, stage, notes, cv_version,
                  today, date_applied, now, source, now, now)
        if USE_POSTGRES:
            cur.execute(f"INSERT INTO scout_pipeline ({cols}) VALUES "
                        f"({','.join([ph]*len(values))}) RETURNING id", values)
            return cur.fetchone()["id"]
        cur.execute(f"INSERT INTO scout_pipeline ({cols}) VALUES "
                    f"({','.join([ph]*len(values))})", values)
        return cur.lastrowid


def update_scout_pipeline(pid, stage=None, notes=None, cv_version=None):
    """Patch stage/notes/cv_version. A stage change bumps date_stage_changed and,
    on first entry to 'applied', stamps date_applied. Returns the updated row."""
    _ensure_scout_tables()
    existing = get_scout_pipeline_job(pid)
    if not existing:
        return None
    now = datetime.now().isoformat()
    sets, params = ["updated_at = {ph}"], [now]
    if stage is not None and stage in SCOUT_STAGES and stage != existing.get("stage"):
        sets.append("stage = {ph}"); params.append(stage)
        sets.append("date_stage_changed = {ph}"); params.append(now)
        if stage == "applied" and not existing.get("date_applied"):
            sets.append("date_applied = {ph}"); params.append(date.today().isoformat())
    if notes is not None:
        sets.append("notes = {ph}"); params.append(notes)
    if cv_version is not None:
        sets.append("cv_version = {ph}"); params.append(cv_version)
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        clause = ", ".join(s.replace("{ph}", ph) for s in sets)
        cur.execute(f"UPDATE scout_pipeline SET {clause} WHERE id = {ph}", tuple(params) + (pid,))
    return get_scout_pipeline_job(pid)


def delete_scout_pipeline(pid) -> bool:
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM scout_pipeline WHERE id = {ph}", (pid,))
        return cur.rowcount > 0


def get_scout_pipeline_stage_counts() -> dict:
    """{stage: count} across all five stages (zero-filled)."""
    _ensure_scout_tables()
    counts = {s: 0 for s in SCOUT_STAGES}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT stage, COUNT(*) AS n FROM scout_pipeline GROUP BY stage")
        for r in cur.fetchall():
            r = dict(r)
            if r["stage"] in counts:
                counts[r["stage"]] = r["n"]
    return counts


def get_scout_pipeline_reminders(days: int = 7) -> list:
    """Applications that have sat in 'applied' for >= `days` days with no stage
    change since — i.e. worth a follow-up nudge. Newest-applied first."""
    _ensure_scout_tables()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM scout_pipeline WHERE stage = 'applied' "
            f"AND date_applied IS NOT NULL AND date_applied <= {ph} "
            f"ORDER BY date_applied ASC", (cutoff,))
        return [dict(r) for r in cur.fetchall()]


def _backfill_scout_pipeline() -> int:
    """Idempotently pull already-scraped scout_jobs into the pipeline at
    stage='saved'. Dedups by job_url (or title+company when the url is blank),
    so re-running never creates duplicates. Returns the number inserted."""
    _ensure_scout_tables()
    inserted = 0
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT title, company, location, url, source, found_date FROM scout_jobs")
        jobs = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT job_url, job_title, company FROM scout_pipeline")
        existing = [dict(r) for r in cur.fetchall()]
        by_url = {e["job_url"] for e in existing if e.get("job_url")}
        by_key = {(e["job_title"], e["company"]) for e in existing}
        ph = "%s" if USE_POSTGRES else "?"
        now = datetime.now().isoformat()
        cols = ("job_title, company, job_url, location, stage, source, "
                "date_saved, date_stage_changed, created_at, updated_at")
        for j in jobs:
            if not j.get("title") or not j.get("company"):
                continue
            if j.get("url") and j["url"] in by_url:
                continue
            if not j.get("url") and (j["title"], j["company"]) in by_key:
                continue
            cur.execute(
                f"INSERT INTO scout_pipeline ({cols}) VALUES ({','.join([ph]*10)})",
                (j["title"], j["company"], j.get("url"), j.get("location"), "saved",
                 j.get("source"), j.get("found_date") or date.today().isoformat(),
                 now, now, now))
            by_url.add(j.get("url")); by_key.add((j["title"], j["company"]))
            inserted += 1
    return inserted


def init_scout_pipeline():
    """Create the pipeline table and backfill from scout_jobs. Safe every boot."""
    _ensure_scout_tables()
    _backfill_scout_pipeline()


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
        # Tier 3 Part 1: tamper-evident hash chain over the audit log. Added as
        # idempotent ALTERs (not baked into CREATE) so existing rows on a live
        # Postgres/SQLite pick them up without a table rebuild. Both start NULL
        # and are filled by _backfill_audit_chain() oldest→newest on first boot.
        _add_column(cur, "agent_audit_log", "prev_hash", "TEXT")
        _add_column(cur, "agent_audit_log", "entry_hash", "TEXT")
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


# ── Audit trail (tamper-evident hash chain, Tier 3 Part 1) ──────────────────────
# Each row stores entry_hash = sha256(prev_hash|timestamp|agent|action|details),
# where prev_hash is the previous row's entry_hash (or "ASFA_GENESIS" for the very
# first row). Any silent edit/delete of an intervening row makes a later row's
# stored hash stop matching a recomputation, so verify_audit_chain() detects it.
#
# CONCURRENCY: the read-previous + insert must be atomic or two racing writes can
# both chain off the same prev_hash and fork the chain. We serialise every write
# with a process-level lock (single gunicorn worker / single dev process, so this
# is sufficient for both backends) AND, on Postgres, additionally take an EXCLUSIVE
# table lock inside the transaction — plain SELECTs (verify) use ACCESS SHARE and
# are not blocked by it, so verification still runs concurrently.

_AUDIT_GENESIS = "ASFA_GENESIS"
_AUDIT_WRITE_LOCK = threading.Lock()


def _audit_ts_str(created_at) -> str:
    """Canonical string form of a row's created_at for hashing. Used identically
    in the write, backfill, and verify paths so recomputation is unambiguous.
    SQLite returns the stored TEXT verbatim; Postgres returns a datetime — both
    normalise to 'YYYY-MM-DD HH:MM:SS' (second precision, no microseconds/tz)."""
    if created_at is None:
        return ""
    if isinstance(created_at, datetime):
        return created_at.strftime("%Y-%m-%d %H:%M:%S")
    return str(created_at)


def _compute_audit_hash(prev_hash, ts, agent_id, action, details) -> str:
    """The exact, deterministic entry hash. `details` is the already-serialised
    JSON string (or None → hashed as empty), matching the stored column value."""
    raw = f"{prev_hash}|{ts}|{agent_id}|{action}|{details if details is not None else ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def log_audit(agent_id, action, outcome, reason=None, details=None, duration_ms=None):
    """Log an agent action to the audit trail, extending the tamper-evident hash
    chain. Serialised across threads so concurrent writes can't fork the chain."""
    _ensure_agent_data_tables()
    details_json = _json_or_none(details)
    # Second-precision timestamp we control, so the value we hash is exactly what
    # the DB stores and returns on read-back (round-trips cleanly on both engines).
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _AUDIT_WRITE_LOCK:
        with get_db() as conn:
            cur = conn.cursor()
            ph = "%s" if USE_POSTGRES else "?"
            if USE_POSTGRES:
                cur.execute("LOCK TABLE agent_audit_log IN EXCLUSIVE MODE")
            cur.execute(
                "SELECT entry_hash FROM agent_audit_log ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            prev_hash = (row["entry_hash"] if row and row["entry_hash"]
                         else _AUDIT_GENESIS)
            entry_hash = _compute_audit_hash(prev_hash, ts, agent_id, action,
                                             details_json)
            cur.execute(
                f"INSERT INTO agent_audit_log "
                f"(agent_id, action, reason, outcome, details, duration_ms, "
                f"created_at, prev_hash, entry_hash) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (agent_id, action, reason, outcome, details_json,
                 int(duration_ms) if duration_ms is not None else None,
                 ts, prev_hash, entry_hash))


def _backfill_audit_chain(batch_size: int = 500) -> int:
    """Fill prev_hash/entry_hash for rows that don't have them yet, oldest→newest,
    in batches. Only touches rows where entry_hash IS NULL — already-chained rows
    are immutable, so this is a no-op after the first run and (crucially) never
    recomputes over tampering, which would hide it. Returns rows hashed."""
    _ensure_agent_data_tables()
    hashed = 0
    with _AUDIT_WRITE_LOCK:
        with get_db() as conn:
            cur = conn.cursor()
            ph = "%s" if USE_POSTGRES else "?"
            if USE_POSTGRES:
                cur.execute("LOCK TABLE agent_audit_log IN EXCLUSIVE MODE")
            prev_hash = _AUDIT_GENESIS
            last_id = -1
            while True:
                cur.execute(
                    f"SELECT id, created_at, agent_id, action, details, entry_hash "
                    f"FROM agent_audit_log WHERE id > {ph} ORDER BY id ASC LIMIT {ph}",
                    (last_id, batch_size))
                rows = [dict(r) for r in cur.fetchall()]
                if not rows:
                    break
                for r in rows:
                    last_id = r["id"]
                    if r["entry_hash"]:
                        # Already part of the established chain — trust and extend.
                        prev_hash = r["entry_hash"]
                        continue
                    ts = _audit_ts_str(r["created_at"])
                    entry_hash = _compute_audit_hash(
                        prev_hash, ts, r["agent_id"], r["action"], r["details"])
                    cur.execute(
                        f"UPDATE agent_audit_log SET prev_hash = {ph}, "
                        f"entry_hash = {ph} WHERE id = {ph}",
                        (prev_hash, entry_hash, r["id"]))
                    prev_hash = entry_hash
                    hashed += 1
    return hashed


def verify_audit_chain(batch_size: int = 500) -> dict:
    """Walk the audit log oldest→newest recomputing each entry hash; the chain is
    valid iff every stored entry_hash matches. Streamed in batches so a large
    table doesn't load into memory at once.
    Returns {valid, total_entries, first_broken_id}."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute("SELECT COUNT(*) AS n FROM agent_audit_log")
        total = int(cur.fetchone()["n"])
        prev_hash = _AUDIT_GENESIS
        last_id = -1
        while True:
            cur.execute(
                f"SELECT id, created_at, agent_id, action, details, entry_hash "
                f"FROM agent_audit_log WHERE id > {ph} ORDER BY id ASC LIMIT {ph}",
                (last_id, batch_size))
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                break
            for r in rows:
                last_id = r["id"]
                ts = _audit_ts_str(r["created_at"])
                expected = _compute_audit_hash(
                    prev_hash, ts, r["agent_id"], r["action"], r["details"])
                if expected != (r["entry_hash"] or ""):
                    return {"valid": False, "total_entries": total,
                            "first_broken_id": r["id"]}
                prev_hash = r["entry_hash"]
    return {"valid": True, "total_entries": total, "first_broken_id": None}


def get_recent_audit_activity(minutes: int = 10) -> dict:
    """Latest audit action per agent, kept only if it landed within the last
    `minutes`. Drives Mission Control's live "who's working right now" view
    (Tier 3 Part 4). One query: MAX(id) per agent (id is monotonic with insertion
    time). Returns {agent_id: {action, created_at, minutes_ago}}."""
    _ensure_agent_data_tables()
    out = {}
    now = datetime.now()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT a.agent_id, a.action, a.created_at FROM agent_audit_log a "
            "JOIN (SELECT agent_id, MAX(id) AS mid FROM agent_audit_log GROUP BY agent_id) b "
            "ON a.id = b.mid")
        for r in cur.fetchall():
            r = dict(r)
            ts = _audit_ts_str(r["created_at"])
            try:
                when = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            mins = (now - when).total_seconds() / 60.0
            if 0 <= mins <= minutes:
                out[r["agent_id"]] = {
                    "action": r["action"], "created_at": ts,
                    "minutes_ago": round(mins, 1)}
    return out


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
    # Tier 3 Part 1: hash-chain any pre-existing audit rows exactly once. No-op on
    # every subsequent boot (only fills NULL entry_hash rows), so it never papers
    # over tampering introduced after the initial backfill.
    _backfill_audit_chain()


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
        """CREATE TABLE IF NOT EXISTS gym_rest_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
        # Optional per-set RPE (reps-in-reserve proxy). NULL = not logged.
        _add_column(cur, "gym_sets", "rpe",
                    "INTEGER CHECK (rpe IS NULL OR rpe BETWEEN 6 AND 10)")
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
    """Return {date_str: status} for every day in the last ``months``. Status is
    "workout" if a session was logged that day, "rest" if a rest day was marked
    (and no session), or False otherwise. A logged workout takes precedence over
    a rest day on the same date."""
    _ensure_gym_tables()
    end = date.today()
    start = end - timedelta(days=months * 31)
    worked, rested = set(), set()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT DISTINCT date FROM gym_sessions WHERE date >= {ph} AND date <= {ph}",
            (start.isoformat(), end.isoformat()))
        for r in cur.fetchall():
            # normalise to YYYY-MM-DD in case of stored timestamps
            worked.add(str(r["date"])[:10])
        cur.execute(
            f"SELECT date FROM gym_rest_days WHERE date >= {ph} AND date <= {ph}",
            (start.isoformat(), end.isoformat()))
        for r in cur.fetchall():
            rested.add(str(r["date"])[:10])
    calendar = {}
    d = start
    while d <= end:
        key = d.isoformat()
        if key in worked:
            calendar[key] = "workout"
        elif key in rested:
            calendar[key] = "rest"
        else:
            calendar[key] = False
        d += timedelta(days=1)
    return calendar


# ── Rest days ────────────────────────────────────────────────────────────────

def add_rest_day(rest_date: str) -> bool:
    """Mark a date as an intentional rest day. Idempotent (unique on date).
    Recomputes the streak so recovery days keep the streak alive."""
    _ensure_gym_tables()
    rest_date = str(rest_date)[:10]
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if USE_POSTGRES:
            cur.execute(
                f"INSERT INTO gym_rest_days (date) VALUES ({ph}) "
                f"ON CONFLICT (date) DO NOTHING", (rest_date,))
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO gym_rest_days (date) VALUES ({ph})", (rest_date,))
    recompute_streak()
    return True


def get_rest_days(start_date=None, end_date=None) -> list:
    """Rest-day dates (YYYY-MM-DD), newest first, optionally within a range."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if start_date and end_date:
            cur.execute(
                f"SELECT date FROM gym_rest_days WHERE date >= {ph} AND date <= {ph} "
                f"ORDER BY date DESC", (str(start_date)[:10], str(end_date)[:10]))
        else:
            cur.execute("SELECT date FROM gym_rest_days ORDER BY date DESC")
        return [str(r["date"])[:10] for r in cur.fetchall()]


def is_rest_day(day: str) -> bool:
    _ensure_gym_tables()
    day = str(day)[:10]
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT 1 FROM gym_rest_days WHERE date = {ph}", (day,))
        return cur.fetchone() is not None


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


def _is_cardio_exercise(ex: dict) -> bool:
    """True if the exercise is cardio (excluded from PR/1RM/rank logic). For
    cardio the ``reps`` field carries duration in minutes and weight is 0."""
    if not ex:
        return False
    return (ex.get("exercise_type") == "cardio") or (ex.get("muscle_group") == "cardio")


def log_set(session_id, exercise_id, set_number, set_type, weight_kg, reps,
            notes="", rpe=None) -> dict:
    """Log a single set. Detects a personal record (by estimated 1RM), updates
    the PR table, awards XP, and bumps the exercise's muscle-group rank.
    Cardio sets (Incline Walk etc.) store duration-in-minutes in ``reps`` with
    weight 0; they earn flat XP but are excluded from PR/1RM/rank logic — a 0kg
    cardio set must never register as a personal record.
    Returns {id, is_pr, one_rep_max, xp_earned, rank}."""
    _ensure_gym_tables()
    weight_kg = float(weight_kg or 0)
    reps = int(reps or 0)
    ex = get_exercise(exercise_id)
    cardio = _is_cardio_exercise(ex)

    # Optional RPE (6–10). Blank/invalid → NULL. Cardio never carries RPE.
    try:
        rpe = None if rpe in (None, "") else int(rpe)
    except (TypeError, ValueError):
        rpe = None
    if rpe is not None and (cardio or not (6 <= rpe <= 10)):
        rpe = None

    if cardio:
        # Cardio never counts as a PR (guards the 0kg-cardio-PR bug) and has no 1RM.
        one_rm = 0.0
        is_pr = False
    else:
        one_rm = calculate_one_rep_max(weight_kg, reps)
        existing = get_pr(exercise_id)
        is_pr = existing is None or one_rm > (existing.get("one_rep_max") or 0)

    with get_db() as conn:
        cur = conn.cursor()
        set_id = _gym_insert(
            cur, "gym_sets",
            "session_id, exercise_id, set_number, set_type, weight_kg, reps, is_pr, notes, rpe",
            (session_id, exercise_id, set_number, set_type, weight_kg, reps,
             bool(is_pr), notes or "", rpe))

    today = date.today().isoformat()
    if is_pr:
        update_pr(exercise_id, weight_kg, reps, one_rm, today, session_id)

    if cardio:
        xp = 50  # flat cardio XP — never a rep/volume/PR bonus
    else:
        xp = _xp_for_set(weight_kg, reps, is_pr)
    add_xp(xp, f"set logged (exercise {exercise_id})")

    rank = None
    if ex and not cardio:
        rank = _rank_for_weight(ex, weight_kg)
        update_muscle_rank(ex["muscle_group"], weight_kg, rank)

    return {"id": set_id, "is_pr": bool(is_pr), "one_rep_max": one_rm,
            "xp_earned": xp, "rank": rank, "is_cardio": cardio}


def get_session_sets(session_id: int) -> list:
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT st.*, e.name AS exercise_name, e.muscle_group,
                       e.exercise_type
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
                       s.date, e.exercise_type, e.muscle_group
                FROM gym_sets st
                JOIN gym_sessions s ON s.id = st.session_id
                JOIN gym_exercises e ON e.id = st.exercise_id
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


def _active_days() -> set:
    """Set of YYYY-MM-DD dates that count toward the streak: any day with a
    logged workout OR a marked rest day (intentional recovery)."""
    _ensure_gym_tables()
    days = set()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT date FROM gym_sessions")
        for r in cur.fetchall():
            days.add(str(r["date"])[:10])
        cur.execute("SELECT date FROM gym_rest_days")
        for r in cur.fetchall():
            days.add(str(r["date"])[:10])
    return days


def recompute_streak() -> int:
    """Recompute the streak as the run of consecutive active days ending today
    (or yesterday, so a still-open today doesn't drop the streak). Rest days
    count as active, so a logged rest day keeps the streak alive. Persists the
    result to gym_xp and returns it."""
    _ensure_gym_xp_row()
    active = _active_days()
    today = date.today()
    if today.isoformat() in active:
        anchor = today
    elif (today - timedelta(days=1)).isoformat() in active:
        anchor = today - timedelta(days=1)
    else:
        anchor = None
    streak = 0
    if anchor is not None:
        d = anchor
        while d.isoformat() in active:
            streak += 1
            d -= timedelta(days=1)
    last_workout = None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(date) AS m FROM gym_sessions")
        row = cur.fetchone()
        if row and row["m"]:
            last_workout = str(row["m"])[:10]
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            "SELECT id FROM gym_xp ORDER BY id LIMIT 1")
        xp_row = cur.fetchone()
        if xp_row:
            cur.execute(
                f"UPDATE gym_xp SET streak_days = {ph}, last_workout_date = {ph} WHERE id = {ph}",
                (streak, last_workout, xp_row["id"]))
    return streak


def get_streak() -> int:
    # Recompute live so rest days / date rollovers are always reflected.
    return recompute_streak()


def update_streak(workout_date: str) -> int:
    """Recompute the streak after a completed workout. Rest days keep the streak
    alive; a gap with neither workout nor rest day resets it. Returns the streak."""
    return recompute_streak()


def get_deload_check() -> dict:
    """Detect deload need: count consecutive calendar weeks (ISO week) with at
    least one logged workout, ending at the most recent trained week. Recommend a
    deload if the user has trained 4+ consecutive weeks with no week fully off.
    Returns {weeks_trained_consecutively, deload_recommended}."""
    _ensure_gym_tables()
    weeks = set()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT date FROM gym_sessions")
        for r in cur.fetchall():
            try:
                d = datetime.strptime(str(r["date"])[:10], "%Y-%m-%d").date()
                iso = d.isocalendar()
                weeks.add((iso[0], iso[1]))
            except ValueError:
                continue

    def week_key(d):
        iso = d.isocalendar()
        return (iso[0], iso[1])

    today = date.today()
    # Anchor at this week if trained, else last week (grace for a fresh week).
    if week_key(today) in weeks:
        anchor = today
    elif week_key(today - timedelta(days=7)) in weeks:
        anchor = today - timedelta(days=7)
    else:
        return {"weeks_trained_consecutively": 0, "deload_recommended": False}

    count = 0
    cursor_week = anchor
    while week_key(cursor_week) in weeks:
        count += 1
        cursor_week -= timedelta(days=7)
    return {"weeks_trained_consecutively": count,
            "deload_recommended": count >= 4}


def get_routine_efficiency_avg(routine_id, exclude_session_id=None) -> float:
    """Rolling average efficiency (kg volume per minute) across past *completed*
    sessions of the same routine, excluding one session id. Sessions with no
    volume or no duration (e.g. cardio-only) are skipped. Returns None if there
    is no prior baseline."""
    _ensure_gym_tables()
    if routine_id is None:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        sql = (f"""SELECT total_volume_kg, duration_minutes FROM gym_sessions
                   WHERE routine_id = {ph} AND end_time IS NOT NULL
                   AND COALESCE(total_volume_kg,0) > 0
                   AND COALESCE(duration_minutes,0) > 0""")
        params = [routine_id]
        if exclude_session_id is not None:
            sql += f" AND id <> {ph}"
            params.append(exclude_session_id)
        cur.execute(sql, tuple(params))
        effs = [float(r["total_volume_kg"]) / float(r["duration_minutes"])
                for r in cur.fetchall()]
    if not effs:
        return None
    return round(sum(effs) / len(effs), 1)


# ── Frontend helpers (last-session, recovery, weekly volume, notes, active) ────

def get_last_session_for_exercise(exercise_id: int, exclude_session_id=None) -> dict:
    """Return the most recent *previous* session that contains this exercise,
    with every set logged for it plus the best set (by est. 1RM). Used to show
    the "LAST TIME" row and ghost placeholders in the workout screen.
    ``exclude_session_id`` skips the in-progress session so mid-workout logging
    never shadows the true prior session.
    Shape: {session_id, date, sets: [...], best: {...}} or None."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        # newest session id that has a set for this exercise
        sql = (f"""SELECT st.session_id, s.date
                FROM gym_sets st JOIN gym_sessions s ON s.id = st.session_id
                WHERE st.exercise_id = {ph}""")
        params = [exercise_id]
        if exclude_session_id is not None:
            sql += f" AND st.session_id <> {ph}"
            params.append(exclude_session_id)
        sql += " ORDER BY s.date DESC, st.session_id DESC LIMIT 1"
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        if not row:
            return None
        session_id = row["session_id"]
        session_date = str(row["date"])[:10]
        cur.execute(
            f"""SELECT id, set_number, set_type, weight_kg, reps, is_pr, notes, rpe
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


# ── Smart progression (autofill + add-weight recommendations) ────────────────
# Session-only suggestions: nothing here ever writes a set — the user always
# confirms via the normal ✓. Cardio is excluded everywhere (reps = minutes,
# weight = 0, so weight math is meaningless for it).

# Typical dumbbell rack, per hand — suggestions snap to these fixed steps.
DUMBBELL_STEPS = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20,
                  22.5, 25, 27.5, 30, 32.5, 35, 40]
PLATE_STEP = 2.5            # barbell/machine plate math granularity
DEFAULT_REP_RANGE = (8, 12)  # when the exercise isn't in any routine
MAX_JUMP_PCT = 0.10          # novice safety cap on a single load jump


def _round_to_plate(weight_kg: float) -> float:
    return round(round(float(weight_kg) / PLATE_STEP) * PLATE_STEP, 2)


def _next_dumbbell_step(weight_kg: float) -> float:
    """Next rack weight strictly above ``weight_kg`` (extrapolates in 2.5s past
    the heaviest rack dumbbell)."""
    for s in DUMBBELL_STEPS:
        if s > weight_kg + 1e-9:
            return float(s)
    return round(weight_kg + PLATE_STEP, 2)


def _prev_dumbbell_step(weight_kg: float) -> float:
    """Next rack weight strictly below ``weight_kg`` (floored at the lightest)."""
    for s in reversed(DUMBBELL_STEPS):
        if s < weight_kg - 1e-9:
            return float(s)
    return float(DUMBBELL_STEPS[0])


def _fmt_kg(v) -> str:
    return ("%g" % round(float(v or 0), 2))


def get_rep_range_for_exercise(exercise_id: int) -> tuple:
    """(rep_min, rep_max) from the first routine containing this exercise,
    else the 8–12 default."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"""SELECT rep_min, rep_max FROM gym_routine_exercises
                WHERE exercise_id = {ph} ORDER BY routine_id, id LIMIT 1""",
            (exercise_id,))
        row = cur.fetchone()
    if row:
        return (int(row["rep_min"] or DEFAULT_REP_RANGE[0]),
                int(row["rep_max"] or DEFAULT_REP_RANGE[1]))
    return DEFAULT_REP_RANGE


def _prior_working_session_count(exercise_id: int, exclude_session_id=None) -> int:
    """Distinct prior sessions with working sets for this exercise — drives the
    recommendation confidence (1 session = medium, 2+ = high)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        sql = (f"""SELECT COUNT(DISTINCT session_id) AS n FROM gym_sets
                   WHERE exercise_id = {ph} AND set_type = 'working'""")
        params = [exercise_id]
        if exclude_session_id is not None:
            sql += f" AND session_id <> {ph}"
            params.append(exclude_session_id)
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
    return int(row["n"] or 0) if row else 0


def get_last_performance(exercise_id: int, exclude_session_id=None) -> dict:
    """Feature A: the most recent prior session's warmup+working sets for an
    exercise, shaped for the workout-screen autofill. Cardio and first-timers
    return {"found": False}."""
    ex = get_exercise(exercise_id)
    if not ex or _is_cardio_exercise(ex):
        return {"found": False}
    last = get_last_session_for_exercise(exercise_id, exclude_session_id)
    if not last:
        return {"found": False}
    sets = [s for s in last["sets"]
            if (s.get("set_type") or "working") in ("warmup", "working")]
    if not sets:
        return {"found": False}
    best = None
    for s in sets:
        if (s.get("set_type") or "working") != "working":
            continue
        if best is None or s["one_rep_max"] > best["one_rep_max"]:
            best = s
    try:
        days_ago = (date.today() - date.fromisoformat(last["date"])).days
    except (TypeError, ValueError):
        days_ago = None
    return {
        "found": True,
        "session_id": last["session_id"],
        "session_date": last["date"],
        "days_ago": days_ago,
        "sets": [{"set_number": s["set_number"],
                  "set_type": s.get("set_type") or "working",
                  "weight_kg": s.get("weight_kg"), "reps": s.get("reps")}
                 for s in sets],
        "best_working": ({"weight_kg": best["weight_kg"], "reps": best["reps"]}
                         if best else None),
        "top_set_1rm": (best["one_rep_max"] if best else None),
    }


def _progression_increment(equipment: str, last_top_weight: float) -> float:
    """Load increment (kg) for a progression, by how the exercise is loaded.
    Dumbbells jump to the next fixed rack step (the smallest possible jump, so
    the % cap doesn't apply); barbell/machine/cable use plate math with the
    novice ~10% single-jump cap (never below one 2.5 plate step)."""
    eq = (equipment or "").lower()
    if eq == "dumbbell":
        return round(_next_dumbbell_step(last_top_weight) - last_top_weight, 2)
    if eq in ("machine", "cable"):
        inc = 5.0 if last_top_weight >= 60 else PLATE_STEP
    else:  # barbell + anything else weight-loaded
        inc = PLATE_STEP
    cap = _round_to_plate(last_top_weight * MAX_JUMP_PCT)
    if inc > cap:
        inc = max(PLATE_STEP, cap)
    return inc


def get_progression_recommendation(exercise_id: int, rep_min=None, rep_max=None,
                                   exclude_session_id=None) -> dict:
    """Feature B: double-progression recommendation off the last prior session's
    working sets. Fill the rep range at a weight, THEN add load:
      all working sets at the top weight hit rep_max → progress (+increment),
      in range but not maxed → beat_reps (same weight, +1 rep),
      below rep_min → hold, or deload (-2.5kg/step) if >2 reps under on the
      top set. Cardio and first-timers return {"found": False}."""
    ex = get_exercise(exercise_id)
    if not ex or _is_cardio_exercise(ex):
        return {"found": False}
    last = get_last_session_for_exercise(exercise_id, exclude_session_id)
    if not last:
        return {"found": False}
    working = [s for s in last["sets"]
               if (s.get("set_type") or "working") == "working"]
    if not working:
        return {"found": False}

    if rep_min is None or rep_max is None:
        default_min, default_max = get_rep_range_for_exercise(exercise_id)
        rep_min = int(rep_min or default_min)
        rep_max = int(rep_max or default_max)

    equipment = (ex.get("equipment") or "").lower()
    dumbbell = equipment == "dumbbell"
    last_top_weight = max(float(s.get("weight_kg") or 0) for s in working)
    reps_at_top = [int(s.get("reps") or 0) for s in working
                   if abs(float(s.get("weight_kg") or 0) - last_top_weight) < 1e-6]
    top_reps = max(reps_at_top)
    sessions_n = _prior_working_session_count(exercise_id, exclude_session_id)
    confidence = "high" if sessions_n >= 2 else "medium"

    base = {"found": True, "last_top_weight": last_top_weight,
            "rep_range": [rep_min, rep_max], "confidence": confidence}

    # Bodyweight-style work logged at 0kg has no load to progress — keep the
    # verdict rep-based instead of suggesting phantom kilos.
    if last_top_weight <= 0:
        target = top_reps + 1
        return {**base, "verdict": "beat_reps", "recommended_weight": 0,
                "recommended_reps": target, "increment": 0,
                "reason": (f"Bodyweight work — progress by adding reps. You got "
                           f"{top_reps} last time; aim for {target}+.")}

    # Most recent prior working set's RPE (reps-in-reserve proxy), if logged.
    # It only nudges the load increment on a *progress* verdict; all existing
    # guardrails (rep-range, deload, 10% cap, rack steps) stay intact.
    rpe_sets = [s for s in working if s.get("rpe") is not None]
    last_rpe = rpe_sets[-1]["rpe"] if rpe_sets else None

    if all(r >= rep_max for r in reps_at_top):
        # RPE 9–10: near-max effort last time — hold and consolidate, don't push.
        if last_rpe is not None and last_rpe >= 9:
            return {**base, "verdict": "hold", "recommended_weight": last_top_weight,
                    "recommended_reps": rep_max, "increment": 0, "rpe_last": last_rpe,
                    "reason": (f"You maxed the {rep_min}–{rep_max} range at "
                               f"{_fmt_kg(last_top_weight)}kg but logged RPE {last_rpe} "
                               f"— that's near max. Hold this weight and own it "
                               f"before adding load.")}
        inc = _progression_increment(equipment, last_top_weight)
        if dumbbell:
            new_w = _next_dumbbell_step(last_top_weight)
            if last_rpe is not None and last_rpe <= 7:   # too easy → extra rack step
                new_w = _next_dumbbell_step(new_w)
            inc = round(new_w - last_top_weight, 2)
        else:
            if last_rpe is not None and last_rpe <= 7:   # too easy → bigger jump,
                cap = _round_to_plate(last_top_weight * MAX_JUMP_PCT)  # still ≤10% cap
                inc = min(inc + PLATE_STEP, max(inc, cap))
            new_w = _round_to_plate(last_top_weight + inc)
            while new_w <= last_top_weight + 1e-9:   # rounding must never stall
                new_w = round(new_w + PLATE_STEP, 2)
            inc = round(new_w - last_top_weight, 2)
        reason = (f"You hit {rep_max}+ reps on all working sets at "
                  f"{_fmt_kg(last_top_weight)}kg last time. Add "
                  f"{_fmt_kg(inc)}kg and aim for {rep_min}.")
        if last_rpe is not None and last_rpe <= 7:
            reason += f" (RPE {last_rpe} — that was easy, so a bigger jump.)"
        return {**base, "verdict": "progress", "recommended_weight": new_w,
                "recommended_reps": rep_min, "increment": inc, "rpe_last": last_rpe,
                "reason": reason}

    if any(r < rep_min for r in reps_at_top):
        if (rep_min - top_reps) > 2:
            new_w = (_prev_dumbbell_step(last_top_weight) if dumbbell
                     else max(_round_to_plate(last_top_weight - PLATE_STEP), 0))
            return {**base, "verdict": "deload", "recommended_weight": new_w,
                    "recommended_reps": rep_min,
                    "increment": round(new_w - last_top_weight, 2),
                    "reason": (f"Your top set was {top_reps} reps at "
                               f"{_fmt_kg(last_top_weight)}kg — well under the "
                               f"{rep_min}–{rep_max} range. Drop to "
                               f"{_fmt_kg(new_w)}kg and rebuild form.")}
        return {**base, "verdict": "hold", "recommended_weight": last_top_weight,
                "recommended_reps": rep_min, "increment": 0,
                "reason": (f"Some sets fell under {rep_min} reps at "
                           f"{_fmt_kg(last_top_weight)}kg. Hold this weight and "
                           f"get every set into the {rep_min}–{rep_max} range.")}

    target = min(min(reps_at_top) + 1, rep_max)
    return {**base, "verdict": "beat_reps", "recommended_weight": last_top_weight,
            "recommended_reps": target, "increment": 0,
            "reason": (f"You're inside the {rep_min}–{rep_max} range at "
                       f"{_fmt_kg(last_top_weight)}kg but haven't maxed it. Keep "
                       f"the weight and beat last time — aim {target}+ per set.")}


def get_routine_recommendations(routine_id: int) -> list:
    """Per-exercise recommendations for a whole routine (dashboard 'Next
    Session Targets'). Cardio entries are skipped."""
    out = []
    for rex in get_routine_exercises(routine_id):
        if rex.get("is_cardio") or _is_cardio_exercise(rex):
            continue
        rec = get_progression_recommendation(
            rex["exercise_id"], rex.get("rep_min"), rex.get("rep_max"))
        rec["exercise_id"] = rex["exercise_id"]
        rec["exercise_name"] = rex["name"]
        out.append(rec)
    return out


def get_gym_sets_for_export(start_date=None, end_date=None) -> list:
    """Flat rows for CSV export: one dict per logged set joined to its session
    date + exercise. Optional inclusive ISO date range (on the session date).
    Cardio rows carry duration (minutes) in ``reps``; the caller splits them.
    Per-set XP is recomputed with the same rules used when it was awarded."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        sql = (f"""SELECT s.date AS date, e.name AS exercise,
                          e.exercise_type AS exercise_type, e.muscle_group AS muscle_group,
                          st.weight_kg AS weight_kg, st.reps AS reps, st.rpe AS rpe,
                          st.set_type AS set_type, st.is_pr AS is_pr
                   FROM gym_sets st
                   JOIN gym_sessions s ON s.id = st.session_id
                   JOIN gym_exercises e ON e.id = st.exercise_id""")
        conds, params = [], []
        if start_date:
            conds.append(f"s.date >= {ph}"); params.append(start_date)
        if end_date:
            conds.append(f"s.date <= {ph}"); params.append(end_date)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY s.date, st.id"
        cur.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
    out = []
    for r in rows:
        cardio = (r.get("exercise_type") == "cardio") or (r.get("muscle_group") == "cardio")
        weight = float(r.get("weight_kg") or 0)
        reps = int(r.get("reps") or 0)
        is_pr = bool(r.get("is_pr"))
        xp = 50 if cardio else _xp_for_set(weight, reps, is_pr)
        out.append({
            "date": str(r.get("date") or "")[:10],
            "exercise": r.get("exercise") or "",
            "weight_kg": "" if cardio else _fmt_kg(weight),
            "reps": "" if cardio else reps,
            "rpe": r.get("rpe") if r.get("rpe") is not None else "",
            "duration_min": reps if cardio else "",
            "pr": "yes" if is_pr else "",
            "xp_earned": xp,
        })
    return out


def get_scout_pipeline_for_export() -> list:
    """Flat rows for CSV export of the Scout pipeline. Includes cv_match_score
    only if that column exists yet (added by the Part 4 CV-match feature)."""
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        has_score = _column_exists(cur, "scout_pipeline", "cv_match_score")
        cur.execute("SELECT * FROM scout_pipeline ORDER BY date_saved DESC, id DESC")
        rows = [dict(r) for r in cur.fetchall()]
    out = []
    for r in rows:
        row = {
            "date_saved": r.get("date_saved") or "",
            "job_title": r.get("job_title") or "",
            "company": r.get("company") or "",
            "stage": r.get("stage") or "",
            "date_applied": r.get("date_applied") or "",
            "date_stage_changed": r.get("date_stage_changed") or "",
            "source": r.get("source") or "",
            "notes": r.get("notes") or "",
        }
        if has_score:
            row["cv_match_score"] = r.get("cv_match_score") if r.get("cv_match_score") is not None else ""
        out.append(row)
    return out


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


def update_session_duration(session_id: int, minutes: int) -> bool:
    """Override a finished session's logged duration (user correction from the
    finish summary). Volume/XP/streak are unchanged. Returns True if it existed."""
    _ensure_gym_tables()
    minutes = max(0, int(minutes or 0))
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE gym_sessions SET duration_minutes = {ph} WHERE id = {ph}",
                    (minutes, session_id))
        return (cur.rowcount or 0) > 0


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


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH FAILURES — persistent login brute-force tracking
# ═══════════════════════════════════════════════════════════════════════════════
# DB-backed (not in-memory) so lockout state survives Railway restarts.
# Timestamps are stored as ISO-8601 UTC TEXT ("YYYY-MM-DD HH:MM:SS.ffffff"),
# which compares correctly as a string on both SQLite and Postgres. Rows older
# than the window are pruned opportunistically on each write.

def _ensure_auth_failures_table():
    with get_db() as conn:
        cur = conn.cursor()
        stmt = """CREATE TABLE IF NOT EXISTS auth_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        )"""
        if USE_POSTGRES:
            stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        cur.execute(stmt)


def record_auth_failure(ip: str) -> int:
    """Record one failed login for this IP. Returns the IP's failure count
    within the last hour (including the one just recorded)."""
    _ensure_auth_failures_table()
    now = datetime.utcnow()
    cutoff = (now - timedelta(hours=1)).isoformat(sep=" ")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM auth_failures WHERE attempted_at < {ph}", (cutoff,))
        cur.execute(
            f"INSERT INTO auth_failures (ip, attempted_at) VALUES ({ph}, {ph})",
            (ip, now.isoformat(sep=" ")))
        cur.execute(
            f"SELECT COUNT(*) AS n FROM auth_failures WHERE ip = {ph} AND attempted_at >= {ph}",
            (ip, cutoff))
        return int(cur.fetchone()["n"])


def count_auth_failures(ip: str, hours: int = 1) -> int:
    """Failures recorded for this IP within the last `hours` hours."""
    _ensure_auth_failures_table()
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat(sep=" ")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COUNT(*) AS n FROM auth_failures WHERE ip = {ph} AND attempted_at >= {ph}",
            (ip, cutoff))
        return int(cur.fetchone()["n"])


def clear_auth_failures(ip: str):
    """Wipe an IP's failure history (called on successful login)."""
    _ensure_auth_failures_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM auth_failures WHERE ip = {ph}", (ip,))


# ── Read-only API keys ─────────────────────────────────────────────────────────
# High-entropy random tokens that let external read-only clients (the MCP
# server) reach a curated set of GET endpoints without the session passphrase.
# Only the SHA-256 of each token is stored — a 256-bit random secret can't be
# brute-forced, so a fast indexed hash (not bcrypt) is the correct, standard
# choice and lets validation be an O(1) unique-index lookup. Created lazily +
# idempotently on first use; works on SQLite + Postgres.

_API_KEYS_READY = False


def _ensure_api_keys_table():
    global _API_KEYS_READY
    if _API_KEYS_READY:
        return
    stmt = """CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_hash TEXT NOT NULL UNIQUE,
        prefix TEXT,
        name TEXT,
        scope TEXT DEFAULT 'read',
        last_used_at TEXT,
        revoked_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )"""
    if USE_POSTGRES:
        stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        stmt = stmt.replace("datetime('now')", "NOW()")
    with get_db() as conn:
        conn.cursor().execute(stmt)
    _API_KEYS_READY = True


def create_api_key(key_hash: str, prefix: str, name: str, scope: str = "read"):
    """Store a new key by its SHA-256 hash. The raw token is never persisted."""
    _ensure_api_keys_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO api_keys (key_hash, prefix, name, scope) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            (key_hash, prefix, name, scope))


def find_active_api_key_by_hash(key_hash: str):
    """Return {id, name, scope} for a non-revoked key matching this hash, else None."""
    _ensure_api_keys_table()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT id, name, scope FROM api_keys "
            f"WHERE key_hash = {ph} AND revoked_at IS NULL",
            (key_hash,))
        row = cur.fetchone()
        return dict(row) if row else None


def touch_api_key(key_id: int):
    """Record that a key was just used (for the last_used_at audit column)."""
    _ensure_api_keys_table()
    now = datetime.utcnow().isoformat(sep=" ")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE api_keys SET last_used_at = {ph} WHERE id = {ph}",
                    (now, key_id))


def list_api_keys():
    """All keys, metadata only (never the token/hash). Newest first."""
    _ensure_api_keys_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, prefix, scope, last_used_at, revoked_at, created_at "
            "FROM api_keys ORDER BY created_at DESC, id DESC")
        return [dict(r) for r in cur.fetchall()]


def revoke_api_key(key_id: int) -> bool:
    """Mark a key revoked. Returns False if it doesn't exist or was already revoked."""
    _ensure_api_keys_table()
    now = datetime.utcnow().isoformat(sep=" ")
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"UPDATE api_keys SET revoked_at = {ph} "
            f"WHERE id = {ph} AND revoked_at IS NULL",
            (now, key_id))
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ── Exercise library (catalogue from hasaneyldrm/exercises-dataset) ────────────
# Full 1,324-exercise catalogue with GIFs, synced from GitHub by
# scripts/sync_exercises.py into the ``exercises`` table. This is deliberately
# SEPARATE from the curated ``gym_exercises`` library (which drives logging,
# ranks and routines): this table is a read-mostly reference browsed at
# /gym/exercises. "Add to workout" bridges a chosen catalogue entry into the
# logging flow via get_or_create_gym_exercise (below). Idempotent on the string
# ``id`` from the dataset; a re-sync updates synced fields but preserves the
# manually-curated ``difficulty``.

_EXERCISES_READY = False

# Equipment values (lowercased) that make an exercise doable at home with no gym.
HOME_EQUIPMENT = {"body weight", "bands", "resistance band"}


def _ensure_exercises_table():
    """Create the exercises table + its filter indexes if missing. Idempotent;
    handles SQLite/Postgres (mirrors _ensure_gym_tables)."""
    global _EXERCISES_READY
    if _EXERCISES_READY:
        return
    create = """CREATE TABLE IF NOT EXISTS exercises (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT,
        target_muscle TEXT,
        equipment TEXT,
        instructions TEXT,
        image_url TEXT,
        gif_url TEXT,
        difficulty TEXT,
        is_home_friendly BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(create)
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_exercises_category ON exercises(category)",
            "CREATE INDEX IF NOT EXISTS idx_exercises_equipment ON exercises(equipment)",
            "CREATE INDEX IF NOT EXISTS idx_exercises_home ON exercises(is_home_friendly)",
        ):
            cur.execute(stmt)
    _EXERCISES_READY = True


def upsert_exercise(ex: dict) -> str:
    """Insert or update one catalogue exercise, keyed on ``id``. Returns
    "inserted" or "updated". On update, only the synced fields are written —
    ``difficulty`` (curated by hand later) is intentionally left untouched."""
    _ensure_exercises_table()
    ph = "%s" if USE_POSTGRES else "?"
    home = bool(ex.get("is_home_friendly"))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM exercises WHERE id = {ph}", (ex["id"],))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                f"""UPDATE exercises SET name = {ph}, category = {ph},
                    target_muscle = {ph}, equipment = {ph}, instructions = {ph},
                    image_url = {ph}, gif_url = {ph}, is_home_friendly = {ph}
                    WHERE id = {ph}""",
                (ex["name"], ex.get("category"), ex.get("target_muscle"),
                 ex.get("equipment"), ex.get("instructions"), ex.get("image_url"),
                 ex.get("gif_url"), home, ex["id"]))
            return "updated"
        cols = ("id, name, category, target_muscle, equipment, instructions, "
                "image_url, gif_url, is_home_friendly")
        cur.execute(
            f"INSERT INTO exercises ({cols}) VALUES ({','.join([ph] * 9)})",
            (ex["id"], ex["name"], ex.get("category"), ex.get("target_muscle"),
             ex.get("equipment"), ex.get("instructions"), ex.get("image_url"),
             ex.get("gif_url"), home))
        return "inserted"


def get_exercises(category=None, equipment=None, home_only=False, q=None,
                  difficulty=None, page=1, per_page=48) -> dict:
    """Filtered, paginated catalogue query. ``equipment`` may be a comma-separated
    list (matched with IN). Returns
    {exercises, total, page, per_page, pages}."""
    _ensure_exercises_table()
    ph = "%s" if USE_POSTGRES else "?"
    where, params = [], []
    if category:
        where.append(f"category = {ph}")
        params.append(category)
    if equipment:
        eqs = [e.strip() for e in str(equipment).split(",") if e.strip()]
        if eqs:
            where.append(f"equipment IN ({','.join([ph] * len(eqs))})")
            params.extend(eqs)
    if home_only:
        where.append(f"is_home_friendly = {ph}")
        params.append(True)
    if difficulty:
        where.append(f"difficulty = {ph}")
        params.append(difficulty)
    if q:
        where.append(f"LOWER(name) LIKE {ph}")
        params.append(f"%{str(q).lower()}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = max(1, min(100, int(per_page)))
    except (TypeError, ValueError):
        per_page = 48
    offset = (page - 1) * per_page

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM exercises {clause}", params)
        row = cur.fetchone()
        total = (row["n"] if isinstance(row, dict) or hasattr(row, "keys")
                 else row[0]) or 0
        cur.execute(
            f"SELECT * FROM exercises {clause} ORDER BY name LIMIT {ph} OFFSET {ph}",
            params + [per_page, offset])
        rows = [dict(r) for r in cur.fetchall()]
    return {
        "exercises": rows, "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 0,
    }


def get_exercise_by_id(ex_id: str) -> dict:
    """One catalogue exercise by its string id, or None. Named to avoid clashing
    with get_exercise() which serves the gym_exercises library."""
    _ensure_exercises_table()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM exercises WHERE id = {ph}", (str(ex_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def get_exercise_facets() -> dict:
    """Distinct categories / equipment / difficulties for building filter UIs."""
    _ensure_exercises_table()
    out = {"categories": [], "equipment": [], "difficulties": []}
    with get_db() as conn:
        cur = conn.cursor()
        for key, col in (("categories", "category"), ("equipment", "equipment"),
                         ("difficulties", "difficulty")):
            cur.execute(
                f"SELECT DISTINCT {col} AS v FROM exercises "
                f"WHERE {col} IS NOT NULL AND {col} <> '' ORDER BY {col}")
            out[key] = [(r["v"] if hasattr(r, "keys") else r[0])
                        for r in cur.fetchall()]
    return out


def count_exercises() -> int:
    """Total rows in the catalogue (used by the sync summary + tests)."""
    _ensure_exercises_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM exercises")
        row = cur.fetchone()
        return (row["n"] if hasattr(row, "keys") else row[0]) or 0


def get_or_create_gym_exercise(name, muscle_group=None, equipment=None,
                               exercise_type=None, instructions=None) -> dict:
    """Bridge a catalogue exercise into the loggable gym_exercises library:
    return the existing gym_exercises row matching ``name``, or create a minimal
    one (NULL rank thresholds → shows as Unranked) and return it. This is how
    "Add to workout" lets any of the 1,324 catalogue exercises be logged with
    the normal gym set-logging flow."""
    _ensure_gym_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM gym_exercises WHERE name = {ph}", (name,))
        row = cur.fetchone()
        if row:
            return _exercise_row_to_dict(row)
        # gym_exercises.muscle_group is NOT NULL — fall back to "other".
        _gym_insert(
            cur, "gym_exercises",
            "name, muscle_group, secondary_muscles, equipment, exercise_type, "
            "instructions",
            (name, muscle_group or "other", json.dumps([]), equipment,
             exercise_type, instructions))
        cur.execute(f"SELECT * FROM gym_exercises WHERE name = {ph}", (name,))
        return _exercise_row_to_dict(cur.fetchone())


def get_gym_logged_history() -> list:
    """Per gym exercise ever logged: its name + the most recent date it was
    trained. Powers the inline "Try Something New" novelty/staleness ranking
    (never-logged vs stale-30d+). Returns [{name, last_date}], newest first.
    Dates are the session's YYYY-MM-DD (gym_sessions.date)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT e.name AS name, MAX(s.date) AS last_date
               FROM gym_sets st
               JOIN gym_sessions s ON s.id = st.session_id
               JOIN gym_exercises e ON e.id = st.exercise_id
               GROUP BY e.name""")
        return [dict(r) for r in cur.fetchall()]


def get_gym_muscle_frequency() -> list:
    """Muscle groups the athlete trains most (by logged set count), most-trained
    first: [{muscle_group, sets}]. Used as the fallback signal for suggestions
    when the current session is still empty."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT e.muscle_group AS muscle_group, COUNT(*) AS sets
               FROM gym_sets st
               JOIN gym_exercises e ON e.id = st.exercise_id
               GROUP BY e.muscle_group
               ORDER BY COUNT(*) DESC""")
        return [dict(r) for r in cur.fetchall()]


def get_catalogue_by_categories(categories) -> list:
    """All catalogue exercises whose ``category`` is in ``categories`` (a list),
    name-ordered. Empty list for no categories. Used by the suggestion ranker to
    pull the candidate pool before Python-side muscle/target filtering."""
    _ensure_exercises_table()
    cats = [c for c in (categories or []) if c]
    if not cats:
        return []
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM exercises WHERE category IN "
            f"({','.join([ph] * len(cats))}) ORDER BY name",
            cats)
        return [dict(r) for r in cur.fetchall()]


def get_all_catalogue_min() -> list:
    """Lightweight (id, name, category, target_muscle, gif_url) for every
    catalogue row — the input the gym-library GIF matcher scans. Kept minimal so
    the match map is cheap to build/cache."""
    _ensure_exercises_table()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, category, target_muscle, gif_url, "
                    "image_url FROM exercises")
        return [dict(r) for r in cur.fetchall()]


# ═══════════════════════════════════════════════════════════════════════════════
# ── Scent Vault — fragrance collection, body products, pairings, wear log ─────
# Standalone module: the 7-bottle fragrance shelf, the body/grooming products
# layered under them, one curated routine (pairing) per fragrance, and a wear
# log driving rotation stats + the smart daily recommendation. All tables are
# namespaced ``fragrance*``/``body_products`` and never touch existing schema.
# Created + seeded idempotently on boot via ``init_fragrance_data()``.

_FRAGRANCE_READY = False


def _ensure_fragrance_tables():
    """Create the fragrance tables if missing. Idempotent; SQLite/Postgres."""
    global _FRAGRANCE_READY
    if _FRAGRANCE_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS fragrances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT NOT NULL,
            notes TEXT,
            concentration TEXT,
            vibe TEXT,
            best_seasons TEXT,
            time_of_day TEXT,
            occasions TEXT,
            longevity_hrs INTEGER,
            wear_count INTEGER DEFAULT 0,
            last_worn_date TEXT,
            image_url TEXT,
            is_signature INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS body_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_type TEXT NOT NULL,
            brand TEXT NOT NULL,
            name TEXT NOT NULL,
            scent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS fragrance_pairings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fragrance_id INTEGER NOT NULL,
            shower_gel_id INTEGER,
            body_scrub_id INTEGER,
            body_lotion_id INTEGER,
            body_oil_id INTEGER,
            deodorant_id INTEGER,
            layering_fragrance_id INTEGER,
            layering_notes TEXT,
            recommendation_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS fragrance_wears (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fragrance_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time_of_day TEXT,
            occasion TEXT
        )""",
        # Tier 3 Part 2: 👍/👎 history for a worn pairing. A history table (not a
        # column) because taste drifts — the score reads only the last N ratings,
        # so an old 👎 can be outgrown. CHECK keeps rating to ±1.
        """CREATE TABLE IF NOT EXISTS fragrance_pairing_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pairing_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating IN (1,-1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _FRAGRANCE_READY = True


# The real shelf: 7 bottles (note pyramids from each house's official listing)
# and the body products they get layered over. Order matters — pairings below
# reference fragrances and products by (name) lookups, not hardcoded ids.
_FRAGRANCE_SEED = [
    ('Boss Bottled', 'Hugo Boss',
     'Top: Apple, Bergamot, Lemon; Heart: Cinnamon, Geranium, Carnation; Base: Sandalwood, Cedar, Vetiver, Musk',
     'EDT', 'warm,spicy,woody,versatile', 'autumn,winter', 'day,evening', 'office,casual,date', 6),
    ('Paradigme', 'Prada',
     'Top: Bergamot, Bitter Orange; Heart: Orange Blossom, Neroli; Base: Ambrette, Musk, Woods',
     'EDP', 'fresh,clean,elegant,citrus', 'spring,summer', 'morning,day', 'office,casual,formal', 7),
    ('Sauvage Eau de Parfum', 'Dior',
     'Top: Bergamot, Spicy notes; Heart: Sichuan Pepper, Lavender, Star Anise, Nutmeg; Base: Ambroxan, Vanilla, Cedar',
     'EDP', 'fresh,spicy,ambery,crowd-pleaser', 'all', 'day,evening', 'casual,date,office', 8),
    ('Bleu de Chanel Eau de Parfum', 'Chanel',
     'Top: Grapefruit, Lemon, Mint, Pink Pepper; Heart: Ginger, Nutmeg, Jasmine; Base: Incense, Vetiver, Cedar, Sandalwood',
     'EDP', 'fresh,woody,sophisticated,signature', 'all', 'day,evening', 'office,date,formal', 9),
    ('Le Beau Le Parfum', 'Jean Paul Gaultier',
     'Top: Bergamot, Coconut; Heart: Tonka Bean; Base: Woody, Vanilla',
     'EDP', 'sweet,coconut,tropical,warm', 'summer,autumn', 'evening,night', 'date,casual', 8),
    ('Le Male Le Parfum', 'Jean Paul Gaultier',
     'Top: Cardamom, Mint; Heart: Lavender, Cinnamon; Base: Vanilla, Tonka, Amber',
     'EDP', 'sweet,spicy,warm,seductive', 'autumn,winter', 'evening,night', 'date,formal', 9),
    ('Y Eau de Parfum', 'Yves Saint Laurent',
     'Top: Bergamot, Apple, Ginger; Heart: Sage, Juniper, Geranium; Base: Amberwood, Tonka, Cedar, Vetiver',
     'EDP', 'fresh,aromatic,woody,modern', 'spring,summer,autumn', 'morning,day', 'office,casual,gym', 7),
]

_BODY_PRODUCT_SEED = [
    ('shower_gel', 'Method', 'Coconut Body Wash', 'Coconut'),
    ('shower_gel', 'Bulldog', 'Original Shower Gel', 'Herbal & Refreshing'),
    ('shower_gel', 'Dove', 'Pampering Body Wash', 'Shea Butter & Vanilla'),
    ('body_scrub', 'Organic Shop', 'Hydrating Body Scrub', 'Coconut & Sugar'),
    ('body_lotion', 'Vaseline', 'Cocoa Radiant Lotion', 'Cocoa'),
    ('body_lotion', 'Dove', 'Body Love Essential Care Lotion', 'Glycerin'),
    ('body_oil', 'Vaseline', 'Cocoa Radiant Body Oil', 'Cocoa'),
    ('deodorant', 'Sure', 'Cotton Dry Antiperspirant', 'Cotton'),
    ('deodorant', 'Native', 'Coconut & Vanilla Deodorant', 'Coconut & Vanilla'),
    ('powder', "Johnson's", 'Baby Powder', 'Classic'),
]

# One curated routine per fragrance. Keys are product names (resolved to ids at
# seed time); ``layer`` is another fragrance's name for combo layering.
_PAIRING_SEED = [
    dict(frag='Boss Bottled', gel='Pampering Body Wash', lotion='Cocoa Radiant Lotion',
         oil='Cocoa Radiant Body Oil', deo='Coconut & Vanilla Deodorant',
         reason='Warm spicy woods love a creamy vanilla/cocoa base — it boosts warmth and longevity in cold months.'),
    dict(frag='Paradigme', gel='Original Shower Gel', lotion='Body Love Essential Care Lotion',
         deo='Cotton Dry Antiperspirant',
         reason='Keep it clean and fresh; skip heavy cocoa so the elegant citrus-neroli stays crisp.'),
    dict(frag='Sauvage Eau de Parfum', gel='Coconut Body Wash', lotion='Cocoa Radiant Lotion',
         oil='Cocoa Radiant Body Oil', deo='Coconut & Vanilla Deodorant',
         reason='Ambroxan + vanilla in Sauvage pairs beautifully with cocoa/coconut for a warm, projecting trail.'),
    dict(frag='Bleu de Chanel Eau de Parfum', gel='Coconut Body Wash', lotion='Cocoa Radiant Lotion',
         deo='Cotton Dry Antiperspirant',
         reason='Your signature. A light cocoa base adds depth without fighting the fresh-woody structure.'),
    dict(frag='Le Beau Le Parfum', gel='Coconut Body Wash', oil='Cocoa Radiant Body Oil',
         deo='Coconut & Vanilla Deodorant',
         reason="Coconut wash + cocoa oil amplify Le Beau's tropical coconut-tonka — a full creamy summer combo."),
    dict(frag='Le Male Le Parfum', gel='Pampering Body Wash', lotion='Cocoa Radiant Lotion',
         oil='Cocoa Radiant Body Oil', deo='Coconut & Vanilla Deodorant',
         layer='Le Beau Le Parfum',
         layer_notes='1 spritz Le Beau on chest, Le Male on neck — coconut + vanilla-amber.',
         reason='Both are sweet JPG scents; the cocoa/vanilla base makes an addictive cold-weather date combo.'),
    dict(frag='Y Eau de Parfum', gel='Original Shower Gel', lotion='Body Love Essential Care Lotion',
         deo='Cotton Dry Antiperspirant',
         reason='Fresh aromatic-woody stays sharp on a clean herbal base — ideal office/gym scent.'),
]


def seed_fragrance_data():
    """Insert the collection once. Skipped entirely when fragrances exist, so a
    live wear history / uploaded images are never clobbered by a redeploy."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM fragrances")
        if int(cur.fetchone()["n"]):
            return
        ph = "%s" if USE_POSTGRES else "?"
        fcols = ("name, brand, notes, concentration, vibe, best_seasons, "
                 "time_of_day, occasions, longevity_hrs, is_signature")
        for row in _FRAGRANCE_SEED:
            sig = 1 if row[0] == 'Bleu de Chanel Eau de Parfum' else 0
            cur.execute(
                f"INSERT INTO fragrances ({fcols}) VALUES ({','.join([ph]*10)})",
                row + (sig,))
        for row in _BODY_PRODUCT_SEED:
            cur.execute(
                f"INSERT INTO body_products (product_type, brand, name, scent) "
                f"VALUES ({','.join([ph]*4)})", row)

        def frag_id(name):
            cur.execute(f"SELECT id FROM fragrances WHERE name = {ph}", (name,))
            r = cur.fetchone()
            return r["id"] if r else None

        def product_id(name):
            cur.execute(f"SELECT id FROM body_products WHERE name = {ph}", (name,))
            r = cur.fetchone()
            return r["id"] if r else None

        pcols = ("fragrance_id, shower_gel_id, body_scrub_id, body_lotion_id, "
                 "body_oil_id, deodorant_id, layering_fragrance_id, "
                 "layering_notes, recommendation_reason")
        for p in _PAIRING_SEED:
            cur.execute(
                f"INSERT INTO fragrance_pairings ({pcols}) VALUES ({','.join([ph]*9)})",
                (frag_id(p['frag']),
                 product_id(p['gel']) if p.get('gel') else None,
                 product_id(p['scrub']) if p.get('scrub') else None,
                 product_id(p['lotion']) if p.get('lotion') else None,
                 product_id(p['oil']) if p.get('oil') else None,
                 product_id(p['deo']) if p.get('deo') else None,
                 frag_id(p['layer']) if p.get('layer') else None,
                 p.get('layer_notes'), p['reason']))


def init_fragrance_data():
    """Create + seed the Scent Vault tables. Safe to call on every boot."""
    _ensure_fragrance_tables()
    seed_fragrance_data()


def _days_since(iso_date):
    """Whole days between an ISO date string and today; None if never/unparseable."""
    if not iso_date:
        return None
    try:
        return (date.today() - date.fromisoformat(str(iso_date)[:10])).days
    except ValueError:
        return None


def _fragrance_row_to_dict(row) -> dict:
    d = dict(row)
    d["days_since_worn"] = _days_since(d.get("last_worn_date"))
    d.pop("created_at", None)
    return d


def get_fragrances() -> list:
    """The whole shelf, with computed days_since_worn."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM fragrances ORDER BY is_signature DESC, name")
        return [_fragrance_row_to_dict(r) for r in cur.fetchall()]


def get_fragrance(fragrance_id: int):
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM fragrances WHERE id = {ph}", (fragrance_id,))
        row = cur.fetchone()
        return _fragrance_row_to_dict(row) if row else None


def get_fragrance_pairing(fragrance_id: int):
    """The fragrance's curated routine with product/fragrance ids resolved to
    human-readable entries (the UI never sees raw FK ids)."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM fragrance_pairings WHERE fragrance_id = {ph} "
            f"ORDER BY id LIMIT 1", (fragrance_id,))
        pairing = cur.fetchone()
        if not pairing:
            return None
        pairing = dict(pairing)

        def product(pid):
            if not pid:
                return None
            cur.execute(f"SELECT * FROM body_products WHERE id = {ph}", (pid,))
            r = cur.fetchone()
            if not r:
                return None
            r = dict(r)
            return {"id": r["id"], "brand": r["brand"], "name": r["name"], "scent": r["scent"]}

        layering = None
        if pairing.get("layering_fragrance_id"):
            cur.execute(f"SELECT id, name, brand FROM fragrances WHERE id = {ph}",
                        (pairing["layering_fragrance_id"],))
            r = cur.fetchone()
            if r:
                layering = dict(r)
        # last-5 rating net (Tier 3 Part 2), computed on the same cursor.
        cur.execute(
            f"SELECT rating FROM fragrance_pairing_ratings WHERE pairing_id = {ph} "
            f"ORDER BY id DESC LIMIT 5", (pairing["id"],))
        net = max(-3, min(3, sum(int(r["rating"]) for r in cur.fetchall())))
        return {
            "id": pairing["id"],
            "shower_gel": product(pairing.get("shower_gel_id")),
            "body_scrub": product(pairing.get("body_scrub_id")),
            "body_lotion": product(pairing.get("body_lotion_id")),
            "body_oil": product(pairing.get("body_oil_id")),
            "deodorant": product(pairing.get("deodorant_id")),
            "layering_fragrance": layering,
            "layering_notes": pairing.get("layering_notes"),
            "reason": pairing.get("recommendation_reason"),
            "rating_net": net,
        }


def get_fragrance_wears(fragrance_id: int, days: int = 90) -> list:
    """Wear rows for the last `days` days, newest first."""
    _ensure_fragrance_tables()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT date, time_of_day, occasion FROM fragrance_wears "
            f"WHERE fragrance_id = {ph} AND date >= {ph} ORDER BY date DESC, id DESC",
            (fragrance_id, cutoff))
        return [dict(r) for r in cur.fetchall()]


def log_fragrance_wear(fragrance_id: int, time_of_day=None, occasion=None):
    """Record one wear today: insert the wear row, bump wear_count and
    last_worn_date. Returns the updated fragrance dict, or None if unknown."""
    _ensure_fragrance_tables()
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT id FROM fragrances WHERE id = {ph}", (fragrance_id,))
        if not cur.fetchone():
            return None
        cur.execute(
            f"INSERT INTO fragrance_wears (fragrance_id, date, time_of_day, occasion) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            (fragrance_id, today, time_of_day, occasion))
        cur.execute(
            f"UPDATE fragrances SET wear_count = wear_count + 1, "
            f"last_worn_date = {ph} WHERE id = {ph}", (today, fragrance_id))
    return get_fragrance(fragrance_id)


def undo_last_fragrance_wear(fragrance_id: int):
    """Mis-tap safety: delete the most recent wear row, decrement wear_count
    (floored at 0) and re-derive last_worn_date from what's left. Returns the
    updated fragrance dict, or None if there was nothing to undo."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT id FROM fragrance_wears WHERE fragrance_id = {ph} "
            f"ORDER BY date DESC, id DESC LIMIT 1", (fragrance_id,))
        last = cur.fetchone()
        if not last:
            return None
        cur.execute(f"DELETE FROM fragrance_wears WHERE id = {ph}", (last["id"],))
        cur.execute(
            f"SELECT MAX(date) AS d FROM fragrance_wears WHERE fragrance_id = {ph}",
            (fragrance_id,))
        prev = cur.fetchone()["d"]
        cur.execute(
            f"UPDATE fragrances SET wear_count = MAX(wear_count - 1, 0), "
            f"last_worn_date = {ph} WHERE id = {ph}"
            if not USE_POSTGRES else
            f"UPDATE fragrances SET wear_count = GREATEST(wear_count - 1, 0), "
            f"last_worn_date = {ph} WHERE id = {ph}",
            (prev, fragrance_id))
    return get_fragrance(fragrance_id)


def set_fragrance_image(fragrance_id: int, image_url: str):
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE fragrances SET image_url = {ph} WHERE id = {ph}",
                    (image_url, fragrance_id))


def get_fragrance_stats() -> dict:
    """Collection insights: totals, most/least worn, signature, neglected
    bottles (>30d unworn), wears this month, and per-fragrance rotation share."""
    frags = get_fragrances()
    month_start = date.today().replace(day=1).isoformat()
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT COUNT(*) AS n FROM fragrance_wears WHERE date >= {ph}",
                    (month_start,))
        wears_this_month = int(cur.fetchone()["n"])
    total_wears = sum(f["wear_count"] or 0 for f in frags)
    worn = sorted(frags, key=lambda f: f["wear_count"] or 0)
    neglected = [
        {"id": f["id"], "name": f["name"], "days_since_worn": f["days_since_worn"]}
        for f in frags
        if f["days_since_worn"] is None or f["days_since_worn"] > 30
    ]
    signature = next((f for f in frags if f["is_signature"]), None)
    return {
        "total_wears": total_wears,
        "wears_this_month": wears_this_month,
        "most_worn": ({"id": worn[-1]["id"], "name": worn[-1]["name"],
                       "wear_count": worn[-1]["wear_count"]} if worn and worn[-1]["wear_count"] else None),
        "least_worn": ({"id": worn[0]["id"], "name": worn[0]["name"],
                        "wear_count": worn[0]["wear_count"]} if worn else None),
        "signature": ({"id": signature["id"], "name": signature["name"]} if signature else None),
        "neglected": neglected,
        "rotation": [
            {"id": f["id"], "name": f["name"], "wear_count": f["wear_count"] or 0,
             "share": round((f["wear_count"] or 0) / total_wears, 3) if total_wears else 0}
            for f in frags
        ],
    }


# ── Scent combo ratings (Tier 3 Part 2) ──────────────────────────────────────
# A 👍/👎 on a worn pairing. Score = clamp(sum of last 5 ratings, -3, +3), so
# taste can drift and old votes age out. The scorer adds net*0.5 (max ±1.5) — a
# nudge that stays below one full season/occasion weight, never an override.

def rate_pairing(pairing_id: int, rating: int) -> bool:
    """Record a 👍(+1)/👎(-1) for a pairing. Returns False if the pairing is
    unknown or the rating is out of range (defence in depth over the CHECK)."""
    if rating not in (1, -1):
        return False
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT id FROM fragrance_pairings WHERE id = {ph}", (pairing_id,))
        if not cur.fetchone():
            return False
        cur.execute(
            f"INSERT INTO fragrance_pairing_ratings (pairing_id, rating) "
            f"VALUES ({ph}, {ph})", (pairing_id, rating))
    return True


def get_pairing_net(pairing_id: int) -> int:
    """clamp(sum of last 5 ratings, -3, +3) for one pairing (0 if none)."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT rating FROM fragrance_pairing_ratings WHERE pairing_id = {ph} "
            f"ORDER BY id DESC LIMIT 5", (pairing_id,))
        return max(-3, min(3, sum(int(r["rating"]) for r in cur.fetchall())))


def get_pairing_nets_by_fragrance() -> dict:
    """{fragrance_id: net} across the whole shelf, so the recommendation scorer
    can fetch every pairing's nudge in one call instead of per-bottle queries.
    Only the last 5 ratings per pairing count (windowed in Python — a handful of
    pairings, so this is cheap)."""
    _ensure_fragrance_tables()
    nets = {}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, fragrance_id FROM fragrance_pairings")
        pairings = [dict(r) for r in cur.fetchall()]
        ph = "%s" if USE_POSTGRES else "?"
        for p in pairings:
            cur.execute(
                f"SELECT rating FROM fragrance_pairing_ratings WHERE pairing_id = {ph} "
                f"ORDER BY id DESC LIMIT 5", (p["id"],))
            net = max(-3, min(3, sum(int(r["rating"]) for r in cur.fetchall())))
            if net:
                nets[p["fragrance_id"]] = net
    return nets


# ── Body composition + progress photos (Part 5) ──────────────────────────────
# Renpho ("Rephno") ships no official public API — only a manual CSV export in
# its app and unofficial reverse-engineered clients. So manual entry is the
# primary path; services/rephno.py is a gated seam for a future sync.

_BODY_READY = False


def _ensure_body_tables():
    global _BODY_READY
    if _BODY_READY:
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS body_composition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_scanned TEXT NOT NULL,
            weight_kg REAL,
            bmi REAL,
            body_fat_percent REAL,
            ffm_kg REAL,
            body_water_percent REAL,
            bmr INTEGER,
            subcutaneous_fat_percent REAL,
            source_id TEXT,
            synced_at TEXT,
            created_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS gym_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            filename TEXT NOT NULL,
            weight_kg REAL,
            body_fat_percent REAL,
            created_at TEXT
        )""",
    ]
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            if USE_POSTGRES:
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur.execute(stmt)
    _BODY_READY = True


_BODY_FIELDS = ("weight_kg", "bmi", "body_fat_percent", "ffm_kg",
                "body_water_percent", "bmr", "subcutaneous_fat_percent")


def _to_float(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def upsert_body_composition(date_scanned, metrics: dict, source_id=None) -> dict:
    """Insert or update a body-composition scan. Manual entries dedup on the
    scan date (one row per day, last write wins). A sync dedups on source_id
    when given. Returns the stored row."""
    _ensure_body_tables()
    now = datetime.now().isoformat()
    vals = {k: _to_float(metrics.get(k)) for k in _BODY_FIELDS}
    if vals.get("bmr") is not None:
        vals["bmr"] = int(vals["bmr"])
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        existing = None
        if source_id:
            cur.execute(f"SELECT id FROM body_composition WHERE source_id = {ph}", (source_id,))
            existing = cur.fetchone()
        if not existing:
            cur.execute(f"SELECT id FROM body_composition WHERE date_scanned = {ph}", (date_scanned,))
            existing = cur.fetchone()
        if existing:
            set_clause = ", ".join(f"{k} = {ph}" for k in _BODY_FIELDS)
            cur.execute(
                f"UPDATE body_composition SET {set_clause}, source_id = {ph}, synced_at = {ph} "
                f"WHERE id = {ph}",
                tuple(vals[k] for k in _BODY_FIELDS) + (source_id, now, existing["id"]))
            row_id = existing["id"]
        else:
            cols = "date_scanned, " + ", ".join(_BODY_FIELDS) + ", source_id, synced_at, created_at"
            values = (date_scanned,) + tuple(vals[k] for k in _BODY_FIELDS) + (source_id, now, now)
            row_id = _gym_insert(cur, "body_composition", cols, values)
    return get_body_composition_row(row_id)


def get_body_composition_row(row_id) -> dict:
    _ensure_body_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT * FROM body_composition WHERE id = {ph}", (row_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def get_body_composition(days=30) -> list:
    """Latest scans within the last `days`, newest first."""
    _ensure_body_tables()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT * FROM body_composition WHERE date_scanned >= {ph} "
            f"ORDER BY date_scanned DESC, id DESC", (cutoff,))
        return [dict(r) for r in cur.fetchall()]


def body_composition_source_exists(source_id) -> bool:
    """True if a synced scan with this source_id already exists (sync dedup)."""
    if not source_id:
        return False
    _ensure_body_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"SELECT 1 FROM body_composition WHERE source_id = {ph} LIMIT 1", (source_id,))
        return cur.fetchone() is not None


def latest_body_composition() -> dict:
    """Most recent scan of any date, or None."""
    _ensure_body_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM body_composition ORDER BY date_scanned DESC, id DESC LIMIT 1")
        r = cur.fetchone()
        return dict(r) if r else None


def add_gym_photo(date_str, filename, weight_kg=None, body_fat_percent=None) -> dict:
    """Record an uploaded progress photo, auto-tagged with the day's weight/bf%."""
    _ensure_body_tables()
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        pid = _gym_insert(
            cur, "gym_photos", "date, filename, weight_kg, body_fat_percent, created_at",
            (date_str, filename, _to_float(weight_kg), _to_float(body_fat_percent), now))
    return {"id": pid, "date": date_str, "filename": filename,
            "weight_kg": _to_float(weight_kg), "body_fat_percent": _to_float(body_fat_percent)}


def get_gym_photos() -> list:
    """All progress photos, newest first."""
    _ensure_body_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM gym_photos ORDER BY date DESC, id DESC")
        return [dict(r) for r in cur.fetchall()]


# ── Weekly-digest summaries (Tier 3 Part 5) ──────────────────────────────────
# Per-module one-week rollups for the Telegram digest. All ranges are half-open
# [start, end_excl) on ISO date strings so a timestamp column (e.g. scout's
# date_stage_changed) on the last day isn't excluded by a string `<=` compare.

def get_gym_week_summary(start_date, end_excl) -> dict:
    """{sessions, volume_kg, prs, avg_rpe} for gym sessions in [start, end_excl)."""
    _ensure_gym_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(total_volume_kg),0) AS vol "
            f"FROM gym_sessions WHERE date >= {ph} AND date < {ph}",
            (start_date, end_excl))
        row = dict(cur.fetchone())
        cur.execute(
            f"SELECT COUNT(*) AS prs FROM gym_sets st "
            f"JOIN gym_sessions s ON s.id = st.session_id "
            f"WHERE s.date >= {ph} AND s.date < {ph} AND st.is_pr = 1",
            (start_date, end_excl))
        prs = int(dict(cur.fetchone())["prs"] or 0)
        cur.execute(
            f"SELECT AVG(st.rpe) AS avg_rpe FROM gym_sets st "
            f"JOIN gym_sessions s ON s.id = st.session_id "
            f"WHERE s.date >= {ph} AND s.date < {ph} AND st.rpe IS NOT NULL",
            (start_date, end_excl))
        avg_rpe = dict(cur.fetchone())["avg_rpe"]
    return {"sessions": int(row["n"] or 0), "volume_kg": round(float(row["vol"] or 0), 1),
            "prs": prs, "avg_rpe": round(float(avg_rpe), 1) if avg_rpe is not None else None}


def get_scout_week_summary(start_date, end_excl) -> dict:
    """{new_saved, new_applied, stage_changes, followups_due} for the week."""
    _ensure_scout_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"

        def cnt(col):
            cur.execute(
                f"SELECT COUNT(*) AS n FROM scout_pipeline "
                f"WHERE {col} >= {ph} AND {col} < {ph}", (start_date, end_excl))
            return int(dict(cur.fetchone())["n"])

        summary = {"new_saved": cnt("date_saved"), "new_applied": cnt("date_applied"),
                   "stage_changes": cnt("date_stage_changed")}
    summary["followups_due"] = len(get_scout_pipeline_reminders(7))
    return summary


def get_scent_week_summary(start_date, end_excl) -> dict:
    """{wears, top} — wear count and the most-worn bottle name for the week."""
    _ensure_fragrance_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COUNT(*) AS n FROM fragrance_wears WHERE date >= {ph} AND date < {ph}",
            (start_date, end_excl))
        wears = int(dict(cur.fetchone())["n"])
        cur.execute(
            f"SELECT f.name AS name, COUNT(*) AS n FROM fragrance_wears w "
            f"JOIN fragrances f ON f.id = w.fragrance_id "
            f"WHERE w.date >= {ph} AND w.date < {ph} "
            f"GROUP BY w.fragrance_id, f.name ORDER BY n DESC LIMIT 1",
            (start_date, end_excl))
        r = cur.fetchone()
    return {"wears": wears, "top": (dict(r)["name"] if r else None)}


def count_security_lockouts(start_iso) -> int:
    """Lockout events (Sentinel security_alert episodics) since start_iso. Read
    from episodic memory because auth_failures is pruned hourly and can't carry a
    weekly count."""
    _ensure_agent_data_tables()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT COUNT(*) AS n FROM agent_memory_episodic "
            f"WHERE event_type = 'security_alert' AND created_at >= {ph}", (start_iso,))
        return int(dict(cur.fetchone())["n"])


# ── FragDB reference lookup (Tier 3 Part 6) ──────────────────────────────────
# A large read-only reference table (~59k Parfumo rows) for name autocomplete +
# notes/accords prefill when adding a fragrance. Populated by
# scripts/import_fragdb.py from a gitignored CSV — never committed. Kept separate
# from the curated `fragrances` shelf, which this never touches.

_FRAGREF_READY = False


def _ensure_fragrance_reference():
    global _FRAGREF_READY
    if _FRAGREF_READY:
        return
    with get_db() as conn:
        cur = conn.cursor()
        stmt = """CREATE TABLE IF NOT EXISTS fragrance_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            concentration TEXT,
            accords TEXT,
            notes TEXT,
            url TEXT
        )"""
        if USE_POSTGRES:
            stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        cur.execute(stmt)
        # Prefix search runs on the name; a plain index serves LIKE 'q%'.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fragref_name ON fragrance_reference(name)")
    _FRAGREF_READY = True


def import_fragrance_reference(records, replace=True, batch=1000) -> int:
    """Bulk-load reference rows. `records` is an iterable of dicts with keys
    name/brand/concentration/accords/notes/url. Idempotent when replace=True
    (clears the table first). Returns rows inserted."""
    _ensure_fragrance_reference()
    cols = ("name", "brand", "concentration", "accords", "notes", "url")
    inserted = 0
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        if replace:
            cur.execute("DELETE FROM fragrance_reference")
        sql = (f"INSERT INTO fragrance_reference ({','.join(cols)}) "
               f"VALUES ({','.join([ph]*len(cols))})")
        buf = []
        for rec in records:
            if not rec.get("name"):
                continue
            buf.append(tuple(rec.get(c) for c in cols))
            if len(buf) >= batch:
                cur.executemany(sql, buf); inserted += len(buf); buf = []
        if buf:
            cur.executemany(sql, buf); inserted += len(buf)
    return inserted


def search_fragrance_reference(query: str, limit: int = 8) -> list:
    """Prefix (then substring) name search for add-fragrance autocomplete. Returns
    [] for a blank/too-short query so we never scan the whole table."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    _ensure_fragrance_reference()
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        # Prefer prefix matches (index-friendly), fall back to substring, dedup by
        # name+brand, cap at `limit`. Two passes keep the common case fast.
        cur.execute(
            f"SELECT name, brand, concentration, accords, notes FROM fragrance_reference "
            f"WHERE name LIKE {ph} ORDER BY name LIMIT {ph}", (q + "%", limit))
        rows = [dict(r) for r in cur.fetchall()]
        if len(rows) < limit:
            seen = {(r["name"], r["brand"]) for r in rows}
            cur.execute(
                f"SELECT name, brand, concentration, accords, notes FROM fragrance_reference "
                f"WHERE name LIKE {ph} AND name NOT LIKE {ph} ORDER BY name LIMIT {ph}",
                ("%" + q + "%", q + "%", limit - len(rows)))
            for r in cur.fetchall():
                r = dict(r)
                if (r["name"], r["brand"]) not in seen:
                    rows.append(r)
    return rows


def fragrance_reference_count() -> int:
    _ensure_fragrance_reference()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM fragrance_reference")
        return int(dict(cur.fetchone())["n"])


def create_fragrance(name, brand, notes=None, concentration=None, vibe=None,
                     best_seasons=None, time_of_day=None, occasions=None,
                     longevity_hrs=None) -> dict:
    """Add a new bottle to the curated shelf (Tier 3 Part 6 add flow). Only ever
    INSERTs — never touches the seeded 7. Returns the new fragrance dict."""
    _ensure_fragrance_tables()
    name = (name or "").strip()
    brand = (brand or "").strip()
    if not name or not brand:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cols = ("name, brand, notes, concentration, vibe, best_seasons, "
                "time_of_day, occasions, longevity_hrs")
        cur.execute(
            f"INSERT INTO fragrances ({cols}) VALUES ({','.join([ph]*9)})",
            (name, brand, notes, concentration, vibe, best_seasons,
             time_of_day, occasions,
             int(longevity_hrs) if longevity_hrs not in (None, "") else None))
        new_id = cur.lastrowid if not USE_POSTGRES else None
        if USE_POSTGRES:
            cur.execute("SELECT MAX(id) AS id FROM fragrances WHERE name = %s AND brand = %s",
                        (name, brand))
            new_id = dict(cur.fetchone())["id"]
    return get_fragrance(new_id)


# ── Steps (manual walking + cardio→step-equivalents) ─────────────────────────
# The `steps` table holds ONE row per logged session (multiple per date), so a
# day's total is a SUM — a day can hold "3,000 manual + 4,200 treadmill" and any
# single entry can be deleted without losing the rest. `detail` stores the raw
# inputs as a JSON string (e.g. {"minutes":30,"kph":6.5,"incline":2}). Mirrors
# the meals/nutrition_goals shape: lazy table creation + a single-row goal.
DEFAULT_STEPS_GOAL = 10000
STEP_SOURCES = ("manual", "treadmill", "bike")
_STEPS_READY = False


def _ensure_steps_tables():
    global _STEPS_READY
    if _STEPS_READY:
        return
    entries = """CREATE TABLE IF NOT EXISTS steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        source TEXT NOT NULL,
        steps INTEGER NOT NULL,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    goal = """CREATE TABLE IF NOT EXISTS steps_goal (
        id INTEGER PRIMARY KEY,
        steps_goal INTEGER NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    if USE_POSTGRES:
        entries = entries.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(entries)
        cur.execute(goal)
    _STEPS_READY = True


def get_steps_goal() -> int:
    """The user's daily step target, defaulting to DEFAULT_STEPS_GOAL until set."""
    _ensure_steps_tables()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT steps_goal FROM steps_goal WHERE id = 1")
        row = cur.fetchone()
    if not row:
        return DEFAULT_STEPS_GOAL
    return int(row["steps_goal"])


def set_steps_goal(steps_goal) -> int:
    """Upsert the single step-goal row. Returns the stored goal. Raises
    ValueError on a non-integer or non-positive target."""
    _ensure_steps_tables()
    if isinstance(steps_goal, bool):
        raise ValueError("steps_goal must be a positive integer")
    try:
        goal = int(steps_goal)
    except (TypeError, ValueError):
        raise ValueError("steps_goal must be a positive integer")
    if goal < 1 or goal > 100000:
        raise ValueError("steps_goal must be between 1 and 100000")

    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO steps_goal (id, steps_goal, updated_at) "
                f"VALUES (1,{ph},NOW()) "
                "ON CONFLICT (id) DO UPDATE SET "
                "steps_goal=EXCLUDED.steps_goal, updated_at=NOW()",
                (goal,))
        else:
            cur.execute(
                "INSERT INTO steps_goal (id, steps_goal, updated_at) "
                f"VALUES (1,{ph},CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET "
                "steps_goal=excluded.steps_goal, updated_at=CURRENT_TIMESTAMP",
                (goal,))
    return get_steps_goal()


def _steps_row(row) -> dict:
    """Public projection of a steps row — parses the JSON detail back to a dict."""
    d = dict(row)
    raw = d.get("detail")
    try:
        detail = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        detail = {}
    return {
        "id": d.get("id"),
        "date": d.get("date"),
        "source": d.get("source"),
        "steps": int(d.get("steps") or 0),
        "detail": detail,
    }


def add_step_entry(date_str, source, steps, detail=None) -> dict:
    """Insert one step session. `steps` is the final (already-converted) count;
    `detail` is a dict of the raw inputs, stored as JSON. Returns the new row."""
    _ensure_steps_tables()
    detail_json = json.dumps(detail) if detail else None
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO steps (date, source, steps, detail) VALUES ({ph},{ph},{ph},{ph})",
            (date_str, source, int(steps), detail_json))
        new_id = cur.lastrowid if not USE_POSTGRES else None
        if USE_POSTGRES:
            cur.execute("SELECT MAX(id) AS id FROM steps WHERE date = %s AND source = %s",
                        (date_str, source))
            new_id = dict(cur.fetchone())["id"]
    return {"id": new_id, "date": date_str, "source": source,
            "steps": int(steps), "detail": detail or {}}


def get_steps_for_date(date_str) -> list:
    """All step sessions for `date_str`, oldest first. Each is a _steps_row dict."""
    _ensure_steps_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM steps WHERE date = {ph} ORDER BY id ASC", (date_str,))
        return [_steps_row(r) for r in cur.fetchall()]


def get_steps_day_total(date_str) -> int:
    """SUM of every step session on `date_str` (0 if none)."""
    _ensure_steps_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COALESCE(SUM(steps),0) AS total FROM steps WHERE date = {ph}",
                    (date_str,))
        row = cur.fetchone()
    return int(row["total"] or 0)


def delete_step_entry(entry_id) -> str:
    """Delete one step session by id. Returns the deleted row's date (so the
    caller can recompute that day's total), or None if nothing was removed."""
    _ensure_steps_tables()
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT date FROM steps WHERE id = {ph}", (entry_id,))
        row = cur.fetchone()
        if not row:
            return None
        date_str = dict(row)["date"]
        cur.execute(f"DELETE FROM steps WHERE id = {ph}", (entry_id,))
    return date_str


def get_steps_week(end_date=None) -> dict:
    """7-day {dates:[], totals:[], goal} strip ending at `end_date` (today by
    default), ZERO-FILLED for unlogged days so gaps show as empty bars — same
    shape as the nutrition week trends."""
    _ensure_steps_tables()
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = [(end - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)]
    ph = "%s" if USE_POSTGRES else "?"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT date, SUM(steps) AS total FROM steps "
            f"WHERE date >= {ph} AND date <= {ph} GROUP BY date",
            (dates[0], end_date))
        by_date = {r["date"]: int(r["total"] or 0) for r in cur.fetchall()}
    return {
        "dates": dates,
        "totals": [by_date.get(d, 0) for d in dates],
        "goal": get_steps_goal(),
    }


# ── Data export (one CSV string per module) ──────────────────────────────────
# Pure read-only serializers used by the "Export All Data" endpoint. Each
# returns a CSV string (with a single header row) or "" when the module has no
# rows / the table was never created — the caller skips empty modules so the
# ZIP only contains files that hold data. Columns map onto the REAL schema; the
# export spec's requested columns that don't exist (serving_size, readiness,
# rpe, merchant, dose) are simply omitted.
import csv as _export_csv
from io import StringIO as _ExportStringIO


def _looks_numeric(s: str) -> bool:
    """True if the whole string is a plain number ('-12.50', '1e3', '.5').
    Used to keep genuine negatives numeric while still guarding formula-ish
    values that merely start with '-'."""
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def csv_safe(value) -> str:
    """Neutralize CSV / spreadsheet formula injection.

    Excel, Google Sheets and LibreOffice evaluate a cell as a formula when it
    begins with '=', '+', '-', '@', a tab or a carriage return — so an
    attacker-influenceable value like ``=HYPERLINK("http://evil")`` (an Open Food
    Facts product name, a Reed/SerpAPI job title, or a user-typed note) executes
    on open. We prefix a single quote so the cell is treated as literal text.

    Negative numbers: a leading '-' is only guarded when the value is NOT a bona
    fide number. This neutralizes real formulas ('-1+1', '-cmd|...') while leaving
    plain negatives ('-12.50', finance income/refunds) numeric and sortable. (The
    stricter "always prefix leading '-'" would turn every negative amount into
    text; the full-number check is safer than a next-char heuristic, which would
    let '-1+1' — a valid formula — through.)

    csv.writer's own quoting of commas/quotes/newlines is unaffected; empty cells
    stay empty. Single source of truth: app.py's _csv_response imports this too.
    """
    s = "" if value is None else str(value)
    if not s:
        return s
    first = s[0]
    if first in ("=", "+", "@", "\t", "\r"):
        return "'" + s
    if first == "-" and not _looks_numeric(s):
        return "'" + s
    return s


def _export_query(sql):
    """Run a read-only SELECT, return list of dict rows. Returns [] if the
    table doesn't exist yet or on any read error (module never used)."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _export_rows_to_csv(header, rows, field_fn):
    """Serialize `rows` to a CSV string. `field_fn(row)` -> list of cells that
    line up with `header`. Returns "" for zero rows (signals: skip module)."""
    if not rows:
        return ""
    buf = _ExportStringIO()
    writer = _export_csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow([csv_safe(c) for c in field_fn(r)])
    return buf.getvalue()


def csv_nutrition(user_id=None):
    """meals table → date,time,food_name,protein_g,carbs_g,fat_g,kcal,notes."""
    rows = _export_query(
        "SELECT date, time, food_name, protein, carbs, fat, calories, notes "
        "FROM meals ORDER BY date DESC, time DESC")
    return _export_rows_to_csv(
        ["date", "time", "food_name", "protein_g", "carbs_g", "fat_g", "kcal", "notes"],
        rows,
        lambda r: [r.get("date"), r.get("time"), r.get("food_name"),
                   r.get("protein"), r.get("carbs"), r.get("fat"),
                   r.get("calories"), r.get("notes")])


def csv_sleep(user_id=None):
    """sleep table → date,duration_hours,quality_1_5,wake_feeling,notes."""
    rows = _export_query(
        "SELECT date, duration, quality, wake_feeling, notes "
        "FROM sleep ORDER BY date DESC")
    return _export_rows_to_csv(
        ["date", "duration_hours", "quality_1_5", "wake_feeling", "notes"],
        rows,
        lambda r: [r.get("date"), r.get("duration"), r.get("quality"),
                   r.get("wake_feeling"), r.get("notes")])


def csv_gym(user_id=None):
    """workouts table → date,exercise,weight_kg,reps,sets,muscle_group,pr_flag,notes."""
    rows = _export_query(
        "SELECT date, exercise, weight_kg, reps, sets, muscle_group, is_pb, notes "
        "FROM workouts ORDER BY date DESC")
    return _export_rows_to_csv(
        ["date", "exercise", "weight_kg", "reps", "sets", "muscle_group", "pr_flag", "notes"],
        rows,
        lambda r: [r.get("date"), r.get("exercise"), r.get("weight_kg"),
                   r.get("reps"), r.get("sets"), r.get("muscle_group"),
                   1 if r.get("is_pb") else 0, r.get("notes")])


def csv_finance(user_id=None):
    """spending table → date,amount_gbp,category,notes."""
    rows = _export_query(
        "SELECT date, amount, category, note FROM spending ORDER BY date DESC")
    return _export_rows_to_csv(
        ["date", "amount_gbp", "category", "notes"],
        rows,
        lambda r: [r.get("date"), r.get("amount"), r.get("category"), r.get("note")])


def csv_body_comp(user_id=None):
    """body_composition table → date,weight_kg,body_fat_percent,bmi."""
    rows = _export_query(
        "SELECT date_scanned, weight_kg, body_fat_percent, bmi "
        "FROM body_composition ORDER BY date_scanned DESC")
    return _export_rows_to_csv(
        ["date", "weight_kg", "body_fat_percent", "bmi"],
        rows,
        lambda r: [r.get("date_scanned"), r.get("weight_kg"),
                   r.get("body_fat_percent"), r.get("bmi")])


def csv_water(user_id=None):
    """hydration_log table → date,volume_ml,time."""
    rows = _export_query(
        "SELECT date, amount_ml, logged_at FROM hydration_log "
        "ORDER BY date DESC, logged_at DESC")
    return _export_rows_to_csv(
        ["date", "volume_ml", "time"],
        rows,
        lambda r: [r.get("date"), r.get("amount_ml"), r.get("logged_at")])


def csv_supplements(user_id=None):
    """supplements_log table → date,supplement_name (date derived from taken_at)."""
    rows = _export_query(
        "SELECT supplement_name, taken_at FROM supplements_log "
        "ORDER BY taken_at DESC")
    return _export_rows_to_csv(
        ["date", "supplement_name"],
        rows,
        lambda r: [(r.get("taken_at") or "")[:10], r.get("supplement_name")])


def csv_steps(user_id=None):
    """steps table → date,source,steps,detail_json (sorted date DESC)."""
    rows = _export_query(
        "SELECT date, source, steps, detail FROM steps ORDER BY date DESC, id DESC")
    return _export_rows_to_csv(
        ["date", "source", "steps", "detail_json"],
        rows,
        lambda r: [r.get("date"), r.get("source"), r.get("steps"),
                   r.get("detail") or ""])


def export_all_csvs():
    """Return {filename: csv_string} for every module that has data. Modules
    with zero rows are omitted so the ZIP never contains empty files."""
    modules = {
        "nutrition.csv": csv_nutrition,
        "sleep.csv": csv_sleep,
        "gym.csv": csv_gym,
        "finance.csv": csv_finance,
        "body_comp.csv": csv_body_comp,
        "water.csv": csv_water,
        "supplements.csv": csv_supplements,
        "steps.csv": csv_steps,
    }
    out = {}
    for filename, fn in modules.items():
        content = fn()
        if content:
            out[filename] = content
    return out


# ── Interview Assistant helpers ─────────────────────────────────────────────────
# Back the /interview page (merged from the former standalone interview_assistant
# app). Tables interview_sessions / interview_qa are created in init_db(). SQLite
# locally, Postgres on Railway — hence the ph placeholder + RETURNING/lastrowid
# split used elsewhere in this module.

def interview_new_session(role, mode):
    """Create a session row and return its id."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        created = datetime.now().isoformat()
        sql = (f"INSERT INTO interview_sessions (role, mode, created_at) "
               f"VALUES ({ph},{ph},{ph})")
        if USE_POSTGRES:
            cur.execute(sql + " RETURNING id", (role, mode, created))
            return cur.fetchone()["id"]
        cur.execute(sql, (role, mode, created))
        return cur.lastrowid


def interview_save_qa(session_id, question, answer, ts):
    """Append a Q&A row to a session and return its id."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        created = datetime.now().isoformat()
        sql = (f"INSERT INTO interview_qa (session_id, question, answer, ts, created_at) "
               f"VALUES ({ph},{ph},{ph},{ph},{ph})")
        vals = (session_id, question, answer, ts, created)
        if USE_POSTGRES:
            cur.execute(sql + " RETURNING id", vals)
            return cur.fetchone()["id"]
        cur.execute(sql, vals)
        return cur.lastrowid


def interview_rate(qa_id, rating):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"UPDATE interview_qa SET rating={ph} WHERE id={ph}", (rating, qa_id))


def interview_list_sessions(limit=100):
    """Sessions that have at least one Q&A, newest first, with question count and
    average non-zero rating. avg_rating/n are coerced to float/int so jsonify
    never chokes on a Postgres Decimal."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT s.id, s.role, s.mode, s.created_at, "
            "       COUNT(q.id) AS n, "
            "       COALESCE(AVG(NULLIF(q.rating, 0)), 0) AS avg_rating "
            "FROM interview_sessions s "
            "LEFT JOIN interview_qa q ON q.session_id = s.id "
            "GROUP BY s.id, s.role, s.mode, s.created_at "
            "HAVING COUNT(q.id) > 0 "
            "ORDER BY s.created_at DESC LIMIT " + str(int(limit)))
        out = []
        for r in cur.fetchall():
            d = dict(r)
            d["n"] = int(d["n"] or 0)
            d["avg_rating"] = float(d["avg_rating"] or 0)
            out.append(d)
        return out


def interview_session_detail(sid):
    """Full session + ordered Q&A, or None if the session doesn't exist."""
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(
            f"SELECT id, role, mode, created_at, ended_at FROM interview_sessions "
            f"WHERE id={ph}", (sid,))
        s = cur.fetchone()
        if not s:
            return None
        cur.execute(
            f"SELECT id, question, answer, rating, ts FROM interview_qa "
            f"WHERE session_id={ph} ORDER BY id", (sid,))
        qa = [dict(r) for r in cur.fetchall()]
    return {"session": dict(s), "qa": qa}


def interview_delete_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cur.execute(f"DELETE FROM interview_qa WHERE session_id={ph}", (sid,))
        cur.execute(f"DELETE FROM interview_sessions WHERE id={ph}", (sid,))
