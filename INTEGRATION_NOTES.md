# INTEGRATION_NOTES.md — Apprenticeship Radar → Scout

Phase 0 audit + every mapping / convention decision made while merging
**Apprenticeship Radar** (`~/Downloads/apprenticeship-radar`) into **Scout**.

---

## ⚠️ Two findings that change the plan

### Finding 1 — Scout is NOT a SQLAlchemy app

The task brief says *"Both are Flask + SQLAlchemy apps."* That is true of Radar
but **false of Scout**.

Scout is not a standalone project — it is a feature module inside **ASFA**
(`/Users/amirsalah/stock-scanner/asfa`), consisting of `services/scout.py`,
`templates/scout.html`, the `scout_*` tables in `database.py`, and the
`/scout` + `/api/scout/*` routes in `app.py`.

ASFA has **no SQLAlchemy anywhere in the Flask app**:

- `requirements.txt` contains no `SQLAlchemy` / `Flask-SQLAlchemy` entry.
- `pip list` in `.venv` shows no SQLAlchemy installed.
- `database.py` is ~366k of hand-written SQL over raw `sqlite3` /
  `psycopg2`, exposed as module-level functions (`db.add_scout_job(...)`,
  `db.get_scout_jobs(...)`).

The only SQLAlchemy in the tree is `odysseus/` — a separate vendored FastAPI
project, explicitly excluded from pytest collection via `norecursedirs` in
`pytest.ini`. It is not part of the Flask app and is not relevant here.

**Decision:** per the brief's own constraint — *"When Radar and Scout disagree
on convention, Scout wins"* — Radar's `db.Model` classes are **rewritten as raw
SQL** in ASFA's `database.py` style, not copied. Adding Flask-SQLAlchemy to ASFA
would mean a second ORM/session layer alongside 366k of working raw SQL, which
directly violates *"one database"* and *"additive changes only"*.

Concretely this means the brief's literal code snippets do not survive verbatim:

| Brief says | Actual implementation |
|---|---|
| `class Employer(db.Model)` | `CREATE TABLE scout_employers` + `db.*_scout_employer()` functions |
| `Employer.query.filter_by(watching=True)` | `db.get_scout_employers(watching_only=True)` |
| `nullable=True`, `db.Column(...)` | `_add_column(cur, "scout_jobs", ..., "TEXT")` |
| Alembic migration | idempotent `_add_column` in `_ensure_scout_tables()` — see Migration below |

The `alias_list` property becomes a helper `db.employer_alias_list(row)` /
inline expansion, since rows come back as plain dicts.

### Finding 2 — `source` column name collision

The brief asks for a new `source` column meaning `'job'` / `'apprenticeship'`.
**`scout_jobs.source` already exists and means something else** — it holds the
*provider*: `"reed"`, `"google_jobs"`, or the SerpAPI `via` string. It is
written by `services/scout.py:_store()` and `scan_jobs_reed()`.

Overloading it would destroy provider information on every existing row and
break the current scan path — a direct violation of *"don't break existing Scout
job functionality."*

**Decision:**

- Keep `scout_jobs.source` as the **provider** (`reed` / `google_jobs` /
  `gov_uk`) — unchanged semantics, no data migration.
- Add a new column **`listing_type`** (`'job'` | `'apprenticeship'`,
  default `'job'`, indexed) for the split the brief wants.
- **Keep the public filter query param named `?source=`** exactly as the brief
  specifies (`?source=apprenticeship`). The route maps `?source=` →
  `listing_type` internally. So the requested URL/UI contract is honoured; only
  the physical column name differs.

---

## Phase 0 answers

### 1. ORM style
**Neither Flask-SQLAlchemy nor plain SQLAlchemy.** Raw SQL via `database.py`:

```python
with get_db() as conn:
    cur = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT * FROM scout_jobs WHERE url = {ph}", (url,))
    return [dict(r) for r in cur.fetchall()]
```

Conventions observed and followed:
- `get_db()` context manager commits on exit; `USE_POSTGRES` switches the
  placeholder between `%s` and `?`.
- Rows are returned as **plain `dict`s** (`RealDictCursor` on Postgres,
  `sqlite3.Row` → `dict()` locally).
- Tables self-initialise lazily: `_ensure_scout_tables()` guarded by a
  module-level `_SCOUT_READY` flag, called at the top of every scout function.
- `CREATE TABLE IF NOT EXISTS` statements are written SQLite-first, then
  `.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")` for
  Postgres.
- New columns are added with the idempotent `_add_column(cur, table, col, def)`.

