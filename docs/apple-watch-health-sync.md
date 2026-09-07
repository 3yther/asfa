# Apple Watch → ASFA health sync

An hourly iOS Shortcut reads HealthKit and POSTs to `/api/health/sync`. ASFA
stores it in `health_metrics` and draws the **Sleep & Recovery** card on the
BODY tab plus the burn row on the nutrition hero.

---

## 1. Get the sync token

**Settings → System → Apple Watch Health Sync → Generate sync key.**

The token is shown **once**. It is stored only as a SHA-256 hash, so it cannot
be recovered afterwards — if you lose it, revoke that key and issue another.

It carries the `health_sync` scope, which permits exactly two things: `POST
/api/health/sync`, and reading health metrics back. It cannot log workouts,
change settings, mint other keys or read your email. Revoking it from the same
card stops the Shortcut immediately.

> **Why a stored key and not a constant in `app.py`.** A token generated at
> server startup changes every time the process restarts. Railway restarts on
> every deploy, so the Shortcut would break each time you shipped anything — and
> break *silently*, as hourly background 401s you would never see. This one
> lives in the database and survives redeploys. It is the same reasoning behind
> the "SECRET_KEY must be persistent" rule in `CLAUDE.md`.

---

## 2. Build the Shortcut

There is no `.shortcut` file in the repo. The format is a signed binary plist
that Apple generates per-device on export, so a checked-in one would either fail
to import or need re-signing on your phone — hand-building it takes about five
minutes and leaves you able to adjust it.

Open **Shortcuts → + → Add Action** and add these in order.

### Setup

| # | Action | Configure |
|---|--------|-----------|
| 1 | **Text** | Paste your token. Rename the action "Token". |
| 2 | **Text** | `https://asfa-production.up.railway.app` — rename "Base URL". |
| 3 | **Date** | Current Date |
| 4 | **Adjust Date** | Subtract **1 Day** from *Date*. Rename "Yesterday". |
| 5 | **Format Date** | *Yesterday*, Custom format `yyyy-MM-dd` |
| 6 | **Format Date** | *Current Date*, Custom format `yyyy-MM-dd` → "Today string" |

### Yesterday — sleep and HRV

HealthKit only has a complete night once the day is over, which is why sleep and
HRV are read for yesterday rather than today.

| # | Action | Configure |
|---|--------|-----------|
| 7 | **Find Health Samples** | Type **Sleep Analysis**, filter Start Date is *Yesterday*, Sort by Start Date |
| 8 | **Calculate Statistics** | Sum, over the durations from step 7 → sleep minutes |
| 9 | **Find Health Samples** | Type **Heart Rate Variability**, Start Date is *Yesterday* |
| 10 | **Calculate Statistics** | **Average** of step 9 → HRV in ms |

For the stage split, repeat step 7 three times filtering **Value** to `Deep`,
`Core` (this is what HealthKit calls light sleep) and `REM`. If your watch or
sleep app does not record stages, leave those three fields out — the API accepts
any subset and the card simply hides the split bar.

### Today — activity

Activity is read for *today* on purpose: it is a running total and you want the
intra-day figure.

| # | Action | Configure |
|---|--------|-----------|
| 11 | **Find Health Samples** | **Active Energy**, Start Date is Today → Calculate Statistics → Sum |
| 12 | **Find Health Samples** | **Steps**, Start Date is Today → Calculate Statistics → Sum |

### Send

| # | Action | Configure |
|---|--------|-----------|
| 13 | **Get Contents of URL** | See below |
| 14 | **Show Notification** | `Health synced` + the response |

**Get Contents of URL:**

- URL: *Base URL* + `/api/health/sync`
- Method: **POST**
- Headers: `Authorization` → `Bearer ` + *Token*  ← keep the trailing space after `Bearer`
- Request Body: **JSON**, with a top-level `days` array of two dictionaries:

