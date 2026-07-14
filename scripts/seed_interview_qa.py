#!/usr/bin/env python3
"""Seed the /interview prep bank with Amir's pre-written Q&A.

Idempotent: safe to run repeatedly. It never duplicates a session or a Q&A, and
it only ever inserts — it never edits or deletes anything the live AI interview
recorder wrote.

How it maps onto the real schema (see database.py):
  interview_sessions(id, role, mode, created_at, ended_at)
  interview_qa(id, session_id, question, answer, rating, ts, created_at)

The table has no category/difficulty columns, so:
  - each seed *category* becomes one interview_sessions row
      role = "Prep Bank: <Category>", mode = "seed"
  - each Q&A is an interview_qa row under that session
  - category/difficulty are preserved via two nullable TEXT columns added
    idempotently with the same ALTER-TABLE-ADD-COLUMN pattern used elsewhere in
    database.py. They stay NULL for real interview sessions, so the recorder is
    unaffected.

Usage:
    python scripts/seed_interview_qa.py                 # seed from default JSON
    python scripts/seed_interview_qa.py path/to.json    # custom source
    python scripts/seed_interview_qa.py --dry-run       # show plan, write nothing
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

SEED_MODE = "seed"  # marks sessions this script owns; keeps them idempotent


def _ph():
    return "%s" if db.USE_POSTGRES else "?"


def _ensure_columns(cur):
    """Add nullable category/difficulty to interview_qa if missing (no-op otherwise)."""
    db._add_column(cur, "interview_qa", "category", "TEXT")
    db._add_column(cur, "interview_qa", "difficulty", "TEXT")


def _find_or_create_session(cur, role):
    """Return the id of the seed session for `role`, creating it if absent."""
    ph = _ph()
    cur.execute(
        f"SELECT id FROM interview_sessions WHERE role={ph} AND mode={ph} "
        f"ORDER BY id LIMIT 1",
        (role, SEED_MODE),
    )
    row = cur.fetchone()
    if row:
        return (row["id"] if not isinstance(row, tuple) else row[0]), False
    created = datetime.now().isoformat()
    sql = (f"INSERT INTO interview_sessions (role, mode, created_at) "
           f"VALUES ({ph},{ph},{ph})")
    if db.USE_POSTGRES:
        cur.execute(sql + " RETURNING id", (role, SEED_MODE, created))
        return cur.fetchone()["id"], True
    cur.execute(sql, (role, SEED_MODE, created))
    return cur.lastrowid, True


def _qa_exists(cur, session_id, question):
    ph = _ph()
    cur.execute(
        f"SELECT 1 FROM interview_qa WHERE session_id={ph} AND question={ph} LIMIT 1",
        (session_id, question),
    )
    return cur.fetchone() is not None


def _role_for(category):
    return f"Prep Bank: {category.capitalize()}"


def seed(path, dry_run=False):
    with open(path, "r", encoding="utf-8") as fh:
        cards = json.load(fh)

    required = {"question", "answer", "category", "difficulty"}
    for i, c in enumerate(cards):
        missing = required - set(c)
        if missing:
            raise SystemExit(f"card {i} missing fields: {sorted(missing)}")

    # Deterministic order: group by category as they first appear.
    categories = []
    for c in cards:
        if c["category"] not in categories:
            categories.append(c["category"])

    db.init_db()  # ensure interview_* tables exist

    inserted = skipped = 0
    sessions_created = 0
    ph = _ph()

    with db.get_db() as conn:
        cur = conn.cursor()
        _ensure_columns(cur)

        session_ids = {}
        for cat in categories:
            role = _role_for(cat)
            if dry_run:
                # Report whether a session already exists without creating one.
                cur.execute(
                    f"SELECT id FROM interview_sessions WHERE role={ph} AND mode={ph} "
                    f"LIMIT 1", (role, SEED_MODE))
                exists = cur.fetchone() is not None
                session_ids[cat] = None
                print(f"  session '{role}' — {'exists' if exists else 'WOULD CREATE'}")
                continue
            sid, created = _find_or_create_session(cur, role)
            session_ids[cat] = sid
            if created:
                sessions_created += 1
                print(f"  session '{role}' — created (id={sid})")
            else:
                print(f"  session '{role}' — reused (id={sid})")

        for c in cards:
            sid = session_ids[c["category"]]
            if dry_run:
                print(f"    [{c['difficulty']:<6}] {c['question'][:60]}")
                continue
            if _qa_exists(cur, sid, c["question"]):
                skipped += 1
                continue
            created = datetime.now().isoformat()
            cur.execute(
                f"INSERT INTO interview_qa "
                f"(session_id, question, answer, rating, ts, category, difficulty, created_at) "
                f"VALUES ({ph},{ph},{ph},0,{ph},{ph},{ph},{ph})",
                (sid, c["question"], c["answer"], created,
                 c["category"], c["difficulty"], created),
            )
            inserted += 1

    if dry_run:
        print(f"\nDry run: {len(cards)} cards across {len(categories)} categories. "
              f"Nothing written.")
    else:
        print(f"\nDone. inserted={inserted} skipped(existing)={skipped} "
              f"sessions_created={sessions_created}")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    dry_run = "--dry-run" in argv[1:]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = args[0] if args else os.path.join(here, "interview_qa_seed.json")
    if not os.path.exists(path):
        raise SystemExit(f"seed file not found: {path}")
    print(f"Seeding from {path}"
          f"  (backend: {'Postgres' if db.USE_POSTGRES else 'SQLite'})"
          + ("  [DRY RUN]" if dry_run else ""))
    seed(path, dry_run=dry_run)


if __name__ == "__main__":
    main(sys.argv)