### 2. Existing job model
Not a class — the table **`scout_jobs`** (`database.py:3017`). Exact columns:

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT (SERIAL on PG) | |
| `title` | TEXT | |
| `company` | TEXT | employer display name |
| `location` | TEXT | |
| `salary` | TEXT | free text, e.g. `"25000"` or `""` |
| `job_type` | TEXT | currently always `"part time"` |
| `url` | TEXT | **dedup key for jobs** (`scout_job_exists`) |
| `description` | TEXT | |
| `source` | TEXT | **provider**: `reed` / `google_jobs` / SerpAPI `via` |
| `posted_date` | TEXT | raw string, both `"25/06/2026"` and `"3 days ago"` |
| `found_date` | TEXT | `"%Y-%m-%d %H:%M:%S"` |
| `is_new` | INTEGER DEFAULT 1 | 1 if posting < ~24h old |
| `applied` | INTEGER DEFAULT 0 | |

Related existing tables: `scout_applications`, `scout_pipeline` (Kanban board
with `cv_match_score`, `missing_keywords`, `match_analysis_at`).

**There is no employer/company/watchlist table** — so `scout_employers` is
created new, per the brief.

### 3. Existing scraper interface
**Module-level functions, not classes.** `services/scout.py` exposes:

- `scan() -> int` — full pass over all sources, persists, emails, returns the
  count inserted. This is the scheduled entry point.
- `scan_jobs_reed(keywords, location="", limit=20) -> list[dict]` — param-driven
  search used by the skill executor; returns plain dicts.
- Private `_fetch_reed(...)`/`_fetch_serpapi(...) -> int` build a plain `dict`
  per listing and hand it to `_store(job, found_date, seen_urls, collected)`,
  which applies the filter and calls `db.add_scout_job(...)`.

So the convention is: **plain `dict` in memory → `db.add_scout_job(**fields)`
→ `bool` inserted.** Every network step is wrapped defensively — a failed
request yields zero results rather than raising.

### 4. Existing scheduler
**APScheduler `BackgroundScheduler`**, built in
`services/scheduler.py:start_scheduler()` (13 jobs). Scout's own job is
registered separately in `app.py:_start_background()` (~line 4223):

```python
sched.add_job(scout_daily_scan, "cron", hour=6, minute=0, id="scout_daily_scan")
```

`scout_daily_scan()` (`app.py:4076`) wraps `scout.scan()`, pings the bell via
`db.add_notification`, and records `db.log_audit("scout", "daily_scan", ...)` +
`db.update_error_budget(...)`.

Double-start is prevented by the `ASFA_BG_STARTED` env flag, and the Procfile
pins `--workers 1`.

### 5. Existing notifier
Two paths, both already in place:

- **Email:** `services/scout.py:_send_email(jobs)` — Gmail SMTP
  (`smtp.gmail.com:587`, STARTTLS), `MIMEText(body, "plain", "utf-8")`,
  credentials from `SCOUT_EMAIL_USER` / `SCOUT_EMAIL_PASS`, hardcoded
  `NOTIFY_EMAIL_TO = "ami.salax08@gmail.com"`. Subject:
  `f"SCOUT — {n} new job{'s' if n != 1 else ''} found"`. Body is **plain text**,
  one bullet per job:
  ```
  • {title} — {company}
    {location}  |  posted: {posted_date}
    {url}
  ```
  Best-effort: any failure is logged and swallowed.
- **In-app bell:** `db.add_notification(msg, "scout")` from `scout_daily_scan`.

There is **no alerts table** in Scout — alert outcomes go to `log_audit`.

### 6. Dashboard
- Route: `@app.route("/scout")` → `scout_page()` → `templates/scout.html`
  (`app.py:3619`).
- Data: `GET /api/scout/jobs?location=&new_only=` → `db.get_scout_jobs(...)`.
- The listings table is in `scout.html` (~line 270), columns
  `Title | Company | Location | Posted | Status | Apply`, rendered client-side
  by `loadJobs()` (~line 419).
- Existing filter UI is a `<select id="loc-filter">` inside `.filter-bar`
  (~line 255) — **not** chips. The brief asks for query-param chips; I add a
  chip row for source next to the existing location `<select>` rather than
  converting the location filter, to keep the change additive.
- All fetches go through the local `apiGet`/`apiPost` helpers, which set
  `credentials: "include"` (required — the `before_request` gate is
  cookie-based).

### 7. Deploy
- **Railway**, `https://asfa-production.up.railway.app`.
- `Procfile`: `web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120`
- **No `railway.json`.**
- **Postgres in prod** via `DATABASE_URL`; **SQLite `asfa.db`** locally
  (`USE_POSTGRES` switches).
- Scheduler runs **in-process**, not a separate worker — safe because
  `--workers 1` + the `ASFA_BG_STARTED` guard. No extra locking needed;
  if worker count ever rises, the guard must become a real lock.
- No Alembic, no migrations directory — schema evolves via
  `CREATE TABLE IF NOT EXISTS` + `_add_column`.

---

## Field mapping: Radar `Vacancy` → Scout `scout_jobs`

**Reused (no new column):**

