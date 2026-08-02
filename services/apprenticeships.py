"""Scout — UK tech apprenticeship watcher.

Merged from the standalone Apprenticeship Radar project. Scans
findapprenticeship.service.gov.uk for every watched employer (and a set of
broad keywords), persists what it finds into Scout's existing `scout_jobs`
table as `listing_type='apprenticeship'`, and alerts only on postings that
match an employer on the watchlist.

Sibling of `services/scout.py`, which does the same job for part-time roles via
Reed/SerpAPI. Both write to one table, surface on one dashboard, and alert
through one notifier (`services/scout_notify.py`).

Best-effort by design, matching services/scout.py: a failed gov.uk request
records a ScanLog row with the error and the cycle continues, so one bad
employer alias can't abort a whole scan.

Environment:
  KEYWORD_SCANS                — comma-separated keyword scans. Default:
                                 cyber security,software developer,data,cloud,
                                 network engineer,devops
  APPRENTICESHIP_MIN_LEVEL     — minimum apprenticeship level (default 4)
  APPRENTICESHIP_POLL_MINUTES  — poll interval in minutes (default 360 = 6h),
                                 also sets the closure-detection window
"""
import logging
import os
import time
from datetime import date, datetime, timedelta

import database as db
from models import Employer, ScanLog, ScoutJob
from models import db as orm
from services.scrapers.gov_uk import GovUkScraper, RawVacancy, ScraperError

logger = logging.getLogger("asfa.apprenticeships")

DEFAULT_KEYWORDS = ("cyber security,software developer,data,cloud,"
                    "network engineer,devops")

# Aliases this short only match on a word boundary — see _alias_matches.
_SHORT_ALIAS_LEN = 3


def keyword_scans() -> list:
    """The keyword list, from KEYWORD_SCANS."""
    raw = os.environ.get("KEYWORD_SCANS") or DEFAULT_KEYWORDS
    return [k.strip() for k in raw.split(",") if k.strip()]


def min_level() -> int:
    try:
        return int(os.environ.get("APPRENTICESHIP_MIN_LEVEL", "4"))
    except ValueError:
        return 4


def poll_minutes() -> int:
    try:
        return int(os.environ.get("APPRENTICESHIP_POLL_MINUTES", "360"))
    except ValueError:
        return 360


# ── Employer matching ────────────────────────────────────────────────────────

def _alias_matches(alias: str, raw_lower: str) -> bool:
    """Case-insensitive substring match in BOTH directions.

    Bidirectional is what makes "AMAZON UK SERVICES LTD." match the `Amazon`
    employer (alias inside raw) and equally lets a terse gov.uk string match a
    longer alias (raw inside alias).

    DELIBERATE DEVIATION: aliases of <= 3 characters must additionally land on a
    word boundary. Plain substring matching means the `EY` alias matches
    "SURREY COUNTY COUNCIL", and a false employer match produces exactly the
    alert spam this module is meant to prevent. Short aliases in the seed list
    (EY, TCS, MOD, JLR, PwC, NCA, BBC) are all acronyms that only ever appear as
    whole words, so the guard costs nothing and kills a real false-positive
    class. Longer aliases keep pure substring behaviour.
    """
    a = alias.lower().strip()
    if not a:
        return False
    if len(a) <= _SHORT_ALIAS_LEN:
        return any(part.strip(".,()&-") == a for part in raw_lower.split())
    return a in raw_lower or raw_lower in a


def match_employer(raw_name: str, employers=None):
    """Return the watched Employer matching this raw gov.uk employer string.

    `employers` may be a pre-fetched list — a scan loads the watchlist once and
    reuses it rather than re-querying per vacancy.
    """
    if not raw_name:
        return None
    raw_lower = raw_name.lower().strip()
    if employers is None:
        employers = Employer.query.filter_by(watching=True).all()
    for emp in employers:
        for alias in emp.alias_list:
            if _alias_matches(alias, raw_lower):
                return emp
    return None


