"""The read model behind the Find-roles screen, and the saved-search alerts.

One place that turns `scout_jobs` rows into what a card needs, so the template
renders and does not decide. Three things it is responsible for:

  * GROUPING. Confirmed Level 4+ roles, off-site listings whose level gov.uk
    never states, and the watchlist REVIEW set are three different things to
    act on, so they are three groups rather than one list with badges.
  * BADGES. new / starred / multi-source / level-unknown, computed here so the
    rules live next to each other instead of in template conditionals.
  * NEVER HIDING ANYTHING. A filter narrows what is shown and says so; nothing
    is dropped silently. `near_home` is a tag, not a filter, and the REVIEW
    group is collapsed and sorted last but always present.

Alerts go through services.scheduler._notify, which already writes the in-app
bell and respects the Telegram toggle. There is deliberately no second sender
here, and no email or Discord path: neither is configured.
"""
import json
import logging
from datetime import date, datetime

import database as db
from services import scout_config

logger = logging.getLogger("asfa.scout_roles")

# Monogram tile colours. Derived from the employer name so a company keeps the
# same tile between visits without storing anything, and taken from the theme's
# series tokens so both themes stay in palette.
TILE_TOKENS = ("--series-1", "--series-2", "--series-3", "--series-4",
               "--gold", "--primary")

SORTS = ("recent", "closing", "level", "near", "fit")


def _tile_token(name: str) -> str:
    """A stable theme token for this employer's monogram tile."""
    key = (name or "?").strip().casefold()
    return TILE_TOKENS[sum(ord(c) for c in key) % len(TILE_TOKENS)]


