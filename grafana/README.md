# ASFA → Grafana monitoring

Wire Grafana to ASFA's Railway Postgres and stand up an **ASFA — Personal
Metrics** dashboard. This folder is the "infra as code" for it:

| File | What it is |
|------|-----------|
| `01-grafana-reader.sql` | Creates the read-only `grafana_reader` Postgres user (default-deny, SELECT on 13 tables only) + verification queries |
| `asfa-personal-metrics.json` | The full dashboard — 5 rows, 13 panels — importable straight into Grafana |
| `README.md` | This runbook |

> **What's automated vs. manual.** The schema recon, the read-only user SQL, and
> every panel query are done and committed here. The steps that need *your*
> Railway/Grafana login — running the SQL, creating the data source, importing
> the dashboard, wiring the alert — are the checklists below. They're
> paste-and-go.

---

## Phase 0 — Recon (done; findings below)

### 0.1 Railway Postgres connection
Grafana Cloud lives outside Railway's private network, so it must use the
**public proxy** endpoint, not the `*.railway.internal` host.
Get it from **Railway → Postgres service → "Connect" tab → "Public Network"**:

- Host: `<something>.proxy.rlwy.net`  ← the public TCP proxy host
- Port: a high port like `5xxxx` (Railway assigns it per service)
- Database: usually `railway`
- The internal host `postgres.railway.internal:5432` will **not** resolve from Grafana Cloud — don't use it.

> **Decision — Grafana Cloud (hosted), not local Docker.** Rationale: zero local
> infra, nothing to keep running on your laptop, and it reaches Railway over the
> public proxy — which is the realistic path for a hosted dashboard you'd demo
> in an interview. Local Docker (`docker run -d -p 3000:3000 grafana/grafana-oss`)
> is a fine alternative if you specifically want Docker/ops on your CV — the data
> source + dashboard steps below are identical either way.

### 0.2 Schema — real column names (verified against `database.py`)
The task's guessed table names were partly off. **Actual** schema the queries use:

| Module | Table | Date column | Key columns |
|--------|-------|-------------|-------------|
| Nutrition | `meals` | `date` (TEXT) | `protein, carbs, fat, calories` (calories nullable → Atwater fallback in SQL) |
| Nutrition goals | `nutrition_goals` | — | `protein_goal, carbs_goal, fat_goal, calorie_goal` |
| Sleep | `sleep` | `date` (TEXT, UNIQUE) | `duration` (hrs), `quality` (1–5). **Readiness is NOT stored** — computed in SQL, mirroring `score_readiness()` |
| Finance | `spending` | `date` (TEXT) | `amount, category, merchant, source` |
| Water | `hydration_log` | `date` (TEXT) | `amount_ml` |
| Supplements | `supplements_log` | — | `supplement_name, taken_at` (TEXT timestamp — **no `date` column**; use `substr(taken_at,1,10)`) |
| Steps | `steps` | `date` (TEXT) | `source, steps` — **multiple rows/day** (per source) → `SUM(steps)` per date |
| Steps goal | `steps_goal` | — | `steps_goal` |
| Gym sessions | `gym_sessions` | `date` (TEXT) | `total_volume_kg` (pre-computed per session) |
| Gym sets | `gym_sets` | — | `weight_kg, reps, is_pr, exercise_id, session_id` |
| Gym PRs | `gym_prs` | `achieved_at` | `weight_kg, reps, one_rep_max, exercise_id` |
| Gym exercises | `gym_exercises` | — | `id → name` (join lookup for exercise names) |
| Body comp | `body_composition` | **`date_scanned`** (not `date`) | `weight_kg, bmi, body_fat_percent, ...` |

