"""habits.date uniqueness tests — one row per day, upserts accumulate.

Self-contained, no pytest dependency: run directly with

    python tests/test_habits_unique.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db, and
passes explicit dates so results don't depend on the system clock.
"""
import os
import sqlite3
import sys
import tempfile

# Point the DB layer at a throwaway file BEFORE importing database.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_habits_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

db.init_db()

DATE = "2026-07-20"


def _rows(date):
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, water_ml, sleep_hours FROM habits WHERE date = ? ORDER BY id",
                    (date,))
        return [dict(r) for r in cur.fetchall()]


def test_1_unique_constraint_exists():
    with db.get_db() as conn:
        cur = conn.cursor()
        assert db._habits_date_is_unique(cur), "habits.date has no UNIQUE constraint"
    print("  1. habits.date carries a UNIQUE constraint  OK")


def test_2_two_inserts_same_date_one_row():
    db.log_water(DATE, 500)
    db.log_water(DATE, 250)
    rows = _rows(DATE)
    assert len(rows) == 1, f"expected 1 row for {DATE}, got {len(rows)}: {rows}"
    print(f"  2. log_water x2 on {DATE} -> 1 row  [actual {len(rows)}]  OK")


def test_3_second_call_updates_not_ignored():
    # The second call must have UPDATED the first row's total, not been dropped.
    rows = _rows(DATE)
    assert rows[0]["water_ml"] == 750, f"expected 750ml, got {rows[0]['water_ml']}"
    assert db.get_water_logged(DATE) == 750, db.get_water_logged(DATE)
    print(f"  3. 500 + 250 accumulated on the same row -> 750ml  [actual {rows[0]['water_ml']}]  OK")


def test_4_raw_duplicate_insert_rejected():
    with db.get_db() as conn:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO habits (date, water_ml, sleep_hours) VALUES (?, 0, 0)",
                        (DATE,))
        except sqlite3.IntegrityError:
            print("  4. raw duplicate INSERT for the same date -> IntegrityError  OK")
            return
    raise AssertionError("duplicate INSERT was accepted; UNIQUE(date) not enforced")


def test_5_separate_dates_still_separate():
    db.log_water("2026-07-21", 400)
    assert len(_rows(DATE)) == 1 and len(_rows("2026-07-21")) == 1
    assert db.get_water_logged("2026-07-21") == 400, db.get_water_logged("2026-07-21")
    print("  5. a different date gets its own row (400ml)  OK")


def test_6_log_sleep_shares_the_day_row():
    db.log_sleep(DATE, 7.5)
    rows = _rows(DATE)
    assert len(rows) == 1, f"log_sleep created a second row: {rows}"
    assert rows[0]["sleep_hours"] == 7.5 and rows[0]["water_ml"] == 750, rows[0]
    print("  6. log_sleep updates the same day row, water total intact  OK")


def test_7_dedupe_migration_collapses_legacy_dupes():
    """Simulate a pre-constraint DB: several rows for one date where the oldest
    holds the true running total, then re-run the migration."""
    legacy = "2026-06-16"
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DROP INDEX IF EXISTS idx_habits_date")
        # Rebuild without UNIQUE so legacy rows can be inserted.
        cur.execute("ALTER TABLE habits RENAME TO habits_old")
        cur.execute("""CREATE TABLE habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            water_ml INTEGER DEFAULT 0,
            sleep_hours REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')))""")
        cur.execute("INSERT INTO habits (id, date, water_ml, sleep_hours, created_at) "
                    "SELECT id, date, water_ml, sleep_hours, created_at FROM habits_old")
        cur.execute("DROP TABLE habits_old")
        # Oldest row carries the full total, later rows partial sums.
        for ml in (3000, 2500, 1000, 500):
            cur.execute("INSERT INTO habits (date, water_ml, sleep_hours) VALUES (?, ?, 0)",
                        (legacy, ml))
        assert not db._habits_date_is_unique(cur), "test setup failed to drop UNIQUE"
        db._dedupe_habits(cur)
        assert db._habits_date_is_unique(cur), "migration did not add UNIQUE(date)"

    rows = _rows(legacy)
    assert len(rows) == 1, f"expected dupes collapsed to 1 row, got {rows}"
    assert rows[0]["water_ml"] == 3000, f"true total lost: {rows[0]['water_ml']} != 3000"
    # Untouched days survive the migration.
    assert db.get_water_logged(DATE) == 750, db.get_water_logged(DATE)
    print(f"  7. 4 legacy rows for {legacy} -> 1 row keeping the 3000ml total  OK")


def test_8_dedupe_is_idempotent():
    with db.get_db() as conn:
        cur = conn.cursor()
        db._dedupe_habits(cur)          # second run must be a clean no-op
    assert len(_rows("2026-06-16")) == 1 and len(_rows(DATE)) == 1
    assert db.get_water_logged(DATE) == 750
    print("  8. re-running the migration is a no-op  OK")


def main():
    tests = [test_1_unique_constraint_exists, test_2_two_inserts_same_date_one_row,
             test_3_second_call_updates_not_ignored, test_4_raw_duplicate_insert_rejected,
             test_5_separate_dates_still_separate, test_6_log_sleep_shares_the_day_row,
             test_7_dedupe_migration_collapses_legacy_dupes, test_8_dedupe_is_idempotent]
    print("habits.date uniqueness tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
