"""Dry run of the `job_pipeline_cleanup` scheduled job. Deletes NOTHING.

Phase 0b. The job (daily 02:30 Europe/London) calls
`database.cleanup_job_pipeline(days=14)`. It deletes `scout_pipeline` rows past
the cutoff that are still at the automatic stage, and EXEMPTS any row the user
moved to a tracked stage (`database.SCOUT_TRACKED_STAGES`). This prints what it
would take right now, broken down by stage, plus what the exemption saves.

Run from the repo root with the venv active:

    python scripts/scout_cleanup_dryrun.py            # 14 days, the job's default
    python scripts/scout_cleanup_dryrun.py --days 30

Reads whatever database the app reads: Postgres when DATABASE_URL is set,
otherwise the local SQLite asfa.db. To run it against production, run it with
that environment's DATABASE_URL set.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

# Taken from the database module so the script cannot drift from the job it
# models. `saved` is absent by construction: `_backfill_scout_pipeline()` inserts
# every scraped job at that stage on each boot, so it is the automatic default
# rather than a choice the user made.
USER_CHOSEN_STAGES = db.SCOUT_TRACKED_STAGES


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14,
                    help="retention window in days (job default: 14)")
    args = ap.parse_args()

    where = ("Postgres (DATABASE_URL)" if db.USE_POSTGRES
             else f"SQLite {db.SQLITE_PATH}")
    print(f"Database : {where}")

    r = db.preview_job_pipeline_cleanup(days=args.days)

    print(f"Window   : {r['days']} days")
    print(f"Cutoff   : {r['cutoff']}  (applications compared by date: {r['cutoff_date']})")
    print()
    print("DRY RUN — nothing was deleted.")
    print()

    print(f"scout_pipeline rows the job would DELETE ({r['pipeline_total']}):")
    if r["pipeline_by_stage"]:
        for stage, n in sorted(r["pipeline_by_stage"].items(),
                               key=lambda kv: (-kv[1], kv[0])):
            flag = "  <-- UNEXPECTED: user-chosen" if stage in USER_CHOSEN_STAGES else ""
            print(f"    {stage:<12} {n:>6}{flag}")
    else:
        print("    (none)")
    print()

    print(f"scout_pipeline rows past the cutoff but EXEMPT "
          f"({r['pipeline_exempt_total']}):")
    if r["pipeline_exempt_by_stage"]:
        for stage, n in sorted(r["pipeline_exempt_by_stage"].items(),
                               key=lambda kv: (-kv[1], kv[0])):
            print(f"    {stage:<12} {n:>6}  kept (user-chosen)")
    else:
        print("    (none)")
    print()

    print(f"scout_pipeline rows that would SURVIVE "
          f"({sum(r['pipeline_surviving_by_stage'].values())}):")
    if r["pipeline_surviving_by_stage"]:
        for stage, n in sorted(r["pipeline_surviving_by_stage"].items(),
                               key=lambda kv: (-kv[1], kv[0])):
            print(f"    {stage:<12} {n:>6}")
    else:
        print("    (none)")
    print()

    print(f"scout_applications rows the job would DELETE ({r['applications_total']}):")
    if r["applications_by_status"]:
        for status, n in sorted(r["applications_by_status"].items(),
                                key=lambda kv: (-kv[1], kv[0])):
            print(f"    {status:<12} {n:>6}")
    else:
        print("    (none)")
    print()

    at_risk = sum(n for s, n in r["pipeline_by_stage"].items()
                  if s in USER_CHOSEN_STAGES)
    print(f"TOTAL rows the job would delete : {r['total']}")
    print(f"  exempt, kept by stage         : {r['pipeline_exempt_total']}"
          f"  ({'/'.join(USER_CHOSEN_STAGES)})")
    if at_risk:
        print()
        print(f"  WARNING: {at_risk} row(s) at a user-chosen stage are still in the")
        print("  delete set. The exemption is not doing its job — investigate before")
        print("  the next 02:30 run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
