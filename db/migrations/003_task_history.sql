-- 003_task_history
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging this PR
-- (same as 001_ownership_completions and 002_flow_framework). The aggregator
-- upserts one row per task per EAT day every poll cycle; a later PR's
-- "Completed Tasks" dashboard page reads from here.
--
-- Purpose: the Tasks page is built from a rolling 24h / 7-day poll cache, so a
-- task disappears once all its records go quiet. This table is the permanent
-- ledger — every task the aggregator sees is recorded daily and never deleted,
-- so completed tasks stay available for later analysis.

CREATE TABLE ownership_task_history (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name            text NOT NULL,
    snapshot_date        date NOT NULL,          -- aggregator EAT date this row describes
    computed_at          timestamptz,            -- aggregator run time (latest cycle of the day)
    is_sanctions         boolean,
    total_records        integer,                -- records IN THE POLL CACHE that day (see note)
    date_first_seen      timestamptz,            -- earliest Airtable `created` across the task
    date_last_modified   timestamptz,
    valid_pct            numeric,
    is_completed         boolean,                -- task crossed 95% Valid
    end_time             timestamptz,
    tat_hours            numeric,
    status_distribution  jsonb,                  -- 7-key verification_status counts
    properly_completed   integer,
    with_company         integer,
    without_company      integer,
    dead_vessels         integer,
    with_reminder        integer,
    completed            integer,
    qa_reviewed          integer,
    qa_changed           integer,
    qa_coverage_pct      numeric,
    agents_worked        jsonb,                  -- [{name, team, records, qa_checked, qa_changed}]
    teams_worked         jsonb,                  -- [{team, records}]
    qa_reviewers         jsonb,                  -- [{name, reviewed, changed}]
    flags                jsonb,                  -- ["stuck", "aging", ...]
    source               text NOT NULL DEFAULT 'pipeline',  -- 'pipeline' | 'recovery-scan'
    recorded_at          timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_history_name_date UNIQUE (task_name, snapshot_date)
);

CREATE INDEX idx_task_history_name      ON ownership_task_history(task_name);
CREATE INDEX idx_task_history_date      ON ownership_task_history(snapshot_date);
CREATE INDEX idx_task_history_completed ON ownership_task_history(is_completed);

-- NOTE on total_records: this is the count of the task's records that were in
-- the rolling poll cache on snapshot_date. For a task born after this ledger
-- starts, the cache holds ALL its records while it is fresh (Fetch A's 7-day
-- window), so the PEAK total_records across a task's rows is its true total.
-- The number shrinks on later days as finished records age out of the cache —
-- that is expected. The "Completed Tasks" page should treat MAX(total_records)
-- as the task's true size.
--
-- RLS / dashboard read access — apply the SAME option you used for
-- 001_ownership_completions. The aggregator writes with the service_role key
-- (bypasses RLS); the dashboard reads with the anon key after sign-in.
--   (a) ALTER TABLE ownership_task_history ENABLE ROW LEVEL SECURITY;
--       CREATE POLICY "authenticated read" ON ownership_task_history
--         FOR SELECT TO authenticated USING (true);
--   (b) GRANT SELECT ON ownership_task_history TO anon, authenticated;
