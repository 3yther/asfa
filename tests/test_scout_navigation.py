"""Getting to Scout, and back out again.

The apprenticeship hub shipped with nothing linking to it: /scout/roles existed
and no page in ASFA mentioned it. This pins the route in:

  * the dashboard's SCOUT entry opens the HUB, which is Scout's main screen now;
  * the older part-time radar keeps its own URL so a bookmark still works, and
    the two pages link to each other rather than one replacing the other;
  * neither is a redirect, so there is no loop to get stuck in.
"""
import os
import re
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_scoutnav_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402

HUB = "/scout/roles"
RADAR = "/scout"


def _client(login=True):
    c = app_module.app.test_client()
    if login:
        c.post("/login", data={"password": os.environ["APP_PASSWORD"]},
               follow_redirects=True)
    return c


def _body(path):
    r = _client().get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_data(as_text=True)


def _hrefs(html):
    return set(re.findall(r'href="([^"]+)"', html))


# ── The way in ──────────────────────────────────────────────────────────────

def test_the_dashboard_scout_entry_opens_the_hub():
    """The one menu entry for Scout. It used to open the radar."""
    assert HUB in _hrefs(_body("/"))


def test_the_dashboard_scout_entry_is_not_the_old_radar():
    html = _body("/")
    entry = re.search(r'<a[^>]*class="[^"]*scout-btn[^"]*"[^>]*>', html)
    assert entry, "the SCOUT menu entry is gone entirely"
    assert f'href="{HUB}"' in entry.group(0)


def test_the_hub_is_reachable_without_knowing_the_url():
    """Follow only what the dashboard links to, and arrive at the hub."""
    for href in _hrefs(_body("/")):
        if href == HUB:
            assert _client().get(href).status_code == 200
            return
    raise AssertionError("no path from the dashboard to the hub")


# ── Both pages still exist ──────────────────────────────────────────────────

def test_the_old_radar_url_still_works_for_bookmarks():
    r = _client().get(RADAR)
    assert r.status_code == 200, "a bookmark to /scout must not break"


def test_neither_page_redirects(): 
    """A redirect is where a loop would come from. Both render directly."""
    for path in (HUB, RADAR):
        r = _client().get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} redirected ({r.status_code})"


def test_they_are_different_pages():
    hub, radar = _body(HUB), _body(RADAR)
    assert "scout_roles.js" in hub and "scout_roles.js" not in radar
    assert "scout-radar" in radar and "scout-radar" not in hub


# ── The way between them ────────────────────────────────────────────────────

def test_the_hub_links_to_the_part_time_radar():
    assert RADAR in _hrefs(_body(HUB))


def test_the_radar_links_back_to_the_hub():
    assert HUB in _hrefs(_body(RADAR))


def test_the_round_trip_terminates():
    """Hub -> radar -> hub, each a 200 with no redirect. If either became a
    redirect to the other this would spin."""
    seen = []
    here = HUB
    for _ in range(6):
        r = _client().get(here, follow_redirects=False)
        assert r.status_code == 200, f"{here} -> {r.status_code}"
        seen.append(here)
        here = RADAR if here == HUB else HUB
    assert set(seen) == {HUB, RADAR}


def test_the_part_time_link_is_not_inside_the_tablist():
    """role="tablist" contains tabs. An <a> among them misreports the tab
    count to a screen reader, so the link sits outside the nav."""
    html = _body(HUB)
    nav = re.search(r'<nav[^>]*role="tablist".*?</nav>', html, re.S)
    assert nav, "the tablist is gone"
    assert f'href="{RADAR}"' not in nav.group(0)
    assert f'href="{RADAR}"' in html


def test_the_employer_watchlist_is_still_reachable():
    """It is linked from the radar page, and that path must survive."""
    assert "/scout/employers" in _hrefs(_body(RADAR))
    assert _client().get("/scout/employers").status_code == 200


# ── The gate ────────────────────────────────────────────────────────────────

def test_both_pages_stay_behind_the_auth_gate():
    c = _client(login=False)
    for path in (HUB, RADAR):
        assert c.get(path).status_code in (302, 401), f"{path} is public"
