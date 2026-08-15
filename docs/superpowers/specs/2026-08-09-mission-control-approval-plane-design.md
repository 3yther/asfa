# Operational Approval Plane for Mission Control — Design

**Date:** 2026-08-09
**Status:** Approved (design phase)
**Branch:** worktree-bridge-cse

## Summary

Surface ASFA's **existing** plan-approval gate inside the Mission Control
operational HUD, and add **real per-agent access-rule enforcement** to plan
execution. This delivers the "operational plane" from the original brief
(clickable agents, approval modals, activity feed, access rules) without
building a parallel approval system or adding an external CSS dependency.

The design was chosen after discovering the codebase already contains most of
the machinery the brief proposed to build from scratch.

## Context: what already exists

- **`templates/mission_control.html`** — a Phaser 3 scene. Rooms and agents are
  already clickable (`onRoomClick` / `openModal(id)`), and an agent modal
  already renders (with an XP input). There is already an activity feed of
  `.arow` rows and a `.modal-overlay` / `.modal-card` component.
- **Agents subsystem** — `db.get_agents()`, XP, energy, heartbeat
  (`services/heartbeat.py`), diaries, per-agent logs
  (`db.add_agent_log` / `db.get_agent_log`), skill registry
  (`agent_skills` table, `db.get_agent_skills`). Endpoints under `/api/agents/*`.
- **Plan approval + execution gate** — `services/planner.py`:
  - `decompose_plan(user_request)` uses Claude to break a request into steps and
    persists it as an `execution_plans` row with `status='pending_approval'`.
  - `execute_plan(plan_id)` runs each step via
    `services/skill_executor.py::execute_skill(agent_id, skill_name, params)`,
    logging every step to `plan_executions` (agent_id, skill_name, input_params,
    output, status, error, duration_ms) and adjusting agent energy.
  - Endpoints: `/api/plan/decompose`, `/api/plan/<id>` (get),
    `/api/plan/<id>/approve`, `/api/plan/<id>/reject`, `/api/plan/<id>/execute`,
    `/api/plan/<id>/results`.
- **`templates/approvals.html`** — an empty placeholder page
  ("No pending approvals"), already routed at `/approvals`.
- **Design system** — hand-built cyberpunk aesthetic in `static/css/style.css`
  (~90KB): `sci-fi-panel`, neon borders, scanlines, decode animations,
  IBM Plex / JetBrains Mono, three.js starfield.

**Gap:** `execute_skill` performs **no access control** — any approved plan can
call any skill on any agent. This is the one genuinely missing capability.

## Decisions (from brainstorming)

1. **Approval semantics:** a real gate for ASFA's *own* actions (not a display
   mock, and explicitly **not** the frozen trading bots — ASFA stays read-only
   toward the bots per the project CLAUDE.md).
2. **Data source:** reuse `execution_plans` + `plan_executions` + agent logs as
   the single source of truth. Do **not** create parallel `approvals` /
   `activity_log` tables. The only new table is `agent_access_rules`.
3. **Styling:** reuse the existing design system. No CYBERCORE / CDN dependency.
4. **Enforcement behavior:** disallowed skill → block that step, log it as a
   distinct **blocked** row, continue the rest of the plan (matches the existing
   per-step failure handling).
5. **Scope this pass:** full surface — backend enforcement + agent-modal
   extension + approval modal + `approvals.html` inbox + mission-log color
   coding.

## Architecture

Two net-new pieces; everything else is reads/wrappers over existing data.

### 1. Access-rule enforcement (backend, net-new)

**Table `agent_access_rules`:**

| column      | type      | notes                                   |
|-------------|-----------|-----------------------------------------|
| id          | PK        | autoincrement / serial (Postgres)       |
| agent_id    | TEXT      | FK-ish to agents                        |
| skill_name  | TEXT      | matches `agent_skills.skill_name`       |
| allowed     | BOOLEAN   | default TRUE                            |
| created_at  | TIMESTAMP | default now                             |

Unique on `(agent_id, skill_name)`. Created in `db.init_db()` alongside the
other Phase-5 control tables, following the existing
`INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY` Postgres rewrite.

**Seeding:** `db.seed_access_rules()` inserts one `allowed=TRUE` row for every
`(agent_id, skill_name)` already in `agent_skills`, idempotently (INSERT …
ON CONFLICT DO NOTHING / INSERT OR IGNORE). Called once during init after the
skill registry is populated, so existing plans keep working unchanged.

**Enforcement point:** `services/skill_executor.py::execute_skill`. Before
dispatching, call `db.is_skill_allowed(agent_id, skill_name)`:
- **Rule:** returns `False` only when an explicit `allowed=FALSE` row exists for
  `(agent_id, skill_name)`; otherwise `True`. Denials are always explicit.
  Because seeding creates an `allowed=TRUE` row for every registered skill, this
  never breaks an existing plan — a step is blocked only if someone has
  deliberately set that pair to denied.
