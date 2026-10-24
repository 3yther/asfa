"""Scout's widened stage model, programme entries, and the spreadsheet import.

PRIVACY: every fixture here is invented — made-up companies, made-up programme
names, made-up notes. No row from the real workbook appears in this repo, and
the workbooks these tests build are written to pytest's tmp_path, never into
the tree. `*.xlsx` is gitignored as a second line of defence.

The contract the import has to honour, and what each group below pins:

  * the stage set is wider than the board used to be, and "interview" survives
    as a legacy value rather than being migrated into a guess;
  * every tracked stage is exempt from the 14-day cleanup;
  * a re-import refreshes what the spreadsheet owns and duplicates nothing;
  * a re-import NEVER overwrites anything changed inside Scout;
  * the Timeline's months are last cycle's windows, stored as unconfirmed.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from services import scout_config  # noqa: E402

_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "import_scout_spreadsheet.py")
_spec = importlib.util.spec_from_file_location("import_scout_spreadsheet",
                                               _SPEC_PATH)
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)

openpyxl = pytest.importorskip("openpyxl")

MONTH_HEADERS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr",
                 "May", "Jun", "Jul", "Aug"]

MASTER_HEADERS = [
    "Company", "Programme", "Sector", "Specialization", "Level", "Duration",
    "University Partner", "Degree Awarded", "Locations", "Salary Y1",
    "Entry Requirements", "T-Level Accepted", "Typical Opens",
    "Typical Closes", "Rolling?", "Cohort Size", "Assessment Process",
    "Official Link", "Notes", "Cloud-Sec Fit"]

APP_HEADERS = ["Company", "Programme", "Status", "Deadline", "Applied Date",
               "Stage Reached", "Next Step", "Next Step Date", "Notes"]


@pytest.fixture(autouse=True)
def clean_programmes():
    db._ensure_scout_tables()
    with db.get_db() as conn:
        conn.cursor().execute("DELETE FROM scout_programmes")
    yield


def _workbook(tmp_path, master=(), timeline=(), applications=(),
              extra_sheets=("Software Engineering", "Read Me")):
    """A synthetic workbook shaped like the real one. Invented data only."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Master List")
    ws.append(["Synthetic sheet title"])          # the real file has one
    ws.append(MASTER_HEADERS)
    for row in master:
        ws.append(list(row) + [""] * (len(MASTER_HEADERS) - len(row)))

    ws = wb.create_sheet("Timeline")
    ws.append(["Company", "Programme"] + MONTH_HEADERS
              + ["Typical Close", "Rolling?", "Fit"])
    for row in timeline:
        ws.append(list(row))

    ws = wb.create_sheet("My Applications")
    ws.append(APP_HEADERS)
    for row in applications:
        ws.append(list(row) + [""] * (len(APP_HEADERS) - len(row)))

    for name in extra_sheets:
        s = wb.create_sheet(name)
        s.append(["Company", "Programme"])
        s.append(["Northwind Systems", "Cyber Apprenticeship"])

    path = tmp_path / "synthetic.xlsx"
    wb.save(path)
    return str(path)


def _master_row(company, programme, **kw):
    row = [company, programme]
    for header in MASTER_HEADERS[2:]:
        row.append(kw.get(header.lower().replace("-", "_").replace("?", "")
                          .replace(" ", "_"), ""))
    return row


# ── 1a. The widened stage model ─────────────────────────────────────────────

def test_the_stage_set_is_the_new_one_in_order():
    assert db.SCOUT_STAGES == (
        "saved", "interested", "applied", "online_assessment",
        "video_interview", "assessment_centre", "offer", "rejected",
        "withdrawn")


def test_saved_is_still_the_only_auto_stage():
    assert db.SCOUT_AUTO_STAGE == "saved"
    assert "saved" not in db.SCOUT_TRACKED_STAGES


