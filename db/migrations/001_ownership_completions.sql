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

-- NOTE: the dashboard reads this table via the Supabase anon key after the
-- user signs in with email+PIN. With RLS disabled (Supabase default for
-- new tables is DISABLED), the anon key cannot read the table. Two options:
--
--   (a) Enable RLS and add a permissive SELECT policy for authenticated users:
--       ALTER TABLE ownership_completions ENABLE ROW LEVEL SECURITY;
--       CREATE POLICY "authenticated read" ON ownership_completions
--         FOR SELECT TO authenticated USING (true);
--
--   (b) Leave RLS off and grant SELECT to the anon role explicitly:
--       GRANT SELECT ON ownership_completions TO anon, authenticated;
--
-- The detector uses the service_role key so it bypasses RLS for INSERT
-- either way. Pick (a) if you want the safety of RLS-by-default; (b) if
-- you want the simplest path matching the existing public bucket model.
