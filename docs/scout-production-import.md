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
| The `.xlsx` | Never leaves your Mac. `railway run` runs the script **locally** and only injects Railway's environment variables, so the database URL points at Railway while the file path points at your disk. |
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

### You need the PUBLIC database URL, not DATABASE_URL

This is the part that bites. The `asfa` service's `DATABASE_URL` points at
`postgres.railway.internal`, which **only resolves inside Railway's private
network**. `railway run` executes the command *on your Mac* and merely injects
the variables, so that hostname is unreachable and the import fails with a DNS
or connection error.

The reachable one is **`DATABASE_PUBLIC_URL`**, and three things are true about
it:

1. it lives on the **Postgres service**, not on `asfa`;
2. it only exists once **Public Access** is on — Railway dashboard →
   the Postgres service → **Settings → Networking → Public Access**. That
   provisions a TCP proxy and populates the variable. If the variable is
   missing after enabling it, redeploy the service so it picks up the new
   networking variables;
3. traffic through that proxy is billed as **network egress**. For a one-time
   import of a few dozen rows that is negligible, but it is why the database is
   private by default — leave Public Access off again afterwards if you prefer.

Check the variable exists without printing its value:

```bash
railway variables --service Postgres --kv | grep -c '^DATABASE_PUBLIC_URL='
```

`1` means it is set. (Newer CLI versions spell this `railway variable list
--kv`; use whichever your version accepts.) Don't print the value — it is a
database credential and this repository is public.

### Local dependencies

`railway run` runs the script **on your Mac**, so your Mac needs the project's
Python dependencies — `psycopg2-binary` above all, or the import dies on
`ModuleNotFoundError: No module named 'psycopg2'` before it reaches the
database:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### The preflight

Run this **before** writing anything. It connects over the public URL and
reports what it found, without ever echoing the credential:

```bash
railway run --service Postgres -- sh -c '
  DATABASE_URL="$DATABASE_PUBLIC_URL" python - <<PY
import os, database as db
url = os.environ.get("DATABASE_URL", "")
print("USE_POSTGRES:", db.USE_POSTGRES)
print("internal hostname (must be False):", "railway.internal" in url)
print("programmes already there:", db.count_scout_programmes()["total"])
PY
'
```

The single quotes matter: they stop your local shell expanding
`$DATABASE_PUBLIC_URL` before `railway run` has injected it. The inner `sh` is
what expands it, after Railway has put the Postgres service's variables in the
environment.

What you need to see:

- **`USE_POSTGRES: True`** — if this prints `False`, no `DATABASE_URL` reached
  the process and you are about to write to a local SQLite file instead. Stop.
- **`internal hostname (must be False): False`** — if this prints `True` you
  picked up `DATABASE_URL` rather than `DATABASE_PUBLIC_URL`, and the connection
  will not work from your Mac.
- **`programmes already there:`** a number. Reaching this line at all means the
  connection succeeded, which is the real proof.

---

## 1. Dry run first

```bash
railway run --service Postgres -- sh -c '
  DATABASE_URL="$DATABASE_PUBLIC_URL" \
  python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx --dry-run
'
```

This reads the workbook and reports what it *would* do, grouped by sheet and by
stage. It writes nothing. Check the counts look like your spreadsheet — roughly
51 programmes from Master List and roughly 64 application rows.

If a row is skipped the script says why (missing company, missing programme,
unrecognised status) as a count, not as content.

Note that the `.xlsx` path is on **your Mac**. `railway run` only injects
environment variables; the file is read locally and never uploaded.

## 2. The real run

Same command without `--dry-run`:

```bash
railway run --service Postgres -- sh -c '
  DATABASE_URL="$DATABASE_PUBLIC_URL" \
  python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx
'
```

## 3. The count check, afterwards

```bash
railway run --service Postgres -- sh -c '
  DATABASE_URL="$DATABASE_PUBLIC_URL" python - <<PY
import os, database as db
print("USE_POSTGRES:", db.USE_POSTGRES)
print("internal hostname (must be False):",
      "railway.internal" in os.environ.get("DATABASE_URL", ""))
print("programmes:", db.count_scout_programmes())
print("watchlist companies:", len(db.get_scout_programme_companies()))
rows = db.get_scout_programmes()
print("with a stage:", sum(1 for r in rows if r.get("stage")))
print("with open months:", sum(1 for r in rows if r.get("open_months")))
print("edited in Scout:", sum(1 for r in rows if r.get("edited_in_scout")))
PY
'
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

If you turned Public Access on just for this, you can turn it off again now.

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

Copy the connection string out of Railway — **the Postgres service → Variables
→ `DATABASE_PUBLIC_URL`**, NOT `asfa → DATABASE_URL`, which is the internal
hostname your Mac cannot reach — and pass it for one command, without putting it
in your shell history or a file:

```bash
read -rs DATABASE_URL && export DATABASE_URL
python scripts/import_scout_spreadsheet.py ~/Documents/apprenticeships.xlsx --dry-run
unset DATABASE_URL
```

`read -rs` does not echo and does not land in history. Unset it when you are
done. Never paste the URL into a commit, an issue or a chat — it is a database
credential, and this repository is public.

---

## Unrelated, but worth knowing: how "never exclude this" is stored

The Excluded screen's **Never exclude this standard / this employer** buttons
write the `scout_filter_overrides` table, not a file, so they survive a Railway
deploy. The filter config resolves in three layers:

1. the defaults in `services/scout_config.py`;
2. `config/scout_filters.json` — the hand-editable file layer, still the right
   place for a rule you want in version control;
3. `scout_filter_overrides` — the corrections made from the screen, applied as
   a **delta** over both.

A delta rather than a replacement, so the file keeps meaning: edit it and the
stored corrections still ride on top of the new value.

Each correction is listed under **Allowed** on the Excluded screen with an
**Undo** button, which is the only way back — they are database rows, not a
file you can edit by hand.

To see them from the command line:

```bash
railway run --service Postgres -- sh -c '
  DATABASE_URL="$DATABASE_PUBLIC_URL" python - <<PY
import database as db
for row in db.list_scout_filter_overrides():
    print(row["op"], row["list_key"], "->", row["value"])
PY
'
```
