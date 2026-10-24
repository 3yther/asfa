# Importing the tracker spreadsheet into PRODUCTION

The one-time import that seeds `scout_programmes` from your workbook. Running
it against production means running it **from your Mac, against Railway's
Postgres**, with the file staying on your Mac the whole time.

There is deliberately **no upload page**. A web form would mean the workbook
crossing the network to a public-repo app and sitting in a request body, a temp
file and probably a log line. `railway run` keeps the file where it already is.

---

## What is safe here, and why

| | |
|---|---|
| The `.xlsx` | Never leaves your Mac. `railway run` runs the script **locally** and only injects production's environment variables, so `DATABASE_URL` points at Railway while the file path points at your disk. |
| The repo | `*.xlsx` and `*.xls` are gitignored. The script takes a path argument and reads it in place; it never copies the workbook anywhere. |
| The terminal | The script prints counts, sheet names, stage names and skip reasons. It never prints a row, a note, an application ID or a candidate reference — not in a dry run, not on an error. |
| The database | Imported data goes into `scout_programmes` and nowhere else. |

---

## Before you start

```bash
npm i -g @railway/cli      # or: brew install railway
railway login
```

Then link the project. The values for this app:

- project: **agile-adventure**
- environment: **production**
- service: **asfa**

```bash
cd /path/to/asfa
railway link --project agile-adventure --environment production --service asfa
```

Confirm you are pointed where you think you are **before** writing anything:

```bash
railway status
railway run python -c "import database as db; print(db.USE_POSTGRES, db.count_scout_programmes())"
```

`USE_POSTGRES` must print `True`. If it prints `False`, `DATABASE_URL` did not
reach the process and you are about to write to a local SQLite file instead —
stop and re-link.

---

## 1. Dry run first

```bash
railway run python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx --dry-run
```

This reads the workbook and reports what it *would* do, grouped by sheet and by
stage. It writes nothing. Check the counts look like your spreadsheet — roughly
51 programmes from Master List and roughly 64 application rows.

If a row is skipped the script says why (missing company, missing programme,
unrecognised status) as a count, not as content.

## 2. The real run

```bash
railway run python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx
```

## 3. The count check, afterwards

```bash
railway run python - <<'PY'
import database as db
print("postgres:", db.USE_POSTGRES)
counts = db.count_scout_programmes()
print("programmes:", counts)
print("watchlist companies:", len(db.get_scout_programme_companies()))
rows = db.get_scout_programmes()
print("with a stage:", sum(1 for r in rows if r.get("stage")))
print("with open months:", sum(1 for r in rows if r.get("open_months")))
print("edited in Scout:", sum(1 for r in rows if r.get("edited_in_scout")))
PY
```

What to expect:

- **programmes** matches the dry run's total.
- **watchlist companies** is the distinct company count — this is what replaces
  the placeholder watchlist in `services/scout_config.py`, so from here on a
  listing from one of your companies earns a ★ on Find roles.
- **with a stage** matches the number of My Applications rows that mapped to a
  stage.
- **edited in Scout** is 0 on a first import.

Then open `https://asfa-production.up.railway.app/scout/roles#tracker` and check
the board looks like your spreadsheet.

---

## Re-running it

Safe. The import is keyed on normalised `(Company, Programme)`:

- fields the **spreadsheet owns** are refreshed every run;
- fields **you own** (stage, next step, dates, notes, links, confirmed windows)
  are written only on first insert;
- a row you have touched inside Scout is left completely alone.

So re-running after editing the spreadsheet updates the programme details and
never overwrites your tracker state.

If a re-run reports more programmes than you expect, a company or programme name
was spelled differently; fix the spelling in the spreadsheet rather than deleting
rows in the database.

---

## If `railway run` is not an option

Get the connection string out of Railway (**asfa → Variables → `DATABASE_URL`**)
and pass it for one command, without putting it in your shell history or a file:

```bash
read -rs DATABASE_URL && export DATABASE_URL
python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx --dry-run
unset DATABASE_URL
```

`read -rs` does not echo and does not land in history. Unset it when you are
done. Never paste the URL into a commit, an issue or a chat — it is a database
credential, and this repository is public.

---

## Unrelated, but worth knowing: "never exclude this" on Railway

The Excluded screen's **Never exclude this standard / this employer** buttons
write `config/scout_filters.json`. Railway's container filesystem is
**ephemeral**, so on production that correction holds until the next deploy or
restart and is then back to whatever the repo's copy says.

To make one stick, copy it into the repo's `config/scout_filters.json` and
commit:

```bash
railway run cat config/scout_filters.json
```

Locally the change is permanent, because the file is on your disk.