def test_interview_survives_as_a_legacy_value():
    """Not migrated: "interview" is something the user recorded, and rewriting
    it to one of the newer stages would invent detail they never entered."""
    assert db.SCOUT_LEGACY_STAGES == ("interview",)
    assert "interview" in db.SCOUT_ALL_STAGES
    assert "interview" not in db.SCOUT_STAGES
    assert "interview" in db.SCOUT_TRACKED_STAGES, "a retired stage is still tracked"
    assert db.scout_stage_label("interview") == "Interview"


@pytest.mark.parametrize("stage,label", [
    ("saved", "Saved"), ("interested", "Interested"), ("applied", "Applied"),
    ("online_assessment", "Online Assessment"),
    ("video_interview", "Video Interview"),
    ("assessment_centre", "Assessment Centre"),
    ("offer", "Offer"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn"),
    ("interview", "Interview"),
])
def test_every_stage_has_a_readable_label(stage, label):
    assert db.scout_stage_label(stage) == label


def test_the_closed_column_holds_rejected_and_withdrawn():
    assert db.SCOUT_CLOSED_STAGES == ("rejected", "withdrawn")


@pytest.mark.parametrize("stage", [
    "interested", "applied", "online_assessment", "video_interview",
    "assessment_centre", "offer", "rejected", "withdrawn", "interview",
])
def test_every_tracked_stage_survives_the_cleanup(stage):
    """The 14-day cleanup must never take a stage the user set — an
    apprenticeship cycle runs for months."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM scout_pipeline")
        ph = "%s" if db.USE_POSTGRES else "?"
        old = "2020-01-01 00:00:00"
        cur.execute(
            f"INSERT INTO scout_pipeline "
            f"(job_title, company, stage, created_at, updated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (f"{stage} role", "Northwind Systems", stage, old, old))

    preview = db.preview_job_pipeline_cleanup(days=14)
    assert preview["pipeline_total"] == 0, f"{stage} was in the delete set"
    assert preview["pipeline_exempt_by_stage"] == {stage: 1}

    db.cleanup_job_pipeline(days=14)
    with db.get_db() as conn:
        n = conn.cursor().execute(
            "SELECT COUNT(*) FROM scout_pipeline").fetchone()[0]
    assert n == 1, f"{stage} row was deleted"


def test_an_untouched_saved_row_is_still_cleaned_up():
    """The clutter control the cleanup exists for has to keep working."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM scout_pipeline")
        ph = "%s" if db.USE_POSTGRES else "?"
        cur.execute(
            f"INSERT INTO scout_pipeline "
            f"(job_title, company, stage, created_at, updated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            ("noise", "Northwind Systems", "saved",
             "2020-01-01 00:00:00", "2020-01-01 00:00:00"))
    assert db.cleanup_job_pipeline(days=14)["pipeline"] == 1


# ── 1b. Programme entries ───────────────────────────────────────────────────

def test_a_programme_can_be_tracked_without_a_live_vacancy():
    """Most of what the user follows is a scheme that has not opened yet."""
    assert db.upsert_scout_programme(
        "Worldpay Analogue", "Cyber Apprenticeship",
        {"level": "6", "cloud_sec_fit": "High"},
        seed_user_fields={"stage": "interested",
                          "next_step": "Watch for opening",
                          "next_step_date": "2026-12-01"}) == "inserted"

    row = db.get_scout_programmes()[0]
    assert row["company"] == "Worldpay Analogue"
    assert row["stage"] == "interested"
    assert row["next_step_date"] == "2026-12-01"
    assert row["linked_external_ref"] is None, "no vacancy linked yet"


def test_a_programme_can_be_linked_to_a_vacancy_but_is_never_auto_merged():
    db.upsert_scout_programme("Northwind Systems", "Cyber Apprenticeship", {})
    row = db.get_scout_programmes()[0]

    # A suggestion is import-visible but does not change the entry.
    assert db.set_scout_programme_stage(
        row["id"], None, linked_external_ref="VAC900001") is True
    row = db.get_scout_programmes()[0]
    assert row["linked_external_ref"] == "VAC900001"
    assert row["edited_in_scout"] == 1, "linking is a user action"


