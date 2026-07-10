"""Export-bundle tests — the /api/export/all-data ZIP and its per-module CSV
serializers, with a focus on the steps.csv addition. Self-contained (no pytest):

    python tests/test_export.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
"""
import io
import os
import sys
import tempfile
import zipfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="asfa_export_test_"), "test.db")
os.environ["ASFA_DB_PATH"] = _TMP_DB
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("APP_PASSWORD", "test-pass")
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402
import database as db       # noqa: E402


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
        s["csrf_token"] = "tok"
    return c


def test_1_steps_omitted_when_empty():
    # No step rows yet → steps.csv must NOT appear (empty-module omission).
    assert "steps.csv" not in db.export_all_csvs(), "empty steps must be omitted"
    print("  1. steps.csv omitted when the table has no rows  OK")


def test_2_csv_steps_content():
    db.add_step_entry("2026-07-09", "manual", 5000, {"steps": 5000})
    db.add_step_entry("2026-07-10", "bike", 14300,
                      {"distance_km": 10, "kph": 20, "terrain": "hilly"})
    csv = db.csv_steps()
    assert csv, "csv_steps must be non-empty once rows exist"
    lines = csv.strip().splitlines()
    assert lines[0] == "date,source,steps,detail_json", lines[0]
    # Sorted date DESC → the 2026-07-10 bike row comes first.
    assert lines[1].startswith("2026-07-10,bike,14300,"), lines[1]
    assert "2026-07-09,manual,5000," in csv, csv
    print("  2. csv_steps header + rows (date DESC, detail JSON)  OK")


def test_3_zip_contains_steps_csv():
    c = _client()
    r = c.post("/api/export/all-data", headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200, r.status_code
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert zf.testzip() is None, "zip integrity (unzip -t equivalent)"
    names = zf.namelist()
    assert "steps.csv" in names, names
    steps_csv = zf.read("steps.csv").decode()
    assert "2026-07-10,bike,14300," in steps_csv, steps_csv
    print(f"  3. export ZIP contains steps.csv; integrity clean ({names})  OK")


def main():
    tests = [test_1_steps_omitted_when_empty, test_2_csv_steps_content,
             test_3_zip_contains_steps_csv]
    print("Export bundle (steps.csv) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
