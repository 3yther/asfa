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
from typing import NamedTuple, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from services import scout_config


BASE_URL = "https://www.findapprenticeship.service.gov.uk"
SEARCH_URL = BASE_URL + "/apprenticeships"

# Descriptive, so gov.uk can identify (and if need be, contact about) the client.
USER_AGENT = "ASFA-Scout/1.0 (+https://github.com/3yther/asfa; apprenticeship watcher)"

LEVEL_RE = re.compile(r"level\s+(\d)", re.I)

# The reference format this scraper was written against.
VAC_REF_RE = re.compile(r"/apprenticeship/(VAC\d+)")

# Any trailing path segment that looks like an id (has a digit in it). Used to
# derive a stable reference from a detail link whose shape VAC_REF_RE does not
# cover, so an unrecognised format costs us the tidy reference and NOT the
# listing. Dropping 16 of 21 "software developer" results for failing a regex
# is the single largest source of the coverage gap measured on 2026-10-23.
ID_SEGMENT_RE = re.compile(r"^(?=[^/]*\d)([A-Za-z0-9][A-Za-z0-9._-]*)$")

# Hrefs that are never a vacancy's identity.
_NON_DETAIL_HREF_RE = re.compile(
    r"^\s*(#|mailto:|tel:|javascript:|data:)", re.I)

# Where a listing's detail page lives. gov.uk cross-posts public-sector roles to
# external recruitment systems, and those links carry their identity in the
# QUERY STRING, not the path.
HOST_FINDAPPRENTICESHIP = "findapprenticeship.service.gov.uk"
HOST_CIVIL_SERVICE = "civilservicejobs.service.gov.uk"

SOURCE_FINDAPPRENTICESHIP = "findapprenticeship"
SOURCE_CIVIL_SERVICE = "civil_service"
SOURCE_OTHER = "other"

# Query parameters that identify a vacancy on an external system, most specific
# first. `jcode` is Civil Service Jobs: every one of the 16 dropped "software
# developer" cards was /csr/viewjob.cgi?jcode=<n>, so stripping the query made
# them all the same reference and the in-page dedup kept exactly one.
EXTERNAL_ID_PARAMS = ("jcode", "vacancyref", "vacancyid", "jobid", "jobref",
                      "requisitionid", "id")

# Analytics parameters, which differ between two links to the SAME vacancy and
# so must not reach the reference.
_TRACKING_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                    "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid",
                    "_ga", "msclkid")

# gov.uk appends this to the link text of an off-site listing. It is screen
# reader guidance, not part of the job title.
OPENS_IN_NEW_TAB_RE = re.compile(
    r"\s*\(\s*opens?\s+in\s+(?:a\s+)?new\s+(?:tab|window)\s*\)\s*", re.I)


def clean_title(text: str) -> str:
    """Strip gov.uk's "(opens in new tab)" suffix and tidy whitespace."""
    return " ".join(OPENS_IN_NEW_TAB_RE.sub(" ", text or "").split())


def _source_for_host(host: str) -> str:
    host = (host or "").lower().removeprefix("www.")
    if not host or host.endswith(HOST_FINDAPPRENTICESHIP):
        return SOURCE_FINDAPPRENTICESHIP
    if host.endswith(HOST_CIVIL_SERVICE):
        return SOURCE_CIVIL_SERVICE
    return SOURCE_OTHER


def _canonical_query(query: str) -> str:
    """Query string with tracking params dropped and the rest ordered.

    Ordering matters: two links to one vacancy that differ only in parameter
    order must produce one reference, or the same role alerts twice.
    """
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if k.lower() not in _TRACKING_PARAMS]
    return urlencode(sorted(pairs))

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
    # Which system hosts the detail page: findapprenticeship / civil_service /
    # other. gov.uk cross-posts public-sector roles off-site, and those listings
    # carry no level or training course, so a caller has to be able to tell them
    # apart rather than treat a missing level as bad parsing.
    source: str = SOURCE_FINDAPPRENTICESHIP
    # Every location this vacancy was listed under. gov.uk renders a
    # multi-location vacancy as one card PER LOCATION and counts each of them in
    # "N results found", so the extra cards are the site repeating itself, not
    # separate jobs. They are merged into one listing and the locations kept
    # here, rather than discarded — which is what the old per-page dedup did.
    # `location` stays the first one seen, so existing callers are unaffected.
    locations: tuple = ()

    def __post_init__(self):
        # Seed from `location` so a single-location vacancy still reports
        # locations == (its location,) and location_count == 1.
        if not self.locations and self.location:
            self.locations = (self.location,)

    @property
    def location_count(self) -> int:
        return len(self.locations)

    def add_location(self, location: str) -> bool:
        """Record another location for this same vacancy. True if it was new."""
        location = (location or "").strip()
        if not location or location in self.locations:
            return False
        self.locations = self.locations + (location,)
        if not self.location:
            self.location = location
        return True

    def as_dict(self):
        d = asdict(self)
        if self.closing_date:
            d["closing_date"] = self.closing_date.isoformat()
        if self.start_date:
            d["start_date"] = self.start_date.isoformat()
        d["locations"] = list(self.locations)
        d["location_count"] = self.location_count
        return d