def test_setting_a_stage_marks_the_row_as_edited_in_scout():
    db.upsert_scout_programme("Northwind Systems", "Cyber Apprenticeship", {})
    row = db.get_scout_programmes()[0]
    assert row["edited_in_scout"] == 0

    db.set_scout_programme_stage(row["id"], "applied")
    assert db.get_scout_programmes()[0]["edited_in_scout"] == 1


def test_an_unknown_stage_is_refused():
    db.upsert_scout_programme("Northwind Systems", "Cyber Apprenticeship", {})
    row = db.get_scout_programmes()[0]
    assert db.set_scout_programme_stage(row["id"], "shortlisted") is False
    assert db.get_scout_programmes()[0]["stage"] is None


def test_a_legacy_stage_is_still_accepted_on_write():
    db.upsert_scout_programme("Northwind Systems", "Cyber Apprenticeship", {})
    row = db.get_scout_programmes()[0]
    assert db.set_scout_programme_stage(row["id"], "interview") is True


# ── 1c. The importer ────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("Not Started", None),
    ("Interested", "interested"),
    ("Applied", "applied"),
    ("OA Complete", "online_assessment"),
    ("Video Interview", "video_interview"),
    ("Assessment Centre", "assessment_centre"),
    ("Offer", "offer"),
    ("Rejected", "rejected"),
    ("Withdrew", "withdrawn"),
])
def test_every_spreadsheet_status_maps_to_the_right_stage(status, expected):
    assert importer.STATUS_TO_STAGE[status.casefold()] == expected


def test_not_started_keeps_the_programme_off_the_board(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        applications=[["Northwind Systems", "Cyber Apprenticeship",
                       "Not Started", "", "", "", "", "", "synthetic note"]])
    importer.run_import(path)

    row = db.get_scout_programmes()[0]
    assert row["stage"] is None, "Not Started must not land on the board"
    assert "northwind systems" in db.get_scout_programme_companies(), \
        "but it stays on the watchlist"


def test_an_unmapped_status_is_skipped_and_counted_by_status(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        applications=[["Northwind Systems", "Cyber Apprenticeship",
                       "Shortlisted", "", "", "", "", "", "synthetic note"]])
    stats = importer.run_import(path)

    assert any("unmapped status: shortlisted" in k for k in stats["skipped"])
    assert db.get_scout_programmes()[0]["stage"] is None


def test_the_category_sheets_and_read_me_are_ignored(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        extra_sheets=("Software Engineering", "Cybersecurity", "Data & AI",
                      "Read Me"))
    stats = importer.run_import(path)

    assert len(stats["sheets_ignored"]) == 4
    assert db.count_scout_programmes()["total"] == 1, \
        "a filtered view must not duplicate the Master List row"


def test_master_list_fields_are_imported(tmp_path):
    row = _master_row("Northwind Systems", "Cyber Apprenticeship")
    row[MASTER_HEADERS.index("Level")] = "6"
    row[MASTER_HEADERS.index("Cloud-Sec Fit")] = "High"
    row[MASTER_HEADERS.index("Locations")] = "Rivertown, Keyport"
    row[MASTER_HEADERS.index("Official Link")] = "https://example.test/nw"
    path = _workbook(tmp_path, master=[row])
    importer.run_import(path)

    got = db.get_scout_programmes()[0]
    assert got["level"] == "6"
    assert got["cloud_sec_fit"] == "High", "Cloud-Sec Fit is stored for sorting"
    assert got["locations"] == "Rivertown, Keyport"
    assert got["official_link"] == "https://example.test/nw"


