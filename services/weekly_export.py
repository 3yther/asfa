"""Weekly CSV export — Sunday noon ET, the week that just ended, by email.

Builds five CSVs (gym, nutrition, steps, sleep, cardio) covering the completed
Sunday 00:00 → Saturday 23:59 week and emails them as attachments.

Window: the job fires Sunday 12:00 America/New_York, so the week it exports is
the one that ended *yesterday* — last Sunday through last Saturday. It never
exports a partial in-flight week.

Table mapping (the obvious names don't all exist — see module notes):
  gym       → gym_sets ⋈ gym_sessions (date lives on the session) ⋈ gym_exercises
  nutrition → meals            (there is no `nutrition_entries` table)
  steps     → steps
  sleep     → sleep            (there is no `sleep_log` table)
  cardio    → cardio_sessions  (all types, not just cycling; `type` distinguishes)

Every exporter is independent: one failing table yields a header-only CSV and a
logged warning rather than killing the whole export, matching how the other
scheduler jobs degrade.

Env vars:
  EXPORT_EMAIL_TO   recipient (defaults to DEFAULT_RECIPIENT below)
  EXPORT_TZ         window/trigger timezone (defaults to America/New_York)
"""
import csv
import io
import logging
import os
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None

import database as db
from services import alerts

logger = logging.getLogger("asfa.weekly_export")

DEFAULT_RECIPIENT = "ami.salax08@gmail.com"


def _tz():
    """The timezone the Sunday-noon trigger and the week window are defined in."""
    name = os.environ.get("EXPORT_TZ", "America/New_York")
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("Unknown EXPORT_TZ %r; falling back to app timezone", name)
        return db.APP_TZ


