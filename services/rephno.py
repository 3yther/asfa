"""Renpho ("Rephno") body-composition sync — SEAM ONLY (Part 5 discovery).

DISCOVERY FINDING (July 2026): Renpho ships **no official public API**. Data
leaves the Renpho Health app only via:
  1. Manual CSV export in the app (Trend → Data Select → Data export).
  2. Unofficial reverse-engineered cloud clients (e.g. the `renpho-api` PyPI
     package / Home-Assistant components) that log into Renpho's private API
     with your account credentials — brittle and unofficial.
  3. Paid third-party aggregators (Terra) that push data to a webhook.

Because there is no official/documented API, the PRIMARY path in ASFA is
manual entry (POST /api/body-composition/manual) plus the progress-photo
gallery. This module is the clean seam to add an automated pull later without
touching the rest of the app.

To wire a real sync later:
  * Set REPHNO_EMAIL / REPHNO_PASSWORD (or REPHNO_API_TOKEN for Terra).
  * Implement `_fetch_recent(days)` against your chosen client, returning a list
    of dicts with keys: source_id, date_scanned, weight_kg, bmi,
    body_fat_percent, ffm_kg, body_water_percent, bmr, subcutaneous_fat_percent.
  * `sync_recent()` already dedups on source_id and upserts. Register it on the
    existing ~06:00 APScheduler slot (see services/scheduler.py).

Until then `is_configured()` returns False and `sync_recent()` is a safe no-op,
so nothing runs or crashes when the env vars are unset.
"""

import logging
import os

import database as db

logger = logging.getLogger("asfa.rephno")


def is_configured() -> bool:
    """True only if Renpho sync credentials are present. Gated so an unset
    integration never runs or errors."""
    return bool(os.environ.get("REPHNO_EMAIL") and os.environ.get("REPHNO_PASSWORD")) \
        or bool(os.environ.get("REPHNO_API_TOKEN"))


def _fetch_recent(days: int = 7) -> list:
    """Fetch the last `days` of scans from Renpho. NOT IMPLEMENTED — this is the
    seam. Return a list of measurement dicts (see module docstring) when wired.
    Kept unimplemented on purpose so we don't ship a brittle reverse-engineered
    client against an undocumented endpoint."""
    raise NotImplementedError(
        "Renpho has no official API; wire a client here (see module docstring).")


def sync_recent(days: int = 7) -> dict:
    """Pull recent scans, dedup on source_id, upsert new rows. Fails soft: an
    unconfigured or unreachable Renpho never raises to the caller/scheduler."""
    if not is_configured():
        logger.info("Renpho sync skipped — REPHNO_* env vars not set.")
        return {"ok": False, "skipped": True, "synced": 0,
                "reason": "not configured"}
    try:
        scans = _fetch_recent(days)
    except NotImplementedError:
        logger.info("Renpho sync skipped — no client implemented yet.")
        return {"ok": False, "skipped": True, "synced": 0,
                "reason": "no client implemented"}
    except Exception as e:  # network/auth/etc — fail soft
        logger.error("Renpho sync failed: %s", e)
        return {"ok": False, "skipped": False, "synced": 0, "error": str(e)[:200]}

    new = 0
    for scan in scans or []:
        sid = scan.get("source_id")
        if sid and db.body_composition_source_exists(sid):
            continue
        db.upsert_body_composition(scan.get("date_scanned"), scan, source_id=sid)
        new += 1
    logger.info("Renpho sync: %d new scan(s) of %d fetched.", new, len(scans or []))
    return {"ok": True, "skipped": False, "synced": new}