```jsonc
{
  "days": [
    {
      "metric_date": "<Yesterday string>",
      "sleep_duration_minutes": 450,
      "sleep_deep_minutes": 90,
      "sleep_light_minutes": 260,
      "sleep_rem_minutes": 100,
      "hrv_ms": 42.5
    },
    {
      "metric_date": "<Today string>",
      "calories_active": 600,
      "calories_total": 2400,
      "steps": 9000
    }
  ]
}
```

Every field except `metric_date` is optional. Send only what you have.

### Automate it

**Automation → Time of Day → Hourly**, run the Shortcut, **Run Immediately** and
turn *Notify When Run* off if the hourly banner gets tiring. Restrict it to
roughly 08:00–22:00 — overnight syncs re-send the same numbers and only cost
battery.

---

## 3. Test it

From your laptop, replacing the token:

```bash
curl -sS -X POST https://asfa-production.up.railway.app/api/health/sync \
  -H "Authorization: Bearer asfa_YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"metric_date":"2026-09-05","sleep_duration_minutes":450,"hrv_ms":42.5,"calories_active":600,"steps":9000}'
```

Expected: `{"success": true, "count": 1, "synced": [...], "errors": []}`.

Read it back:

```bash
curl -sS https://asfa-production.up.railway.app/api/health/metrics/latest \
  -H "Authorization: Bearer asfa_YOUR_TOKEN"
```

Then open the dashboard's BODY tab. The card needs one day for the numbers, two
for the sparkline, and **four** before the recovery badge switches from generic
thresholds to your own baseline.

---

## Behaviour worth knowing

**Re-syncing the same day is safe and expected.** Each POST merges into that
date's row — fields you send overwrite, fields you omit are left alone. That is
what lets the hourly job push today's activity repeatedly without wiping the
sleep and HRV already stored for that date.

**Steps do not double up.** Steps are mirrored into the existing `steps` table
under source `apple_watch`, and each sync *replaces* that row rather than adding
one. Manual and cardio-derived step entries for the same day are untouched.
(Appending was the obvious implementation and would have been badly wrong: the
day total is a `SUM`, and the watch posts a running total, so a 10,000-step day
would have read as six figures by evening.)

**Sleep is not written to the manual sleep log.** The HABITS "Sleep & Recovery"
card is your own hand-entered log with a subjective 1–5 quality the watch cannot
supply, so the sync never overwrites it. The BODY card prefers watch data and
falls back to the manual log for duration, so the two never disagree.

**Watch calories are not the same number as the gym page's expenditure.** The
gym figure is back-calculated from intake and weight change; the watch's is a
sensor estimate of movement. Both are kept, because they answer different
questions.

**The burn row on the nutrition page only shows for today**, since the watch
sends a running total and a past day's stored value is whatever it happened to
be at that day's final sync.

**Future dates are rejected** — a wrong phone clock would otherwise park a
permanent phantom day at the head of every chart.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401 unauthorized` | Token wrong, revoked, or the header is missing the space in `Bearer <token>`. |
| `{"success": false}` with an `errors` array | Read the array: usually a `metric_date` that is not `YYYY-MM-DD`, or a payload with no recognised metric fields. |
| `403 CSRF` | You sent a session cookie instead of the bearer token. The Shortcut should not send cookies. |
| Card says "waiting for watch data" | Nothing stored yet. Run the Shortcut manually and check its notification. |
| Badge says "generic bands" | Fewer than four days of HRV. It switches to your baseline automatically. |
| Steps look far too high | A pre-fix client appending instead of replacing; delete the day's `apple_watch` rows and re-sync. |

---

## API reference

| Method | Path | Auth |
|---|---|---|
| POST | `/api/health/sync` | session, or `health_sync` key |
| GET | `/api/health/metrics?days=7` | session, or any key |
| GET | `/api/health/metrics/latest` | session, or any key |
| GET | `/api/health/recovery?days=7` | session |

`POST` accepts either a single flat object or `{"days": [...]}`. Recognised
fields: `sleep_duration_minutes`, `sleep_deep_minutes`, `sleep_light_minutes`,
`sleep_rem_minutes`, `hrv_ms`, `calories_active`, `calories_total`, `steps`.
Unknown fields are ignored; negative and unparseable values are dropped
per-field rather than overwriting good data.
