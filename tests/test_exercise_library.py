"""Exercise Library tests — sync mapping, catalogue DB layer, filters, search,
the gym-log bridge, and the read/POST routes.

Self-contained, no pytest dependency: run directly with

    python tests/test_exercise_library.py

Uses an ISOLATED temp SQLite DB (ASFA_DB_PATH) so it never touches asfa.db, and
uses a fixed in-memory fixture (dataset-shaped) so results don't depend on the
network. Existing gym tests are unaffected — the catalogue lives in its own
`exercises` table.
"""
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_exl_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)          # force SQLite, not prod Postgres
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import database as db          # noqa: E402
import sync_exercises as sync  # noqa: E402

# Dataset-shaped fixture: home vs gym equipment, multiple categories, two squats
# (for search), instructions as a {lang:text} dict and as a plain string.
RAW = [
    {"id": "0001", "name": "barbell squat", "category": "upper legs",
     "target": "quads", "equipment": "barbell", "body_part": "upper legs",
     "instructions": {"en": "Step one. Step two.", "es": "Uno."},
     "gif_url": "videos/0001.gif", "image": "images/0001.jpg"},
    {"id": "0002", "name": "bodyweight squat", "category": "upper legs",
     "target": "quads", "equipment": "body weight",
     "instructions": {"en": "Squat down slowly."}, "gif_url": "videos/0002.gif"},
    {"id": "0003", "name": "push-up", "category": "chest", "target": "pectorals",
     "equipment": "body weight", "instructions": "Lower then push up."},
    {"id": "0004", "name": "band pull-apart", "category": "shoulders",
     "target": "delts", "equipment": "bands",
     "instructions": {"en": "Pull the band apart."}},
    {"id": "0005", "name": "dumbbell curl", "category": "upper arms",
     "target": "biceps", "equipment": "dumbbell",
     "instructions": {"en": "Curl the weight up."}},
]


def test_1_map_exercise():
    row = sync.map_exercise(RAW[0])
    assert row["id"] == "0001" and row["name"] == "barbell squat"
    assert row["category"] == "upper legs" and row["target_muscle"] == "quads"
    assert row["instructions"] == "Step one. Step two.", row["instructions"]  # English picked
    assert row["gif_url"].endswith("/videos/0001.gif") and row["gif_url"].startswith("https://")
    assert row["is_home_friendly"] is False                # barbell → gym only
    # plain-string instructions + home derivation for body weight
    r3 = sync.map_exercise(RAW[2])
    assert r3["instructions"] == "Lower then push up."
    assert r3["is_home_friendly"] is True
    assert sync.map_exercise(RAW[3])["is_home_friendly"] is True   # bands → home
    print("  1. map_exercise: fields mapped, EN instructions, home derivation  OK")


def test_2_sync_inserts():
    res = sync.sync(RAW, dry_run=False)
    assert res == {"fetched": 5, "inserted": 5, "updated": 0, "skipped": 0,
                   "preview": []}, res
    assert db.count_exercises() == 5, db.count_exercises()
    print(f"  2. sync inserts 5 rows; count=5  [actual {db.count_exercises()}]  OK")


def test_3_idempotent_resync():
    res = sync.sync(RAW, dry_run=False)
    assert res["inserted"] == 0 and res["updated"] == 5, res
    assert db.count_exercises() == 5, "re-sync must not duplicate"
    print("  3. re-sync -> inserted 0, updated 5, no duplicates  OK")


def test_4_difficulty_preserved():
    # Curate a difficulty by hand, then re-sync — it must survive.
    ph = "%s" if db.USE_POSTGRES else "?"
    with db.get_db() as conn:
        conn.cursor().execute(
            f"UPDATE exercises SET difficulty = {ph} WHERE id = {ph}",
            ("beginner", "0002"))
    sync.sync(RAW, dry_run=False)
    assert db.get_exercise_by_id("0002")["difficulty"] == "beginner"
    print("  4. re-sync preserves manually-curated difficulty  OK")


def test_5_filter_category():
    r = db.get_exercises(category="upper legs")
    assert r["total"] == 2, r["total"]
    names = {e["name"] for e in r["exercises"]}
    assert names == {"barbell squat", "bodyweight squat"}, names
    print(f"  5. filter category='upper legs' -> 2  [actual {r['total']}]  OK")


def test_6_filter_equipment():
    r = db.get_exercises(equipment="body weight")
    assert r["total"] == 2, r["total"]
    # comma list matches with IN
    r2 = db.get_exercises(equipment="barbell,dumbbell")
    assert r2["total"] == 2, r2["total"]
    print("  6. filter equipment (single + comma-list IN)  OK")


