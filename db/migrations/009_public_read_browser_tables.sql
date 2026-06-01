-- 009_public_read_browser_tables.sql — fixes the 007/008 role-scope bug.
-- Every table the dashboard reads from the BROWSER must allow SELECT to both
-- anon (signed-out) AND authenticated (signed-in) users. 007 created these
-- policies TO anon only, so signed-in users were blocked (blank panels).
-- TO public covers both roles. Writes remain browser-blocked (no write policy;
-- service_role bypasses RLS). Idempotent.
BEGIN;

ALTER TABLE ownership_completions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE ownership_qa_sampling  ENABLE ROW LEVEL SECURITY;
ALTER TABLE flow_alerts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE ownership_task_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon read ownership_completions"  ON ownership_completions;
DROP POLICY IF EXISTS "read ownership_completions"       ON ownership_completions;
CREATE POLICY "read ownership_completions"  ON ownership_completions  FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS "anon read ownership_qa_sampling"  ON ownership_qa_sampling;
DROP POLICY IF EXISTS "read ownership_qa_sampling"       ON ownership_qa_sampling;
CREATE POLICY "read ownership_qa_sampling"  ON ownership_qa_sampling  FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS "anon read flow_alerts"            ON flow_alerts;
DROP POLICY IF EXISTS "read flow_alerts"                 ON flow_alerts;
CREATE POLICY "read flow_alerts"            ON flow_alerts            FOR SELECT TO public USING (true);

DROP POLICY IF EXISTS "anon read ownership_task_history" ON ownership_task_history;
DROP POLICY IF EXISTS "read ownership_task_history"      ON ownership_task_history;
CREATE POLICY "read ownership_task_history" ON ownership_task_history FOR SELECT TO public USING (true);

GRANT SELECT ON ownership_completions, ownership_qa_sampling, flow_alerts, ownership_task_history
  TO anon, authenticated;

COMMIT;
