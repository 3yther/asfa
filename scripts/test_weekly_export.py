#!/usr/bin/env python3
"""Manually trigger the weekly CSV export and show what it produced.

    python scripts/test_weekly_export.py              # build only, no email
    python scripts/test_weekly_export.py --send       # actually send the email
    python scripts/test_weekly_export.py --write DIR  # also dump CSVs to DIR

Safe to run any time: it only reads, and without --send it never touches SMTP.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import alerts, weekly_export  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send the email")
    ap.add_argument("--write", metavar="DIR", help="write the CSVs to DIR")
    ap.add_argument("--start", help="override window start (YYYY-MM-DD)")
    ap.add_argument("--end", help="override window end (YYYY-MM-DD)")
    args = ap.parse_args()

    auto_start, auto_end = weekly_export.week_window()
    print(f"Export timezone : {os.environ.get('EXPORT_TZ', 'America/New_York')}")
    print(f"Computed window : {auto_start} (Sun) → {auto_end} (Sat)")
    if args.start or args.end:
        print(f"Overridden to   : {args.start or auto_start} → {args.end or auto_end}")
    print(f"SMTP configured : {alerts.smtp_configured()}")
    print()

    res = weekly_export.send_weekly_export(
        start=args.start, end=args.end, dry_run=not args.send)

    print(f"{'FILE':<16}{'ROWS':>6}  {'BYTES':>7}")
    print("-" * 33)
    for filename, content in res["attachments"]:
        print(f"{filename:<16}{res['counts'][filename]:>6}  {len(content):>7}")
    print("-" * 33)
    print(f"{'TOTAL':<16}{res['rows']:>6}")
    print()

    for filename, content in res["attachments"]:
        print(f"===== {filename} =====")
        lines = content.strip().splitlines()
        for line in lines[:6]:
            print("  " + line)
        if len(lines) > 6:
            print(f"  … {len(lines) - 6} more line(s)")
        print()

    if args.write:
        os.makedirs(args.write, exist_ok=True)
        for filename, content in res["attachments"]:
            path = os.path.join(args.write, filename)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            print(f"wrote {path}")
        print()

    print(f"Recipient : {res['to']}")
    print(f"Subject   : ASFA Weekly Export — {res['start']} to {res['end']}")
    print(f"Emailed   : {res['emailed']}")
    if res.get("skipped"):
        print(f"Skipped   : {res['skipped']}")


if __name__ == "__main__":
    main()
