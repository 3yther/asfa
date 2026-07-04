#!/usr/bin/env python3
"""One-off importer for a Renpho body-composition CSV export (Tier 4 Part 3).

Renpho has no public API — the only bulk export is the in-app "Export data"
CSV. This script bulk-loads that export into the ``body_composition`` table so
scans don't have to be retyped one by one through the manual-entry form.

DEDUP: one row per ``date_scanned`` (same rule as the manual-entry endpoint).
    A date already present in the table is reported as a duplicate and skipped —
    the importer never overwrites an existing scan. Insertion itself reuses
    ``db.upsert_body_composition`` so the column mapping / insert logic is not
    duplicated here.

SAFETY: by default this runs against a *throwaway copy* of the local asfa.db, so
    a first run can never mutate real data. Pass ``--live`` to write to the real
    local asfa.db. (This is a local one-off; it does not target prod Postgres.)

USAGE:
    python scripts/import_renpho.py <path-to-export.csv>          # dry copy
    python scripts/import_renpho.py <path-to-export.csv> --live   # real asfa.db

WARNING: COLUMN MAPPING IS UNVERIFIED against a real Renpho export. It is built from
    Renpho's commonly-documented export headers (see COLUMN_MAP). Renpho varies
    headers by app version / locale / unit system, so on first use compare your
    file's header row against COLUMN_MAP and adjust the candidate lists — that
    is the ONE place to edit. Weight/FFM are assumed to already be in **kg** and
    are not unit-converted.
"""

import csv
import os
import re
import shutil
import sys
import tempfile

# ── Column mapping — EDIT HERE to match your export's header row ──────────────
# Maps a body_composition field -> list of candidate CSV header names. Matching
# is case-insensitive and ignores spaces/units punctuation (see _norm), so
# "Body Fat(%)", "body fat", and "Body_Fat" all match the same entry.
COLUMN_MAP = {
    "date_scanned":            ["Time of Measurement", "Measurement Time", "Date", "Time", "Timestamp"],
    "weight_kg":               ["Weight(kg)", "Weight (kg)", "Weight", "Body Weight(kg)"],
    "bmi":                     ["BMI"],
    "body_fat_percent":        ["Body Fat(%)", "Body Fat", "Body Fat Percentage"],
    "ffm_kg":                  ["Fat-free Body Weight(kg)", "Fat-Free Mass(kg)", "Fat Free Mass", "FFM", "Lean Body Mass(kg)"],
    "body_water_percent":      ["Body Water(%)", "Body Water", "Water(%)"],
    "bmr":                     ["BMR(kcal)", "BMR", "Basal Metabolic Rate"],
    "subcutaneous_fat_percent": ["Subcutaneous Fat(%)", "Subcutaneous Fat"],
}

METRIC_FIELDS = [f for f in COLUMN_MAP if f != "date_scanned"]


def _norm(s):
    """Normalise a header for tolerant matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _build_header_index(fieldnames):
    """Return {body_field: actual_csv_header} for headers we recognise."""
    norm_to_actual = {_norm(h): h for h in (fieldnames or [])}
    resolved = {}
    for field, candidates in COLUMN_MAP.items():
        for cand in candidates:
            actual = norm_to_actual.get(_norm(cand))
            if actual is not None:
                resolved[field] = actual
                break
    return resolved


_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")


def _parse_date(raw):
    """Renpho stores a datetime like '2024-01-15 07:30:00'. Reduce to an ISO
    date (YYYY-MM-DD). Returns None if no date can be found."""
    if not raw:
        return None
    m = _DATE_RE.search(str(raw))
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y}-{mo:02d}-{d:02d}"


def _to_float(v):
    try:
        return None if v in (None, "") else float(str(v).strip())
    except (TypeError, ValueError):
        return None


def import_csv(path, db):
    """Parse the export and load new scans. Returns a summary dict."""
    read = inserted = skipped = 0
    failures = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header_index = _build_header_index(reader.fieldnames)
        if "date_scanned" not in header_index:
            raise SystemExit(
                "No recognised date column. Header row was:\n  "
                + ", ".join(reader.fieldnames or ["<empty>"])
                + "\nAdjust COLUMN_MAP['date_scanned'] to match, then re-run."
            )

        # Existing dates → dedup set. days is huge so every stored scan is returned.
        seen_dates = {r["date_scanned"] for r in db.get_body_composition(days=100000)}

        for lineno, row in enumerate(reader, start=2):  # line 1 is the header
            read += 1
            date_scanned = _parse_date(row.get(header_index["date_scanned"]))
            if not date_scanned:
                failures.append((lineno, "unparseable/missing date"))
                continue
            metrics = {
                field: _to_float(row.get(header_index[field]))
                for field in METRIC_FIELDS if field in header_index
            }
            if all(v is None for v in metrics.values()):
                failures.append((lineno, "no numeric metrics on this row"))
                continue
            if date_scanned in seen_dates:
                skipped += 1
                continue
            db.upsert_body_composition(date_scanned, metrics,
                                       source_id=f"renpho:{date_scanned}")
            seen_dates.add(date_scanned)
            inserted += 1

    return {"read": read, "inserted": inserted, "skipped": skipped,
            "failures": failures, "header_index": header_index}


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    live = "--live" in argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python scripts/import_renpho.py <export.csv> [--live]")
    csv_path = args[0]
    if not os.path.isfile(csv_path):
        raise SystemExit(f"CSV not found: {csv_path}")

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    real_db = os.path.join(repo, "asfa.db")

    if live:
        os.environ["ASFA_DB_PATH"] = real_db
        os.environ.pop("DATABASE_URL", None)  # local-only tool; never prod Postgres
        target_desc = f"LIVE local DB → {real_db}"
        tmp_copy = None
    else:
        tmp_copy = tempfile.NamedTemporaryFile(
            prefix="asfa_renpho_", suffix=".db", delete=False).name
        if os.path.isfile(real_db):
            shutil.copy2(real_db, tmp_copy)
        os.environ["ASFA_DB_PATH"] = tmp_copy
        os.environ.pop("DATABASE_URL", None)
        target_desc = f"DRY RUN on throwaway copy → {tmp_copy} (real asfa.db untouched)"

    # database.py resolves ASFA_DB_PATH at import time, so import it only now.
    sys.path.insert(0, repo)
    import database as db  # noqa: E402

    print(f"Target: {target_desc}")
    summary = import_csv(csv_path, db)
    hi = summary["header_index"]
    print("Column mapping used (body_field <- csv header):")
    for field in COLUMN_MAP:
        print(f"  {field:26s} <- {hi.get(field, '(missing — not imported)')}")
    print()
    print(f"Rows read:                {summary['read']}")
    print(f"Rows inserted:            {summary['inserted']}")
    print(f"Rows skipped (duplicate): {summary['skipped']}")
    print(f"Rows failed to parse:     {len(summary['failures'])}")
    for lineno, reason in summary["failures"]:
        print(f"    line {lineno}: {reason}")
    if not live:
        print("\nThis was a DRY RUN. Re-run with --live to write to the real asfa.db.")


if __name__ == "__main__":
    main(sys.argv)
