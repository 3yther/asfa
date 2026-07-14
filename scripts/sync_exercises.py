#!/usr/bin/env python3
"""Sync the ASFA exercise catalogue from the hasaneyldrm/exercises-dataset repo.

Fetches the public-domain exercises.json (1,324 exercises, each with a form-demo
GIF), maps each record onto the ``exercises`` table, and upserts it. Idempotent:
keyed on the dataset's string ``id``, so re-running only updates the synced
fields — the manually-curated ``difficulty`` column is never touched.

Usage:
    python scripts/sync_exercises.py                 # fetch + upsert
    python scripts/sync_exercises.py --dry-run       # preview, write nothing
    python scripts/sync_exercises.py --file a.json   # sync from a local file
    python scripts/sync_exercises.py --url <URL>     # override the source URL

Importable by tests: fetch_exercises(), map_exercise(), sync().
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

DATASET_URL = ("https://raw.githubusercontent.com/hasaneyldrm/"
               "exercises-dataset/main/data/exercises.json")
# Relative gif_url / image paths in the dataset resolve against the repo root.
MEDIA_BASE = "https://raw.githubusercontent.com/hasaneyldrm/exercises-dataset/main/"


def _media_url(path):
    """Absolute media URL for a dataset-relative path, or None if absent."""
    if not path:
        return None
    if str(path).startswith(("http://", "https://")):
        return path
    return MEDIA_BASE + str(path).lstrip("/")


def _pick_english(value):
    """Instructions come as {lang: text}; prefer English, else the first value.
    A plain string is returned as-is."""
    if isinstance(value, dict):
        return value.get("en") or next(iter(value.values()), None)
    return value


def map_exercise(raw: dict) -> dict:
    """Map one dataset record onto the exercises-table row shape. Derives
    is_home_friendly from equipment (body weight / bands / resistance band)."""
    equipment = raw.get("equipment")
    home = (equipment or "").strip().lower() in db.HOME_EQUIPMENT
    return {
        "id": str(raw.get("id")),
        "name": raw.get("name"),
        "category": raw.get("category") or raw.get("body_part"),
        "target_muscle": raw.get("target") or raw.get("muscle_group"),
        "equipment": equipment,
        "instructions": _pick_english(raw.get("instructions")),
        "image_url": _media_url(raw.get("image") or raw.get("image_url")),
        "gif_url": _media_url(raw.get("gif_url") or raw.get("gif")),
        "is_home_friendly": home,
    }


def fetch_exercises(url: str = DATASET_URL) -> list:
    """Fetch + parse the dataset JSON from a URL. Returns the raw record list."""
    import requests
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def load_exercises_file(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sync(records, dry_run: bool = False) -> dict:
    """Map + upsert every record. Returns {fetched, inserted, updated, skipped}.
    A record is skipped when it has no id or name. In dry-run, nothing is
    written and everything valid counts as "inserted" for the preview."""
    fetched = len(records)
    inserted = updated = skipped = 0
    preview = []
    for raw in records:
        row = map_exercise(raw)
        if not row.get("id") or row["id"] == "None" or not row.get("name"):
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            if len(preview) < 5:
                preview.append(row)
            continue
        result = db.upsert_exercise(row)
        if result == "inserted":
            inserted += 1
        else:
            updated += 1
    return {"fetched": fetched, "inserted": inserted, "updated": updated,
            "skipped": skipped, "preview": preview}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync the ASFA exercise catalogue.")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview the sync without writing to the DB")
    ap.add_argument("--file", help="sync from a local JSON file instead of the URL")
    ap.add_argument("--url", default=DATASET_URL, help="override the dataset URL")
    args = ap.parse_args(argv)

    if args.file:
        print(f"Loading exercises from {args.file} …")
        records = load_exercises_file(args.file)
    else:
        print(f"Fetching exercises from {args.url} …")
        records = fetch_exercises(args.url)

    result = sync(records, dry_run=args.dry_run)

    if args.dry_run:
        print("\nDRY RUN — no changes written. Sample mapped rows:\n")
        for row in result["preview"]:
            home = "home" if row["is_home_friendly"] else "gym"
            print(f"  [{row['id']}] {row['name']}  "
                  f"({row.get('category')}/{row.get('equipment')}, {home})")
            print(f"        gif: {row.get('gif_url')}")

    verb = "would insert" if args.dry_run else "inserted"
    print(f"\nFetched {result['fetched']}, {verb} {result['inserted']}, "
          f"updated {result['updated']}, skipped {result['skipped']}.")
    if not args.dry_run:
        print(f"Catalogue now holds {db.count_exercises()} exercises.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