# ── Persistence ──────────────────────────────────────────────────────────────

def _persist_vacancies(raws, employer, new_bucket, employers=None) -> int:
    """Insert new apprenticeships, refresh last_seen on ones we already have.

    Dedup is by `external_ref` (the gov.uk VAC reference), which is what makes
    the same posting surfacing under several keyword scans collapse to one row.
    Returns the count newly inserted.

    Only vacancies matched to a *watched* employer go into `new_bucket` (and so
    into an alert). Unmatched ones are still persisted — the record is worth
    having — they just don't notify.
    """
    new_count = 0
    now = datetime.utcnow()
    for raw in raws:
        existing = ScoutJob.query.filter_by(
            external_ref=raw.reference,
            listing_type="apprenticeship",
        ).first()
        if existing:
            existing.last_seen = now
            existing.status = "open"          # re-seen: it's live again/still
            # Link the employer if a keyword scan found it first (unmatched)
            # and a later employer scan identified it.
            if not existing.employer_id and employer:
                existing.employer_id = employer.id
                existing.company = employer.name
            continue

        matched = employer or match_employer(raw.employer_name_raw, employers)

        row = ScoutJob(
            listing_type="apprenticeship",
            external_ref=raw.reference,
            title=raw.title,
            # `company` is what the existing dashboard table renders, so it gets
            # the canonical employer name when we have one, raw gov.uk text
            # otherwise. employer_name_raw always keeps the unmodified string.
            company=(matched.name if matched else raw.employer_name_raw),
            employer_id=(matched.id if matched else None),
            employer_name_raw=raw.employer_name_raw,
            location=raw.location,
            salary=raw.wage,                  # Scout's salary is free text
            job_type="apprenticeship",
            url=raw.url,
            description=raw.training_course,
            source="gov_uk",                  # provider, same vocabulary as reed
            posted_date=raw.posted_text,
            found_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            is_new=1,
            applied=0,
            level=raw.level,
            training_course=raw.training_course,
            closing_text=raw.closing_text,
            closing_date=raw.closing_date,
            start_date=raw.start_date,
            status="open",
            last_seen=now,
            alerted=0,
        )
        orm.session.add(row)
        orm.session.flush()                   # assign an id before we bucket it
        new_count += 1
        if matched:
            new_bucket.append(row)
    orm.session.commit()
    return new_count


def _log_scan(kind, query, found, new, duration_ms, error=None):
    """Record one employer-alias or keyword scan. Never raises."""
    try:
        orm.session.add(ScanLog(
            kind=kind,
            # NEVER name this `query` — it shadows Model.query. See models.py.
            query_text=(query or "")[:200],
            results_found=found,
            new_vacancies=new,
            duration_ms=duration_ms,
            error=error[:2000] if error else None,
        ))
        orm.session.commit()
    except Exception as e:
        orm.session.rollback()
        logger.warning("apprenticeships: scan log failed: %s", e)