- If not allowed: return
  `{"success": False, "output": None, "error": "access denied: <skill>",
    "blocked": True}` **without** executing the skill.

`execute_plan` already logs `success`/`failure`; extend it so a `blocked`
result is recorded with `status='blocked'` in `plan_executions` (new status
value; ordinary failures stay `failure`). Energy penalty for a blocked step
matches the existing failure penalty. The plan continues to the next step.

### 2. Read endpoints (thin wrappers over existing data)

- `GET /api/plan/pending` → list of `execution_plans` with
  `status='pending_approval'` (id, request, step count, created_at). Feeds the
  approval inbox and the "Approve pending" button count.
- `GET /api/agents/<id>/access` → rows from `agent_access_rules` for the agent
  (skill_name, allowed) for the modal's badges.
- `GET /api/agents/<id>/activity` → recent `plan_executions` for that agent
  merged with `get_agent_log(id)`, newest first, capped (e.g. 10), each tagged
  with a status (`success` / `blocked` / `failure` / `info`).

Approve / deny reuse the **existing** `/api/plan/<id>/approve` and
`/api/plan/<id>/reject`. Approve is followed by the existing
`/api/plan/<id>/execute` (same as the current flow).

All new endpoints are auth-gated (default `before_request` gate; nothing added
to `_PUBLIC_ENDPOINTS`).

### 3. Frontend (existing design system, no new deps)

- **Extend the existing agent modal** (`openModal` in `mission_control.html`):
  add an energy bar, access-rule badges (green = allowed, magenta = denied via
  `/api/agents/<id>/access`), a recent-activity list
  (`/api/agents/<id>/activity`), and an **"Approve pending (N)"** button shown
  only when `/api/plan/pending` contains plans (N > 0). Reuse the existing
  `.modal-overlay` / `.modal-card` structure and CSS tokens.
- **Approval modal:** a second modal (or a mode of the existing one) showing the
  agent, the plan's request + step detail, arguments in a monospace code box,
  and **Approve** / **Deny** buttons. Approve → POST approve then execute;
  Deny → POST reject. Then toast + refresh the agent modal + activity feed.
- **Approvals inbox (`approvals.html`):** replace the placeholder body with a
  real list from `/api/plan/pending`, each row opening the same approval modal.
  Styled with the existing `sci-fi-panel`.
- **Mission-log color coding:** the activity feed already exists; color-code rows
  by status (green success / magenta blocked / yellow pending / cyan info)
  using existing accent tokens. No new panel.

## Data flow

```
User (Mission Control / Approvals inbox)
  -> GET /api/plan/pending                 (existing execution_plans)
  -> open approval modal
  -> POST /api/plan/<id>/approve           (existing)
  -> POST /api/plan/<id>/execute           (existing)
        -> execute_plan
             -> execute_skill (NEW: is_skill_allowed check)
                  allowed  -> run skill      -> plan_executions status=success
                  denied   -> skip           -> plan_executions status=blocked
  -> activity feed / mission log reflect success|blocked|failure
```

## Error handling

- Unknown agent / plan id → 404 (matches existing endpoint conventions).
- `/api/plan/<id>/approve` on a non-pending plan → existing behavior unchanged.
- Enforcement failure never crashes a plan: a blocked step is logged and the
  loop continues, identical to the current failure path.
- Frontend fetches go through `apiGet`/`apiPost` (credentials included) per the
  ASFA critical rule; failures show a toast, modal stays open.

## Testing

No automated suite exists yet; add focused tests for the net-new logic:

1. `is_skill_allowed` / `seed_access_rules`: seeding creates an allow row for
   every registered `(agent_id, skill_name)`; an explicit `allowed=FALSE` row
   makes `is_skill_allowed` return False.
2. `execute_skill` with a denied pair returns `blocked` and does **not** run the
   underlying skill (assert the side effect / dispatch did not happen).
3. `execute_plan` with one denied step: that step is `blocked` in
   `plan_executions`, later steps still execute.

Manual verification: run `python app.py`, log in, seed a `pending_approval`
plan, open Mission Control, click an agent, confirm badges + "Approve pending",
approve via the modal, confirm the activity feed / mission log updates and a
denied skill shows as blocked.

## Explicitly out of scope

- No parallel `approvals` / `activity_log` tables or `/api/approvals` /
  `/api/activity` endpoints (reuse the plan system instead).
- No CYBERCORE CSS / external CDN.
- No changes to the trading bots or any write path toward them (frozen /
  read-only per project CLAUDE.md).
- No UI for *editing* access rules in this pass — rules are seeded and enforced;
  a management UI can be a follow-up.
