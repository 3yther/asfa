-- ============================================================================
-- Grafana read-only Postgres user for ASFA
-- ----------------------------------------------------------------------------
-- Run this ONCE against the ASFA Railway Postgres database, connected as the
-- database OWNER (the app's main user / the default `postgres` role).
--
-- Railway → Postgres service → "Data" tab → "Query" (or `psql "$DATABASE_URL"`
-- via the Railway CLI). Then paste this whole file.
--
-- Design: DEFAULT-DENY. A fresh Postgres role has NO table privileges, so the
-- only tables grafana_reader can read are the ones explicitly GRANTed below.
-- Sensitive tables (agent_audit_log, auth_failures, claude_api_calls,
-- csp_reports, conversations, memories, kv_store, agent_* memory tables) are
-- simply never granted -> SELECT on them returns "permission denied".
--
-- NOTE: replace <DBNAME> with the actual database name (Railway default is
-- usually `railway`). The password below is pre-generated; rotate if it has
-- been shared in plaintext anywhere.
-- ============================================================================

-- 1. The role (login user, SELECT-only by construction, no CREATEDB/SUPERUSER)
CREATE USER grafana_reader WITH PASSWORD 'I1MFLlLayCDLAc8LPslTzajJIXRe';

-- 2. Let it connect and see the public schema (but not create objects in it)
GRANT CONNECT ON DATABASE railway TO grafana_reader;   -- <-- change `railway` if your DB name differs
GRANT USAGE  ON SCHEMA public   TO grafana_reader;

-- 3. SELECT only on the exact tables the dashboard queries. Nothing else.
--    (12 tables: the 8 modules + 4 join/goal lookup tables the panels need.)
GRANT SELECT ON
    meals,              -- nutrition: per-meal macros + calories
    nutrition_goals,    -- nutrition: goal reference lines
    sleep,              -- sleep duration + quality (readiness computed in SQL)
    spending,           -- finance: amount/category/date
    hydration_log,      -- water: amount_ml/date
    supplements_log,    -- supplements: supplement_name/taken_at
    steps,              -- steps: per-source daily steps (sum per day)
    steps_goal,         -- steps: goal threshold line
    gym_sessions,       -- gym: total_volume_kg per session/day
    gym_sets,           -- gym: is_pr rows for the PR timeline
    gym_prs,            -- gym: current PR per exercise (alt PR source)
    gym_exercises,      -- gym: id -> exercise name (join only)
    body_composition    -- body comp: date_scanned + weight/bf%/etc.
TO grafana_reader;

-- 4. Belt-and-braces: make sure no accidental future default privileges or a
--    prior PUBLIC grant let it write. (Postgres 14- allowed PUBLIC CREATE on
--    the public schema; revoke it so grafana_reader can't create tables.)
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE railway FROM grafana_reader;      -- <-- change `railway` if needed
GRANT CONNECT ON DATABASE railway TO grafana_reader;     -- re-grant just CONNECT

-- ============================================================================
-- VERIFY (run these AFTER the grants; paste the output back)
-- ============================================================================

-- (a) Exactly which tables can grafana_reader read? Expect ONLY the 13 above.
SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'grafana_reader'
ORDER BY table_name;

-- (b) Prove it CANNOT see the sensitive tables. Expect 0 rows for each.
SELECT table_name
FROM information_schema.role_table_grants
WHERE grantee = 'grafana_reader'
  AND table_name IN ('agent_audit_log','auth_failures','claude_api_calls',
                     'csp_reports','conversations','memories','kv_store');

-- (c) Prove it has no write privileges anywhere. Expect 0 rows.
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'grafana_reader'
  AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE');
