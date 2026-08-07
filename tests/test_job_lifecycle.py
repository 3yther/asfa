"""Scout job lifecycle — auto-archive after 14 days, history, bulk delete.

The three things that would hurt if they broke, in order:

  1. the nightly archive must be idempotent and must never touch a fresh row —
     it runs unattended every night and a wrong cutoff silently empties the
     active board;
  2. active and history must be genuinely disjoint views of the same table;
  3. the bulk delete is a HARD delete, so it must refuse anything that isn't a
     list of integers naming already-archived rows.

Everything runs against the module's own SQLite file (see conftest.py). No
network, no scheduler thread.
"""
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402
import database as db      # noqa: E402
from services import scheduler  # noqa: E402

STAMP = "%Y-%m-%d %H:%M:%S"


def _ago(days=0, minutes=0):
    """A found_date `days`/`minutes` in the past, in the writers' own format."""
    return (db.now_local() - timedelta(days=days, minutes=minutes)).strftime(STAMP)


def _seed(title, found_date, company="TK Maxx", location="Erith", url=None):
    """Insert one scout job. `url` is the dedup key, so it defaults to unique."""
    db.add_scout_job(
        title=title, company=company, location=location, salary="",
        job_type="part time", url=url or f"https://example.com/{title}",
        description="", source="reed", posted_date="Today",
        found_date=found_date)
    rows = db.get_scout_jobs()
    return next(r for r in rows if r["title"] == title)


@pytest.fixture(autouse=True)
def clean_jobs():
    """Each test starts from an empty scout_jobs — the module shares one file."""
    db._ensure_scout_tables()
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM scout_jobs")
    yield


@pytest.fixture
def client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


CSRF = {"X-CSRF-Token": "tok"}


# ── Schema ───────────────────────────────────────────────────────────────────

def test_archived_at_column_exists_and_defaults_to_null():
    """The migration is additive: a newly scraped job is active, not archived."""
    job = _seed("Sales Assistant", _ago(days=1))
    assert "archived_at" in job, "archived_at column missing from scout_jobs"
    assert job["archived_at"] is None, "new jobs must start active"


# ── Auto-archive ─────────────────────────────────────────────────────────────

def test_archives_only_jobs_older_than_the_window():
    old = _seed("Old Job", _ago(days=20))
    fresh = _seed("Fresh Job", _ago(days=3))

    assert db.archive_stale_scout_jobs() == 1

    by_id = {r["id"]: r for r in db.get_scout_jobs()}
    assert by_id[old["id"]]["archived_at"] is not None
    assert by_id[fresh["id"]]["archived_at"] is None


def test_window_boundary_is_fourteen_days():
    """13d23h stays active, 14d1min goes. The window is exactly 14 days."""
    just_inside = _seed("Just Inside", _ago(days=13, minutes=60))
    just_outside = _seed("Just Outside", _ago(days=14, minutes=1))

    assert db.archive_stale_scout_jobs() == 1

    by_id = {r["id"]: r for r in db.get_scout_jobs()}
    assert by_id[just_inside["id"]]["archived_at"] is None
    assert by_id[just_outside["id"]]["archived_at"] is not None


def test_archive_is_idempotent():
    """Second run archives nothing and leaves the first stamp untouched — the
    job re-fires after any redeploy, so this is the property that matters."""
    _seed("Old Job", _ago(days=20))

    assert db.archive_stale_scout_jobs() == 1
    first_stamp = db.get_scout_jobs(archived=True)[0]["archived_at"]

    assert db.archive_stale_scout_jobs() == 0
    assert db.get_scout_jobs(archived=True)[0]["archived_at"] == first_stamp


def test_jobs_without_a_found_date_are_never_archived():
    """No creation stamp means no age — leave it active rather than guess."""
    _seed("No Date", None)
    _seed("Empty Date", "")

    assert db.archive_stale_scout_jobs() == 0
    assert len(db.get_scout_jobs(archived=False)) == 2


def test_archive_window_and_clock_are_injectable():
    """`days`/`now` exist so the cutoff can be reasoned about (and tested)
    without waiting two weeks."""
    _seed("Five Days Old", _ago(days=5))

    assert db.archive_stale_scout_jobs(days=30) == 0
    assert db.archive_stale_scout_jobs(days=3) == 1


def test_archive_stamps_local_time():
    _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()
    stamp = db.get_scout_jobs(archived=True)[0]["archived_at"]
    assert str(stamp).startswith(db.now_local().strftime("%Y-%m-%d"))


# ── Scheduler task ───────────────────────────────────────────────────────────