class GovUkScraper:
    def __init__(self, user_agent: str = USER_AGENT, timeout: int = 20,
                 min_interval: float = MIN_REQUEST_INTERVAL,
                 max_pages: Optional[int] = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html"})
        self.timeout = timeout
        self.min_interval = min_interval
        # Read from config at construction, so one scan uses one cap throughout
        # even if the config file is edited mid-run.
        self.max_pages = (max_pages if max_pages is not None
                          else scout_config.max_pages())
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
        self, params: dict, min_level: int, max_pages: Optional[int] = None,
        stats: Optional[dict] = None
    ) -> list[RawVacancy]:
        """Walk pagination, newest first, until there is nothing left to fetch.

        Stop conditions, in the order they are checked:

          "empty_page"        a page returned no cards at all;
          "identical_page"    this page is the same cards in the same order as
                              the previous one, so paging is having no effect
                              and the next request would repeat it. Identity is
                              (reference, locations) per card, NOT reference
                              alone: a page carrying further LOCATIONS for
                              vacancies already seen has the same references
                              but is a different page with real slots behind
                              it, which is the case that broke the old rule;
          "reached_site_total" we have fetched as many result slots as the site
                              says exist, so there is provably nothing more;
          "page_cap"          `max_pages` reached — the site may well have more.

        The old condition was "this page added no NEW REFERENCES", which is
        wrong whenever a page is full of additional LOCATIONS for vacancies
        already seen: that page adds no reference but is a different page with
        real slots behind it. Measured on the "software" search, it stopped
        after 20 of the site's 32 slots and lost 12 vacancies. Identity of the
        page itself is the only safe signal, because gov.uk serves the same page
        for an out-of-range PageNumber rather than an empty one.
        """
        if max_pages is None:
            max_pages = self.max_pages
        by_ref: dict[str, RawVacancy] = {}
        order: list[str] = []
        slots_fetched = 0
        reported_total = None
        previous_page_identity = None

        for page in range(1, max_pages + 1):
            p = dict(params)
            if page > 1:
                p["PageNumber"] = page

            # Per-page stats, merged into the caller's dict afterwards, so the
            # page's own slot count can be read here instead of being inferred
            # from a running total.
            page_stats: dict = {}
            page_results = self._search_one_page(p, min_level, stats=page_stats)
            slots_fetched += page_stats.get("cards_seen", 0)
            if page_stats.get("reported_total") is not None:
                reported_total = page_stats["reported_total"]
            if stats is not None:
                _merge_stats(stats, page_stats)
                stats["pages_fetched"] = stats.get("pages_fetched", 0) + 1
                stats["slots_fetched"] = slots_fetched

            if not page_results:
                if stats is not None:
                    stats.setdefault("stopped_because", "empty_page")
                break

            page_identity = [(r.reference, r.locations) for r in page_results]

            for r in page_results:
                existing = by_ref.get(r.reference)
                if existing is not None:
                    # A multi-location vacancy can straddle a page boundary, so
                    # the merge has to happen here too, not only within a page.
                    for loc in r.locations or ((r.location,) if r.location else ()):
                        if existing.add_location(loc) and stats is not None:
                            stats["extra_locations"] = stats.get("extra_locations", 0) + 1
                    continue
                by_ref[r.reference] = r
                order.append(r.reference)

            if page_identity == previous_page_identity:
                # The same page served twice: paging has stopped working.
                if stats is not None:
                    stats.setdefault("stopped_because", "identical_page")
                break
            previous_page_identity = page_identity

            if reported_total is not None and slots_fetched >= reported_total:
                # Every slot the site claims is accounted for. Fetching further
                # pages can only repeat what we have.
                if stats is not None:
                    stats.setdefault("stopped_because", "reached_site_total")
                break
        else:
            if stats is not None:
                # Ran the full range without breaking: the cap is what stopped
                # us, so the site may well have more.
                stats.setdefault("stopped_because", "page_cap")

        return [by_ref[r] for r in order]

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

        # Keyed by reference so a vacancy repeated once per location becomes one
        # listing carrying every location, rather than one listing and N-1
        # silently discarded cards.
        in_page: dict[str, RawVacancy] = {}
        order: list[str] = []
        for card in cards:
            v = self._parse_card(card, stats=stats)
            if not v:
                # No parseable detail link at all. Counted, because a link shape
                # we don't cover would drop real listings without a trace.
                if stats is not None:
                    stats["dropped_no_ref"] = stats.get("dropped_no_ref", 0) + 1
                    _note_unmatched(stats, card)
                continue
            existing = in_page.get(v.reference)
            if existing is not None:
                # Same vacancy, another location. The site counted this card in
                # its result total, so it consumed a result slot.
                added = existing.add_location(v.location)
                if stats is not None:
                    stats["duplicate_cards"] = stats.get("duplicate_cards", 0) + 1
                    if added:
                        stats["extra_locations"] = stats.get("extra_locations", 0) + 1
                continue
            if v.level is not None and v.level < min_level:
                if stats is not None:
                    stats["dropped_below_level"] = stats.get("dropped_below_level", 0) + 1
                continue
            if stats is not None and v.level is None:
                stats["level_unparsed"] = stats.get("level_unparsed", 0) + 1
            in_page[v.reference] = v
            order.append(v.reference)
        vacancies = [in_page[r] for r in order]
        if stats is not None:
            stats["kept"] = stats.get("kept", 0) + len(vacancies)
        return vacancies

    def _parse_card(self, card, stats: Optional[dict] = None) -> Optional[RawVacancy]:
        # Reference from the card's title link. Prefer the VAC form this scraper
        # was built for; otherwise derive a stable id rather than discard the
        # listing (see reference_from_href).
        link = card.find("a", href=VAC_REF_RE) or _primary_link(card)
        if not link:
            return None
        reference, kind, source = reference_from_href(link.get("href", ""))
        if not reference:
            return None
        if stats is not None:
            kinds = stats.setdefault("ref_kinds", {})
            kinds[kind] = kinds.get(kind, 0) + 1
            sources = stats.setdefault("sources", {})
            sources[source] = sources.get(source, 0) + 1

        # urljoin handles both a site-relative href and an absolute one, so a
        # fully-qualified link can't become BASE_URL + "https://...".
        url = urljoin(BASE_URL + "/", link["href"].strip())

        # Title, less gov.uk's "(opens in new tab)" screen-reader suffix.
        title = clean_title(link.get_text(strip=True))

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
            source=source,
        )