**Adjustments from the task's guesses:** no `workouts` table for training volume
(it's `gym_sessions.total_volume_kg`); PR timeline joins `gym_sets`+`gym_exercises`+`gym_sessions`
(exercise *names* aren't on the sets table); `body_composition` uses `date_scanned`;
`supplements_log` has only `taken_at`, no `date`. All `date` columns are **TEXT
`YYYY-MM-DD`**, so every query casts `date::timestamptz` for `$__timeFilter()`.

### 0.3 Connection limits
Railway's Postgres default `max_connections` is ~**100 (shared Hobby/Trial), up to
~500 on larger plans**. Confirm yours with `SHOW max_connections;`. Grafana's
Postgres data source pools connections — **cap it** in the data-source settings:
Max open = 5, Max idle = 2, Max lifetime = 14400s. That's plenty for a 13-panel
dashboard on a ~30s refresh and leaves headroom for ASFA's own app + the
5-minute schedulers. Don't leave it unbounded.

### 0.4 Read-only user — **run `01-grafana-reader.sql`**
Default-deny by construction: a fresh role has no table rights, so `grafana_reader`
can read **only** the 13 explicitly-granted tables. `agent_audit_log`,
`auth_failures`, `claude_api_calls`, `csp_reports`, `conversations`, `memories`,
`kv_store` are never granted → `permission denied`.

Run it as the DB owner via **Railway → Postgres → "Data" → "Query"**, or:
```bash
railway run psql "$DATABASE_URL" -f grafana/01-grafana-reader.sql
```
Then paste the output of the three `VERIFY` queries at the bottom of the file
into your notes. Expect: exactly 13 tables with `SELECT`, zero sensitive tables,
zero write privileges.

> ⚠️ **Change the DB name.** The SQL hardcodes `railway` in the `GRANT CONNECT`
> lines — if `\l` / the Connect tab shows a different database name, edit those.
> Also rotate the pre-generated password if it's been shared in plaintext.

---

## Phase 1 — Connect Grafana to Postgres

1. Sign up at **grafana.com** → create a free Grafana Cloud stack (or `docker run
   -d -p 3000:3000 grafana/grafana-oss` and open `localhost:3000`).
2. **Connections → Data sources → Add data source → PostgreSQL**:
   - **Host:** `<public-proxy-host>:<port>` from Phase 0.1
   - **Database:** `railway` (or your actual DB name)
   - **User:** `grafana_reader`  ← the read-only user, **not** the app's main user
   - **Password:** the one in `01-grafana-reader.sql`
   - **TLS/SSL Mode:** `require`  ← Railway enforces TLS. Do **not** pick `disable`.
   - **Connection limits:** Max open 5 / Max idle 2 / Max lifetime 14400 (Phase 0.3)
   - **PostgreSQL version:** 15 (or match `SHOW server_version;`)
   - Click **Save & test** → must say "Database Connection OK" before continuing.
3. **Prove the read-only scope** in Explore (pick the data source, run raw SQL):
   - `SELECT * FROM meals LIMIT 5;` → returns rows ✅
   - `SELECT * FROM agent_audit_log LIMIT 1;` → **`permission denied for table
     agent_audit_log`** ✅ ← this is the security proof; screenshot it.

---

## Phase 2 — Import the dashboard

1. **Dashboards → New → Import → Upload JSON file** → `asfa-personal-metrics.json`.
2. When prompted for the **`ASFA Postgres`** input, pick the data source from Phase 1.
3. Import. Default range is **Last 30 days**; every time-series/agg panel uses
   `$__timeFilter(...)`, so the top-right picker drives them all consistently.
   (Deliberately *not* picker-bound: "Macro Split (Today)", "Protein Consistency
   (last 7d)", "Monthly Spending Pace", "PR Timeline", "Days Since Last Entry" —
   these are point-in-time by design.)

**The 13 panels, row by row:**
- **NUTRITION** — Daily Calories (bars) vs red goal line · Protein Consistency %
  (7-day bar gauge) · Macro Split donut (today's P/C/F grams)
- **SLEEP & RECOVERY** — Sleep Duration (hrs, left axis) + Quality (bars, right
  axis) · Readiness Score trend (0–100, computed, green/orange/red thresholds)
- **GYM & STEPS** — Training Volume bars (Σ `total_volume_kg`/day) · Daily Steps
  bars vs green goal line · PR Timeline table (is_pr sets, newest first)
- **FINANCE & HYDRATION** — Spending by Category pie · Water Intake bars vs 2,500ml
  blue target line · Monthly Spending Pace stat (MTD + projected month-end)
- **SYSTEM (observability)** — Rows Logged per Day (stacked bars per table — how
  consistently ASFA is used) · Days Since Last Entry per Module (table, cells go
  orange ≥3d / red ≥5d so a stalled habit is obvious at a glance)

If a panel shows **"No data"**: it almost always means that table has no rows yet
for the selected range (e.g. no `body_composition` scans, no gym PRs) — not a
broken query. Widen the picker or log a row to confirm.

---

## Phase 3 — Alerting (proof-of-concept)

**Alert:** "Water intake below 1,000 ml for 2 consecutive days."

1. **Alerting → Alert rules → New alert rule.**
2. **Query A** (data source = ASFA Postgres), `format = table`:
   ```sql
   SELECT date, SUM(amount_ml) AS ml
   FROM hydration_log
   WHERE date::date >= CURRENT_DATE - 2
   GROUP BY date
   HAVING SUM(amount_ml) < 1000
   ```
3. **Expression B** — `Reduce` A with `Count` → **Expression C** — `Threshold`:
   `IS ABOVE 1` (i.e. 2 days both under 1,000 ml present → fire).
4. **Evaluate** every 1h, **for** 0m. **Contact point:** email (Grafana Cloud has
   a built-in SMTP), or a webhook to ASFA's Telegram bot — reuse
   `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`: contact point type *Webhook*,
   URL `https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat_id>`,
   or a small relay if you want a formatted message.
5. **Test it:** temporarily change `< 1000` to `< 100000` so recent days qualify,
   save, wait one eval cycle, confirm the notification lands, then **reset to
   `< 1000`**. This is the difference between a dashboard and monitoring.

---

## Verify checklist

- [ ] `grafana_reader` reads meals ✅ / `agent_audit_log` → permission denied ✅ (Phase 1.3 screenshot)
- [ ] Data source "Save & test" = OK, **SSL Mode = require** (not disable)
- [ ] All 13 panels render real data (or "No data" explained by an empty table)
- [ ] Time-range picker moves all `$__timeFilter` panels together
- [ ] Alert fires on the forced-low threshold, then threshold reset to 1,000 ml
- [ ] The three `VERIFY` query outputs from `01-grafana-reader.sql` saved
