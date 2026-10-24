"""One-time import of the tracker spreadsheet into Scout.

    python scripts/import_scout_spreadsheet.py PATH_TO_XLSX [--dry-run]

The file is read from wherever you point it and is never copied into the repo.
`*.xlsx` is gitignored for the same reason.

PRIVACY
-------
This repository is public and the workbook holds application IDs, candidate
references and personal notes. So this script NEVER prints row content — not in
dry-run, not in a real run, not on an error. Every line it writes is a count, a
sheet name, a stage name or a skip reason. Notes, links, IDs and the names of
individual applications go from the file into the private database and nowhere
else. If you need to see a row, read it in Scout.

WHAT IT READS
-------------
  Master List     ~51 programmes -> scout_programmes (the watchlist)
  Timeline        open months per programme -> scout_programmes.open_months
  My Applications ~64 rows -> the tracker stage on the matching programme

The category sheets (Software Engineering, Cybersecurity, ...) are filtered
views of Master List and are ignored, as is Read Me.

IDEMPOTENT
----------
Keyed on normalised (Company, Programme). Re-running refreshes the fields the
spreadsheet owns and never duplicates a row. It never overwrites tracker state,
and a row edited inside Scout is left completely alone — see
database.upsert_scout_programme. After the first import Scout is the source of
truth; there is no sync back. Use --export-csv to get data out.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

# Sheets that carry data, and the ones deliberately skipped.
SHEET_MASTER = "Master List"
SHEET_TIMELINE = "Timeline"
SHEET_APPLICATIONS = "My Applications"
WANTED_SHEETS = (SHEET_MASTER, SHEET_TIMELINE, SHEET_APPLICATIONS)

# Spreadsheet status -> Scout stage. "Not Started" maps to None: the programme
# stays on the watchlist but does not appear on the board, because nothing has
# happened yet and a column of untouched rows is what makes a board useless.
STATUS_TO_STAGE = {
    "not started": None,
    "interested": "interested",
    "applied": "applied",
    "oa complete": "online_assessment",
    "video interview": "video_interview",
    "assessment centre": "assessment_centre",
    "offer": "offer",
    "rejected": "rejected",
    "withdrew": "withdrawn",
}

# Master List column header -> scout_programmes column.
MASTER_COLUMNS = {
    "company": "company",
    "programme": "programme",
    "sector": "sector",
    "specialization": "specialization",
    "level": "level",
    "duration": "duration",
    "university partner": "university_partner",
    "degree awarded": "degree_awarded",
    "locations": "locations",
    "salary y1": "salary_y1",
    "entry requirements": "entry_requirements",
    "t-level accepted": "t_level_accepted",
    "typical opens": "typical_opens",
    "typical closes": "typical_closes",
    "rolling?": "rolling",
    "cohort size": "cohort_size",
    "assessment process": "assessment_process",
    "official link": "official_link",
    "notes": "import_notes",
    "cloud-sec fit": "cloud_sec_fit",
}

# The Timeline sheet's month columns, in the order the apprenticeship year runs.
MONTHS = ("sep", "oct", "nov", "dec", "jan", "feb", "mar", "apr",
          "may", "jun", "jul", "aug")


def _norm_header(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _cell(value):
    """A cell as a trimmed string, or "" — never None, never a stray float."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _is_marked(value) -> bool:
    """True when a Timeline month cell marks the programme as open.

    Any non-empty mark counts. The sheet uses a dot, but x/X/yes/1 all appear in
    hand-edited rows and treating only one glyph as valid would silently drop
    months.
    """
    text = _cell(value).casefold()
    return bool(text) and text not in ("0", "-", "n", "no", "false")


def _find_header_row(rows, required: str = "company"):
    """Index of the header row, found by looking for a known column name.

    The sheets have a title line above the header in places, so the header
    cannot be assumed to be row 1.
    """
    for i, row in enumerate(rows[:10]):
        headers = [_norm_header(c) for c in row]
        if required in headers:
            return i
    return None


def _sheet_rows(workbook, name):
    """(headers, data_rows) for one sheet, or (None, []) when it is absent."""
    if name not in workbook.sheetnames:
        return None, []
    rows = [list(r) for r in workbook[name].iter_rows(values_only=True)]
    header_index = _find_header_row(rows)
    if header_index is None:
        return None, []
    headers = [_norm_header(c) for c in rows[header_index]]
    return headers, rows[header_index + 1:]


def _row_dict(headers, row) -> dict:
    out = {}
    for i, header in enumerate(headers):
        if header:
            out[header] = row[i] if i < len(row) else None
    return out


# ── The three sheets ────────────────────────────────────────────────────────