def _monogram(name: str) -> str:
    words = [w for w in (name or "").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()


def _days_left(closing):
    if not closing:
        return None
    try:
        if isinstance(closing, str):
            closing = datetime.strptime(closing[:10], "%Y-%m-%d").date()
        elif isinstance(closing, datetime):
            closing = closing.date()
    except (ValueError, TypeError):
        return None
    return (closing - date.today()).days


def _locations(row) -> list:
    raw = (row.get("location") or "").strip()
    return [raw] if raw else []


def watchlist_terms() -> list:
    """Every name that earns a listing a star, from all three sources.

    `services/apprenticeships.py` stars a listing from the Employer table or an
    imported programme company, but it does so on a TRANSIENT attribute — there
    is no `starred` column on scout_jobs, so nothing is persisted and a read of
    the table can never answer the question. The read model therefore asks the
    same question of the same sources, rather than reading a column that is
    always empty and quietly never showing a star.
    """
    terms = set()
    for getter in (db.get_scout_programme_companies,
                   db.get_watched_employer_names):
        try:
            terms.update(t for t in getter() if t)
        except Exception as e:
            logger.warning("scout_roles: watchlist source %s failed: %s",
                           getattr(getter, "__name__", getter), e)
    # The config list is the fallback a fresh install has before any import.
    terms.update(scout_config.settings()["watchlist_employers"])
    return sorted(terms)


def to_card(row: dict, last_visited: str = "", detail: dict = None,
            watchlist=None) -> dict:
    """One scout_jobs row as the card and panel need it."""
    detail = detail or {}
    closing = detail.get("closing_date") or row.get("closing_date")
    employer = row.get("employer_name_raw") or row.get("company") or ""
    found = row.get("found_date") or ""

    # Computed, never read from the row: see watchlist_terms(). With no
    # watchlist passed the row's own value is used, which keeps a caller that
    # has already resolved it (the notifier) working unchanged.
    starred = bool(row.get("starred"))
    if not starred and watchlist is not None:
        starred = bool(scout_config.matched_watchlist_employer(
            employer, watchlist=watchlist))

    return {
        "ref": row.get("external_ref") or "",
        "title": row.get("title") or "Untitled",
        "employer": employer,
        "monogram": _monogram(employer),
        "tile_token": _tile_token(employer),
        "locations": _locations(row),
        "location_count": len(_locations(row)),
        "level": row.get("level"),
        "course": row.get("training_course") or "",
        "source": row.get("source") or "",
        "url": row.get("url") or "",
        "status": row.get("status") or "open",
        "near_home": bool(row.get("near_home")),
        "starred": starred,
        "needs_review": bool(row.get("needs_review")),
        "review_standard": row.get("review_standard") or "",
        "saved": bool(row.get("saved")),
        "stage": row.get("stage") or "",
        "found_date": found,
        "is_new": bool(last_visited and found and found > last_visited),
        # Detail-panel fields. None renders as a muted dash — never invented.
        "wage": detail.get("wage") or row.get("salary") or None,
        "hours": detail.get("hours"),
        "start_date": detail.get("start_date"),
        "duration": detail.get("duration"),
        "closing_date": closing,
        "days_left": _days_left(closing),
        "description": detail.get("description"),
        "detail_fetched": bool(detail.get("fetched_at")),
    }


def _sort_key(card, sort):
    if sort == "closing":
        days = card["days_left"]
        return (days is None, days if days is not None else 0)
    if sort == "level":
        return (card["level"] is None, -(card["level"] or 0))
    if sort == "near":
        return (not card["near_home"], card["employer"].casefold())
    if sort == "fit":
        return (0 if card["starred"] else 1, card["employer"].casefold())
    return (card["found_date"] < "", card["found_date"] * -1
            if isinstance(card["found_date"], int) else "")


def _sorted(cards, sort):
    if sort == "recent":
        return sorted(cards, key=lambda c: c["found_date"] or "", reverse=True)
    return sorted(cards, key=lambda c: _sort_key(c, sort))


def _matches(card, f) -> bool:
    """Filters NARROW what is shown. Each one is reported as an active chip."""
    if f.get("level"):
        try:
            if card["level"] is None or card["level"] < int(f["level"]):
                return False
        except (TypeError, ValueError):
            pass
    if f.get("source") and card["source"] != f["source"]:
        return False
    if f.get("near_home") and not card["near_home"]:
        return False
    if f.get("watchlist_only") and not card["starred"]:
        return False
    if f.get("hide_closed") and card["status"] != "open":
        return False
    if f.get("new_only") and not card["is_new"]:
        return False
    if f.get("q"):
        needle = f["q"].strip().casefold()
        haystack = " ".join([card["title"], card["employer"], card["course"]]
                            + card["locations"]).casefold()
        if needle and needle not in haystack:
            return False
    return True


def roles_view(filters: dict = None, sort: str = "recent",
               mark_visited: bool = False) -> dict:
    """Everything the Find-roles screen renders.

    Returns three groups plus counts. `mark_visited` is passed only by the page
    load, so refreshing the list does not keep resetting the "new since" line.
    """
    filters = filters or {}
    sort = sort if sort in SORTS else "recent"

    last_visited = (db.touch_scout_last_visited() if mark_visited
                    else db.scout_last_visited())

    rows = db.get_apprenticeship_rows()
    details = db.get_all_scout_job_details()
    watchlist = watchlist_terms()

    cards = [to_card(r, last_visited, details.get(r.get("external_ref")),
                     watchlist=watchlist)
             for r in rows]

    level_4_plus, level_unknown, review = [], [], []
    for card in cards:
        if card["needs_review"]:
            review.append(card)
        elif card["level"] is None:
            level_unknown.append(card)
        else:
            level_4_plus.append(card)

    shown = {
        # The REVIEW group keeps its own order: it is sorted below everything
        # else by being rendered last, and collapsed in the template.
        "level_4_plus": _sorted([c for c in level_4_plus
                                 if _matches(c, filters)], sort),
        "level_unknown": _sorted([c for c in level_unknown
                                  if _matches(c, filters)], sort),
        "review": _sorted([c for c in review if _matches(c, filters)], sort),
    }

    return {
        "groups": shown,
        "counts": {
            "total": len(cards),
            "shown": sum(len(v) for v in shown.values()),
            "level_4_plus": len(level_4_plus),
            "level_unknown": len(level_unknown),
            "review": len(review),
            "new_since_last_visit": sum(1 for c in cards if c["is_new"]),
            "near_home": sum(1 for c in cards if c["near_home"]),
            "saved": sum(1 for c in cards if c["saved"]),
        },
        "last_visited": last_visited,
        "sort": sort,
        "filters": filters,
        # The stage list travels with the view so the stage select on a card is
        # built from the same source as the Tracker's columns, rather than a
        # copy of SCOUT_STAGES hardcoded in the frontend.
        "stages": [{"key": k, "label": db.scout_stage_label(k)}
                   for k in db.SCOUT_STAGES],
        # The stage Scout's own backfill uses. A card sitting there was parked
        # by the scan, not chosen by the user, and the screen says so.
        "auto_stage": db.SCOUT_AUTO_STAGE,
        "sources": sorted({c["source"] for c in cards if c["source"]}),
        "levels": sorted({c["level"] for c in cards
                          if c["level"] is not None}),
        "distances": list(scout_config.DISTANCE_VALUES),
        "home_postcode": scout_config.home_postcode(),
    }


# ── Saved-search alerts ────────────────────────────────────────────────────

def channel_states() -> list:
    """Each channel and whether it is actually usable.

    Never returns a token, a chat id or an address — only a boolean and a
    reason, because this is rendered in a page.
    """
    from services import telegram_bot

    telegram_ok = False
    try:
        telegram_ok = bool(telegram_bot.is_configured())
    except Exception:
        telegram_ok = False

    return [
        {"key": "bell", "label": "In-app bell", "configured": True,
         "detail": "Always available — no configuration needed."},
        {"key": "telegram", "label": "Telegram", "configured": telegram_ok,
         "detail": ("Bot token and chat ID are set." if telegram_ok else
                    "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable.")},
    ]


def run_saved_searches() -> dict:
    """Match each enabled saved search and notify on anything new.

    Uses services.scheduler._notify, which writes the in-app bell and sends
    Telegram subject to the user's own toggle. No second sender, and no email
    or Discord path — neither is configured.
    """
    from services.scheduler import _notify

    summary = {"searches": 0, "notified": 0, "matches": 0}
    try:
        searches = db.list_saved_searches()
    except Exception as e:
        logger.error("scout_roles: could not list saved searches: %s", e)
        return summary

    for search in searches:
        if not search.get("enabled"):
            continue
        summary["searches"] += 1
        try:
            view = roles_view(search.get("filters") or {}, sort="recent")
        except Exception as e:
            logger.warning("scout_roles: saved search %s failed: %s",
                           search.get("id"), e)
            continue

        new_cards = [c for group in ("level_4_plus", "level_unknown")
                     for c in view["groups"][group] if c["is_new"]]
        summary["matches"] += len(new_cards)
        db.record_saved_search_run(search["id"], len(new_cards))
        if not new_cards:
            continue

        channels = search.get("channel_list") or ["bell"]
        lines = [f"\U0001f50e {search['name']} — "
                 f"{len(new_cards)} new role{'' if len(new_cards) == 1 else 's'}"]
        for card in new_cards[:8]:
            star = "★ " if card["starred"] else ""
            level = f"L{card['level']}" if card["level"] else "L?"
            lines.append(f"{star}{level} {card['title']} — {card['employer']}")
        if len(new_cards) > 8:
            lines.append(f"...and {len(new_cards) - 8} more")

        _notify("\n".join(lines), kind="info",
                telegram="telegram" in channels)
        summary["notified"] += 1

    return summary
