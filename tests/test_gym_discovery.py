"""Gym inline-discovery tests — the muscle-aware /api/exercises/suggested ranker,
the gym-library ⇄ catalogue GIF matcher, the add-to-session bridge, and that the
separate library page is gone while /gym still renders.

Self-contained, no pytest dependency:

    python tests/test_gym_discovery.py

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
    {"id": "0006", "name": "barbell bent over row", "category": "back",
     "target": "lats", "equipment": "barbell", "gif_url": "videos/0006.gif"},
    {"id": "0007", "name": "pull-up", "category": "back", "target": "lats",
     "equipment": "body weight", "gif_url": "videos/0007.gif"},
    {"id": "0008", "name": "dumbbell biceps curl", "category": "upper arms",
     "target": "biceps", "equipment": "dumbbell", "gif_url": "videos/0008.gif"},
    {"id": "0009", "name": "triceps pushdown", "category": "upper arms",
     "target": "triceps", "equipment": "cable", "gif_url": "videos/0009.gif"},
    {"id": "0010", "name": "barbell squat", "category": "upper legs",
     "target": "quads", "equipment": "barbell", "gif_url": "videos/0010.gif"},
]


def _names(result):
    return [e["name"] for e in result["exercises"]]


def setup():
    """Seed the catalogue and a controlled gym history: bench logged TODAY
    (recent), cable crossover logged 40 days ago (stale). push-up + incline are
    never logged (novel). Also a back move so muscle-frequency has a runner-up."""
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


def main():
    setup()
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