def read_master(workbook, stats):
    """Master List -> {(company_norm, programme_norm): (company, programme, fields)}."""
    headers, rows = _sheet_rows(workbook, SHEET_MASTER)
    out = {}
    if headers is None:
        stats["skipped"]["master sheet missing or unreadable"] += 1
        return out

    for row in rows:
        d = _row_dict(headers, row)
        company = _cell(d.get("company"))
        programme = _cell(d.get("programme"))
        stats["rows_read"][SHEET_MASTER] += 1
        if not company:
            stats["skipped"]["master row with no company"] += 1
            continue
        fields = {}
        for header, column in MASTER_COLUMNS.items():
            if column in ("company", "programme"):
                continue
            if header in d:
                fields[column] = _cell(d[header])
        key = (db.normalise_programme_key(company),
               db.normalise_programme_key(programme))
        if key in out:
            stats["skipped"]["master duplicate company+programme"] += 1
            continue
        out[key] = (company, programme, fields)
    return out


def read_timeline(workbook, stats):
    """Timeline -> {key: {"open_months": "sep,oct", "timeline_close", "rolling", ...}}.

    The months are LAST CYCLE's windows, not confirmed dates for the next one,
    so they are stored as `open_months` and the UI must label them unconfirmed.
    A confirmed date goes in `confirmed_open_date` with `confirmed_source`,
    which an import never writes.
    """
    headers, rows = _sheet_rows(workbook, SHEET_TIMELINE)
    out = {}
    if headers is None:
        stats["skipped"]["timeline sheet missing or unreadable"] += 1
        return out

    for row in rows:
        d = _row_dict(headers, row)
        company = _cell(d.get("company"))
        programme = _cell(d.get("programme"))
        stats["rows_read"][SHEET_TIMELINE] += 1
        if not company:
            stats["skipped"]["timeline row with no company"] += 1
            continue
        months = [m for m in MONTHS if m in d and _is_marked(d[m])]
        fields = {"open_months": ",".join(months)}
        for header, column in (("typical close", "timeline_close"),
                               ("rolling?", "rolling"),
                               ("fit", "timeline_fit")):
            if header in d:
                fields[column] = _cell(d[header])
        if months:
            stats["timeline_months"] += len(months)
            stats["timeline_with_months"] += 1
        else:
            stats["skipped"]["timeline row with no open month marked"] += 1
        out[(db.normalise_programme_key(company),
             db.normalise_programme_key(programme))] = fields
    return out


def read_applications(workbook, stats):
    """My Applications -> {key: user_fields}. Unmapped statuses are skipped."""
    headers, rows = _sheet_rows(workbook, SHEET_APPLICATIONS)
    out = {}
    if headers is None:
        stats["skipped"]["applications sheet missing or unreadable"] += 1
        return out

    for row in rows:
        d = _row_dict(headers, row)
        company = _cell(d.get("company"))
        programme = _cell(d.get("programme"))
        stats["rows_read"][SHEET_APPLICATIONS] += 1
        if not company:
            stats["skipped"]["application row with no company"] += 1
            continue

        status = _cell(d.get("status")).casefold()
        if status not in STATUS_TO_STAGE:
            # Counted by the STATUS, never by the row — a status is a category,
            # not personal data.
            stats["skipped"][f"unmapped status: {status or '(blank)'}"] += 1
            continue
        stage = STATUS_TO_STAGE[status]

        fields = {"stage": stage}
        for header, column in (("deadline", "deadline"),
                               ("applied date", "applied_date"),
                               ("next step", "next_step"),
                               ("next step date", "next_step_date"),
                               ("notes", "notes")):
            if header in d:
                fields[column] = _cell(d[header])
        # "Stage Reached" is free text the user wrote; it goes into the private
        # notes rather than being guessed at as a stage.
        reached = _cell(d.get("stage reached"))
        if reached:
            fields["notes"] = ((fields.get("notes") or "") +
                               (" | " if fields.get("notes") else "") +
                               f"Stage reached: {reached}").strip()

        key = (db.normalise_programme_key(company),
               db.normalise_programme_key(programme))
        stats["stages"][stage or "(not started)"] += 1
        out[key] = (company, programme, fields)
    return out


# ── Import ──────────────────────────────────────────────────────────────────

def _new_stats():
    return {
        "rows_read": Counter(),
        "stages": Counter(),
        "skipped": Counter(),
        "results": Counter(),
        "timeline_months": 0,
        "timeline_with_months": 0,
        "sheets_present": [],
        "sheets_ignored": [],
    }


