"""Export-bundle tests — the /api/export/all-data ZIP and its per-module CSV
serializers, with a focus on the steps.csv addition. Self-contained (no pytest):

    python tests/test_export.py

Uses an ISOLATED temp SQLite DB via ASFA_DB_PATH so it never touches asfa.db.
"""
import csv as _csv
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


def test_4_csv_safe_neutralizes_formulas():
    # Dangerous leading chars → prefixed with a single quote (inert text).
    for danger in ('=HYPERLINK("http://x")', "+SUM(A1)", "@foo", "-1+1",
                   "\tTAB", "\rCR"):
        out = db.csv_safe(danger)
        assert out == "'" + danger, (danger, out)
    print("  4. csv_safe neutralizes =/+/@/-formula/tab/CR leads  OK")


def test_5_csv_safe_preserves_numbers_and_text():
    # Plain negatives stay numeric (NOT prefixed); ordinary text untouched.
    assert db.csv_safe("-12.50") == "-12.50", db.csv_safe("-12.50")
    assert db.csv_safe(-12.5) == "-12.5", db.csv_safe(-12.5)
    assert db.csv_safe("-1e3") == "-1e3", db.csv_safe("-1e3")
    assert db.csv_safe("Chicken, rice") == "Chicken, rice"
    assert db.csv_safe("") == "" and db.csv_safe(None) == ""
    print("  5. csv_safe keeps -12.50 numeric + leaves text/empty alone  OK")


def test_6_poisoned_food_name_neutralized_in_export():
    # An attacker-influenceable food_name (e.g. from an Open Food Facts barcode)
    # must land in nutrition.csv as inert text, and csv.writer must still quote
    # the embedded comma correctly.
    payload = '=HYPERLINK("http://evil"),rice'
    db.log_meal("2026-07-10", payload, 10, 20, 5)
    csv_text = db.csv_nutrition()
    # Parse back so quoting is handled — the food_name cell is index 2.
    rows = list(_csv.reader(io.StringIO(csv_text)))
    hit = next((r for r in rows[1:] if r[2].startswith("'=HYPERLINK")), None)
    assert hit is not None, csv_text
    assert hit[2] == "'" + payload, hit[2]        # neutralized, comma intact
    print("  6. poisoned food_name exports as inert quoted text  OK")


def main():
    tests = [test_1_steps_omitted_when_empty, test_2_csv_steps_content,
             test_3_zip_contains_steps_csv, test_4_csv_safe_neutralizes_formulas,
             test_5_csv_safe_preserves_numbers_and_text,
             test_6_poisoned_food_name_neutralized_in_export]
    print("Export bundle (steps.csv) tests:")
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