def test_an_application_without_a_master_row_still_gets_tracked(tmp_path):
    """Losing a tracked application because a reference sheet is incomplete
    would be the worst failure this importer could have."""
    path = _workbook(
        tmp_path,
        applications=[["Ghost Holdings", "Phantom Scheme", "Applied",
                       "", "2026-10-01", "", "", "", "synthetic note"]])
    stats = importer.run_import(path)

    assert stats["results"]["programme not in Master List"] == 1
    assert db.get_scout_programmes()[0]["stage"] == "applied"


# ── Idempotence and the "never overwrite my edits" rule ────────────────────

def test_a_re_import_does_not_duplicate_anything(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship"),
                _master_row("Blackbriar Industries", "Software Apprenticeship")],
        applications=[["Northwind Systems", "Cyber Apprenticeship", "Applied",
                       "", "2026-10-01", "", "", "", "synthetic note"]])
    importer.run_import(path)
    first = db.count_scout_programmes()["total"]

    for _ in range(3):
        importer.run_import(path)

    assert db.count_scout_programmes()["total"] == first == 2


def test_the_key_is_normalised_so_spelling_cannot_duplicate(tmp_path):
    """The sheets spell a company inconsistently; the key has to survive that."""
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        applications=[["  NORTHWIND  systems. ", "cyber apprenticeship",
                       "Applied", "", "", "", "", "", "synthetic note"]])
    importer.run_import(path)

    assert db.count_scout_programmes()["total"] == 1
    assert db.get_scout_programmes()[0]["stage"] == "applied"


def test_a_re_import_refreshes_what_the_spreadsheet_owns(tmp_path):
    row = _master_row("Northwind Systems", "Cyber Apprenticeship")
    row[MASTER_HEADERS.index("Level")] = "4"
    importer.run_import(_workbook(tmp_path, master=[row]))
    assert db.get_scout_programmes()[0]["level"] == "4"

    row[MASTER_HEADERS.index("Level")] = "6"
    importer.run_import(_workbook(tmp_path, master=[row]))
    assert db.get_scout_programmes()[0]["level"] == "6"


def test_a_re_import_never_overwrites_the_tracker_state(tmp_path):
    """The spreadsheet seeds the stage once. After that it is the user's."""
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        applications=[["Northwind Systems", "Cyber Apprenticeship", "Applied",
                       "", "2026-10-01", "", "", "", "synthetic note"]])
    importer.run_import(path)
    row = db.get_scout_programmes()[0]
    assert row["stage"] == "applied"

    db.set_scout_programme_stage(row["id"], "offer",
                                 next_step="synthetic next step")
    importer.run_import(path)

    row = db.get_scout_programmes()[0]
    assert row["stage"] == "offer", "the import reset a stage the user set"
    assert row["next_step"] == "synthetic next step"


def test_a_row_edited_in_scout_is_left_entirely_alone(tmp_path):
    """Even the import-owned fields: a correction made in Scout must not be
    undone by the next import."""
    row = _master_row("Northwind Systems", "Cyber Apprenticeship")
    row[MASTER_HEADERS.index("Level")] = "4"
    importer.run_import(_workbook(tmp_path, master=[row]))

    entry = db.get_scout_programmes()[0]
    db.set_scout_programme_stage(entry["id"], "applied")

    row[MASTER_HEADERS.index("Level")] = "2"
    stats = importer.run_import(_workbook(tmp_path, master=[row]))

    assert db.get_scout_programmes()[0]["level"] == "4", \
        "an import overwrote a field on a row the user had edited"
    assert stats["results"]["skipped"] == 1


# ── Timeline month parsing ─────────────────────────────────────────────────

def test_open_months_are_parsed_from_the_timeline(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        timeline=[["Northwind Systems", "Cyber Apprenticeship",
                   "•", "•", "•", "", "", "", "", "", "", "",
                   "", "", "November", "No", "High"]])
    importer.run_import(path)

    got = db.get_scout_programmes()[0]
    assert got["open_months"] == "sep,oct,nov"
    assert got["timeline_close"] == "November"
    assert got["timeline_fit"] == "High"


