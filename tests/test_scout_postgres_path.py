"""The Scout helpers against psycopg2's calling convention.

WHY THIS FILE EXISTS. Every test in this suite runs on SQLite, and
`sqlite3.Cursor.execute()` RETURNS THE CURSOR, so

    rows = conn.cursor().execute(sql).fetchall()

works there and reads fine. psycopg2's `execute()` returns **None**, so the
same line is `None.fetchall()` in production — an AttributeError the whole
SQLite suite is structurally incapable of noticing. `count_scout_programmes()`
shipped with exactly that and failed on the first real Postgres call.

So this file drives the helpers through a cursor that behaves like psycopg2
and is backed by a real SQLite database, which gives real rows AND the
production calling convention:

  * `execute()` returns None             -> catches chained .execute().fetch*()
  * rows come back as plain dicts        -> catches row[0] positional indexing
  * `lastrowid` is always None           -> catches id-after-insert
  * a literal `?` placeholder raises     -> catches hardcoded SQLite params

It is not a substitute for running against Postgres, and does not claim to be:
it cannot catch a type error in SQL (boolean vs integer) or a dialect-only
function. It catches the driver-shaped mistakes, which is the class that bit.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


class FakePsycopgCursor:
    """sqlite3 underneath, psycopg2's contract on the surface."""

    def __init__(self, raw):
        self._raw = raw
        self.lastrowid = None          # psycopg2: always None after an INSERT

    def execute(self, sql, params=None):
        if params is not None and "?" in sql:
            # psycopg2 does not understand qmark parameters.
            raise RuntimeError(
                "a literal '?' placeholder reached the Postgres path — "
                "use the USE_POSTGRES-aware placeholder")
        self._raw.execute(sql.replace("%s", "?"), params or ())
        return None                    # THE WHOLE POINT

    def _row(self, r):
        return dict(r) if r is not None else None

    def fetchone(self):
        return self._row(self._raw.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._raw.fetchall()]

    def fetchmany(self, size=None):
        return [self._row(r) for r in self._raw.fetchmany(size)]

    def __getattr__(self, name):
        return getattr(self._raw, name)


class FakePsycopgConnection:
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def cursor(self):
        return FakePsycopgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


