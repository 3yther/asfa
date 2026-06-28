# CLAUDE.md

## Project Overview
ASFA is a Flask-based personal assistant dashboard deployed on **Railway**
(`https://asfa-production.up.railway.app`). It aggregates personal data —
Gmail, Google Calendar, Spotify, news, weather, finance/bot trading activity,
habits, goals, and reflections — into a single password-gated dashboard, with
an AI layer (Anthropic) for briefings/insights and scheduled background jobs
for proactive reminders and daily summaries.

## Stack
- **Backend:** Python 3.12, Flask 3.0, gunicorn (1 worker / 8 threads), flask-cors
- **Scheduling:** APScheduler 3.10 (in-process)
- **AI:** anthropic 0.109
- **Integrations:** Google API client + google-auth-oauthlib (Gmail, Calendar),
  Spotify (OAuth), python-telegram-bot, NewsAPI, OpenWeatherMap
- **Storage:** SQLite locally (`asfa.db`), PostgreSQL via `DATABASE_URL` in prod (psycopg2)
- **Frontend:** vanilla JS (`static/js/main.js`), server-rendered Jinja templates
- **Deploy:** Railway, `Procfile` → `gunicorn app:app`

## Key Files
- `app.py` — Flask app, all routes, `before_request` auth gate, OAuth flows, `db.init_db()`
- `database.py` — DB layer (SQLite/Postgres), imported as `db`
- `services/` — integration modules:
  - `ai.py`, `briefing.py`, `insights.py` — Anthropic-backed features
  - `gmail.py`, `gcal.py` — Google integrations
  - `spotify.py` — Spotify OAuth + playback
  - `news.py`, `weather.py` — external data
  - `bots.py` — trading bot health/activity
  - `scheduler.py` — APScheduler job definitions (`start_scheduler()`)
  - `telegram_bot.py`, `alerts.py` — notifications
  - `obsidian_sync.py` — daily vault sync to `~/Obsidian/asfa/` (agent profiles,
    daily logs, live summary; seeds editable second-brain notes once; local FS only)
- `templates/` — `index.html` (dashboard), `login.html`
- `static/js/main.js` — frontend logic + `apiGet`/`apiPost` HTTP helpers
- `.env.example` — full list of supported env vars

## Environment Variables
**Required:**
- `SECRET_KEY` — Flask session signing key. **Must be set and persistent** (see Critical Rules).
- `APP_PASSWORD` — shared passphrase that gates the entire dashboard. App stays locked until set.
- `ANTHROPIC_API_KEY` — for AI briefings/insights.

**Google OAuth (Gmail + Calendar):**
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` — must EXACTLY match an Authorized redirect URI in the
  Google OAuth client (scheme, host, path, no trailing slash).

**Spotify OAuth (optional):**
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`
- `SPOTIFY_REDIRECT_URI` — must EXACTLY match the redirect URI registered in the Spotify dashboard.

**Optional:** `DATABASE_URL` (Postgres in prod), `NEWS_API_KEY`, `WEATHER_API_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OBSIDIAN_VAULT_PATH`,
`DISCORD_WEBHOOK_URL`, SMTP vars (`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/
`SUMMARY_EMAIL_TO`/`SUMMARY_EMAIL_FROM`).

## Critical Rules
- **Never commit `.env`.** It is gitignored along with `*.db`/`*.sqlite`,
  `token.json`, `credentials.json`, `google_token.json`. Keep it that way.
- **`SECRET_KEY` must be persistent.** A new/rotated key invalidates all sessions
  and breaks the OAuth state stored in the session mid-flow. Set it once in
  Railway and do not regenerate it on each boot.
- **All `fetch` calls need `credentials: "include"`.** The auth gate is
  session-cookie based; requests without the cookie get 401. Always go through
  the `apiGet`/`apiPost` helpers in `main.js`, which already set it.
- **The `before_request` auth gate exempts only `login` and `static`.** Every
  other endpoint requires `session["authed"]`. The exempt set is
  `_PUBLIC_ENDPOINTS = {"login", "static"}` in `app.py`. Any new public route
  must be added there explicitly. The gate **fails closed**: if `APP_PASSWORD`
  is unset the app returns 503 and stays locked.

## Architecture Notes
- **`db` must be imported before routes.** `app.py` does `import database as db`
  near the top and calls `db.init_db()` during startup, before/while the route
  functions reference `db`. Reordering this caused a `NameError: 'db'`
  (see Known Issues Fixed).
- **APScheduler runs 13 jobs** (`services/scheduler.py:start_scheduler`):
  morning briefing (09:00 UTC), bedtime reminder (mon–fri 22:30 + sun/sat 00:00),
  market-open reminder (mon–fri 14:00), reflection prompt (22:00),
  daily summary (21:00 UTC), obsidian sync (midnight), supplement reminders
  (09:00 + 20:00), water check (every 30 min), bot-trade poll (every 5 min),
  weekly review (sun 18:00), DB backup (03:00 Europe/London → private repo).
  Single gunicorn worker so jobs run once.
- **OAuth state must be validated.** Both Google and Spotify flows generate a
  `state`, store it in the session, and verify the returned `state` matches on
  callback (rejecting with 400 on mismatch) to prevent CSRF / auth-code injection.
  Google: `session["oauth_state"]`; Spotify: `session["spotify_oauth_state"]`.

## Known Issues Fixed
- **`credentials: include` added to fetch helpers** — frontend requests were
  hitting the session auth gate and getting 401s; `apiGet`/`apiPost` now send
  the session cookie. Use these helpers for any new endpoint calls.
- **`db` NameError fixed** — endpoints raised `NameError: 'db'` after an import
  was dropped; `import database as db` + `db.init_db()` were restored at module load.

## What's Not Built Yet
- **Email briefing** — SMTP daily-summary email path exists in config but the
  email delivery is not fully wired; summaries currently go to Telegram + the
  in-app bell.
- **Telegram bridge** — inbound Telegram → app command bridge (two-way control)
  is not implemented; Telegram is outbound notifications only.

## Testing
- No automated test suite yet. Verify changes by running locally:
  `python app.py` (uses SQLite `asfa.db`, reads `.env` via python-dotenv).
- For auth-gated endpoints, log in first (or you'll get 401/redirect); confirm
  new frontend calls go through `apiGet`/`apiPost`.
- OAuth flows require the redirect URI to match exactly for the environment
  you're testing (local `http://localhost:5000/...` vs prod Railway URL).
- When adding a scheduled job, confirm it registers in `start_scheduler()` and
  remember there is only one gunicorn worker.
