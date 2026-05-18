-- 002_flow_framework
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR
-- that introduces the Flow Framework v2 detector. The detector will fail on
-- the first insert if these tables / columns don't exist.

-- ---------------------------------------------------------------------------
-- ownership_qa_sampling: Flow B in-progress pool.
-- Records currently in 'Selected for BO QA ' (trailing space — literal
-- Airtable choice name) with a qa_assignee. Separate table from
-- ownership_completions because Flow B is in-progress, NOT a completion.
-- Total Completions = Flow A + Flow C only.
-- ---------------------------------------------------------------------------
CREATE TABLE ownership_qa_sampling (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    airtable_record_id  text UNIQUE NOT NULL,
    imo                 text,
    role                text,
    qa_assignee         text NOT NULL,
    sampled_at          timestamptz NOT NULL,           -- detector clock at first detection
    detected_at         timestamptz NOT NULL DEFAULT NOW(),
    raw_payload         jsonb
);
CREATE INDEX idx_qa_sampling_sampled_at ON ownership_qa_sampling(sampled_at);
CREATE INDEX idx_qa_sampling_qa_assignee ON ownership_qa_sampling(qa_assignee);

-- ---------------------------------------------------------------------------
-- flow_alerts: data-integrity alerts surfaced on the Team Activity Alerts
-- panel. UNIQUE on (airtable_record_id, alert_type) so first detection wins;
-- subsequent identical alerts are no-ops. resolved_at is NULL while the
-- alert condition is still met; the detector clears it (back to NULL) if a
-- previously-resolved record bounces back into the bad state, and sets it
-- to NOW() when the condition no longer applies. Dashboard counts open
-- alerts WHERE resolved_at IS NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE flow_alerts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    airtable_record_id  text NOT NULL,
    alert_type          text NOT NULL,    -- 'missing_qa_assignee' | 'missing_qa_status' | 'stuck_in_sampling'
    verification_status text,
    qa_assignee         text,
    qa_status           text,
    first_seen_at       timestamptz NOT NULL DEFAULT NOW(),
    resolved_at         timestamptz,      -- NULL while open; set when condition no longer applies
    raw_payload         jsonb,
    UNIQUE(airtable_record_id, alert_type)
);
CREATE INDEX idx_flow_alerts_type        ON flow_alerts(alert_type);
CREATE INDEX idx_flow_alerts_first_seen  ON flow_alerts(first_seen_at);
CREATE INDEX idx_flow_alerts_open        ON flow_alerts(alert_type, resolved_at)
                                          WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------------------
-- Add `flow` column to ownership_completions for A/C classification.
-- Backfill is a no-op against the existing data (all current rows are at
-- vs = 'tagged' or 'need to be update' so they don't match the WHERE clause).
-- That's intentional per the spec — first deploy starts with zero A/C counts
-- and the next detector cycle correctly populates flow on new rows.
-- ---------------------------------------------------------------------------
ALTER TABLE ownership_completions ADD COLUMN flow text;
CREATE INDEX idx_ownership_completions_flow ON ownership_completions(flow);

UPDATE ownership_completions
   SET flow = 'A'
 WHERE flow IS NULL
   AND verification_status IN ('Done', 'Valid');

-- ---------------------------------------------------------------------------
-- Read access. The dashboard signs in via Supabase auth (authenticated
-- role) and reads anon-style. Matches the GRANT pattern used by
-- ownership_completions (per 001 migration's NOTE block).
-- ---------------------------------------------------------------------------
GRANT SELECT ON ownership_qa_sampling TO anon, authenticated;
GRANT SELECT ON flow_alerts           TO anon, authenticated;
