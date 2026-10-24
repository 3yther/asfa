"""Fills in the detail-panel fields for listings Scout has already kept.

The search results page carries a title, employer, location, level and course,
and nothing else. Wage, hours, start date, duration, closing date and the
vacancy's own description live on each listing's own page, so the panel needs
one extra request per listing.

THE CONDITIONS THIS RUNS UNDER
------------------------------
Approved on these terms, and each is enforced in code rather than left to a
caller to remember:

  1. only listings Scout has KEPT, never the whole search — the batch is driven
     off `scout_jobs`, so a row has to already exist to be eligible, and the
     URL is re-checked against the vacancy-detail shape before any request;
  2. one second between requests, the existing identifying User-Agent, no
     login, no cookies, no account pages — the spacing and the UA come from
     the same GovUkScraper the search uses, cookies are blocked at the policy
     level, and a URL that is not a vacancy detail page is refused without a
     request;
  3. each page is cached and never re-fetched within 24 hours
     (`database.DETAIL_CACHE_HOURS`);
  4. a 429 or any 5xx STOPS THE BATCH. There is no retry loop — the one
     behaviour that would make this impolite;
  5. only the six panel fields are stored. The HTML is parsed and discarded;
     there is no column for it;
  6. personal use. Nothing fetched here is exposed publicly.

Everything is best-effort per listing: a single page that will not parse
records a status and the batch carries on, because one odd vacancy should not
stop the other forty.
"""
import logging

import database as db
from services.scrapers.gov_uk import (DETAIL_URL_RE, DetailFetchBackoff,
                                      GovUkScraper, ScraperError)

logger = logging.getLogger("asfa.scout_detail")

# Listings per run. Small on purpose: at one request a second this is ~25s of
# polite traffic, and the scheduled job comes round again.
DEFAULT_BATCH = 25


def fetch_missing_details(limit: int = DEFAULT_BATCH, scraper=None) -> dict:
    """Fetch detail pages for kept listings that need one. Never raises.

    Returns a summary dict:
        {"considered", "fetched", "failed", "refused", "stopped_early",
         "stop_reason", "fields_filled"}
    """
    summary = {"considered": 0, "fetched": 0, "failed": 0, "refused": 0,
               "stopped_early": False, "stop_reason": None,
               "fields_filled": 0}

    try:
        pending = db.scout_details_needing_fetch(limit=limit)
    except Exception as e:
        logger.error("scout_detail: could not list pending listings: %s", e)
        summary["stop_reason"] = "query_failed"
        return summary

    summary["considered"] = len(pending)
    if not pending:
        return summary

    scraper = scraper or GovUkScraper()

    for row in pending:
        ref, url = row.get("external_ref"), (row.get("url") or "").strip()

        # Belt and braces: the batch query only returns kept listings, and this
        # re-checks the shape. An off-site cross-post (Civil Service Jobs and
        # the like) is someone else's site and is not ours to crawl.
        if not DETAIL_URL_RE.match(url):
            summary["refused"] += 1
            db.save_scout_job_detail(ref, {}, status="refused_not_detail_url")
            continue

        try:
            fields = scraper.fetch_detail(url)
        except DetailFetchBackoff as e:
            # Stop the whole batch. Do not retry, do not move to the next one.
            summary["stopped_early"] = True
            summary["stop_reason"] = str(e)
            logger.warning("scout_detail: backing off and stopping — %s", e)
            break
        except ScraperError as e:
            summary["failed"] += 1
            db.save_scout_job_detail(ref, {}, status="error")
            logger.info("scout_detail: %s could not be fetched (%s)", ref, e)
            continue
        except Exception as e:                      # pragma: no cover
            summary["failed"] += 1
            db.save_scout_job_detail(ref, {}, status="error")
            logger.warning("scout_detail: unexpected error on %s: %s", ref, e)
            continue

        filled = sum(1 for key in db.DETAIL_FIELDS if fields.get(key))
        summary["fields_filled"] += filled
        # "ok" even when nothing parsed: the page WAS fetched, and the cache
        # has to remember that so the next run does not immediately try again.
        db.save_scout_job_detail(
            ref, fields, status="ok" if filled else "ok_empty")
        summary["fetched"] += 1

    logger.info("scout_detail: considered %d, fetched %d, failed %d, "
                "refused %d%s", summary["considered"], summary["fetched"],
                summary["failed"], summary["refused"],
                " (stopped early)" if summary["stopped_early"] else "")
    return summary


def detail_for(external_ref: str) -> dict:
    """The panel's fields for one listing, with None where nothing is known.

    The panel renders None as a muted dash. A field is never invented or
    carried over from another listing.
    """
    row = db.get_scout_job_detail(external_ref) or {}
    return {key: row.get(key) or None for key in db.DETAIL_FIELDS}
