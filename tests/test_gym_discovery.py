"""Gym inline-discovery tests — the muscle-aware /api/exercises/suggested ranker,
the gym-library ⇄ catalogue GIF matcher, the add-to-session bridge, and that the
separate library page is gone while /gym still renders.

Runs either way — standalone (no pytest dependency) or under pytest:

    python tests/test_gym_discovery.py
    pytest tests/test_gym_discovery.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) and a fixed dataset-shaped
catalogue fixture so nothing hits the network or asfa.db.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_gymdisc_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import database as db                    # noqa: E402
import sync_exercises as sync            # noqa: E402
from services import exercise_match as em  # noqa: E402

# Catalogue fixture: chest-heavy so muscle-match + novelty ordering are testable,
# plus back / arms / legs so cross-muscle exclusion is provable. Names deliberately
# equal the gym library names below, so history links by normalised name.
RAW = [
    {"id": "0001", "name": "barbell bench press", "category": "chest",
     "target": "pectorals", "equipment": "barbell",
     "instructions": {"en": "Press the bar."}, "gif_url": "videos/0001.gif"},
    {"id": "0002", "name": "push-up", "category": "chest", "target": "pectorals",
     "equipment": "body weight", "instructions": "Lower then push up.",
     "gif_url": "videos/0002.gif"},
    {"id": "0003", "name": "cable crossover", "category": "chest",
     "target": "pectorals", "equipment": "cable", "gif_url": "videos/0003.gif"},
    {"id": "0004", "name": "incline dumbbell press", "category": "chest",
     "target": "pectorals", "equipment": "dumbbell", "gif_url": "videos/0004.gif"},
    {"id": "0005", "name": "chest stretch", "category": "chest",
     "target": "pectorals", "equipment": "body weight", "gif_url": "videos/0005.gif"},
    # The four back targets below mirror how the real dataset partitions its 203
    # back rows: lats / upper back / traps / spine (there is no "lower back").
    {"id": "0006", "name": "barbell bent over row", "category": "back",
     "target": "upper back", "equipment": "barbell", "gif_url": "videos/0006.gif"},
    {"id": "0007", "name": "pull-up", "category": "back", "target": "lats",
     "equipment": "body weight", "gif_url": "videos/0007.gif"},
    {"id": "0011", "name": "barbell shrug", "category": "back", "target": "traps",
     "equipment": "barbell", "gif_url": "videos/0011.gif"},
    {"id": "0012", "name": "back extension on exercise ball", "category": "back",
     "target": "spine", "equipment": "body weight", "gif_url": "videos/0012.gif"},
    # Forearms live under "lower arms", not "upper arms" — the distinction that
    # made them unreachable when they were listed as a target of "biceps".
    {"id": "0013", "name": "barbell wrist curl", "category": "lower arms",
     "target": "forearms", "equipment": "barbell", "gif_url": "videos/0013.gif"},
    {"id": "0008", "name": "dumbbell biceps curl", "category": "upper arms",
     "target": "biceps", "equipment": "dumbbell", "gif_url": "videos/0008.gif"},
    {"id": "0009", "name": "triceps pushdown", "category": "upper arms",
     "target": "triceps", "equipment": "cable", "gif_url": "videos/0009.gif"},
    {"id": "0010", "name": "barbell squat", "category": "upper legs",
     "target": "quads", "equipment": "barbell", "gif_url": "videos/0010.gif"},
    # The pec deck, under the exact mechanical name the real dataset uses for
    # it. Nobody calls it this, which is why searching "pec deck" found nothing.
    {"id": "0014", "name": "lever seated fly", "category": "chest",
     "target": "pectorals", "equipment": "leverage machine",
     "gif_url": "videos/0014.gif"},
]


def _names(result):
    return [e["name"] for e in result["exercises"]]


def setup_module(module=None):
    """Seed the catalogue and a controlled gym history: bench logged TODAY
    (recent), cable crossover logged 40 days ago (stale). push-up + incline are
    never logged (novel). Also a back move so muscle-frequency has a runner-up.

    Named ``setup_module`` (not ``setup``) so pytest runs it too: bare ``setup``
    is nose-style, which pytest dropped in 8.0 — under pytest it was never
    called, leaving every query against an unseeded DB returning nothing."""
    sync.sync(RAW, dry_run=False)

    def log(name, muscle, days_ago):
        gx = db.get_or_create_gym_exercise(name=name, muscle_group=muscle)
        d = (date.today() - timedelta(days=days_ago)).isoformat()
        sid = db.create_session(None, d, d + "T10:00:00")
        db.log_set(sid, gx["id"], 1, "working", 60, 8)
        return gx

    log("barbell bench press", "chest", 0)     # recent
    log("cable crossover", "chest", 40)         # stale
    log("barbell bent over row", "back", 3)     # gives chest the frequency lead
    em.build_gym_gif_map(force=True)            # rebuild cache against fixture


def test_1_suggested_muscle_matched():
    r = em.suggest_exercises(session_muscles=["chest"], exclude_names=[], limit=12)
    cats = {e["category"] for e in r["exercises"]}
    assert cats == {"chest"}, cats                       # only chest surfaced
    assert not r["fallback"], "explicit muscles → not a fallback"
    assert all(e["gif_url"] for e in r["exercises"]), "every card carries a GIF"
    print("  1. suggested(chest) → only chest, GIFs present, not fallback  OK")


def test_2_excludes_session_and_ranks_novel_first():
    # Whole-session exclusion (case/punctuation-insensitive) + ranking.
    r = em.suggest_exercises(session_muscles=["chest"],
                             exclude_names=["Push-Up"], limit=12)
    names = _names(r)
    assert "push-up" not in names, names                 # excluded despite casing
    # Novel (never logged) ranks above stale, stale above recently trained.
    assert names.index("incline dumbbell press") < names.index("cable crossover")
    assert names.index("cable crossover") < names.index("barbell bench press")
    # Stretches are de-prioritised as "something new".
    assert names[-1] == "chest stretch" or "chest stretch" not in names[:2]
    print("  2. excludes today's session; novel > stale > recent; stretch sinks  OK")


def test_3_cross_muscle_isolation():
    r = em.suggest_exercises(session_muscles=["back"], exclude_names=[], limit=12)
    cats = {e["category"] for e in r["exercises"]}
    assert cats == {"back"}, cats
    assert "push-up" not in _names(r)
    print("  3. suggested(back) never leaks chest moves  OK")


def test_4_biceps_triceps_disambiguation():
    # biceps and triceps share the 'upper arms' category — the target must split them.
    rb = em.suggest_exercises(session_muscles=["biceps"], limit=12)
    assert _names(rb) == ["dumbbell biceps curl"], _names(rb)
    rt = em.suggest_exercises(session_muscles=["triceps"], limit=12)
    assert _names(rt) == ["triceps pushdown"], _names(rt)
    print("  4. biceps/triceps split by target_muscle within 'upper arms'  OK")


def test_5_fallback_when_session_empty():
    # No session muscles → fall back to the most-trained historical group (chest).
    r = em.suggest_exercises(session_muscles=[], exclude_names=[], limit=12)
    assert r["fallback"] is True
    assert r["muscles"] == ["chest"], r["muscles"]       # chest has the most sets
    assert len(r["exercises"]) > 0
    print(f"  5. empty session → fallback to top group {r['muscles']}, non-empty  OK")


def test_6_limit_respected():
    r = em.suggest_exercises(session_muscles=["chest"], limit=2)
    assert len(r["exercises"]) == 2, len(r["exercises"])
    print("  6. limit caps the result count  OK")


def test_7_search_filters_by_q():
    assert db.get_exercises(q="row")["total"] == 1        # barbell bent over row
    assert db.get_exercises(q="press")["total"] == 2      # bench + incline
    assert db.get_exercises(q="PRESS")["total"] == 2      # case-insensitive
    assert db.get_exercises(q="zzz")["total"] == 0
    print("  7. search endpoint filters by q (case-insensitive)  OK")


def test_8_name_normalisation_and_gif_match():
    # "Bench Press (Barbell)" ↔ dataset "barbell bench press".
    assert em.normalise_name("Bench Press (Barbell)") == "bench press"
    assert em.normalise_name("Push-Ups!") == "push ups"
    gx = db.get_or_create_gym_exercise(name="Bench Press (Barbell)",
                                       muscle_group="chest")
    gif_map = em.build_gym_gif_map(force=True)
    assert gx["id"] in gif_map, "gym exercise got no GIF match"
    assert gif_map[gx["id"]]["gif_url"].endswith("/videos/0001.gif")
    print("  8. name-normalise + token GIF match ('Bench Press (Barbell)')  OK")


def test_9_enrich_gym_exercises_with_gifs():
    lib = em.enrich_gym_exercises_with_gifs(db.get_all_exercises())
    assert lib, "gym library empty"
    assert all("gif_url" in e for e in lib), "gif_url key missing"
    matched = [e for e in lib if e["gif_url"]]
    assert matched, "no gym exercise matched a catalogue GIF"
    print(f"  9. gym library enriched with GIFs ({len(matched)}/{len(lib)} matched)  OK")


def test_10_add_to_session_writes_gym_row():
    # The existing bridge get_or_create's a loggable gym_exercises row.
    ex = db.get_exercise_by_id("0004")                    # incline dumbbell press
    gx = db.get_or_create_gym_exercise(name=ex["name"], muscle_group=ex["category"],
                                       equipment=ex["equipment"])
    assert gx["id"] and gx["name"] == "incline dumbbell press"
    assert any(e["name"] == "incline dumbbell press" for e in db.get_all_exercises())
    # …and it is immediately loggable through the normal set flow.
    sid = db.create_session(None, date.today().isoformat(), "10:00")
    res = db.log_set(sid, gx["id"], 1, "working", 40, 10)
    assert res["id"], "set did not log against the bridged exercise"
    print("  10. add-to-session bridge writes a loggable gym_exercises row  OK")


def test_11_routes():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"

    # /gym still renders; the separate library page is gone.
    assert client.get("/gym").status_code == 200
    assert client.get("/gym/exercises").status_code == 404, "old library page must 404"

    # suggested endpoint
    r = client.get("/api/exercises/suggested?muscles=chest")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert "exercises" in body and isinstance(body["exercises"], list)

    # suggested with exclude
    r = client.get("/api/exercises/suggested?muscles=chest&exclude=push-up")
    assert "push-up" not in [e["name"] for e in r.get_json()["exercises"]]

    # search still works
    r = client.get("/api/exercises?q=row")
    assert r.status_code == 200 and r.get_json()["total"] == 1

    # add-to-workout bridge (CSRF via test header)
    r = client.post("/api/exercises/0002/add-to-workout",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200 and r.get_json()["gym_exercise"]["name"] == "push-up"
    print("  11. routes: /gym 200, /gym/exercises 404, /suggested, search, bridge  OK")



def test_12_no_dead_library_references():
    # The deleted page's template + page-JS are gone; nothing links to /gym/exercises.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "templates", "gym-exercises.html"))
    assert not os.path.exists(os.path.join(root, "static", "js", "exercises.js"))
    for rel in ("templates/gym.html", "templates/nav.html", "static/js/gym.js"):
        txt = open(os.path.join(root, rel)).read()
        assert "/gym/exercises" not in txt, f"{rel} still links the removed page"
    print("  12. no dead references to the removed /gym/exercises page  OK")

def test_13_granular_back_split():
    # The broad "back" key still returns the whole category — the gym library
    # stores "back" as a muscle_group, so this must never narrow.
    rb = em.suggest_exercises(session_muscles=["back"], limit=12)
    assert {e["category"] for e in rb["exercises"]} == {"back"}
    assert len(rb["exercises"]) == 4, _names(rb)          # all four back rows
    # …while each granular key narrows to exactly its own target.
    assert _names(em.suggest_exercises(session_muscles=["lats"])) == ["pull-up"]
    assert _names(em.suggest_exercises(session_muscles=["upper back"])) == \
        ["barbell bent over row"]
    assert _names(em.suggest_exercises(session_muscles=["traps"])) == ["barbell shrug"]
    # The dataset files lower-back work under "spine"; both names must resolve.
    for key in ("lower back", "spine"):
        assert _names(em.suggest_exercises(session_muscles=[key])) == \
            ["back extension on exercise ball"], key
    # A granular key is a real match, not a silent fallback to the top group.
    assert em.suggest_exercises(session_muscles=["lats"])["fallback"] is False
    print("  13. back splits into lats/upper back/traps/spine; broad 'back' intact  OK")


def test_14_forearms_reachable():
    # Regression: forearms were listed as a target of "biceps" (category "upper
    # arms") but are category "lower arms", so the category check hid all of them.
    r = em.suggest_exercises(session_muscles=["forearms"])
    assert _names(r) == ["barbell wrist curl"], _names(r)
    assert r["fallback"] is False
    # …and they must not leak back into biceps, which is a different category.
    assert "barbell wrist curl" not in _names(em.suggest_exercises(session_muscles=["biceps"]))
    print("  14. forearms reachable via their own key, not leaking into biceps  OK")


def test_15_pec_deck_findable_by_gym_floor_name():
    # Regression: the dataset files the pec deck as "lever seated fly", so a
    # search for what's written on the machine returned nothing and the athlete
    # logged a cable fly instead.
    assert em.resolve_alias("pec deck") == "lever seated fly"
    assert em.resolve_alias("Pec Deck") == "lever seated fly"      # case-insensitive
    assert em.resolve_alias("barbell squat") is None, "non-aliases search as-is"
    assert em.display_alias("lever seated fly") == "Pec Deck"
    assert em.display_alias("push-up") is None, "ordinary names need no alias"

    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"

    r = client.get("/api/exercises?q=pec+deck")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["total"] == 1, f"pec deck unfindable: {body['total']} hits"
    hit = body["exercises"][0]
    assert hit["name"] == "lever seated fly"
    assert hit["aka"] == "Pec Deck", "card must show the gym-floor name"
    print("  15. 'pec deck' resolves to lever seated fly and shows as Pec Deck  OK")


def test_16_bridged_alias_logs_under_gym_floor_name():
    # Adding it to a workout must write "Pec Deck" — the log is read by a human,
    # and logging "lever seated fly" is the same unrecognisable name in a new place.
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"
    r = client.post("/api/exercises/0014/add-to-workout",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["gym_exercise"]["name"] == "Pec Deck"
    print("  16. bridging the pec deck logs it as 'Pec Deck'  OK")


def test_17_alias_maps_are_consistent():
    # A display name for a row nothing can reach is dead weight; an alias key
    # that isn't normalised can never be hit, since lookup normalises first.
    for shown in em.DISPLAY_ALIASES:
        assert shown in em.NAME_ALIASES.values(), f"{shown} unreachable by any alias"
    for key in em.NAME_ALIASES:
        assert key == em.normalise_name(key), f"alias key {key!r} never matches"
    print("  17. alias maps consistent: every display name is reachable  OK")


def test_18_suggestions_rotate_daily_not_frozen():
    # Regression: within an equal-score band the ranker used to tiebreak
    # alphabetically, so "Try Something New" showed the same A-named twelve on
    # every visit forever (163 chest rows in prod, 12 slots) and never rotated.
    # It must now rotate day to day while staying stable within a single day.
    import datetime as _dt

    class _FixedDate(_dt.date):
        _t = _dt.date(2026, 1, 1)

        @classmethod
        def today(cls):
            return cls._t

    orig = em.date

    def order_on(day):
        _FixedDate._t = day
        em.date = _FixedDate
        r = em.suggest_exercises(session_muscles=["chest"], limit=12)
        return [e["name"] for e in r["exercises"]]

    try:
        base = _dt.date(2026, 1, 1)
        # Stable within a day: the same date yields the identical order.
        assert order_on(base) == order_on(base), "order must be stable within a day"
        # Not frozen: across a week the order changes at least once.
        week = [order_on(base + _dt.timedelta(days=i)) for i in range(7)]
        assert any(o != week[0] for o in week), "panel never rotates (frozen)"
    finally:
        em.date = orig
    print("  18. suggestions rotate across days, stable within a day  OK")


def test_19_replace_keeps_logged_sets():
    # Issue 4: the athlete logged a cable fly, realises it was the Pec Deck, and
    # hits Replace — the sets must be re-tagged to the new exercise with their
    # weight/reps intact, not deleted and re-entered.
    from datetime import date as _d
    a = db.get_or_create_gym_exercise(name="cable chest fly", muscle_group="chest")
    b = db.get_or_create_gym_exercise(name="Pec Deck", muscle_group="chest")
    sid = db.create_session(None, _d.today().isoformat(), "10:00")
    db.log_set(sid, a["id"], 1, "working", 40, 12)
    db.log_set(sid, a["id"], 2, "working", 45, 10)

    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"

    r = client.post(f"/api/gym/sessions/{sid}/swap-exercise",
                    json={"from_exercise_id": a["id"], "to_exercise_id": b["id"]},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["moved"] == 2, r.get_json()

    sets = db.get_session_sets(sid)
    assert sets and all(s["exercise_id"] == b["id"] for s in sets), "sets not re-pointed"
    assert not any(s["exercise_id"] == a["id"] for s in sets), "old exercise still owns sets"
    assert {(s["weight_kg"], s["reps"]) for s in sets} == {(40.0, 12), (45.0, 10)}, \
        "weight/reps must survive the swap"

    # Guardrails: swapping to a missing exercise 404s; a no-op swap moves nothing.
    assert client.post(f"/api/gym/sessions/{sid}/swap-exercise",
                       json={"from_exercise_id": b["id"], "to_exercise_id": 999999},
                       headers={"X-CSRF-Token": "tok"}).status_code == 404
    assert db.reassign_session_exercise(sid, b["id"], b["id"]) == 0
    print("  19. Replace re-tags logged sets to the new exercise, numbers kept  OK")


def main():
    setup_module()
    tests = [
        test_1_suggested_muscle_matched,
        test_2_excludes_session_and_ranks_novel_first,
        test_3_cross_muscle_isolation,
        test_4_biceps_triceps_disambiguation,
        test_5_fallback_when_session_empty,
        test_6_limit_respected,
        test_7_search_filters_by_q,
        test_8_name_normalisation_and_gif_match,
        test_9_enrich_gym_exercises_with_gifs,
        test_10_add_to_session_writes_gym_row,
        test_11_routes,
        test_12_no_dead_library_references,
        test_13_granular_back_split,
        test_14_forearms_reachable,
        test_15_pec_deck_findable_by_gym_floor_name,
        test_16_bridged_alias_logs_under_gym_floor_name,
        test_17_alias_maps_are_consistent,
        test_18_suggestions_rotate_daily_not_frozen,
        test_19_replace_keeps_logged_sets,
    ]
    print("Gym inline-discovery tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
