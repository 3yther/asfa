"""Scraper for findapprenticeship.service.gov.uk

Ported from the standalone Apprenticeship Radar project. The parsing internals
are deliberately unchanged — the selectors, the level regex, the per-page
reference dedup and the pagination stop conditions are load-bearing and already
tested against live gov.uk markup. Only the docstring and the default
User-Agent were adapted for Scout.

Two search modes:
    1. by_employer(name)  — search results filtered to one employer
    2. by_keyword(term)   — broad keyword scan (catches employer-name-agnostic postings)

Both return a list of `RawVacancy` dataclasses. RawVacancy is an internal DTO:
the caller (`services/apprenticeships.py`) matches each one to an Employer and
converts it to a ScoutJob row at the persist boundary, which is where Scout's
own conventions take over.

Parsing strategy: BeautifulSoup on the search results page. The card layout
is stable HTML with class names starting `das-search-results` / `faa-`.
If gov.uk changes its markup, only the CSS selectors here need updating.

Politeness (do not loosen): at most 5 pages per search, a minimum 1s gap
between *every* HTTP request, and a descriptive User-Agent identifying the
client.

The gap is enforced at the request layer (`_throttle`), not in the pagination
loop. A scan issues one search per employer alias plus one per keyword, and a
per-loop sleep only spaced out pages *within* a search — consecutive searches
still fired their first request back-to-back, so a scan with many aliases sent
a burst of page-1 requests with no gap at all. Throttling every request against
the previous one closes that, and costs nothing when DB work between searches
has already used up the interval.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.findapprenticeship.service.gov.uk"
SEARCH_URL = BASE_URL + "/apprenticeships"

# Descriptive, so gov.uk can identify (and if need be, contact about) the client.
USER_AGENT = "ASFA-Scout/1.0 (+https://github.com/3yther/asfa; apprenticeship watcher)"

LEVEL_RE = re.compile(r"level\s+(\d)", re.I)
VAC_REF_RE = re.compile(r"/apprenticeship/(VAC\d+)")

# The site's own result count, from the <title> ("47 results found").
#
# Anchored at a word boundary and captured as a number ON PURPOSE. This used to
# be `if "0 results found" in title`, a plain substring test — which is also true
# of "10 results found", "20", "50", "100", "1000"... Any total ending in a zero
# digit was read as "no results" and the whole page was discarded. Where the
# title carries the total (identical on every page) that silently zeroed the
# entire search. See test_zero_results_detection_is_not_a_substring_match.
RESULT_COUNT_RE = re.compile(r"\b([\d,]+)\s+results?\s+found", re.I)

# Minimum seconds between any two requests this scraper makes. Floor, not a
# target: see the module docstring.
MIN_REQUEST_INTERVAL = 1.0


@dataclass
class RawVacancy:
    reference: str
    title: str
    employer_name_raw: str
    location: str = ""
    wage: str = ""
    training_course: str = ""
    level: Optional[int] = None
    closing_text: str = ""
    closing_date: Optional[date] = None
    start_date: Optional[date] = None
    posted_text: str = ""
    url: str = ""

    def as_dict(self):
        d = asdict(self)
        if self.closing_date:
            d["closing_date"] = self.closing_date.isoformat()
        if self.start_date:
            d["start_date"] = self.start_date.isoformat()
        return d


class GovUkScraper:
    def __init__(self, user_agent: str = USER_AGENT, timeout: int = 20,
                 min_interval: float = MIN_REQUEST_INTERVAL):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html"})
        self.timeout = timeout
        self.min_interval = min_interval
        # None until the first request, so a fresh scraper doesn't stall a second.
        self._last_request_at: Optional[float] = None

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------
    def by_employer(self, employer_name: str, min_level: int = 4,
                    stats: Optional[dict] = None) -> list[RawVacancy]:
        """Search for one employer's live vacancies."""
        return self._search_all_pages(
            self.search_params(employer_name), min_level, stats=stats)

    def by_keyword(self, keyword: str, min_level: int = 4,
                   stats: Optional[dict] = None) -> list[RawVacancy]:
        """Broad keyword search — useful for spotting new employers."""
        return self._search_all_pages(
            self.search_params(keyword), min_level, stats=stats)

    @staticmethod
    def search_params(term: str) -> dict:
        """The query this scraper sends for a search term.

        One place, so what goes on the wire can be asserted in a test and
        printed by the baseline tool rather than reconstructed by reading
        `by_keyword`. Today that is the search term and a fixed sort, and
        nothing else — no location, distance, level or route filter. Page 1
        carries no page parameter; `_search_all_pages` adds PageNumber for 2+.
        """
        return {"searchTerm": term, "sort": "AgeAsc"}

    # ------------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        """Block until `min_interval` has passed since the previous request.

        Uses a monotonic clock so a system clock adjustment mid-scan can't turn
        the wait into a long sleep (or skip it).
        """
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _search_all_pages(
        self, params: dict, min_level: int, max_pages: int = 5,
        stats: Optional[dict] = None
    ) -> list[RawVacancy]:
        """Walk pagination. Stop once we hit an empty page or max_pages."""
        results: list[RawVacancy] = []
        seen_refs = set()

        for page in range(1, max_pages + 1):
            p = dict(params)
            if page > 1:
                p["PageNumber"] = page
            page_results = self._search_one_page(p, min_level, stats=stats)
            if stats is not None:
                stats["pages_fetched"] = stats.get("pages_fetched", 0) + 1
            if not page_results:
                if stats is not None:
                    stats.setdefault("stopped_because", "empty_page")
                break

            fresh = [r for r in page_results if r.reference not in seen_refs]
            if not fresh:  # we're seeing repeats — pagination exhausted
                if stats is not None:
                    stats.setdefault("stopped_because", "repeats")
                break
            for r in fresh:
                seen_refs.add(r.reference)
                results.append(r)
        else:
            if stats is not None:
                # Ran the full range without breaking: the cap is what stopped
                # us, so the site may well have more.
                stats.setdefault("stopped_because", "page_cap")

        return results

    def _search_one_page(self, params: dict, min_level: int,
                         stats: Optional[dict] = None) -> list[RawVacancy]:
        self._throttle()
        try:
            resp = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ScraperError(f"HTTP error searching gov.uk: {e}") from e

        return self._parse_search_page(resp.text, min_level, stats=stats)

    @staticmethod
    def reported_total(html: str):
        """The total the site itself claims, or None if the title doesn't say.

        This is the number to compare a scrape against: if gov.uk reports 300
        results and we collected 43, the gap is ours, not the site's.
        """
        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("title")
        if not title:
            return None
        m = RESULT_COUNT_RE.search(title.get_text())
        return int(m.group(1).replace(",", "")) if m else None

    def _parse_search_page(self, html: str, min_level: int,
                           stats: Optional[dict] = None) -> list[RawVacancy]:
        """Parse one results page.

        `stats`, when given, is accumulated in place so a caller can see why a
        page yielded what it did rather than inferring it. A silent drop is the
        failure mode that made the coverage shortfall impossible to diagnose:
        cards that do not match the expected link shape simply vanished, which
        looks identical to the site having fewer listings.
        """
        soup = BeautifulSoup(html, "html.parser")

        total = self.reported_total(html)
        if stats is not None and total is not None:
            stats["reported_total"] = total

        # The site explicitly says there are none. Parsed as a number, not a
        # substring — see RESULT_COUNT_RE.
        if total == 0:
            return []

        cards = soup.find_all("li", class_=re.compile(r"das-search-results__list-item"))
        if stats is not None:
            stats["cards_seen"] = stats.get("cards_seen", 0) + len(cards)

        vacancies: list[RawVacancy] = []
        seen_in_page = set()
        for card in cards:
            v = self._parse_card(card)
            if not v:
                # No parseable /apprenticeship/VAC… link. Counted, because a
                # reference format this regex doesn't cover would drop real
                # listings here without a trace.
                if stats is not None:
                    stats["dropped_no_ref"] = stats.get("dropped_no_ref", 0) + 1
                    _note_unmatched(stats, card)
                continue
            if v.reference in seen_in_page:
                continue  # multi-location postings render as duplicate cards
            seen_in_page.add(v.reference)
            if v.level is not None and v.level < min_level:
                if stats is not None:
                    stats["dropped_below_level"] = stats.get("dropped_below_level", 0) + 1
                continue
            if stats is not None and v.level is None:
                stats["level_unparsed"] = stats.get("level_unparsed", 0) + 1
            vacancies.append(v)
        if stats is not None:
            stats["kept"] = stats.get("kept", 0) + len(vacancies)
        return vacancies

    def _parse_card(self, card) -> Optional[RawVacancy]:
        # Reference from URL
        link = card.find("a", href=VAC_REF_RE)
        if not link:
            return None
        m = VAC_REF_RE.search(link["href"])
        if not m:
            return None
        reference = m.group(1)
        url = BASE_URL + link["href"]

        # Title
        title = link.get_text(strip=True)

        # The card has a series of <p class="govuk-body"> paragraphs after the h2.
        # First one is employer name (unlabelled), rest have <b>Label</b> value.
        paragraphs = card.find_all("p", class_=lambda c: c and "govuk-body" in c)
        employer_name_raw = ""
        location = ""
        wage = ""
        training_course = ""
        closing_text = ""
        posted_text = ""
        start_date_str = ""

        for i, p in enumerate(paragraphs):
            text = p.get_text(" ", strip=True)
            b = p.find("b")
            if not b:
                # First unlabelled = employer, second = location (grey text)
                if not employer_name_raw:
                    employer_name_raw = text
                elif not location:
                    location = text
                elif text.startswith("Posted "):
                    posted_text = text
                continue

            label = b.get_text(strip=True).lower()
            value = text.replace(b.get_text(strip=True), "", 1).strip()

            if "start date" in label:
                start_date_str = value
            elif "training course" in label:
                training_course = value
            elif "wage" in label:
                wage = value

        # Closing info sits in its own paragraph without a <b> label
        closing_p = card.find(
            "p", string=re.compile(r"Closes|Closed", re.I)
        )
        if not closing_p:
            for p in paragraphs:
                t = p.get_text(" ", strip=True)
                if t.startswith("Closes") or "closed" in t.lower()[:20]:
                    closing_p = p
                    break
        if closing_p:
            closing_text = closing_p.get_text(" ", strip=True)

        # Posted text if we missed it
        if not posted_text:
            posted_span = card.find(string=re.compile(r"Posted\s+\d"))
            if posted_span:
                posted_text = posted_span.strip()

        # Level from training course text
        level = None
        if training_course:
            m = LEVEL_RE.search(training_course)
            if m:
                level = int(m.group(1))

        closing_date = _parse_uk_date(closing_text)
        start_date = _parse_uk_date(start_date_str)

        return RawVacancy(
            reference=reference,
            title=title,
            employer_name_raw=employer_name_raw,
            location=location,
            wage=wage,
            training_course=training_course,
            level=level,
            closing_text=closing_text,
            closing_date=closing_date,
            start_date=start_date,
            posted_text=posted_text,
            url=url,
        )


class ScraperError(Exception):
    pass


def _note_unmatched(stats: dict, card) -> None:
    """Record a sample of the hrefs on a dropped card.

    The point is to make an unknown reference format visible instead of
    guessable. VAC_REF_RE only matches `/apprenticeship/VAC<digits>`; if gov.uk
    also serves numeric-only references, or a different path, those listings are
    dropped silently and a scrape just looks small. A handful of real hrefs
    answers that immediately.
    """
    samples = stats.setdefault("unmatched_href_samples", [])
    if len(samples) >= 5:
        return
    for a in card.find_all("a", href=True):
        href = a["href"]
        if "apprentice" in href.lower() and href not in samples:
            samples.append(href)
            if len(samples) >= 5:
                return


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_DATE_RE = re.compile(
    r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+(\d{4})",
    re.I,
)


def _parse_uk_date(text: str) -> Optional[date]:
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month_name, year = m.groups()
    try:
        return date(int(year), _MONTHS[month_name.lower()], int(day))
    except (KeyError, ValueError):
        return None