@pytest.mark.parametrize("marker", ["•", "●", "x", "X", "yes", "1",
                                    "✓"])
def test_any_non_empty_marker_counts_as_an_open_month(marker):
    """The sheet uses a dot, but hand-edited rows use x/yes/1. Treating only one
    glyph as valid would silently drop months."""
    assert importer._is_marked(marker) is True


@pytest.mark.parametrize("blank", ["", "   ", None, "0", "-", "no", "N"])
def test_an_empty_or_negative_marker_is_not_an_open_month(blank):
    assert importer._is_marked(blank) is False


def test_months_are_stored_in_apprenticeship_year_order(tmp_path):
    """Sep to Aug, not alphabetical — the Upcoming grid reads them in order."""
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        timeline=[["Northwind Systems", "Cyber Apprenticeship"]
                  + ["x"] * 12 + ["Rolling", "Yes", "Low"]])
    importer.run_import(path)
    assert db.get_scout_programmes()[0]["open_months"] == \
        "sep,oct,nov,dec,jan,feb,mar,apr,may,jun,jul,aug"


def test_the_timeline_months_are_stored_as_unconfirmed(tmp_path):
    """They are LAST cycle's windows. A confirmed date is a separate column the
    import never writes."""
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        timeline=[["Northwind Systems", "Cyber Apprenticeship", "x"]
                  + [""] * 11 + ["November", "No", "High"]])
    importer.run_import(path)

    got = db.get_scout_programmes()[0]
    assert got["open_months"] == "sep"
    assert got["confirmed_open_date"] is None
    assert got["confirmed_close_date"] is None
    assert got["confirmed_source"] is None


def test_a_confirmed_date_can_be_entered_later_and_survives_an_import(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")],
        timeline=[["Northwind Systems", "Cyber Apprenticeship", "x"]
                  + [""] * 11 + ["November", "No", "High"]])
    importer.run_import(path)
    row = db.get_scout_programmes()[0]

    db.set_scout_programme_stage(
        row["id"], None, confirmed_open_date="2026-09-15",
        confirmed_source="employer careers page")
    importer.run_import(path)

    row = db.get_scout_programmes()[0]
    assert row["confirmed_open_date"] == "2026-09-15"
    assert row["confirmed_source"] == "employer careers page"


# ── Privacy: the report prints counts, never content ──────────────────────

def test_the_dry_run_prints_no_row_content(tmp_path, capsys):
    """This repo is public. The report must be countable and nothing else."""
    secret_note = "ZZSECRETNOTEZZ"
    secret_company = "ZZSECRETCOMPANYZZ"
    secret_programme = "ZZSECRETPROGRAMMEZZ"
    secret_link = "https://zzsecretlinkzz.test/x"

    row = _master_row(secret_company, secret_programme)
    row[MASTER_HEADERS.index("Notes")] = secret_note
    row[MASTER_HEADERS.index("Official Link")] = secret_link
    path = _workbook(
        tmp_path, master=[row],
        applications=[[secret_company, secret_programme, "Applied", "",
                       "2026-10-01", "ZZREACHEDZZ", "ZZNEXTZZ", "",
                       secret_note]])

    stats = importer.run_import(path, dry_run=True)
    importer.report(stats, dry_run=True)
    out = capsys.readouterr().out

    for secret in (secret_note, secret_company, secret_programme, secret_link,
                   "ZZREACHEDZZ", "ZZNEXTZZ"):
        assert secret not in out, f"the report leaked {secret!r}"
    assert "DRY RUN" in out
    assert "nothing was written" in out


def test_the_real_run_report_prints_no_row_content(tmp_path, capsys):
    secret = "ZZSECRETZZ"
    row = _master_row(secret, secret)
    row[MASTER_HEADERS.index("Notes")] = secret
    path = _workbook(tmp_path, master=[row])

    importer.report(importer.run_import(path), dry_run=False)
    out = capsys.readouterr().out
    assert secret not in out


