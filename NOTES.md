# Gym module bug-fix notes (branch: feat/gym-bug-fixes)

Resolution log for the five interconnected gym issues. Version tags:
**[Tier 1]** = correctness fixes; **[Data-driven]** = decisions verified against
the real 1,324-row `exercises` catalogue (`asfa.db`), not the test fixture.

---

## Issue 3 — Granular muscle groups  [Data-driven] — already committed (#48)

The granular `MUSCLE_MAP` split was already merged in commit `bee68a0` (#48). Verified
against the live catalogue (`SELECT category, target_muscle, COUNT(*) ...`):

| Broad | Split into (with real row counts) |
|-------|-----------------------------------|
| back  | lats (81), upper back (88), traps (15), **spine** (19) — dataset has **no** "lower back"; `lower back` aliases to `spine` |
| chest | **not split** — all 158 rows are target `pectorals` (+5 `serratus anterior`); no upper/mid/lower split exists |
| shoulders | **not split** — all 143 rows are target `delts`; no anterior/lateral/posterior split exists |
| legs  | quads (44), hamstrings (28), glutes (144), calves (59, category `lower legs`) |
| arms  | biceps (151), triceps (141), forearms (37, category **`lower arms`**) |

**Decision:** only split where the dataset supports it. The prompt's suggested chest
(upper/mid/lower) and shoulder (ant/lat/post) splits **do not exist** in this dataset,
so they were not fabricated. `MUSCLE_MAP` keeps the broad keys (the gym library stores
those as `muscle_group`) and adds granular keys additively.

Real-data check: all 17 `MUSCLE_MAP` keys return a full 12 results from
`suggest_exercises`. The frontend discovery panel is session-driven (auto from the
muscles being trained) plus a search box; there is no manual broad-only dropdown to
replace.

## Issue 1 — "Try Something New" returns nothing / stale  [Tier 1]

The endpoint was **not** empty — with the granular map it returns 12 for every key.
The real bug: with an essentially empty training log, every unlogged exercise ties in
the top "new to you" score band (100), and the ranker tiebroke **alphabetically**. So
the panel showed the same A-named twelve on every visit — 12 of 163 chest moves,
forever — and buried everything later in the alphabet (the Pec Deck included).

**Fix** (`services/exercise_match.py`): break ties with a per-day seeded shuffle —
stable within a day (panel doesn't reshuffle as you log), fresh the next, so the whole
pool cycles through. Cross-band ranking (novel > stale > recent) is untouched. Real-data
result: **137** distinct chest exercises surface over 30 days (was 12). Test: `test_18`.

## Issue 2 — Pec Deck missing  [Data-driven] [Tier 1]

Not missing — the dataset files it as **`lever seated fly`** (id `0596`), its mechanism,
not the gym-floor name. A plain `LIKE` search for "pec deck" found nothing, so the
athlete logged the nearest findable name (a cable fly).

**Fix** (`exercise_match.py` + `exercises.py`): a small vernacular→catalogue alias map
(`NAME_ALIASES`) resolved on the search endpoint, and its inverse (`DISPLAY_ALIASES`) so
the discovery card and the bridged `gym_exercises` row both read "Pec Deck". Every alias
target verified to exist in the live catalogue. Also covers the reverse pec deck /
rear-delt machine (`lever seated reverse fly`, id `0602`). Tests: `test_15`–`test_17`.
Combined with Issue 1's rotation, the Pec Deck now also appears in the suggestion panel.

## Issue 4 — Swap/Replace keeps logged sets  [Tier 1]

A swap (⇄) button already existed but **discarded** logged sets: it re-tagged the card
and left the old sets orphaned under the old exercise — invisible on the card yet still
counted in session totals. Fixing a mislabelled exercise meant delete + re-enter.

**Fix:** `db.reassign_session_exercise(session, from, to)` +
`POST /api/gym/sessions/<id>/swap-exercise` re-point a session's sets from one exercise
to another with weight/reps/rpe intact. `swapExercise()` (`static/js/gym.js`) now calls
it whenever sets exist and keeps them on the card; an empty slot still swaps fresh.
Guards against swapping onto an exercise already in the session; 404s on a missing
target. XP/PRs/ranks already awarded are not recomputed (they never downgrade — same
policy as `delete_set`). Test: `test_19`.

## Issue 5 — Telegram bot shows 0 data  [Tier 1] — root cause was NOT (only) DATABASE_URL

The prompt's hypothesis was a missing Railway `DATABASE_URL`. Investigation found a
**code-level disconnect** that no env change would fix:

- The Telegram bot delegates to `services.ai.chat()` → `build_context_block()`.
- That block read `db.get_workouts()` / `db.get_pbs()`, which query the **legacy
  `workouts` table**. **Nothing writes to `workouts` any more** (no `INSERT INTO
  workouts`, no `log_workout` DB function) — the /gym tracker replaced it with
  `gym_sessions` / `gym_sets` / `gym_prs`.
- Real-data proof (`asfa.db`): legacy `workouts` = **0** rows, legacy `pbs` = **0**,
  while the gym tracker holds 2 sessions / 4 sets / 1 PR. Hence "0 data despite active
  logging" — regardless of `DATABASE_URL`.
- Body weight is **not** affected: both `/api/gym/weight` and the context read the
  shared legacy `body_weight` table.

**Fix** (`services/ai.py`): added `_recent_gym_workouts()` and `_gym_pbs()` adapters that
read the live gym tracker and shape rows exactly as the context template expects;
`build_context_block()` now uses them. Verified the context surfaces the real log
(bench 60kg×9 🏆, etc.).

**Still an ops action for the user (cannot be done from here — no Railway access, secrets
are gitignored):** confirm `DATABASE_URL` is set to the Railway Postgres URL in the ASFA
service variables. Without it, ASFA runs on SQLite on Railway's **ephemeral** filesystem,
so all logged data (gym and otherwise) is wiped on every redeploy — a separate, real
data-durability problem. Set it, then redeploy. The code fix above is required for gym
data to appear even once `DATABASE_URL` is correct.

---

## Test status

`pytest tests/` → **173 passed, 2 failed**. The 2 failures
(`test_nutrition_hub::test_3/test_4`) are **pre-existing and unrelated**: they reproduce
on a clean tree (`git stash`), and are cross-file test-isolation pollution (both
`test_export.py` and `test_nutrition_expansion.py` leak DB state into `test_nutrition_hub`
via a shared `ASFA_DB_PATH` set at import). Each file passes in isolation. Not caused by,
and out of scope for, this branch. The gym suite (`test_gym_discovery.py`) is 19/19 green.