def test_scheduler_task_archives_stale_jobs():
    _seed("Old Job", _ago(days=20))
    _seed("Fresh Job", _ago(days=1))

    scheduler.archive_stale_jobs()

    assert len(db.get_scout_jobs(archived=True)) == 1
    assert len(db.get_scout_jobs(archived=False)) == 1


def test_scheduler_task_swallows_db_failures(monkeypatch):
    """A scheduled job that raises kills nothing else here, but it does burn the
    agent's error budget — the task logs and moves on instead."""
    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "archive_stale_scout_jobs", boom)
    scheduler.archive_stale_jobs()   # must not raise


def test_archive_job_is_registered_at_2am_london(monkeypatch):
    """Registration is the half that can't be unit-tested by calling the
    function: a task nobody scheduled never runs."""
    calls = []

    class FakeScheduler:
        def __init__(self, *a, **kw):
            pass

        def add_job(self, fn, *a, **kw):
            calls.append((fn, kw))

        def start(self):
            pass

        def get_jobs(self):
            return calls

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", None)   # don't leak the fake

    job = next((kw for fn, kw in calls if fn is scheduler.archive_stale_jobs), None)
    assert job is not None, "archive_stale_jobs was never scheduled"
    assert (job["hour"], job["minute"]) == (2, 0)
    assert job["timezone"] == "Europe/London"


# ── Active vs history separation ─────────────────────────────────────────────

def test_active_and_history_are_disjoint_views():
    _seed("Old Job", _ago(days=20))
    _seed("Fresh Job", _ago(days=1))
    db.archive_stale_scout_jobs()

    active = db.get_scout_jobs(archived=False)
    history = db.get_scout_jobs(archived=True)
    everything = db.get_scout_jobs()

    assert [r["title"] for r in active] == ["Fresh Job"]
    assert [r["title"] for r in history] == ["Old Job"]
    assert len(everything) == len(active) + len(history)


def test_endpoints_split_active_and_history(client):
    _seed("Old Job", _ago(days=20))
    _seed("Fresh Job", _ago(days=1))
    db.archive_stale_scout_jobs()

    active = client.get("/api/scout/jobs/active").get_json()
    history = client.get("/api/scout/jobs/history").get_json()

    assert [r["title"] for r in active] == ["Fresh Job"]
    assert [r["title"] for r in history] == ["Old Job"]
    assert all(r["archived_at"] is None for r in active)
    assert all(r["archived_at"] for r in history)


def test_jobs_endpoint_still_returns_everything(client):
    """/api/scout/jobs is the pre-existing contract — it stays unfiltered."""
    _seed("Old Job", _ago(days=20))
    _seed("Fresh Job", _ago(days=1))
    db.archive_stale_scout_jobs()

    assert len(client.get("/api/scout/jobs").get_json()) == 2


def test_created_at_is_exposed_for_the_countdown(client):
    """days_remaining is computed client-side off created_at, so the field has
    to be there and has to be the found_date."""
    job = _seed("Fresh Job", _ago(days=2))
    row = client.get("/api/scout/jobs/active").get_json()[0]
    assert row["created_at"] == job["found_date"]


def test_active_view_keeps_the_existing_filters(client):
    _seed("Erith Job", _ago(days=1), location="Erith")
    _seed("Dartford Job", _ago(days=1), location="Dartford")

    rows = client.get("/api/scout/jobs/active?location=erith").get_json()
    assert [r["title"] for r in rows] == ["Erith Job"]

    rows = client.get("/api/scout/jobs/active?source=apprenticeship").get_json()
    assert rows == []


def test_history_is_newest_archived_first(client):
    old = _seed("Older", _ago(days=40))
    newer = _seed("Newer", _ago(days=20))
    # Two separate runs → two distinct archived_at stamps.
    db.archive_stale_scout_jobs(days=30)
    db.archive_stale_scout_jobs(now=db.now_local() + timedelta(seconds=61))

    rows = client.get("/api/scout/jobs/history").get_json()
    assert [r["id"] for r in rows] == [newer["id"], old["id"]]


# ── Bulk delete ──────────────────────────────────────────────────────────────

def test_bulk_delete_removes_archived_jobs(client):
    a = _seed("Old A", _ago(days=20))
    b = _seed("Old B", _ago(days=21))
    db.archive_stale_scout_jobs()

    r = client.delete("/api/scout/jobs/bulk",
                      json={"job_ids": [a["id"], b["id"]]}, headers=CSRF)

    assert r.status_code == 200
    assert r.get_json()["deleted"] == 2
    assert db.get_scout_jobs() == []


