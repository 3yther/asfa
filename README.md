# ASFA — a personal OS

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)
![Deploy](https://img.shields.io/badge/deploy-Railway-0B0D0E?logo=railway&logoColor=white)

ASFA is a single-user, password-gated life dashboard that pulls the scattered
signals of one person's day — email, calendar, music, news, weather, workouts,
sleep, nutrition, money, habits, job hunting, and live trading-bot P&L — into one
dark telemetry console with an Anthropic-backed AI layer on top. It's built for
me: an always-on command centre I actually open every morning, not a product for
a market. Everything runs from one Flask app on Railway, one Postgres database in
prod, and a fleet of scheduled jobs that nudge me before I have to think.

---

## Current state

Built in numbered tiers. Each tier is a self-contained slice — a data model, its
routes, its card(s), and (for the recent ones) its tests — that ships and gets
reviewed on its own branch before merge.

| Tier | Area | What it does |
|---|---|---|
| **1** | Core dashboard | Auth gate, morning briefing, Gmail + Calendar, weather, news, the AI chat orb. |
| **2** | Health & training | RPE-driven gym logging, CSV import, body-composition tracking, Renpho scale sync, MCP wiring. |
| **3** | Polish | Hash-audited data, scent/fragrance ratings, card-layout system, mission-control live view, Telegram digest, fragrance DB. |
| **4** | Habits & goals | Streaks, supplement/water reminders, reflections, weekly AI review. |
| **5** | API cost control | Claude vision-cache, opt-in briefings, per-route rate limits, token telemetry. |
| **6** | Sleep | Nightly sleep logging + trends, feeds the daily readiness picture. |
| **7** | Nutrition | Per-meal logging, macros/calorie roll-ups by day, spending-adjacent food tracking. |
| **8** | Finance | Richer money telemetry — spending by category, balances, and the live bot-trading P&L card on the FUNDS tab. |

Tiers 6–8 (Sleep, Nutrition, Finance), the locked design system, and the CDN-hang
fix all landed in the most recent working session.

---

## Stack

- **Backend:** Python 3.12, Flask 3.0, gunicorn (1 worker / 8 threads)
- **Scheduling:** APScheduler 3.10, in-process — **17 background jobs**
- **AI:** `anthropic` 0.109 — briefings, insights, chat, vision photo-logging
- **Storage:** PostgreSQL in prod (`DATABASE_URL`, psycopg2); SQLite (`asfa.db`) locally
- **Frontend:** server-rendered Jinja + vanilla JS (no framework), design-system CSS tokens
- **Integrations:** Google (Gmail, Calendar), Spotify, NewsAPI, OpenWeatherMap, Telegram
- **Extras:** stdio **MCP server** (`scripts/asfa_mcp.py`) exposing ASFA as Claude-drivable tools
- **Deploy:** Railway — `Procfile` → `gunicorn app:app`

---

## Features — the real loops

ASFA isn't a metrics wall; it's a set of daily loops that close.

- **Gym → readiness.** RPE-tagged sets, body comp, and Renpho scale imports build a
  training history. Sleep (Tier 6) feeds the same readiness picture. *(The automatic
  gym→readiness wire is designed but not yet live — see below.)*
- **Spending by category.** Tier 8 rolls transactions into category totals and balances,
  sitting next to the live trading-bot P&L so "what I earn" and "what I spend" share a screen.
- **Job-hunting pipeline.** Application tracking with an Indeed-backed search surface and
  resume/company context wired through MCP tooling.
- **Nutrition logging.** Per-meal entries roll up into daily macros and calories, including
  AI vision logging — photograph the plate, get the entry.
- **Sleep tracking.** Nightly duration + trend, one row per night, no double-store.
- **Morning briefing.** One Anthropic call that already knows the week — calendar, weather,
  unread mail, habits, bot P&L — spoken aloud via an iPhone alarm shortcut if you want it.
- **Proactive nudges.** Bedtime, hydration, supplements, market-open, reflection, and a
  Sunday AI-written weekly review, delivered to Telegram and the in-app bell.

What makes it different is discipline, not surface area: **one source of truth per module.**
Sleep lives in the sleep tables, nutrition in the nutrition tables, finance in the finance
tables — no metric is written twice and reconciled later.

---

## Design system

A locked dark-telemetry aesthetic. Tokens live in `static/css/style.css` under `:root`
and are treated as fixed — components consume them, nothing hard-codes a hex.

- **Palette:** near-black voids (`--void #05080D`, `--panel #0B111A`), cyan primaries
  (`--cyan #3DE1F0`), and a status set — `--online` green, `--warn` amber, `--alert` red.
- **Series colours:** a four-step categorical ramp (`--series-1..4`) for charts, so every
  graph in the app reads as one system.
- **Type:** IBM Plex Mono for telemetry, Inter for prose.
- **Spacing & radius:** a fixed scale (`--s1..s7`, `--r-sm/-md/-pill`) and one shared easing curve.
- **Components:** cards, buttons, inputs, and meta-strips are built once against the tokens
  and reused across every tier, so a new tier inherits the look for free.

---

## How to run

```bash
# Python 3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # fill in your keys (see below)
python app.py                 # http://localhost:5000  (SQLite asfa.db, auto-inits)
```

`db.init_db()` runs on boot and creates the schema, so there's no separate migration
step for local dev — first run bootstraps `asfa.db`.

### Environment variables

**Required**

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing — **must be persistent** (rotating it drops all sessions and breaks mid-flight OAuth). |
| `APP_PASSWORD` | Shared passphrase gating the whole dashboard. Unset → app fails closed (503). |
| `ANTHROPIC_API_KEY` | AI briefings, insights, chat, vision. |

**Google OAuth (Gmail + Calendar):** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI` (must match a registered redirect URI *exactly* — scheme, host,
path, no trailing slash).

**Optional:** `DATABASE_URL` (Postgres in prod), `SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI`,
`NEWS_API_KEY`, `WEATHER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`OBSIDIAN_VAULT_PATH`, `DISCORD_WEBHOOK_URL`, SMTP vars. See `.env.example` for the full list.

### Test endpoints

With the app running and a session cookie (log in first, or the auth gate returns 401):

```bash
curl -b cookies.txt http://localhost:5000/api/briefing        # morning briefing JSON
curl -b cookies.txt http://localhost:5000/api/finance/summary # finance telemetry
curl -b cookies.txt http://localhost:5000/api/sleep/recent    # recent sleep rows
```

Frontend calls must go through the `apiGet`/`apiPost` helpers in `main.js` — they set
`credentials: "include"` so the session cookie rides along.

---

## Architecture

- **167 Flask routes** in `app.py`, all behind a `before_request` auth gate that exempts
  only `login` and `static` (`_PUBLIC_ENDPOINTS`). The gate **fails closed** — no
  `APP_PASSWORD`, no app.
- **17 APScheduler jobs** (`services/scheduler.py`): morning briefing, bedtime, market-open,
  reflection, daily summary, obsidian sync, supplements, water check, 5-min bot-trade poll,
  weekly review + digest, DB backup, CSP-report cleanup, diary generation, and a heartbeat.
  **Single gunicorn worker** so each job fires exactly once.
- **One service module per concern** (`services/*.py`) — `ai`, `briefing`, `insights`,
  `gmail`, `gcal`, `spotify`, `news`, `weather`, `bots`, `nutrition`, `alerts`,
  `obsidian_sync`, and friends. `database.py` is the single DB layer, imported as `db`.
- **MCP server** (`scripts/asfa_mcp.py`) exposes ASFA as tools over stdio, calling the
  `database` layer directly (no auth/CSRF dance) so Claude Desktop / Code can drive it in
  natural language. Write tools require a token; reads stay open to the local process.
- **OAuth CSRF-hardened:** Google and Spotify flows both store a `state` in the session and
  reject a mismatched callback with 400.
- **One source of truth per module** — no metric is stored in two places and reconciled.

---

## Performance

- **CDN hang fix (critical).** The two CDN `<script>` tags (Chart.js, three.js) loaded
  synchronously in `<head>` with no `defer`, blocking HTML parsing. When a CDN was
  unreachable the browser stalled on the request until its ~300s connection timeout, then
  resumed and fired every card's entry animation at once — the "blank for ~5 minutes, then
  everything fades in together" bug. Adding `defer` makes them non-blocking while preserving
  document order (three.js before the deferred `starfield.js`; Chart.js ready before
  `main.js`'s `DOMContentLoaded` handler). Page render never blocks on a CDN again.
- **Query shape.** Reads roll up server-side (daily macro/calorie sums, category spending
  totals) rather than shipping raw rows to the client and summing in JS.
- **Deploy.** One gunicorn worker, 8 threads, 120s timeout on Railway — the scheduler and
  Telegram bot run as in-process threads and must not be duplicated across workers.

---

## What's not built yet

Honest backlog, not vapor:

- **Tier 6.5 — insights agent.** An agent that reads across sleep/nutrition/training and
  surfaces correlations proactively. Designed, not started.
- **Apple Health integration.** No HealthKit import path yet — sleep and body data are entered
  or synced from Renpho, not pulled from Health.
- **Gym → readiness wire.** The data for both sides exists; the automatic readiness score that
  folds last night's sleep into today's training recommendation isn't wired.
- **Finance card retirement.** Tier 8 ships a richer Finance card; the older money card it
  supersedes is being phased out, not yet fully removed.
- **Email briefing delivery.** SMTP config exists but delivery isn't fully wired — summaries
  go to Telegram + the in-app bell for now.
- **Two-way Telegram.** Outbound notifications only; inbound Telegram → app command bridge is
  not implemented.

---

## Running tests

Tests are **self-contained and pytest-free** — each points the DB layer at an isolated temp
SQLite file (`ASFA_DB_PATH`) and passes explicit dates, so they never touch `asfa.db` and
don't depend on the system clock. Run them directly:

```bash
python tests/test_sleep.py       # Tier 6 — sleep DB helpers + one Flask-client endpoint check
python tests/test_nutrition.py   # Tier 7 — meal logging, daily macro/calorie roll-ups
python tests/test_finance.py     # Tier 8 — transactions + finance endpoints
```

No suite runner is wired yet; run each file and read its output. A non-zero exit means a
failure.
