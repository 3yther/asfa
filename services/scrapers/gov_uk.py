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

Politeness (do not loosen): at most 5 pages per search, a 1s sleep between
page fetches, and a descriptive User-Agent identifying the client.
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
    def __init__(self, user_agent: str = USER_AGENT, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html"})
        self.timeout = timeout

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------
    def by_employer(self, employer_name: str, min_level: int = 4) -> list[RawVacancy]:
        """Search for one employer's live vacancies."""
        params = {
            "searchTerm": employer_name,
            "sort": "AgeAsc",
        }
        return self._search_all_pages(params, min_level)

    def by_keyword(self, keyword: str, min_level: int = 4) -> list[RawVacancy]:
        """Broad keyword search — useful for spotting new employers."""
        params = {
            "searchTerm": keyword,
            "sort": "AgeAsc",
        }
        return self._search_all_pages(params, min_level)

    # ------------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------------
    def _search_all_pages(
        self, params: dict, min_level: int, max_pages: int = 5
    ) -> list[RawVacancy]:
        """Walk pagination. Stop once we hit an empty page or max_pages."""
        results: list[RawVacancy] = []
        seen_refs = set()

        for page in range(1, max_pages + 1):
            p = dict(params)
            if page > 1:
                p["PageNumber"] = page
            page_results = self._search_one_page(p, min_level)
            if not page_results:
                break

            fresh = [r for r in page_results if r.reference not in seen_refs]
            if not fresh:  # we're seeing repeats — pagination exhausted
                break
            for r in fresh:
                seen_refs.add(r.reference)
                results.append(r)

            time.sleep(1.0)  # be polite

        return results

    def _search_one_page(self, params: dict, min_level: int) -> list[RawVacancy]:
        try:
            resp = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ScraperError(f"HTTP error searching gov.uk: {e}") from e

        return self._parse_search_page(resp.text, min_level)

    def _parse_search_page(self, html: str, min_level: int) -> list[RawVacancy]:
        soup = BeautifulSoup(html, "html.parser")

        # Result count sanity check — "0 results found" short-circuits
        title = soup.find("title")
        if title and "0 results found" in title.get_text():
            return []

        cards = soup.find_all("li", class_=re.compile(r"das-search-results__list-item"))
        vacancies: list[RawVacancy] = []
        seen_in_page = set()
        for card in cards:
            v = self._parse_card(card)
            if not v:
                continue
            if v.reference in seen_in_page:
                continue  # multi-location postings render as duplicate cards
            seen_in_page.add(v.reference)
            if v.level is not None and v.level < min_level:
                continue
            vacancies.append(v)
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