@pytest.fixture
def pg_like(tmp_path, monkeypatch):
    """Run the helpers as if psycopg2 were underneath."""
    path = str(tmp_path / "pg_like.db")

    # Build the schema through the normal SQLite path first.
    monkeypatch.setattr(db, "SQLITE_PATH", path)
    monkeypatch.setattr(db, "USE_POSTGRES", False)
    # Both guards are module-level "already done" flags; the schema has to be
    # built again in THIS file, not reused from whichever database the suite
    # touched first.
    monkeypatch.setattr(db, "_SCOUT_READY", False)
    monkeypatch.setattr(db, "_APPRENTICESHIPS_READY", False)
    db.init_db()                  # kv_store and friends, not just scout_*
    db._ensure_scout_tables()
    db.init_apprenticeships()
    monkeypatch.setattr(db, "_SCOUT_READY", True)
    monkeypatch.setattr(db, "_APPRENTICESHIPS_READY", True)

    import contextlib

    @contextlib.contextmanager
    def _fake_get_db():
        conn = FakePsycopgConnection(path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(db, "USE_POSTGRES", True)
    monkeypatch.setattr(db, "get_db", _fake_get_db)
    yield path


# Every new read/count helper, with arguments that exercise its SQL.
READ_HELPERS = [
    ("get_apprenticeship_rows", lambda: db.get_apprenticeship_rows()),
    ("get_apprenticeship_rows(open)",
     lambda: db.get_apprenticeship_rows(include_closed=False)),
    ("get_all_scout_job_details", lambda: db.get_all_scout_job_details()),
    ("find_scout_pipeline_row",
     lambda: db.find_scout_pipeline_row("https://x/1", "T", "C")),
    ("get_scout_pipeline", lambda: db.get_scout_pipeline()),
    ("get_scout_job_detail", lambda: db.get_scout_job_detail("VAC1")),
    ("scout_details_needing_fetch",
     lambda: db.scout_details_needing_fetch(limit=5)),
    ("scout_detail_candidate", lambda: db.scout_detail_candidate("VAC1")),
    ("count_scout_job_details", lambda: db.count_scout_job_details()),
    ("get_scout_programmes", lambda: db.get_scout_programmes()),
    ("get_scout_programme_companies",
     lambda: db.get_scout_programme_companies()),
    ("count_scout_programmes", lambda: db.count_scout_programmes()),
    ("get_scout_exclusions", lambda: db.get_scout_exclusions(limit=10)),
    ("count_scout_exclusions", lambda: db.count_scout_exclusions()),
    ("list_saved_searches", lambda: db.list_saved_searches()),
    ("list_scout_filter_overrides", lambda: db.list_scout_filter_overrides()),
    ("scout_filter_overrides_stamp",
     lambda: db.scout_filter_overrides_stamp()),
    ("get_watched_employer_names", lambda: db.get_watched_employer_names()),
    ("scout_last_visited", lambda: db.scout_last_visited()),
    ("preview_job_pipeline_cleanup",
     lambda: db.preview_job_pipeline_cleanup()),
]


@pytest.mark.parametrize("name,call",
                         READ_HELPERS, ids=[n for n, _ in READ_HELPERS])
def test_every_read_helper_works_on_the_postgres_path(pg_like, name, call):
    """On an EMPTY database, so this is purely about the driver contract."""
    call()


@pytest.mark.parametrize("name,call",
                         READ_HELPERS, ids=[n for n, _ in READ_HELPERS])
def test_every_read_helper_works_with_rows_present(pg_like, name, call):
    _seed(pg_like)
    call()


def _seed(path):
    """Write fixtures through plain SQLite, then read them back as psycopg2."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO scout_jobs (title, company, location, url, source, "
        "found_date, listing_type, external_ref, employer_name_raw, level, "
        "training_course, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("Cyber Apprentice", "Northwind", "Leeds",
         "https://www.findapprenticeship.service.gov.uk/apprenticeship/VAC1",
         "gov_uk", "2026-10-01", "apprenticeship", "VAC1", "Northwind", 6,
         "Cyber security technical professional (level 6)", "open"))
    cur.execute(
        "INSERT INTO scout_pipeline (job_title, company, job_url, stage, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("Cyber Apprentice", "Northwind",
         "https://www.findapprenticeship.service.gov.uk/apprenticeship/VAC1",
         "applied", "2026-10-01 09:00:00", "2026-10-01 09:00:00"))
    cur.execute(
        "INSERT INTO scout_job_details (external_ref, wage, fetched_at, "
        "fetch_status) VALUES (?,?,?,?)",
        ("VAC1", "GBP 23,400 a year", "2026-10-01 09:00:00", "ok"))
    cur.execute(
        "INSERT INTO scout_programmes (company, programme, company_norm, "
        "programme_norm, stage, edited_in_scout) VALUES (?,?,?,?,?,?)",
        ("Northwind", "Cyber Scheme", "northwind", "cyber scheme",
         "applied", 1))
    cur.execute(
        "INSERT INTO scout_exclusions (external_ref, title, employer, reason, "
        "detail, listing_type) VALUES (?,?,?,?,?,?)",
        ("VAC9", "Security Guard", "Bastion", "title_excluded",
         "security guard", "apprenticeship"))
    cur.execute(
        "INSERT INTO scout_saved_searches (name, filters_json, channels, "
        "enabled, created_at) VALUES (?,?,?,?,?)",
        ("L6 cyber", '{"level": "6"}', "bell,telegram", 1,
         "2026-10-01 09:00:00"))
    cur.execute(
        "INSERT INTO scout_filter_overrides (list_key, value, op, created_at) "
        "VALUES (?,?,?,?)",
        ("digital_standards", "widget wrangler", "add", "2026-10-01T09:00:00"))
    conn.commit()
    conn.close()


# ── The specific failure that was reported ─────────────────────────────────

def test_count_scout_programmes_returns_real_counts_on_the_postgres_path(pg_like):
    """The exact call that raised AttributeError in production."""
    _seed(pg_like)
    counts = db.count_scout_programmes()
    assert counts["total"] == 1
    assert counts["by_stage"]["applied"] == 1
    assert counts["edited_in_scout"] == 1


def test_count_scout_job_details_returns_real_counts(pg_like):
    """The other chained pair the report named."""
    _seed(pg_like)
    counts = db.count_scout_job_details()
    assert counts["total"] == 1
    assert counts["by_status"]["ok"] == 1


def test_the_fake_cursor_really_does_break_a_chained_call(pg_like):
    """If execute() silently returned the cursor, every test above would pass
    against the broken code and this file would be worthless."""
    with db.get_db() as conn:
        cur = conn.cursor()
        assert cur.execute("SELECT 1 AS n") is None
        with pytest.raises(AttributeError):
            cur.execute("SELECT 1 AS n").fetchall()


def test_the_fake_cursor_rejects_a_sqlite_placeholder(pg_like):
    with db.get_db() as conn:
        cur = conn.cursor()
        with pytest.raises(RuntimeError, match="literal '\\?'"):
            cur.execute("SELECT * FROM scout_jobs WHERE id = ?", (1,))


def test_rows_are_plain_dicts_not_tuples(pg_like):
    """RealDictCursor yields mappings, so row[0] would raise KeyError."""
    _seed(pg_like)
    row = db.get_apprenticeship_rows()[0]
    assert isinstance(row, dict)
    with pytest.raises(KeyError):
        row[0]


# ── Writes that need an id back ────────────────────────────────────────────

def test_add_saved_search_returns_an_id_without_lastrowid(pg_like):
    """psycopg2's lastrowid is always None, so the id has to come from
    RETURNING. Without it the route answers "could not create"."""
    new_id = db.add_saved_search("Near nothing", {"level": "6"}, ["bell"])
    assert new_id, "no id came back on the Postgres path"
    assert any(s["id"] == new_id for s in db.list_saved_searches())


def test_the_other_scout_writes_work_on_the_postgres_path(pg_like):
    _seed(pg_like)
    assert db.add_scout_filter_override("digital_standards", "x", "add")
    assert db.upsert_scout_programme("Castellan", "Scheme", {"level": "6"})
    assert db.save_scout_job_detail("VAC1", {"wage": "GBP 1"}, status="ok")
    # Returns the PREVIOUS visit stamp, so "" the first time is correct. What
    # matters here is that the write went through: the second call sees it.
    db.touch_scout_last_visited()
    assert db.scout_last_visited()
    assert db.upsert_scout_pipeline_for_listing(
        "Cyber Apprentice", "Northwind",
        job_url="https://www.findapprenticeship.service.gov.uk"
                "/apprenticeship/VAC1", stage="offer")
    assert db.log_scout_exclusions([{
        "external_ref": "VAC8", "title": "T", "employer": "E",
        "reason": "title_excluded", "detail": "d",
        "listing_type": "apprenticeship"}])