def _detect_closures() -> int:
    """Mark apprenticeships stale for 3 poll intervals as closed.

    Never deletes — the open/close window of a posting is the historical data
    this whole thing exists to accumulate.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=poll_minutes() * 3)
    stale = ScoutJob.query.filter(
        ScoutJob.listing_type == "apprenticeship",
        ScoutJob.status == "open",
        ScoutJob.last_seen.isnot(None),
        ScoutJob.last_seen < cutoff,
    ).all()
    for row in stale:
        row.status = "closed"
    if stale:
        orm.session.commit()
    return len(stale)


# ── Closing-soon reminder ────────────────────────────────────────────────────

_CLOSING_SOON_KEY = "apprenticeship_closing_soon_sent"


def closing_soon(days: int = 3) -> list:
    """Open, watched apprenticeships closing within `days` (today included)."""
    today = date.today()
    return (ScoutJob.query
            .filter(ScoutJob.listing_type == "apprenticeship",
                    ScoutJob.status == "open",
                    ScoutJob.employer_id.isnot(None),
                    ScoutJob.closing_date.isnot(None),
                    ScoutJob.closing_date >= today,
                    ScoutJob.closing_date <= today + timedelta(days=days))
            .order_by(ScoutJob.closing_date.asc())
            .all())


def _maybe_send_closing_soon() -> int:
    """Send the closing-soon reminder at most once per calendar day.

    The once-a-day guard is a key/value row rather than a scan of sent alerts:
    Scout has no alerts table (alert outcomes go to log_audit), and a kv flag is
    cheap and survives restarts.
    """
    from services import scout_notify

    today = db.today_str()
    if db.kv_get(_CLOSING_SOON_KEY) == today:
        return 0
    urgent = closing_soon(days=3)
    if not urgent:
        # Don't set the flag — if something starts closing later today we still
        # want to catch it on the next poll.
        return 0
    scout_notify.alert_closing_soon(urgent, days=3)
    db.kv_set(_CLOSING_SOON_KEY, today)
    return len(urgent)


# ── The scan cycle ───────────────────────────────────────────────────────────

def scan_apprenticeships() -> dict:
    """One full gov.uk pass. Returns a summary dict; never raises.

    Order: every watched employer's aliases, then the keyword scans, then
    closure detection, then alerts, then the once-daily closing-soon pass.
    """
    from services import scout_notify

    scraper = GovUkScraper()
    level = min_level()
    summary = {"employers_scanned": 0, "keywords_scanned": 0, "new": 0,
               "closed": 0, "alerted": 0, "errors": []}
    new_bucket = []

    employers = Employer.query.filter_by(watching=True).all()

    # ---- Per-employer scans ----
    for emp in employers:
        for alias in emp.alias_list:
            started = time.time()
            try:
                raws = scraper.by_employer(alias, min_level=level)
            except ScraperError as e:
                summary["errors"].append(f"{alias}: {e}")
                _log_scan("employer", alias, 0, 0,
                          int((time.time() - started) * 1000), error=str(e))
                continue
            duration_ms = int((time.time() - started) * 1000)
            new_count = _persist_vacancies(raws, employer=emp,
                                           new_bucket=new_bucket,
                                           employers=employers)
            summary["employers_scanned"] += 1
            summary["new"] += new_count
            _log_scan("employer", alias, len(raws), new_count, duration_ms)

    # ---- Keyword scans (surface employers we don't track by name) ----
    for kw in keyword_scans():
        started = time.time()
        try:
            raws = scraper.by_keyword(kw, min_level=level)
        except ScraperError as e:
            summary["errors"].append(f"kw:{kw}: {e}")
            _log_scan("keyword", kw, 0, 0,
                      int((time.time() - started) * 1000), error=str(e))
            continue
        duration_ms = int((time.time() - started) * 1000)
        new_count = _persist_vacancies(raws, employer=None,
                                       new_bucket=new_bucket,
                                       employers=employers)
        summary["keywords_scanned"] += 1
        summary["new"] += new_count
        _log_scan("keyword", kw, len(raws), new_count, duration_ms)

    # ---- Closure detection ----
    try:
        summary["closed"] = _detect_closures()
    except Exception as e:
        logger.error("apprenticeships: closure detection failed: %s", e)
        orm.session.rollback()

    # ---- Alert on new, employer-matched postings only ----
    if new_bucket:
        try:
            scout_notify.alert_new_listings(apprenticeships=new_bucket)
            for row in new_bucket:
                row.alerted = 1
            orm.session.commit()
            summary["alerted"] = len(new_bucket)
        except Exception as e:
            orm.session.rollback()
            logger.error("apprenticeships: alert failed: %s", e)

    # ---- Once-daily closing-soon reminder ----
    try:
        _maybe_send_closing_soon()
    except Exception as e:
        logger.error("apprenticeships: closing-soon failed: %s", e)
        orm.session.rollback()

    logger.info("Apprenticeship scan complete — %d new, %d closed, %d alerted",
                summary["new"], summary["closed"], summary["alerted"])
    return summary