def week_window(now: datetime = None):
    """(start, end) date strings for the most recently completed Sun→Sat week.

    `end` is the last Saturday strictly before today, `start` the Sunday six days
    before it. Fired Sunday noon that is simply "yesterday and the six days
    before it"; defining it this way keeps a manual mid-week trigger sane too.
    """
    if now is None:
        tz = _tz()
        now = datetime.now(tz) if tz else db.now_local()
    today = now.date()
    # Monday=0 … Saturday=5, Sunday=6. Step back to the previous Saturday,
    # never landing on today (a Saturday run means last Saturday, not today).
    days_back = (today.weekday() - 5) % 7 or 7
    end = today - timedelta(days=days_back)
    start = end - timedelta(days=6)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _rows_to_csv(columns, rows):
    """Render rows (sequence of dict-likes) as CSV text with a header line.

    `columns` is a list of (output_header, row_key) pairs. The two differ where
    a column needs a unit suffix — the naming convention the existing
    `database.csv_*` serializers use, kept identical here so the weekly export
    and the /api/export/all-data bundle don't disagree on column names.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for header, _ in columns])
    for row in rows:
        d = dict(row)
        writer.writerow([d.get(key) for _, key in columns])
    return buf.getvalue()


def _query(sql, start, end):
    """Run a two-parameter date-range query against SQLite or Postgres."""
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql.replace("?", ph), (start, end))
        return cur.fetchall()


# ── Per-table exporters ────────────────────────────────────────────────────────
# Each returns (filename, csv_text) and never raises.

def _safe(name, columns, fn):
    try:
        return fn()
    except Exception as e:
        logger.warning("%s export failed (%s); emitting header-only CSV", name, e)
        return _rows_to_csv(columns, [])


GYM_COLUMNS = [("date", "date"), ("session_id", "session_id"),
               ("exercise", "exercise"), ("muscle_group", "muscle_group"),
               ("set_number", "set_number"), ("set_type", "set_type"),
               ("weight_kg", "weight_kg"), ("reps", "reps"), ("rpe", "rpe"),
               ("pr_flag", "is_pr"), ("pre_workout", "pre_workout_type"),
               ("notes", "notes"), ("completed_at", "completed_at")]


def export_gym(start, end):
    """Every set logged in the window. gym_sets has no date of its own — the
    calendar day lives on gym_sessions, so the join is what makes this filterable."""
    def run():
        rows = _query("""
            SELECT ses.date          AS date,
                   gs.session_id     AS session_id,
                   ex.name           AS exercise,
                   ex.muscle_group   AS muscle_group,
                   gs.set_number     AS set_number,
                   gs.set_type       AS set_type,
                   gs.weight_kg      AS weight_kg,
                   gs.reps           AS reps,
                   gs.rpe            AS rpe,
                   gs.is_pr          AS is_pr,
                   gs.pre_workout_type AS pre_workout_type,
                   gs.notes          AS notes,
                   gs.completed_at   AS completed_at
            FROM gym_sets gs
            JOIN gym_sessions ses ON ses.id = gs.session_id
            LEFT JOIN gym_exercises ex ON ex.id = gs.exercise_id
            WHERE ses.date >= ? AND ses.date <= ?
            ORDER BY ses.date, gs.session_id, gs.set_number
        """, start, end)
        return _rows_to_csv(GYM_COLUMNS, rows)
    return "gym.csv", _safe("gym", GYM_COLUMNS, run)


NUTRITION_COLUMNS = [("date", "date"), ("time", "time"),
                     ("food_name", "food_name"), ("kcal", "calories"),
                     ("protein_g", "protein"), ("carbs_g", "carbs"),
                     ("fat_g", "fat"), ("source", "source"),
                     ("food_source", "food_source"), ("barcode", "barcode"),
                     ("notes", "notes"), ("created_at", "created_at")]


def export_nutrition(start, end):
    """Meals logged in the window. The table is `meals`, not `nutrition_entries`."""
    def run():
        rows = _query("""
            SELECT date, time, food_name, calories, protein, carbs, fat,
                   source, food_source, barcode, notes, created_at
            FROM meals
            WHERE date >= ? AND date <= ?
            ORDER BY date, time
        """, start, end)
        return _rows_to_csv(NUTRITION_COLUMNS, rows)
    return "nutrition.csv", _safe("nutrition", NUTRITION_COLUMNS, run)


STEPS_COLUMNS = [("date", "date"), ("source", "source"), ("steps", "steps"),
                 ("detail_json", "detail"), ("created_at", "created_at")]


def export_steps(start, end):
    """Daily step counts. One row per logged source per day, as stored."""
    def run():
        rows = _query("""
            SELECT date, steps, source, detail, created_at
            FROM steps
            WHERE date >= ? AND date <= ?
            ORDER BY date, source
        """, start, end)
        return _rows_to_csv(STEPS_COLUMNS, rows)
    return "steps.csv", _safe("steps", STEPS_COLUMNS, run)


SLEEP_COLUMNS = [("date", "date"), ("duration_hours", "duration"),
                 ("quality_1_5", "quality"), ("wake_feeling", "wake_feeling"),
                 ("notes", "notes"), ("created_at", "created_at")]


def export_sleep(start, end):
    """Sleep logs. The table is `sleep`, not `sleep_log`."""
    def run():
        rows = _query("""
            SELECT date, duration, quality, wake_feeling, notes, created_at
            FROM sleep
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """, start, end)
        return _rows_to_csv(SLEEP_COLUMNS, rows)
    return "sleep.csv", _safe("sleep", SLEEP_COLUMNS, run)


CARDIO_COLUMNS = [("date", "date"), ("type", "type"),
                  ("distance_miles", "distance_miles"),
                  ("duration_minutes", "duration_minutes"),
                  ("avg_speed", "avg_speed"), ("max_speed", "max_speed"),
                  ("elevation_gain", "elevation_gain"),
                  ("steps_equivalent", "steps_equivalent"),
                  ("effort_1_10", "perceived_effort"),
                  ("start_time", "start_time"), ("notes", "notes"),
                  ("created_at", "created_at")]


def export_cardio(start, end):
    """All cardio sessions in the window — cycling included, but not filtered to
    it; the `type` column distinguishes, and filtering here would silently drop
    runs and walks from the export."""
    def run():
        rows = _query("""
            SELECT date, type, distance_miles, duration_minutes, avg_speed,
                   max_speed, elevation_gain, steps_equivalent, perceived_effort,
                   start_time, notes, created_at
            FROM cardio_sessions
            WHERE date >= ? AND date <= ?
            ORDER BY date, start_time
        """, start, end)
        return _rows_to_csv(CARDIO_COLUMNS, rows)
    return "cardio.csv", _safe("cardio", CARDIO_COLUMNS, run)


EXPORTERS = [export_gym, export_nutrition, export_steps, export_sleep, export_cardio]


def build_export(start=None, end=None):
    """Build all five CSVs for the window. Returns (start, end, attachments)
    where attachments is a list of (filename, csv_text)."""
    if start is None or end is None:
        start, end = week_window()
    attachments = [fn(start, end) for fn in EXPORTERS]
    return start, end, attachments


def _row_count(csv_text):
    """Data rows in a rendered CSV (total lines minus the header)."""
    return max(0, len(csv_text.strip().splitlines()) - 1)


def send_weekly_export(start=None, end=None, dry_run=False):
    """Build the week's CSVs and email them. Returns a result dict; never raises.

    `dry_run=True` builds the CSVs and skips the send — used by the test script
    and safe to call by hand at any time.
    """
    start, end, attachments = build_export(start, end)
    counts = {name: _row_count(text) for name, text in attachments}
    total = sum(counts.values())
    result = {"start": start, "end": end, "counts": counts, "rows": total,
              "attachments": attachments, "emailed": False}

    subject = f"ASFA Weekly Export — {start} to {end}"
    body = "Your weekly ASFA data export. See attached."
    to_addr = os.environ.get("EXPORT_EMAIL_TO", DEFAULT_RECIPIENT)
    result["to"] = to_addr

    if dry_run:
        result["skipped"] = "dry_run"
        return result
    if not alerts.smtp_configured():
        logger.warning("Weekly export built (%d rows) but SMTP is not configured; "
                       "no email sent.", total)
        result["skipped"] = "smtp_not_configured"
        return result

    result["emailed"] = alerts.send_email_with_attachments(
        subject, body, attachments, to_addr=to_addr)
    if not result["emailed"]:
        result["skipped"] = "send_failed"
    return result
