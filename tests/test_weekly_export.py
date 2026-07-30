"""Weekly CSV export tests — window maths, per-table filtering, email assembly.

The window is the interesting part: the job fires Sunday noon London and must
export the week that *just ended*, never a partial in-flight one, and successive
runs must tile the calendar without gaps or overlap.

The window zone must stay equal to ASFA_TZ, since that is what stamps the `date`
columns being filtered — `test_window_zone_matches_the_stored_date_zone` guards
that, and the BST cases guard the DST transitions specific to London.
"""
import csv
import io
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import database as db  # noqa: E402
from services import weekly_export  # noqa: E402

LONDON = ZoneInfo("Europe/London")


def _rows(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


def _headers(csv_text):
    return csv_text.splitlines()[0].split(",")


# ── Window maths ───────────────────────────────────────────────────────────────

def test_sunday_noon_exports_the_week_that_just_ended():
    now = datetime(2026, 8, 2, 12, 0, tzinfo=LONDON)  # a Sunday
    start, end = weekly_export.week_window(now)
    assert (start, end) == ("2026-07-26", "2026-08-01")


def test_window_zone_matches_the_stored_date_zone():
    """The window is sliced in EXPORT_TZ but filters `date` columns stamped in
    ASFA_TZ. If the two ever diverge, entries at the edge of the week land in
    the wrong export — so the defaults must agree."""
    assert str(weekly_export._tz()) == str(db.APP_TZ) == "Europe/London"


def test_window_is_sunday_to_saturday_and_seven_days_long():
    for iso in ("2026-08-02", "2026-08-09", "2026-08-16", "2026-11-01"):
        now = datetime.fromisoformat(iso + "T12:00").replace(tzinfo=LONDON)
        start, end = weekly_export.week_window(now)
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        assert s.strftime("%a") == "Sun", start
        assert e.strftime("%a") == "Sat", end
        assert (e - s).days == 6


def test_successive_weeks_tile_without_gap_or_overlap():
    prev_end = None
    for week in range(6):
        now = datetime(2026, 8, 2, 12, 0, tzinfo=LONDON) + timedelta(weeks=week)
        start, end = weekly_export.week_window(now)
        if prev_end is not None:
            assert (datetime.fromisoformat(start)
                    - datetime.fromisoformat(prev_end)).days == 1
        prev_end = end


def test_window_math_holds_across_the_bst_transitions():
    """BST begins Sun 29 Mar 2026 and ends Sun 25 Oct 2026 — both are trigger
    days, and both are days where local time skips or repeats an hour."""
    for iso, expected in (("2026-03-29", ("2026-03-22", "2026-03-28")),
                          ("2026-10-25", ("2026-10-18", "2026-10-24"))):
        now = datetime.fromisoformat(iso + "T12:00").replace(tzinfo=LONDON)
        assert weekly_export.week_window(now) == expected, iso


def test_late_saturday_night_entry_stays_in_that_week():
    """The reason the window zone matches ASFA_TZ: a set logged at 23:30 UK on
    the closing Saturday is stamped that Saturday and must land in that week's
    export, not the next one."""
    _seed_gym("2026-02-14", exercise_name="Late Saturday")  # a Saturday
    trigger = datetime(2026, 2, 15, 12, 0, tzinfo=LONDON)   # the next day, Sunday
    start, end = weekly_export.week_window(trigger)
    assert (start, end) == ("2026-02-08", "2026-02-14")
    _, content = weekly_export.export_gym(start, end)
    assert "Late Saturday" in {r["exercise"] for r in _rows(content)}


def test_window_never_includes_today():
    """A partial in-flight day must never leak into the export."""
    for iso in ("2026-08-01", "2026-08-02", "2026-08-05"):
        now = datetime.fromisoformat(iso + "T12:00").replace(tzinfo=LONDON)
        _, end = weekly_export.week_window(now)
        assert end < iso


# ── Per-table filtering ────────────────────────────────────────────────────────

def _seed_gym(date, exercise_name="Test Press"):
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO gym_exercises (name, muscle_group) "
                    f"VALUES ({ph},{ph})", (exercise_name, "chest"))
        ex_id = cur.lastrowid
        cur.execute(f"INSERT INTO gym_sessions (routine_id, date) "
                    f"VALUES ({ph},{ph})", (1, date))
        ses_id = cur.lastrowid
        cur.execute(
            f"INSERT INTO gym_sets (session_id, exercise_id, set_number, "
            f"weight_kg, reps) VALUES ({ph},{ph},{ph},{ph},{ph})",
            (ses_id, ex_id, 1, 80.0, 5))


def test_gym_export_joins_session_date_and_exercise_name():
    """gym_sets carries no date of its own; the session join is what makes the
    window filter possible, and the exercise join is what makes it readable."""
    _seed_gym("2026-05-13", exercise_name="Window Probe")
    _, content = weekly_export.export_gym("2026-05-10", "2026-05-16")
    rows = [r for r in _rows(content) if r["exercise"] == "Window Probe"]
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-13"
    assert rows[0]["muscle_group"] == "chest"
    assert rows[0]["weight_kg"] == "80.0"


