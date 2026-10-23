"""Phase 0b — the `job_pipeline_cleanup` dry run.

`cleanup_job_pipeline` is a HARD delete with no filter on stage: every
`scout_pipeline` row past the cutoff goes, including ones the user moved to
applied / interview / offer themselves. Apprenticeship cycles run for months,
so the 14-day window the job was tuned for will silently take them.

These tests pin two things:

  1. `preview_job_pipeline_cleanup` deletes nothing, ever;
  2. what it predicts is exactly what the real job then does — a dry run that
     disagrees with the job it models is worse than none.

The stage exemption is now implemented: a row the user moved to any
`SCOUT_TRACKED_STAGES` stage is kept however old it is, and only untouched rows
still at `SCOUT_AUTO_STAGE` ('saved') are deleted. The tests at the bottom pin
that, including the `interested` tier and the boot backfill's promise never to
write back over a stage the user set.
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
    for stage in db.SCOUT_STAGES:
        _seed_pipeline(stage, days_old=40)
    _seed_application("pending", days_old=40)
    before = _pipeline_count()

    db.preview_job_pipeline_cleanup(days=14)
    db.preview_job_pipeline_cleanup(days=14)   # twice, in case one latches

    assert _pipeline_count() == before == len(db.SCOUT_STAGES)


def test_preview_reports_only_untouched_rows_as_doomed(clean_pipeline):
    _seed_pipeline("applied", days_old=40)
    _seed_pipeline("applied", days_old=40)
    _seed_pipeline("interview", days_old=40)
    _seed_pipeline("saved", days_old=40)
    _seed_pipeline("saved", days_old=40)

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["pipeline_by_stage"] == {"saved": 2}
    assert r["pipeline_total"] == 2
    assert r["pipeline_exempt_by_stage"] == {"applied": 2, "interview": 1}
    assert r["pipeline_exempt_total"] == 3


def test_preview_separates_survivors_from_doomed(clean_pipeline):
    _seed_pipeline("saved", days_old=40)       # past the cutoff, untouched
    _seed_pipeline("applied", days_old=40)     # past the cutoff, exempt
    _seed_pipeline("applied", days_old=1)      # inside the window
    _seed_pipeline("saved", days_old=1)

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["pipeline_by_stage"] == {"saved": 1}
    assert r["pipeline_exempt_by_stage"] == {"applied": 1}
    assert r["pipeline_surviving_by_stage"] == {"applied": 1, "saved": 1}


def test_stage_cannot_be_null_so_the_null_guard_is_belt_and_braces(clean_pipeline):
    """`stage` is NOT NULL DEFAULT 'saved', so the `stage IS NULL` arm of the
    cleanup's WHERE can't be reached today. It stays as defence in case a
    migration ever relaxes the column: a stage-less row carries no user intent,
    so treating it as untouched is the right default. Pinning the constraint
    here is what makes that arm provably dead rather than untested."""
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        with pytest.raises(Exception):
            cur.execute(
                f"INSERT INTO scout_pipeline "
                f"(job_title, company, stage, created_at, updated_at) "
                f"VALUES ({ph},{ph},NULL,{ph},{ph})",
                ("Legacy row", "ACME", _ago_stamp(40), _ago_stamp(40)))


def test_preview_counts_applications_by_status(clean_pipeline):
    _seed_application("pending", days_old=40)
    _seed_application("interview", days_old=40)
    _seed_application("pending", days_old=1)   # inside the window

    r = db.preview_job_pipeline_cleanup(days=14)

    assert r["applications_by_status"] == {"interview": 1, "pending": 1}
    assert r["applications_total"] == 2


def test_preview_window_is_configurable(clean_pipeline):
    _seed_pipeline("saved", days_old=20)

    assert db.preview_job_pipeline_cleanup(days=14)["pipeline_total"] == 1
    assert db.preview_job_pipeline_cleanup(days=30)["pipeline_total"] == 0


def test_preview_echoes_its_cutoff(clean_pipeline):
    r = db.preview_job_pipeline_cleanup(days=14)
    assert r["days"] == 14
    assert r["cutoff"].startswith(_ago_date(14))
    assert r["cutoff_date"] == _ago_date(14)


# ── The dry run agrees with the real job ─────────────────────────────────────

def test_preview_predicts_exactly_what_the_job_deletes(clean_pipeline):
    for stage in db.SCOUT_STAGES:
        _seed_pipeline(stage, days_old=40)
    _seed_pipeline("saved", days_old=1)        # survivor, inside the window
    _seed_application("pending", days_old=40)

    predicted = db.preview_job_pipeline_cleanup(days=14)
    actual = db.cleanup_job_pipeline(days=14)

    assert predicted["pipeline_total"] == actual["pipeline"]
    assert predicted["applications_total"] == actual["applications"]
    assert predicted["total"] == actual["total"]
    # the fresh saved row + one row per tracked stage
    assert _pipeline_count() == 1 + len(db.SCOUT_TRACKED_STAGES)


# ── The stage exemption ──────────────────────────────────────────────────────

def test_the_stage_model_is_what_we_think_it_is():
    assert db.SCOUT_STAGES == (
        "saved", "interested", "applied", "interview", "offer", "rejected")
    assert db.SCOUT_AUTO_STAGE == "saved"
    assert db.SCOUT_TRACKED_STAGES == (
        "interested", "applied", "interview", "offer", "rejected")
    assert db.SCOUT_AUTO_STAGE not in db.SCOUT_TRACKED_STAGES


@pytest.mark.parametrize("stage", ["interested", "applied", "interview",
                                   "offer", "rejected"])
def test_user_chosen_stages_survive_however_old(clean_pipeline, stage):
    """The Phase 0b fix. An apprenticeship cycle runs for months, so age alone
    must not take a row the user moved."""
    _seed_pipeline(stage, days_old=400)

    r = db.preview_job_pipeline_cleanup(days=14)
    assert r["pipeline_total"] == 0
    assert r["pipeline_exempt_by_stage"] == {stage: 1}

    db.cleanup_job_pipeline(days=14)
    assert _pipeline_count() == 1, f"{stage} row was deleted"


def test_untouched_saved_rows_are_still_deleted(clean_pipeline):
    """The clutter control the job was added for has to keep working."""
    for _ in range(3):
        _seed_pipeline("saved", days_old=40)
    _seed_pipeline("saved", days_old=1)

    assert db.cleanup_job_pipeline(days=14)["pipeline"] == 3
    assert _pipeline_count() == 1


def test_a_mixed_board_keeps_only_what_the_user_touched(clean_pipeline):
    _seed_pipeline("saved", days_old=40, title="noise 1")
    _seed_pipeline("saved", days_old=40, title="noise 2")
    _seed_pipeline("interested", days_old=40, title="mean to apply")
    _seed_pipeline("applied", days_old=40, title="applied months ago")
    _seed_pipeline("offer", days_old=40, title="offer")

    db.cleanup_job_pipeline(days=14)

    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT job_title, stage FROM scout_pipeline ORDER BY job_title").fetchall()
    assert [tuple(r) for r in rows] == [
        ("applied months ago", "applied"),
        ("mean to apply", "interested"),
        ("offer", "offer"),
    ]


def test_interested_is_accepted_by_the_stage_validator(clean_pipeline):
    """A stage the cleanup exempts but the API rejects would be unreachable."""
    assert "interested" in db.SCOUT_STAGES


# ── The boot backfill must not touch a stage the user set ────────────────────

def _seed_scout_job(title, company="ACME", url=None):
    db.add_scout_job(
        title=title, company=company, location="London", salary="",
        job_type="apprenticeship",
        url=url or f"https://example.test/{title.replace(' ', '-')}",
        description="", source="gov_uk", posted_date="",
        found_date=_ago_stamp(1))


def test_backfill_never_rewrites_a_user_set_stage(clean_pipeline):
    """The boot path runs on every deploy; it must be a no-op for tracked rows."""
    url = "https://example.test/cyber-apprentice"
    _seed_scout_job("Cyber Apprentice", url=url)
    db._backfill_scout_pipeline()

    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(f"UPDATE scout_pipeline SET stage = {ph} WHERE job_url = {ph}",
                    ("applied", url))

    for _ in range(3):                      # three more boots
        db._backfill_scout_pipeline()

    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT stage FROM scout_pipeline WHERE job_url = ?"
            if not db.USE_POSTGRES else
            "SELECT stage FROM scout_pipeline WHERE job_url = %s", (url,)).fetchall()
    assert [r[0] for r in rows] == ["applied"], \
        "backfill duplicated the row or reset the stage"


def test_backfill_does_not_duplicate_a_manually_saved_row(clean_pipeline):
    """The bug the unconditional dedup fixes.

    A row saved by hand with no url, then moved to 'applied', used to get a
    second 'saved' copy once the scraper found the same role WITH a url: the
    url check missed and the title+company check was skipped. That stranded the
    user's stage on one copy and put a fresh duplicate on the board.
    """
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_pipeline "
            f"(job_title, company, job_url, stage, created_at, updated_at) "
            f"VALUES ({ph},{ph},NULL,{ph},{ph},{ph})",
            ("Cyber Apprentice", "ACME", "applied",
             _ago_stamp(1), _ago_stamp(1)))

    _seed_scout_job("Cyber Apprentice", url="https://example.test/cyber")
    db._backfill_scout_pipeline()

    with db.get_db() as conn:
        rows = conn.cursor().execute(
            "SELECT job_title, stage FROM scout_pipeline").fetchall()
    assert [tuple(r) for r in rows] == [("Cyber Apprentice", "applied")], \
        f"expected one untouched row, got {[tuple(r) for r in rows]}"


@pytest.mark.parametrize("variant", [
    "cyber apprentice",            # case
    "  Cyber   Apprentice  ",      # whitespace
])
def test_backfill_dedup_is_normalised(clean_pipeline, variant):
    with db.get_db() as conn:
        cur = conn.cursor()
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_pipeline "
            f"(job_title, company, stage, created_at, updated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (variant, "ACME", "interested", _ago_stamp(1), _ago_stamp(1)))

    _seed_scout_job("Cyber Apprentice")
    db._backfill_scout_pipeline()

    assert _pipeline_count() == 1, "normalised dedup should have skipped the insert"


def test_backfill_still_inserts_genuinely_new_listings(clean_pipeline):
    """The dedup must not be so eager that nothing ever lands."""
    _seed_scout_job("Cyber Apprentice")
    _seed_scout_job("Cloud Apprentice")

    assert db._backfill_scout_pipeline() == 2
    assert _pipeline_count() == 2

    assert db._backfill_scout_pipeline() == 0       # idempotent
    assert _pipeline_count() == 2


def test_backfill_inserts_at_the_auto_stage(clean_pipeline):
    _seed_scout_job("Cyber Apprentice")
    db._backfill_scout_pipeline()
    with db.get_db() as conn:
        stage = conn.cursor().execute(
            "SELECT stage FROM scout_pipeline").fetchone()[0]
    assert stage == db.SCOUT_AUTO_STAGE