def test_7_filter_home_only():
    r = db.get_exercises(home_only=True)
    assert r["total"] == 3, r["total"]        # 0002, 0003, 0004
    assert all(e["is_home_friendly"] for e in r["exercises"])
    print(f"  7. filter home_only -> 3  [actual {r['total']}]  OK")


def test_8_filter_combined():
    r = db.get_exercises(home_only=True, category="chest")
    assert r["total"] == 1 and r["exercises"][0]["name"] == "push-up", r
    print("  8. combined home_only + category='chest' -> push-up  OK")


def test_9_search_partial():
    assert db.get_exercises(q="squat")["total"] == 2
    assert db.get_exercises(q="push")["total"] == 1
    assert db.get_exercises(q="SQUAT")["total"] == 2       # case-insensitive
    assert db.get_exercises(q="zzz")["total"] == 0
    print("  9. search 'squat'->2, 'push'->1, case-insensitive  OK")


def test_10_pagination():
    r = db.get_exercises(per_page=2, page=1)
    assert len(r["exercises"]) == 2 and r["total"] == 5 and r["pages"] == 3, r
    r2 = db.get_exercises(per_page=2, page=3)
    assert len(r2["exercises"]) == 1 and r2["page"] == 3, r2
    print("  10. pagination: per_page=2 -> 3 pages, last page 1 row  OK")


def test_11_facets():
    f = db.get_exercise_facets()
    assert "upper legs" in f["categories"] and "chest" in f["categories"]
    assert "body weight" in f["equipment"] and "barbell" in f["equipment"]
    assert "beginner" in f["difficulties"], f["difficulties"]   # from test_4
    print("  11. facets expose categories/equipment/difficulties  OK")


def test_12_skips_bad_records():
    bad = [{"name": "no id here"}, {"id": "0009"}, {"id": "0010", "name": "ok row"}]
    res = sync.sync(bad, dry_run=False)
    assert res["skipped"] == 2 and res["inserted"] == 1, res
    print("  12. sync skips records missing id or name  OK")


def test_13_get_or_create_gym_bridge():
    ex = db.get_exercise_by_id("0002")
    gx = db.get_or_create_gym_exercise(
        name=ex["name"], muscle_group=ex["category"], equipment=ex["equipment"])
    assert gx and gx["name"] == "bodyweight squat" and gx["id"]
    # second call returns the same gym_exercise (no duplicate row)
    gx2 = db.get_or_create_gym_exercise(name=ex["name"])
    assert gx2["id"] == gx["id"], "bridge must be idempotent on name"
    print("  13. get_or_create_gym_exercise bridges + is idempotent  OK")


def test_14_routes():
    import app as app_module
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["csrf_token"] = "tok"

    r = client.get("/api/exercises?q=squat")
    assert r.status_code == 200 and r.get_json()["total"] == 2, r.get_json()

    r = client.get("/api/exercises/0003")
    assert r.status_code == 200 and r.get_json()["name"] == "push-up"

    assert client.get("/api/exercises/nope").status_code == 404

    r = client.get("/api/exercises/facets")
    assert r.status_code == 200 and "chest" in r.get_json()["categories"]

    r = client.post("/api/exercises/0001/add-to-workout",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] and body["gym_exercise"]["name"] == "barbell squat"

    # The standalone browse page was removed — discovery is now inline on /gym.
    # The data APIs above stay; only the page route is gone.
    assert client.get("/gym/exercises").status_code == 404
    print("  14. routes: list/detail/404/facets/add-to-workout; page removed  OK")


def test_15_existing_gym_unaffected():
    # The curated gym library + its API still work alongside the catalogue.
    db.init_gym_data()
    lib = db.get_all_exercises()
    assert isinstance(lib, list) and len(lib) > 0, "gym_exercises library empty"
    print(f"  15. existing gym_exercises library intact ({len(lib)} rows)  OK")


def main():
    tests = [
        test_1_map_exercise, test_2_sync_inserts, test_3_idempotent_resync,
        test_4_difficulty_preserved, test_5_filter_category, test_6_filter_equipment,
        test_7_filter_home_only, test_8_filter_combined, test_9_search_partial,
        test_10_pagination, test_11_facets, test_12_skips_bad_records,
        test_13_get_or_create_gym_bridge, test_14_routes, test_15_existing_gym_unaffected,
    ]
    print("Exercise Library tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