def run_import(path: str, dry_run: bool = False) -> dict:
    """Read the workbook and upsert. Returns counts only."""
    import openpyxl

    stats = _new_stats()
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        stats["sheets_present"] = [n for n in workbook.sheetnames
                                   if n in WANTED_SHEETS]
        stats["sheets_ignored"] = [n for n in workbook.sheetnames
                                   if n not in WANTED_SHEETS]

        master = read_master(workbook, stats)
        timeline = read_timeline(workbook, stats)
        applications = read_applications(workbook, stats)
    finally:
        workbook.close()

    # Every key any sheet mentions. An application for a programme missing from
    # Master List still gets a row — losing a tracked application because a
    # reference sheet is incomplete would be the worst possible failure here.
    keys = set(master) | set(applications)
    for key in sorted(keys):
        if key in master:
            company, programme, fields = master[key]
        else:
            company, programme, _ = applications[key]
            fields = {}
            stats["results"]["programme not in Master List"] += 1

        fields = dict(fields)
        fields.update(timeline.get(key, {}))

        seed = {}
        if key in applications:
            seed = dict(applications[key][2])
            # A stage of None means "not started": leave the column NULL so the
            # programme stays on the watchlist and off the board.
            if seed.get("stage") is None:
                seed.pop("stage", None)

        if dry_run:
            stats["results"]["would upsert"] += 1
            continue
        stats["results"][db.upsert_scout_programme(
            company, programme, fields, seed_user_fields=seed)] += 1

    stats["timeline_only_keys"] = len(set(timeline) - keys)
    return stats


def report(stats, dry_run: bool):
    """Counts only. No row content, ever."""
    print()
    print("DRY RUN — nothing was written." if dry_run else "Import complete.")
    print()
    print("sheets read:")
    for name in stats["sheets_present"]:
        print(f"    {name:<22}{stats['rows_read'][name]:>6} data rows")
    if stats["sheets_ignored"]:
        print(f"    ignored: {len(stats['sheets_ignored'])} other sheet(s) "
              f"(category views and Read Me)")
    print()
    print("rows per resulting stage:")
    if stats["stages"]:
        for stage, n in sorted(stats["stages"].items(),
                               key=lambda kv: (-kv[1], kv[0])):
            print(f"    {stage:<22}{n:>6}")
    else:
        print("    (none)")
    print()
    print("timeline:")
    print(f"    programmes with months{stats['timeline_with_months']:>6}")
    print(f"    open months marked    {stats['timeline_months']:>6}")
    if stats.get("timeline_only_keys"):
        print(f"    timeline-only rows    {stats['timeline_only_keys']:>6}"
              f"   (no Master List or application match)")
    print()
    print("result:")
    for key, n in sorted(stats["results"].items()):
        print(f"    {key:<22}{n:>6}")
    print()
    print("skipped:")
    if stats["skipped"]:
        for reason, n in sorted(stats["skipped"].items(),
                                key=lambda kv: (-kv[1], kv[0])):
            print(f"    {reason:<46}{n:>6}")
    else:
        print("    (none)")
    if not dry_run:
        counts = db.count_scout_programmes()
        print()
        print("in the database now:")
        print(f"    programmes            {counts['total']:>6}")
        print(f"    not started           {counts['not_started']:>6}")
        print(f"    edited in Scout       {counts['edited_in_scout']:>6}"
              f"   (left untouched by this run)")
        print()
        print("Scout is the source of truth from here. There is no sync back to")
        print("the spreadsheet; use --export-csv to get data out.")


def export_csv(path: str) -> int:
    """One-way CSV export of the tracker, so the data can leave.

    Writes wherever you point it. Keep it out of the repo — it carries notes.
    """
    import csv
    rows = db.get_scout_programmes()
    columns = ["company", "programme", "stage", "level", "cloud_sec_fit",
               "next_step", "next_step_date", "deadline", "applied_date",
               "typical_opens", "typical_closes", "open_months",
               "confirmed_open_date", "confirmed_close_date",
               "confirmed_source", "rolling", "locations", "official_link",
               "link", "notes", "edited_in_scout"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
    return len(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="path to the .xlsx, read in place")
    ap.add_argument("--dry-run", action="store_true",
                    help="print counts only, write nothing")
    ap.add_argument("--export-csv", metavar="OUT.csv",
                    help="export the tracker to CSV and exit "
                         "(carries notes — keep it out of the repo)")
    args = ap.parse_args()

    if args.export_csv:
        n = export_csv(args.export_csv)
        print(f"exported {n} programme row(s)")
        return 0

    if not args.path:
        ap.error("a path to the spreadsheet is required")
    if not os.path.isfile(args.path):
        # The PATH is the user's own argument, so echoing it leaks nothing they
        # did not just type. Row content is what must never be printed.
        print(f"no such file: {args.path}", file=sys.stderr)
        return 1

    stats = run_import(args.path, dry_run=args.dry_run)
    report(stats, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
