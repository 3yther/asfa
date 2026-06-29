"""Scout — part-time job hunting agent.

Pulls part-time roles across a set of South-East London / North Kent locations
from two real job-search APIs — Reed (https://www.reed.co.uk/developers) and
SerpAPI's Google Jobs engine — filters to a list of target high-street
employers (or anything posted in the last 24h), and stores new finds in the
scout_jobs table.

Best-effort by design: every network step is wrapped defensively — a failed
request yields zero jobs rather than raising, and `scan()` always returns an
integer count so the API can return valid JSON regardless of upstream state.

Required environment variables:
  - REED_API_KEY  — Reed API key, used as the HTTP Basic auth username.
  - SERPAPI_KEY   — SerpAPI key for the google_jobs engine.
Either may be unset; the corresponding source is simply skipped.

Optional — email notification on new finds (Gmail SMTP):
  - SCOUT_EMAIL_USER — Gmail address to send from / authenticate with.
  - SCOUT_EMAIL_PASS — Gmail app password. Both must be set or email is skipped.
"""
import logging
import os
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import requests

try:  # SerpAPI Python client (package: google-search-results)
    from serpapi import GoogleSearch
except ImportError:  # surfaced clearly; SerpAPI source is skipped if missing
    GoogleSearch = None

import database as db

logger = logging.getLogger("asfa.scout")

REED_BASE = "https://www.reed.co.uk/api/1.0/search"
SERPAPI_BASE = "https://serpapi.com/search"

# Email notification (Gmail SMTP) for new finds.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
NOTIFY_EMAIL_TO = "ami.salax08@gmail.com"

# South-East London / North Kent catchment.
LOCATIONS = [
    "Erith", "Bexleyheath", "Bluewater", "Dartford",
    "Charlton", "Woolwich", "Belvedere", "Plumstead",
]

# Reed runs one request per (keyword x location).
REED_KEYWORDS = [
    "part time retail", "sales assistant", "customer service", "team member",
]

# Google Jobs (SerpAPI) runs one combined query per location.
JOB_TYPES = ["retail", "sales assistant", "customer service", "team member"]

# Roles are kept if the employer matches one of these (case-insensitive) OR the
# posting is < 24h old regardless of employer.
TARGET_COMPANIES = [
    "TK Maxx", "NEXT", "Nike", "Ernest Jones", "Farmfoods", "iSmash",
    "UNIQLO", "McDonald's", "H&M", "Zara", "Primark", "River Island",
    "New Look", "Sports Direct", "JD Sports", "Marks & Spencer",
    "Boots", "Superdrug", "Costa", "Greggs",
]
_TARGETS_LC = [c.lower() for c in TARGET_COMPANIES]

# Network timeout (seconds) for upstream API calls.
REQUEST_TIMEOUT = 20


def _company_is_target(company: str) -> bool:
    if not company:
        return False
    c = company.lower()
    return any(t in c for t in _TARGETS_LC)


