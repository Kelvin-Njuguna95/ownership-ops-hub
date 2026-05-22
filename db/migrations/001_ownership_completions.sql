-- 001_ownership_completions
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR
-- that introduces .poll_work/completion_detector.py. The detector inserts
-- here every poll cycle; the dashboard's Hourly Output page reads from here.

CREATE TABLE ownership_completions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    airtable_record_id  text UNIQUE NOT NULL,
    imo                 text,
    role                text,
    verification_status text,
    company_id_and_name text,
    add_a_new_company   text,
    completed_by        text NOT NULL,
    completed_at        timestamptz NOT NULL,
    detected_at         timestamptz NOT NULL DEFAULT NOW(),
    requested_by        text,
    raw_payload         jsonb
);

CREATE INDEX idx_completed_at ON ownership_completions(completed_at);
CREATE INDEX idx_completed_by ON ownership_completions(completed_by);

-- Read access — matches the public-data model used by 002_flow_framework.sql (dashboard reads with the publishable key after sign-in; the detector writes with the service_role key, which bypasses this grant).
GRANT SELECT ON ownership_completions TO anon, authenticated;