def test_a_dry_run_writes_nothing(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship")])
    importer.run_import(path, dry_run=True)
    assert db.count_scout_programmes()["total"] == 0


def test_the_dry_run_counts_rows_per_sheet_and_stage(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship"),
                _master_row("Blackbriar Industries", "Software Apprenticeship")],
        applications=[["Northwind Systems", "Cyber Apprenticeship", "Applied",
                       "", "", "", "", "", "synthetic"],
                      ["Blackbriar Industries", "Software Apprenticeship",
                       "Offer", "", "", "", "", "", "synthetic"]])
    stats = importer.run_import(path, dry_run=True)

    assert stats["rows_read"]["Master List"] == 2
    assert stats["rows_read"]["My Applications"] == 2
    assert stats["stages"]["applied"] == 1
    assert stats["stages"]["offer"] == 1
    assert stats["results"]["would upsert"] == 2


# ── CSV export, so the data can leave ─────────────────────────────────────

def test_the_tracker_exports_to_csv(tmp_path):
    db.upsert_scout_programme(
        "Northwind Systems", "Cyber Apprenticeship",
        {"level": "6", "cloud_sec_fit": "High"},
        seed_user_fields={"stage": "applied", "notes": "synthetic note"})

    out = tmp_path / "tracker.csv"
    assert importer.export_csv(str(out)) == 1

    import csv
    with open(out, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["company"] == "Northwind Systems"
    assert rows[0]["stage"] == "applied"
    assert rows[0]["cloud_sec_fit"] == "High"


# ── Programmes become the watchlist ──────────────────────────────────────

def test_imported_programmes_become_the_watchlist(tmp_path):
    path = _workbook(
        tmp_path,
        master=[_master_row("Northwind Systems", "Cyber Apprenticeship"),
                _master_row("Blackbriar Industries", "Software Apprenticeship")])
    importer.run_import(path)

    companies = db.get_scout_programme_companies()
    assert companies == ["blackbriar industries", "northwind systems"]


def test_the_programme_watchlist_drives_the_review_protection():
    """It replaces the placeholder config list: a tracked employer's listing is
    never silently dropped on its standard."""
    watchlist = ["northwind systems"]

    kept = scout_config.judge("Project Manager Apprentice", "NORTHWIND SYSTEMS LTD",
                              "Business administrator (level 4)",
                              watchlist=watchlist)
    assert kept.keep is True
    assert kept.reason == scout_config.KEEP_WATCHLIST_REVIEW

    dropped = scout_config.judge("Project Manager Apprentice", "Unrelated Ltd",
                                 "Business administrator (level 4)",
                                 watchlist=watchlist)
    assert dropped.keep is False


def test_the_config_watchlist_is_the_fallback_before_any_import():
    """A fresh install with no import still protects the obvious names."""
    v = scout_config.judge("Project Manager Apprentice", "BAE SYSTEMS PLC",
                           "Business administrator (level 4)")
    assert v.reason == scout_config.KEEP_WATCHLIST_REVIEW


def test_an_empty_programme_watchlist_does_not_protect_everything():
    v = scout_config.judge("Project Manager Apprentice", "Northwind Systems",
                           "Business administrator (level 4)", watchlist=[])
    assert v.keep is False


# ── Diagnosing applications with no Master List programme ───────────────────
#
# "programme not in Master List: 13" is a number, not a diagnosis. It covers
# three different situations that want three different answers, so the dry run
# now splits it — in COUNTS ONLY, like everything else this script prints.

def _linkable_workbook(tmp_path):
    """One company with a single Master List programme, whose application
    spells the programme differently; one company with two programmes and an
    application matching neither; and one company Master List has never heard
    of. All invented."""
    master = [
        ["Northwind Systems", "Cyber Degree Apprenticeship"],
        ["Orrery Bank", "Technology Apprenticeship"],
        ["Orrery Bank", "Data Apprenticeship"],
    ]
    applications = [
        # Same scheme, written differently -> linkable.
        ["Northwind Systems", "Cyber Security Degree", "Applied"],
        # Company known, two programmes -> ambiguous, never linked.
        ["Orrery Bank", "Software Apprenticeship", "Interested"],
        # Company unknown -> genuinely new.
        ["Castellan Ltd", "Engineering Scheme", "Applied"],
    ]
    return _workbook(tmp_path, master=master, applications=applications)


def test_the_dry_run_splits_the_unmatched_applications_by_cause(tmp_path):
    stats = importer.run_import(_linkable_workbook(tmp_path), dry_run=True)
    um = stats["unmatched"]
    assert um["total"] == 3
    assert um["company_known"] == 2
    assert um["one_programme_company"] == 1
    assert um["linkable"] == 1
    assert um["ambiguous"] == 1
    assert um["company_unknown"] == 1


def test_the_split_is_printed_as_counts_with_no_names(tmp_path, capsys):
    stats = importer.run_import(_linkable_workbook(tmp_path), dry_run=True)
    importer.report(stats, dry_run=True)
    out = capsys.readouterr().out

    assert "applications with no Master List programme:" in out
    assert "probable programme-name mismatch" in out
    assert "ambiguous — never linked" in out
    for name in ("Northwind", "Orrery", "Castellan", "Cyber", "Technology",
                 "Engineering Scheme"):
        assert name not in out, f"{name!r} leaked into the report"


def test_the_suggestion_is_offered_but_never_taken_on_its_own(tmp_path, capsys):
    path = _linkable_workbook(tmp_path)
    stats = importer.run_import(path, dry_run=True)
    importer.report(stats, dry_run=True)
    out = capsys.readouterr().out
    assert "--link-single-programme-companies" in out
    assert "never automatic" in out

    # Without the flag the linkable application still gets its own row.
    importer.run_import(path)
    rows = db.get_scout_programmes()
    northwind = [r for r in rows if r["company"] == "Northwind Systems"]
    assert len(northwind) == 2, "no link was made, so both rows exist"


def test_the_flag_links_only_the_single_programme_company(tmp_path):
    importer.run_import(_linkable_workbook(tmp_path), link_single=True)
    rows = db.get_scout_programmes()

    northwind = [r for r in rows if r["company"] == "Northwind Systems"]
    assert len(northwind) == 1, "the application joined the Master List row"
    assert northwind[0]["programme"] == "Cyber Degree Apprenticeship"
    assert northwind[0]["stage"] == "applied", "and brought its stage with it"

    # The ambiguous one is untouched: still three Orrery Bank rows would be
    # wrong, so it keeps its own and the two Master List rows stand.
    orrery = [r for r in rows if r["company"] == "Orrery Bank"]
    assert len(orrery) == 3
    assert sorted(r["programme"] for r in orrery) == [
        "Data Apprenticeship", "Software Apprenticeship",
        "Technology Apprenticeship"]

    # The unknown company is untouched too.
    assert [r["programme"] for r in rows
            if r["company"] == "Castellan Ltd"] == ["Engineering Scheme"]


def test_the_flag_reports_how_many_it_linked(tmp_path, capsys):
    stats = importer.run_import(_linkable_workbook(tmp_path),
                                link_single=True)
    assert stats["link_applied"] == 1
    importer.report(stats, dry_run=False)
    out = capsys.readouterr().out
    assert "linked to the company's single Master List programme: 1" in out


def test_linking_never_overwrites_an_application_that_already_matched(tmp_path):
    """A Master List row with an application of its own is not a free target:
    taking it would replace that application's stage with another's."""
    path = _workbook(
        tmp_path,
        master=[["Northwind Systems", "Cyber Degree Apprenticeship"]],
        applications=[
            ["Northwind Systems", "Cyber Degree Apprenticeship", "Offer"],
            ["Northwind Systems", "Cyber Security Degree", "Applied"],
        ])
    stats = importer.run_import(path, link_single=True)
    assert stats["unmatched"]["one_programme_company"] == 1
    assert stats["unmatched"]["target_has_own_application"] == 1
    assert stats["unmatched"]["linkable"] == 0
    assert stats["link_applied"] == 0

    rows = [r for r in db.get_scout_programmes()
            if r["company"] == "Northwind Systems"]
    stages = {r["programme"]: r["stage"] for r in rows}
    assert stages["Cyber Degree Apprenticeship"] == "offer", "not overwritten"
    assert stages["Cyber Security Degree"] == "applied"


def test_two_applications_cannot_both_claim_one_master_row(tmp_path):
    path = _workbook(
        tmp_path,
        master=[["Northwind Systems", "Cyber Degree Apprenticeship"]],
        applications=[
            ["Northwind Systems", "Cyber Security Degree", "Applied"],
            ["Northwind Systems", "Cyber Apprenticeship 2027", "Offer"],
        ])
    stats = importer.run_import(path, link_single=True)
    assert stats["unmatched"]["one_programme_company"] == 2
    assert stats["unmatched"]["linkable"] == 1
    assert stats["unmatched"]["link_collisions"] == 1
    assert stats["link_applied"] == 1
    assert len([r for r in db.get_scout_programmes()
                if r["company"] == "Northwind Systems"]) == 2


def test_the_flag_can_be_previewed_with_dry_run_first(tmp_path):
    """So the number can be checked before anything is written."""
    stats = importer.run_import(_linkable_workbook(tmp_path), dry_run=True,
                                link_single=True)
    assert stats["link_applied"] == 1
    # 3 Master List rows + the 2 applications that could not be linked. The
    # linked one folds onto its Master List row instead of adding a sixth.
    assert stats["results"]["would upsert"] == 5
    assert importer.run_import(_linkable_workbook(tmp_path), dry_run=True
                               )["results"]["would upsert"] == 6, (
        "and without the flag it would be six")
    assert db.get_scout_programmes() == [], "and nothing was written"


def test_a_second_run_with_the_flag_changes_nothing_further(tmp_path):
    path = _linkable_workbook(tmp_path)
    importer.run_import(path, link_single=True)
    before = len(db.get_scout_programmes())
    importer.run_import(path, link_single=True)
    assert len(db.get_scout_programmes()) == before


# ── Blank rows are not "skipped rows" ───────────────────────────────────────

def test_rows_that_are_entirely_empty_are_ignored_silently(tmp_path):
    """openpyxl yields a row for anything the sheet has FORMATTED, so a
    workbook with a fill applied below the data returns hundreds of empty
    rows. Counting those as skipped buried the real problems."""
    wb = openpyxl.load_workbook(_workbook(
        tmp_path,
        master=[["Northwind Systems", "Cyber Degree Apprenticeship"]],
        applications=[["Northwind Systems", "Cyber Degree Apprenticeship",
                       "Applied"]]))
    for sheet in ("Master List", "Timeline", "My Applications"):
        for _ in range(40):
            wb[sheet].append([None] * 6)
    path = tmp_path / "with_blank_rows.xlsx"
    wb.save(path)

    stats = importer.run_import(str(path), dry_run=True)
    assert stats["rows_read"]["Master List"] == 1
    assert stats["rows_read"]["My Applications"] == 1
    for reason in stats["skipped"]:
        assert "no company" not in reason, (
            f"a blank row was reported as skipped: {reason}")


def test_a_row_with_content_but_no_company_is_still_reported(tmp_path):
    """Only ENTIRELY empty rows are silent. A row someone half-filled is a
    real problem and must not be swallowed with them."""
    path = _workbook(
        tmp_path,
        master=[["Northwind Systems", "Cyber Degree Apprenticeship"],
                ["", "Orphaned Programme"]])
    stats = importer.run_import(path, dry_run=True)
    assert stats["skipped"]["master row with no company"] == 1
