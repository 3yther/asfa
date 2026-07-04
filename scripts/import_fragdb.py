#!/usr/bin/env python3
"""One-shot importer for the FragDB reference table (Tier 3 Part 6).

Loads a static, downloaded fragrance dataset into the local ``fragrance_reference``
table used for name autocomplete + notes/accords prefill when adding a bottle.

DATASET (download yourself — it is NOT committed; see .gitignore):
    Parfumo Fragrance Dataset (via TidyTuesday, 2024-12-10), ~59k rows, ~13 MB.
        curl -L -o scripts/data/parfumo_data_clean.csv \\
          https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2024/2024-12-10/parfumo_data_clean.csv

LICENSE / REUSE: the data was web-scraped from Parfumo.com (Kaggle upload by
    Olga G. Miufana, surfaced via rfordatascience/tidytuesday). No explicit open
    license is attached, and Parfumo's ToS governs the source. It is used here
    ONLY for local, personal, read-only prefill convenience — the CSV is
    gitignored and never redistributed, and it never overwrites the curated
    shelf. Review the source terms before any other use.

USAGE:
    python scripts/import_fragdb.py [path-to-csv]
        (defaults to scripts/data/parfumo_data_clean.csv)

Expected columns: Name, Brand, Concentration, Main_Accords,
                  Top_Notes, Middle_Notes, Base_Notes, URL
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db  # noqa: E402

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                           "parfumo_data_clean.csv")
_NA = {"", "NA", "N/A", "None", "null"}


def _clean(v):
    v = (v or "").strip()
    return None if v in _NA else v


def _notes(top, mid, base):
    """Compose a 'Top: …; Heart: …; Base: …' string like the curated shelf."""
    parts = []
    for label, val in (("Top", top), ("Heart", mid), ("Base", base)):
        val = _clean(val)
        if val:
            parts.append(f"{label}: {val}")
    return "; ".join(parts) or None


def _records(path):
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = _clean(row.get("Name"))
            if not name:
                continue
            yield {
                "name": name,
                "brand": _clean(row.get("Brand")),
                "concentration": _clean(row.get("Concentration")),
                "accords": _clean(row.get("Main_Accords")),
                "notes": _notes(row.get("Top_Notes"), row.get("Middle_Notes"),
                                row.get("Base_Notes")),
                "url": _clean(row.get("URL")),
            }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    if not os.path.exists(path):
        print(f"CSV not found: {path}\nDownload it first (see this file's header).")
        return 1
    print(f"Importing {path} …")
    t0 = time.time()
    n = db.import_fragrance_reference(_records(path), replace=True)
    dt = time.time() - t0
    print(f"Imported {n} reference rows in {dt:.1f}s "
          f"(table now holds {db.fragrance_reference_count()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