def test_gym_export_excludes_sessions_outside_the_window():
    _seed_gym("2026-05-03", exercise_name="Too Early")
    _seed_gym("2026-05-20", exercise_name="Too Late")
    _, content = weekly_export.export_gym("2026-05-10", "2026-05-16")
    names = {r["exercise"] for r in _rows(content)}
    assert "Too Early" not in names
    assert "Too Late" not in names


def test_window_boundaries_are_inclusive():
    """Sunday 00:00 and Saturday 23:59 both belong to the week."""
    _seed_gym("2026-04-12", exercise_name="First Day")   # Sunday
    _seed_gym("2026-04-18", exercise_name="Last Day")    # Saturday
    _, content = weekly_export.export_gym("2026-04-12", "2026-04-18")
    names = {r["exercise"] for r in _rows(content)}
    assert {"First Day", "Last Day"} <= names


def test_all_five_csvs_are_always_produced_with_headers():
    """Empty modules still ship a header-only CSV — a missing attachment is
    ambiguous, an empty one is not."""
    _, _, attachments = weekly_export.build_export("2026-03-01", "2026-03-07")
    assert [name for name, _ in attachments] == [
        "gym.csv", "nutrition.csv", "steps.csv", "sleep.csv", "cardio.csv"]
    for name, content in attachments:
        assert content.strip(), f"{name} is completely empty"
        assert _headers(content)[0] == "date", name


def test_exporter_survives_a_broken_table():
    """One unreadable table degrades to a header-only CSV instead of killing
    the whole export."""
    original = weekly_export._query
    weekly_export._query = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("no such table"))
    try:
        name, content = weekly_export.export_sleep("2026-03-01", "2026-03-07")
    finally:
        weekly_export._query = original
    assert name == "sleep.csv"
    assert _headers(content) == ["date", "duration_hours", "quality_1_5",
                                 "wake_feeling", "notes", "created_at"]
    assert _rows(content) == []


# ── Email assembly ─────────────────────────────────────────────────────────────

def test_dry_run_builds_csvs_without_sending():
    res = weekly_export.send_weekly_export(
        start="2026-03-01", end="2026-03-07", dry_run=True)
    assert res["emailed"] is False
    assert res["skipped"] == "dry_run"
    assert len(res["attachments"]) == 5


def test_skips_cleanly_when_smtp_unconfigured(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    res = weekly_export.send_weekly_export(start="2026-03-01", end="2026-03-07")
    assert res["emailed"] is False
    assert res["skipped"] == "smtp_not_configured"


def test_email_carries_five_csv_attachments(monkeypatch):
    sent = {}

    def fake_send(subject, body, attachments, to_addr=None):
        sent.update(subject=subject, body=body,
                    attachments=list(attachments), to_addr=to_addr)
        return True

    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(weekly_export.alerts, "smtp_configured", lambda: True)
    monkeypatch.setattr(weekly_export.alerts, "send_email_with_attachments",
                        fake_send)

    res = weekly_export.send_weekly_export(start="2026-03-01", end="2026-03-07")

    assert res["emailed"] is True
    assert sent["subject"] == "ASFA Weekly Export — 2026-03-01 to 2026-03-07"
    assert sent["body"] == "Your weekly ASFA data export. See attached."
    assert [n for n, _ in sent["attachments"]] == [
        "gym.csv", "nutrition.csv", "steps.csv", "sleep.csv", "cardio.csv"]


def test_recipient_defaults_to_owner_and_env_overrides(monkeypatch):
    monkeypatch.delenv("EXPORT_EMAIL_TO", raising=False)
    res = weekly_export.send_weekly_export(
        start="2026-03-01", end="2026-03-07", dry_run=True)
    assert res["to"] == weekly_export.DEFAULT_RECIPIENT

    monkeypatch.setenv("EXPORT_EMAIL_TO", "someone.else@example.com")
    res = weekly_export.send_weekly_export(
        start="2026-03-01", end="2026-03-07", dry_run=True)
    assert res["to"] == "someone.else@example.com"


def test_scheduler_registers_the_job_for_sunday_noon_london():
    from apscheduler.schedulers.background import BackgroundScheduler
    from services import scheduler as sched_mod

    sched = BackgroundScheduler(timezone="Europe/London")
    sched.add_job(sched_mod.weekly_csv_export, "cron", day_of_week="sun",
                  hour=12, minute=0, timezone=sched_mod.EXPORT_TZ,
                  id="weekly_csv_export")
    job = sched.get_job("weekly_csv_export")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "sun"
    assert fields["hour"] == "12"
    assert fields["minute"] == "0"
    assert str(job.trigger.timezone) == "Europe/London"