class ScraperError(Exception):
    pass


# Keys accumulated by summing; everything else is merged by rule below.
_COUNTER_KEYS = ("cards_seen", "kept", "dropped_no_ref", "dropped_below_level",
                 "level_unparsed", "duplicate_cards", "extra_locations")


def _merge_stats(target: dict, page: dict) -> None:
    """Fold one page's stats into the per-search totals.

    `_search_all_pages` gives each page its own dict so it can read that page's
    slot count, then merges here. Counters add, the reported total is the
    latest seen, and the dicts and sample list are combined rather than
    replaced.
    """
    for key in _COUNTER_KEYS:
        if key in page:
            target[key] = target.get(key, 0) + page[key]
    if page.get("reported_total") is not None:
        target["reported_total"] = page["reported_total"]
    for key in ("ref_kinds", "sources"):
        if key in page:
            merged = dict(target.get(key, {}))
            for k, v in page[key].items():
                merged[k] = merged.get(k, 0) + v
            target[key] = merged
    if page.get("unmatched_href_samples"):
        samples = target.setdefault("unmatched_href_samples", [])
        for href in page["unmatched_href_samples"]:
            if href not in samples and len(samples) < 8:
                samples.append(href)


def _note_unmatched(stats: dict, card, limit: int = 8) -> None:
    """Record EVERY href on a dropped card, unfiltered.

    The previous version kept only hrefs containing "apprentice" — it filtered
    by a guess about the very format it existed to discover. A card linking to
    e.g. /vacancy/<id> was dropped AND left unsampled, so the diagnostic read
    "16 dropped, 0 samples" and said nothing. Record everything and let the
    reader judge; a handful of raw hrefs is cheap.
    """
    samples = stats.setdefault("unmatched_href_samples", [])
    for a in card.find_all("a", href=True):
        if len(samples) >= limit:
            return
        href = (a["href"] or "").strip()
        if href and href not in samples:
            samples.append(href)