def test_bulk_delete_leaves_active_jobs_alone(client):
    active = _seed("Fresh Job", _ago(days=1))
    archived = _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()

    r = client.delete("/api/scout/jobs/bulk",
                      json={"job_ids": [active["id"], archived["id"]]}, headers=CSRF)

    assert r.get_json() == {"ok": True, "deleted": 1, "requested": 2}
    assert [j["title"] for j in db.get_scout_jobs()] == ["Fresh Job"]


def test_bulk_delete_is_a_hard_delete(client):
    job = _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()
    client.delete("/api/scout/jobs/bulk", json={"job_ids": [job["id"]]}, headers=CSRF)

    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM scout_jobs")
        assert dict(cur.fetchone())["n"] == 0


def test_bulk_delete_ignores_unknown_ids(client):
    r = client.delete("/api/scout/jobs/bulk", json={"job_ids": [98765]}, headers=CSRF)
    assert r.status_code == 200 and r.get_json()["deleted"] == 0


def test_bulk_delete_accepts_an_empty_selection(client):
    r = client.delete("/api/scout/jobs/bulk", json={"job_ids": []}, headers=CSRF)
    assert r.status_code == 200 and r.get_json()["deleted"] == 0


@pytest.mark.parametrize("payload", [
    {},                                  # no job_ids at all
    {"job_ids": "1,2,3"},                # a string, not a list
    {"job_ids": {"id": 1}},              # an object
    {"job_ids": [1, "two"]},             # a non-numeric entry
    {"job_ids": [1, None]},              # a null entry
    {"job_ids": [1, [2]]},               # a nested list
])
def test_bulk_delete_rejects_malformed_payloads(client, payload):
    """It is a hard delete — anything ambiguous is a 400, never a partial run."""
    job = _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()

    r = client.delete("/api/scout/jobs/bulk", json=payload, headers=CSRF)

    assert r.status_code == 400
    assert db.get_scout_jobs()[0]["id"] == job["id"], "nothing may be deleted"


def test_bulk_delete_caps_the_batch_size(client):
    over = list(range(db.MAX_BULK_DELETE + 1))
    r = client.delete("/api/scout/jobs/bulk", json={"job_ids": over}, headers=CSRF)
    assert r.status_code == 400


def test_bulk_delete_accepts_numeric_strings(client):
    """The frontend sends numbers, but a hand-rolled call sending "12" is
    unambiguous — coerce rather than 400."""
    job = _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()

    r = client.delete("/api/scout/jobs/bulk",
                      json={"job_ids": [str(job["id"])]}, headers=CSRF)
    assert r.get_json()["deleted"] == 1


def test_bulk_delete_requires_csrf(client):
    job = _seed("Old Job", _ago(days=20))
    db.archive_stale_scout_jobs()

    r = client.delete("/api/scout/jobs/bulk", json={"job_ids": [job["id"]]})

    assert r.status_code == 403
    assert len(db.get_scout_jobs()) == 1


def test_lifecycle_endpoints_are_behind_the_auth_gate():
    anon = app_module.app.test_client()
    for path in ("/api/scout/jobs/active", "/api/scout/jobs/history"):
        assert anon.get(path).status_code in (302, 401), path
    assert anon.delete("/api/scout/jobs/bulk",
                       json={"job_ids": [1]}).status_code in (302, 401, 403)


def test_db_helper_can_delete_active_rows_when_asked():
    """The guard rail is a default, not a wall — the helper still supports an
    explicit unrestricted delete for future callers."""
    job = _seed("Fresh Job", _ago(days=1))
    assert db.delete_scout_jobs([job["id"]]) == 0
    assert db.delete_scout_jobs([job["id"]], archived_only=False) == 1


# ── UI wiring (static guard for the checkbox/modal logic) ────────────────────

def test_history_ui_controls_are_wired_into_the_template():
    """The selection logic itself is exercised in the browser; this pins the
    element ids and endpoints that logic hangs off, so a rename can't silently
    detach the checkboxes from the delete call."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "scout.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()

    for needle in ('id="hist-toggle"', 'id="hist-panel"', 'id="hist-select-all"',
                   'id="hist-delete"', 'id="del-modal"', 'id="del-confirm"',
                   'id="del-cancel"', "/api/scout/jobs/active",
                   "/api/scout/jobs/history", "/api/scout/jobs/bulk",
                   "This cannot be undone.", "daysRemaining"):
        assert needle in html, f"missing from scout.html: {needle}"

    assert '<div class="panel" id="hist-panel" hidden>' in html, \
        "history must start hidden"
