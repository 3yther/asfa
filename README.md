# ASFA — AI Software For Amir

A JARVIS-style personal life command centre. Morning briefings, Gmail + Calendar,
voice control, habit/gym/money tracking, live trading-bot P&L, Telegram assistant,
daily scores, and an AI brain that knows your whole week.

## Stack

- **Backend:** Flask (Python 3.11), APScheduler background jobs
- **Frontend:** Vanilla HTML/CSS/JS, Chart.js, Web Speech API
- **AI:** Anthropic Claude (chat, email summaries, briefings, vision photo logging)
- **DB:** SQLite by default; PostgreSQL automatically when `DATABASE_URL` is set
- **Deploy:** Railway-ready (`Procfile`, `runtime.txt`)

## Quick start

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
python app.py               # http://localhost:5000
```

## Environment variables

| Var | Required | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | console.anthropic.com |
| `NEWS_API_KEY` | for news | newsapi.org |
| `WEATHER_API_KEY` | for weather | openweathermap.org (free tier) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | for Gmail/Calendar | see below |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | optional | see below |
| `DATABASE_URL` | optional | Railway Postgres plugin |
| `SECRET_KEY` | recommended | any random string |

## Google OAuth setup (Gmail + Calendar)

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project (e.g. `asfa`).
2. **APIs & Services → Library** → enable **Gmail API** and **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**, fill in app name + your email.
   - Scopes: add `gmail.readonly` and `calendar`.
   - Add yourself as a **test user** (stays in "Testing" mode — fine for personal use).
4. **Credentials → Create credentials → OAuth client ID**:
   - Type: **Web application**
   - Authorized redirect URIs:
     - `http://localhost:5000/oauth/callback` (local)
     - `https://YOUR-APP.up.railway.app/oauth/callback` (production)
5. Copy the client ID/secret into `.env`, restart, and click **Connect Google** in the header.

## Telegram bot setup

1. Message [@BotFather](https://t.me/botfather) on Telegram → `/newbot` → pick a name → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Message your new bot once (anything), then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — your `chat.id` is in the JSON.
   Put it in `TELEGRAM_CHAT_ID`.
3. Restart ASFA. You'll now get: 06:30 briefings, bedtime + water nudges,
   trade alerts when a bot opens/closes a position, and you can chat with ASFA
   from anywhere — messages are answered by the AI brain with full context.

## iPhone Shortcut — briefing when your alarm stops

1. Open **Shortcuts** → **Automation** tab → **+** → **When Alarm → Is Stopped** → Run Immediately.
2. Add action **Get Contents of URL**: `https://YOUR-APP.up.railway.app/api/briefing`.
3. Add action **Get Dictionary Value** → key `text`.
4. Add action **Speak Text** (pick a voice/rate you like).
5. Done — when you stop your morning alarm, your phone reads the briefing aloud.

## Deploy to Railway

1. Push this repo to GitHub, create a new Railway project from it.
2. Add all env vars in Railway → Variables (plus `SECRET_KEY`).
3. Optionally add the **PostgreSQL** plugin — `DATABASE_URL` is picked up automatically.
4. Add your Railway URL's `/oauth/callback` to the Google OAuth redirect URIs.

> The Procfile runs **one** gunicorn worker on purpose — the scheduler and
> Telegram bot run as background threads and must not be duplicated.

## Voice commands the orb understands

- "What's my day look like?" / "How am I doing this week?"
- "Log 500ml water" · "Log 7.5 hours sleep" · "Log weight 75kg"
- "Log bench 80kg 5 reps" · "Spent £12 on lunch"
- "How are my bots?" · "Remember that I prefer morning workouts"

## Smart notifications

| When | What |
|---|---|
| 06:30 daily | Morning briefing (Telegram + in-app) |
| 22:30 weekdays / 00:00 weekends | Bedtime reminder |
| Daytime, 3h without water | Hydration nudge |
| 14:00 weekdays | "Market opens in 30 min" |
| 22:00 daily | End-of-day reflection prompt |
| Every 5 min | Bot position diff → trade alerts |
| Sunday 18:00 | AI-written weekly review |