def _is_recent(posted: str) -> bool:
    """True if a posting looks like it's from the last ~24 hours.

    Handles both the relative strings Google Jobs returns ('Today',
    '3 hours ago', '1 day ago') and the absolute DD/MM/YYYY dates Reed returns.
    """
    if not posted:
        return False
    t = posted.strip().lower()
    if "just posted" in t or "today" in t or "just now" in t:
        return True
    m = re.search(r"(\d+)\s*(hour|hr|minute|min|day)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith(("hour", "hr", "minute", "min")):
            return True
        if unit.startswith("day"):
            return n <= 1
        return False
    # Reed absolute date, e.g. "25/06/2026".
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(posted.strip(), fmt).date()
            return (datetime.now().date() - d) <= timedelta(days=1)
        except ValueError:
            continue
    return False


def _fetch_reed(found_date: str, seen_urls: set, collected: list) -> int:
    """Query Reed for every (keyword x location) and store qualifying jobs."""
    api_key = os.getenv("REED_API_KEY")
    if not api_key:
        logger.info("scout: REED_API_KEY not set — skipping Reed source")
        return 0

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    inserted = 0

    for location in LOCATIONS:
        for keyword in REED_KEYWORDS:
            params = {
                "keywords": keyword,
                "locationName": location,
                "distancefromLocation": 3,
                "fullTime": "false",
                "partTime": "true",
                "minimumDate": yesterday,
            }
            try:
                resp = requests.get(
                    REED_BASE, params=params, auth=(api_key, ""),
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as e:
                logger.warning("scout reed fetch failed (%s/%s): %s",
                               location, keyword, e)
                continue
            if resp.status_code != 200:
                logger.warning("scout reed %s/%s -> HTTP %s",
                               location, keyword, resp.status_code)
                continue
            try:
                results = resp.json().get("results", []) or []
            except ValueError:
                logger.warning("scout reed %s/%s -> invalid JSON",
                               location, keyword)
                continue
            logger.info("scout reed %s/%s -> %d results",
                        location, keyword, len(results))

            for r in results:
                salary = (r.get("salary")
                          or r.get("maximumSalary")
                          or r.get("minimumSalary") or "")
                job = {
                    "title": r.get("jobTitle") or "",
                    "company": r.get("employerName") or "",
                    "location": r.get("locationName") or location,
                    "salary": str(salary) if salary else "",
                    "url": r.get("jobUrl") or "",
                    "posted_date": r.get("date") or "",
                    "description": (r.get("jobDescription") or "").strip(),
                    "source": "reed",
                }
                if _store(job, found_date, seen_urls, collected):
                    inserted += 1
    return inserted


def _fetch_serpapi(found_date: str, seen_urls: set, collected: list) -> int:
    """Query Google Jobs via SerpAPI, one combined query per location."""
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        logger.info("scout: SERPAPI_KEY not set — skipping SerpAPI source")
        return 0
    if GoogleSearch is None:
        logger.warning("scout: google-search-results not installed — "
                       "skipping SerpAPI source")
        return 0

    job_type = " ".join(JOB_TYPES)
    inserted = 0

    for location in LOCATIONS:
        params = {
            "engine": "google_jobs",
            "q": f"part time {job_type} {location}",
            "location": "London, England",
            "chips": "date_posted:today",
            "api_key": api_key,
        }
        try:
            results = GoogleSearch(params).get_dict()
        except Exception as e:
            logger.warning("scout serpapi fetch failed (%s): %s", location, e)
            continue
        if results.get("error"):
            logger.warning("scout serpapi %s -> %s", location, results["error"])
            continue
        jobs = results.get("jobs_results", []) or []
        logger.info("scout serpapi %s -> %d results", location, len(jobs))

        for r in jobs:
            ext = r.get("detected_extensions") or {}
            link = r.get("link") or ""
            if not link:
                apply_opts = r.get("apply_options") or []
                if apply_opts:
                    link = apply_opts[0].get("link") or ""
            job = {
                "title": r.get("title") or "",
                "company": r.get("company_name") or "",
                "location": r.get("location") or location,
                "salary": (r.get("detected_extensions") or {}).get("salary", ""),
                "url": link,
                "posted_date": ext.get("posted_at") or "",
                "description": (r.get("description") or "").strip(),
                "source": r.get("via") or "google_jobs",
            }
            if _store(job, found_date, seen_urls, collected):
                inserted += 1
    return inserted


def _store(job: dict, found_date: str, seen_urls: set, collected: list) -> bool:
    """Apply the target-company / recency filter and insert. Returns True if a
    new row was written. Dedups within this run via seen_urls and across runs
    via the DB's url uniqueness check in add_scout_job."""
    url = job.get("url")
    if not url or url in seen_urls:
        return False
    recent = _is_recent(job.get("posted_date", ""))
    if not (recent or _company_is_target(job.get("company", ""))):
        return False
    seen_urls.add(url)
    inserted = db.add_scout_job(
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        salary=job.get("salary", ""),
        job_type="part time",
        url=url,
        description=job.get("description", ""),
        source=job.get("source", ""),
        posted_date=job.get("posted_date", ""),
        found_date=found_date,
        is_new=1 if recent else 0,
    )
    if inserted:
        collected.append(job)
    return inserted


def _send_email(jobs: list) -> bool:
    """Email the list of newly-found jobs via Gmail SMTP. No-op (returns False)
    unless both SCOUT_EMAIL_USER and SCOUT_EMAIL_PASS are set and jobs is
    non-empty. Best-effort: any failure is logged and swallowed."""
    if not jobs:
        return False
    user = os.getenv("SCOUT_EMAIL_USER")
    password = os.getenv("SCOUT_EMAIL_PASS")
    if not (user and password):
        logger.info("scout: SCOUT_EMAIL_USER/SCOUT_EMAIL_PASS not set — "
                    "skipping email notification")
        return False

    n = len(jobs)
    subject = f"SCOUT — {n} new job{'s' if n != 1 else ''} found"
    lines = []
    for j in jobs:
        lines.append(
            f"• {j.get('title', '') or 'Untitled'} — {j.get('company', '') or 'Unknown'}\n"
            f"  {j.get('location', '') or '—'}  |  posted: {j.get('posted_date', '') or 'n/a'}\n"
            f"  {j.get('url', '') or '(no link)'}"
        )
    body = f"Scout found {n} new part-time role{'s' if n != 1 else ''}:\n\n" \
           + "\n\n".join(lines)

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = NOTIFY_EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(user, [NOTIFY_EMAIL_TO], msg.as_string())
        logger.info("scout: emailed %d new job(s) to %s", n, NOTIFY_EMAIL_TO)
        return True
    except Exception as e:
        logger.warning("scout email send failed: %s", e)
        return False


def scan_jobs_reed(keywords, location="", limit=20) -> list:
    """Param-driven Reed search used by the skill executor (the `scan_jobs`
    skill). Unlike scan(), this honors the caller's keywords/location instead of
    the fixed REED_KEYWORDS/LOCATIONS lists, does not apply the
    target-company/recency filter, and returns the matched jobs as a list of
    dicts (newest results first, capped at `limit`). Newly-seen jobs are also
    persisted via add_scout_job (deduped by url) so the dashboard sees them.

    keywords may be a string or a list of strings. Best-effort: returns [] if
    REED_API_KEY is unset or every request fails. Salary is returned as a
    numeric annual figure (0 if unknown) so filter_results can compare it.
    """
    api_key = os.getenv("REED_API_KEY")
    if not api_key:
        logger.info("scout.scan_jobs_reed: REED_API_KEY not set — returning []")
        return []

    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k for k in (keywords or []) if k] or ["retail"]
    locations = [location] if location else LOCATIONS

    found_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    collected: list = []
    seen_urls: set = set()

    for loc in locations:
        for keyword in keywords:
            if len(collected) >= limit:
                return collected[:limit]
            params = {
                "keywords": keyword,
                "locationName": loc,
                "distancefromLocation": 10,
                "partTime": "true",
            }
            try:
                resp = requests.get(
                    REED_BASE, params=params, auth=(api_key, ""),
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as e:
                logger.warning("scout scan_jobs_reed fetch failed (%s/%s): %s",
                               loc, keyword, e)
                continue
            if resp.status_code != 200:
                logger.warning("scout scan_jobs_reed %s/%s -> HTTP %s",
                               loc, keyword, resp.status_code)
                continue
            try:
                results = resp.json().get("results", []) or []
            except ValueError:
                logger.warning("scout scan_jobs_reed %s/%s -> invalid JSON",
                               loc, keyword)
                continue

            for r in results:
                url = r.get("jobUrl") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                salary_num = r.get("maximumSalary") or r.get("minimumSalary") or 0
                try:
                    salary_num = float(salary_num)
                except (TypeError, ValueError):
                    salary_num = 0.0
                posted = r.get("date") or ""
                job = {
                    "id": r.get("jobId"),
                    "title": r.get("jobTitle") or "",
                    "company": r.get("employerName") or "",
                    "location": r.get("locationName") or loc,
                    "salary": salary_num,
                    "url": url,
                    "posted_date": posted,
                    "source": "reed",
                }
                # Persist (deduped by url) so finds surface on the dashboard.
                db.add_scout_job(
                    title=job["title"], company=job["company"],
                    location=job["location"],
                    salary=str(salary_num) if salary_num else "",
                    job_type="part time", url=url,
                    description=(r.get("jobDescription") or "").strip(),
                    source="reed", posted_date=posted, found_date=found_date,
                    is_new=1 if _is_recent(posted) else 0,
                )
                collected.append(job)
                if len(collected) >= limit:
                    return collected[:limit]
    return collected[:limit]


def apply_for_job(job_id, cv_version="default", job=None) -> dict:
    """Record an application to a previously-scanned job. Marks the scout_jobs
    row applied (if found) and inserts a scout_applications row. Returns
    {"id": application/job id, "status": "submitted"}.

    `job`, if supplied, is the full job dict (company/title/location) threaded
    from an upstream scan/filter step — it's preferred for the application
    record. Otherwise job_id is matched against stored scout_jobs row ids. If
    neither resolves to job details, a bare application keyed by job_id is still
    recorded so the action is auditable.
    """
    applied_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not isinstance(job, dict):
        job = next(
            (j for j in db.get_scout_jobs() if str(j.get("id")) == str(job_id)),
            None,
        )
    if job:
        db.add_scout_application(
            company=job.get("company", ""),
            role=job.get("title", ""),
            location=job.get("location", ""),
            method=f"scout/cv:{cv_version}",
            applied_date=applied_date,
            status="submitted",
        )
        db.mark_scout_job_applied(job.get("id"))
        return {"id": job.get("id"), "status": "submitted"}

    db.add_scout_application(
        company="", role=str(job_id), location="",
        method=f"scout/cv:{cv_version}", applied_date=applied_date,
        status="submitted",
    )
    return {"id": job_id, "status": "submitted"}


def scan() -> int:
    """Run a full pass over both sources. Saves new, non-duplicate, qualifying
    jobs, emails them if any were found, and returns the count inserted."""
    found_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seen_urls = set()
    collected: list = []

    new_count = 0
    new_count += _fetch_reed(found_date, seen_urls, collected)
    new_count += _fetch_serpapi(found_date, seen_urls, collected)

    if collected:
        _send_email(collected)

    logger.info("Scout scan complete — %d new jobs found", new_count)
    return new_count