def _primary_link(card):
    """The anchor that identifies this card: its title link.

    Prefers an anchor inside a heading, which is where the job title sits in
    gov.uk's card markup. Falls back to the first anchor with real text that is
    not a fragment or mailto, so a "save this vacancy" control or an icon-only
    link cannot become the listing's identity.
    """
    for level in ("h1", "h2", "h3", "h4"):
        for heading in card.find_all(level):
            a = heading.find("a", href=True)
            if a and not _NON_DETAIL_HREF_RE.match(a["href"] or ""):
                return a
    for a in card.find_all("a", href=True):
        href = a["href"] or ""
        if _NON_DETAIL_HREF_RE.match(href):
            continue
        if a.get_text(strip=True):
            return a
    return None


class LinkIdentity(NamedTuple):
    """How a card's detail link identifies its vacancy.

    `kind` is reported in the scrape stats so an unexpected link shape shows up
    as a count rather than as a quiet shortfall:

      "vac"    /apprenticeship/VAC1000123                  -> VAC1000123
      "id"     /vacancy/1000284512                         -> 1000284512
      "jcode"  …/viewjob.cgi?jcode=2017911                 -> jcode:2017911
      "url"    external, no recognised id param            -> host/path?query
      "path"   on-site, no id segment at all               -> the path
    """
    reference: Optional[str]
    kind: Optional[str]
    source: str = SOURCE_FINDAPPRENTICESHIP


def reference_from_href(href: str) -> LinkIdentity:
    """Identify the vacancy a detail link points at.

    On-site links keep their existing treatment. For an EXTERNAL link the query
    string is part of the identity and must not be discarded: gov.uk cross-posts
    public-sector roles to systems like Civil Service Jobs, where every vacancy
    shares the path `/csr/viewjob.cgi` and differs only by `?jcode=`. Reducing
    those to the path gave 16 cards one reference, and the in-page dedup then
    kept exactly one of them.

    References from external systems are namespaced ("jcode:2017911", or
    host+path+query) so they cannot collide with an on-site numeric id.
    """
    if not href:
        return LinkIdentity(None, None, SOURCE_OTHER)
    href = href.strip()
    if _NON_DETAIL_HREF_RE.match(href):
        return LinkIdentity(None, None, SOURCE_OTHER)

    parts = urlsplit(href)
    source = _source_for_host(parts.netloc)

    # On-site: the VAC reference is the canonical identity wherever it appears.
    if source == SOURCE_FINDAPPRENTICESHIP:
        m = VAC_REF_RE.search(href)
        if m:
            return LinkIdentity(m.group(1), "vac", source)

    path = parts.path.rstrip("/")
    query = _canonical_query(parts.query)

    if source != SOURCE_FINDAPPRENTICESHIP:
        # An id parameter is the tightest identity available, and short enough
        # to read in an alert.
        params = {k.lower(): v for k, v in
                  parse_qsl(parts.query, keep_blank_values=True)}
        for name in EXTERNAL_ID_PARAMS:
            value = params.get(name)
            if value:
                return LinkIdentity(f"{name}:{value}", name, source)
        # No recognised parameter: the whole normalised URL, which is still
        # distinct per vacancy. Scheme is dropped so http/https don't split one
        # listing in two.
        host = parts.netloc.lower().removeprefix("www.")
        ref = f"{host}{path}" + (f"?{query}" if query else "")
        return LinkIdentity(ref or None, "url", source) if ref else \
            LinkIdentity(None, None, source)

    if not path:
        return LinkIdentity(None, None, source)

    segments = [s for s in path.split("/") if s]
    if segments and ID_SEGMENT_RE.match(segments[-1]):
        return LinkIdentity(segments[-1], "id", source)
    return LinkIdentity(path.lower(), "path", source)


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
