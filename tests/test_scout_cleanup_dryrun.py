"""Phase 0b — the `job_pipeline_cleanup` dry run.

`cleanup_job_pipeline` is a HARD delete with no filter on stage: every
`scout_pipeline` row past the cutoff goes, including ones the user moved to
applied / interview / offer themselves. Apprenticeship cycles run for months,
so the 14-day window the job was tuned for will silently take them.

These tests pin two things:

  1. `preview_job_pipeline_cleanup` deletes nothing, ever;
  2. what it predicts is exactly what the real job then does — a dry run that
     disagrees with the job it models is worse than none.

They deliberately do NOT assert the current destructive behaviour is correct.
The exemption fix is proposed and awaiting a decision; when it lands, the
"predicts what the job does" tests should still pass unchanged, and the
`user-chosen stages are taken` test below is the one that must then flip.
"""
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db      # noqa: E402

STAMP = "%Y-%m-%d %H:%M:%S"


def _ago_stamp(days):
    from datetime import datetime
    return (datetime.now() - timedelta(days=days)).strftime(STAMP)


def _ago_date(days):
    from datetime import datetime
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _seed_pipeline(stage, days_old, title=None, company="ACME"):
    stamp = _ago_stamp(days_old)
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_pipeline "
            f"(job_title, company, stage, created_at, updated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (title or f"{stage} role {days_old}d", company, stage, stamp, stamp))


def _seed_application(status, days_old):
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_applications "
            f"(company, role, applied_date, status) VALUES ({ph},{ph},{ph},{ph})",
            ("ACME", "Cyber Apprentice", _ago_date(days_old), status))


def _pipeline_count():
    with db.get_db() as conn:
        return conn.cursor().execute("SELECT COUNT(*) FROM scout_pipeline").fetchone()[0]


@pytest.fixture
def clean_pipeline():
    """Empty both tables so counts in a test are only that test's rows."""
    db._ensure_scout_tables()
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM scout_pipeline")
        cur.execute("DELETE FROM scout_applications")
    yield


# ── The dry run is read-only ─────────────────────────────────────────────────

def test_preview_deletes_nothing(clean_pipeline):
    for stage in ("saved", "applied", "interview", "offer", "rejected"):
        _seed_pipeline(stage, days_old=40)
    _seed_application("pending", days_old=40)
    before = _pipeline_count()

    db.preview_job_pipeline_cleanup(days=14)
    db.preview_job_pipeline_cleanup(days=14)   # twice, in case one latches

    assert _pipeline_count() == before == 5


def test_preview_reports_counts_by_stage(clean_pipeline):
    _seed_pipeline("applied", days_old=40)
    _seed_pipeline("applied", days_old=40)
    _seed_pipeline("interview", days_old=40)
    _seed_pipeline("saved", days_old=40)

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["pipeline_by_stage"] == {"applied": 2, "interview": 1, "saved": 1}
    assert r["pipeline_total"] == 4


def test_preview_separates_survivors_from_doomed(clean_pipeline):
    _seed_pipeline("applied", days_old=40)     # past the cutoff
    _seed_pipeline("applied", days_old=1)      # inside the window
    _seed_pipeline("saved", days_old=1)

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["pipeline_by_stage"] == {"applied": 1}
    assert r["pipeline_surviving_by_stage"] == {"applied": 1, "saved": 1}


def test_preview_counts_applications_by_status(clean_pipeline):
    _seed_application("pending", days_old=40)
    _seed_application("interview", days_old=40)
    _seed_application("pending", days_old=1)   # inside the window

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["applications_by_status"] == {"interview": 1, "pending": 1}
    assert r["applications_total"] == 2


def test_preview_window_is_configurable(clean_pipeline):
    _seed_pipeline("applied", days_old=20)

    assert db.preview_job_pipeline_cleanup(days=14)["pipeline_total"] == 1
    assert db.preview_job_pipeline_cleanup(days=30)["pipeline_total"] == 0


def test_preview_echoes_its_cutoff(clean_pipeline):
    r = db.preview_job_pipeline_cleanup(days=14)
    assert r["days"] == 14
    assert r["cutoff"].startswith(_ago_date(14))
    assert r["cutoff_date"] == _ago_date(14)


# ── The dry run agrees with the real job ─────────────────────────────────────

def test_preview_predicts_exactly_what_the_job_deletes(clean_pipeline):
    for stage in ("saved", "applied", "interview", "offer", "rejected"):
        _seed_pipeline(stage, days_old=40)
    _seed_pipeline("saved", days_old=1)        # survivor
    _seed_application("pending", days_old=40)

    predicted = db.preview_job_pipeline_cleanup(days=14)
    actual = db.cleanup_job_pipeline(days=14)

    assert predicted["pipeline_total"] == actual["pipeline"]
    assert predicted["applications_total"] == actual["applications"]
    assert predicted["total"] == actual["total"]
    assert _pipeline_count() == 1              # only the fresh row left


# ── The conflict this phase exists to surface ────────────────────────────────

def test_user_chosen_stages_are_currently_taken(clean_pipeline):
    """CURRENT behaviour, recorded so the fix has something to flip.

    A listing moved to applied / interview / offer is deleted purely for being
    older than the window. When the exemption lands, these stages must survive
    and this test should be inverted.
    """
    for stage in ("applied", "interview", "offer"):
        _seed_pipeline(stage, days_old=40)
    _seed_pipeline("saved", days_old=40)

    r = db.preview_job_pipeline_cleanup(days=14)
    user_chosen = {s: n for s, n in r["pipeline_by_stage"].items()
                   if s in ("applied", "interview", "offer", "rejected")}

    assert user_chosen == {"applied": 1, "interview": 1, "offer": 1}, \
        "the job takes user-chosen rows; this is the Phase 0b conflict"

    db.cleanup_job_pipeline(days=14)
    assert _pipeline_count() == 0, "including the ones the user chose"


def test_saved_is_the_automatic_default_not_a_user_choice():
    """Why `saved` is not in the exemption list in the proposed fix.

    `_backfill_scout_pipeline()` runs on every boot and inserts every scraped
    scout_jobs row at stage='saved', so `saved` cannot be read as "the user
    saved this".
    """
    import inspect
    src = inspect.getsource(db._backfill_scout_pipeline)
    assert '"saved"' in src or "'saved'" in src
    assert db.SCOUT_STAGES[0] == "saved"