| Radar field | Scout column | Note |
|---|---|---|
| `title` | `title` | |
| `location` | `location` | |
| `wage` | `salary` | Scout's salary is already free-text, so `"£17,000 a year"` fits |
| `url` | `url` | |
| `posted_text` | `posted_date` | already free-text in Scout |
| `first_seen` | `found_date` | same meaning, same `"%Y-%m-%d %H:%M:%S"` format |
| — | `job_type` | set to `"apprenticeship"` (jobs use `"part time"`) |
| — | `source` | set to `"gov_uk"` — matches the existing provider vocabulary |
| employer display | `company` | matched employer's canonical name if matched, else the raw gov.uk string, so the existing table renders correctly |

**New columns on `scout_jobs`** (all nullable / defaulted, existing rows
unaffected):

| Column | Type | Purpose |
|---|---|---|
| `listing_type` | TEXT DEFAULT `'job'`, indexed | `'job'` \| `'apprenticeship'` — see Finding 2 |
| `external_ref` | TEXT, unique, indexed | gov.uk `VAC…` reference; dedup key for apprenticeships |
| `employer_id` | INTEGER | FK → `scout_employers.id` |
| `employer_name_raw` | TEXT | raw gov.uk employer string, kept regardless of match |
| `level` | INTEGER | 3/4/6/7; NULL for jobs |
| `training_course` | TEXT | e.g. "Digital and technology solutions professional (level 6)" |
| `closing_text` | TEXT | raw close string |
| `closing_date` | TEXT (ISO `YYYY-MM-DD`) | parsed close date |
| `start_date` | TEXT (ISO) | parsed start date — not in the brief's table, but `RawVacancy` already parses it and it costs nothing to keep |
| `status` | TEXT DEFAULT `'open'` | `open` \| `closed`; drives closure detection |
| `last_seen` | TEXT | refreshed every poll; drives closure detection |
| `alerted` | INTEGER DEFAULT 0 | set after a successful alert |

Dates are stored as **ISO `TEXT`**, not a `Date` type — every other date in
ASFA's schema is TEXT, and it sorts/compares correctly in both SQLite and
Postgres. The brief's `Date` type is a SQLAlchemy concept that doesn't apply
here.

`unique` on `external_ref` is enforced by a `CREATE UNIQUE INDEX … WHERE
external_ref IS NOT NULL` (partial index) so the thousands of existing job rows
with `NULL` don't collide.

**New tables:**

- `scout_employers` — the watchlist. Same shape as Radar's `Employer`, named
  with the `scout_` prefix to match every other Scout table. `created_at` is
  TEXT, `watching` is INTEGER 0/1 (no native bool in SQLite).
- `scout_scan_logs` — Radar's `ScanLog`. **The column holding the search term is
  named `query_text`**, per the brief's critical-bug warning. Since there is no
  ORM here the shadowing bug cannot occur, but the name is kept for consistency
  with Radar and to avoid `query` being a reserved-ish word in raw SQL.

**No `scout_alerts` table.** Scout logs alert outcomes through
`db.log_audit(...)` + `db.add_notification(...)`; adding Radar's `Alert` table
would be a parallel second system, against *"one notifier."*

## Scraper output type decision

**`RawVacancy` is kept as an internal DTO.** The dataclass stays exactly as
Radar has it (it's what the parser is written against and what makes the dedup
readable), and conversion to Scout's convention — a plain `dict` handed to
`db.add_scout_job(...)` — happens in the persist step
(`services/apprenticeships.py:_persist_vacancies`). This preserves the tested
parsing behaviour byte-for-byte while matching Scout's `dict → db.add_*`
convention at the boundary.

## Migration

ASFA has no Alembic. Schema changes land as idempotent DDL inside
`_ensure_scout_tables()` — `CREATE TABLE IF NOT EXISTS` for the two new tables
and `_add_column()` for each new `scout_jobs` column. This runs automatically on
first use in both SQLite and Postgres, so **no manual migration command is
needed** on Railway; the redeploy applies it.

## Scheduler decision

**Two jobs on separate cadences**, not one merged cycle:

- `scout_daily_scan` (existing, 06:00 daily) — untouched.
- `scout_apprenticeship_scan` — `interval`, `APPRENTICESHIP_POLL_MINUTES`
  (default **360** = 6h).

Rationale: Scout's job scan is daily, so it does *not* poll more often than 6h
and the brief's own rule would allow reuse — but the two have genuinely
different natural cadences (gov.uk postings move faster than the daily digest,
and apprenticeship closure detection needs multiple polls per day to work). Two
jobs on the same `sched` object keeps it *one scheduler*, which is what the
constraint actually protects.

The closing-soon pass rides on the same job, guarded to once per day via
`log_audit` lookup.

## New env vars

| Var | Default | Purpose |
|---|---|---|
| `KEYWORD_SCANS` | `cyber security,software developer,data,cloud,network engineer,devops` | comma-separated keyword scans |
| `APPRENTICESHIP_POLL_MINUTES` | `360` | poll interval |
| `APPRENTICESHIP_MIN_LEVEL` | `4` | minimum apprenticeship level |

Email reuses the existing `SCOUT_EMAIL_USER` / `SCOUT_EMAIL_PASS`.
