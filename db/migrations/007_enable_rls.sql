-- 007_enable_rls.sql — security phase 5
-- Enables Row Level Security on every ownership_* / flow_* table and adds the
-- minimum policies needed for the browser-facing anon key.
--
-- Why: the publishable anon key is hardcoded in deploy/index.html (browser
-- bundle, public). Without RLS, that key has full read/write on every table.
-- With RLS enabled and only SELECT policies for anon, the browser can read
-- but cannot write/update/delete. The server-side pipeline uses the
-- service_role key, which always bypasses RLS.
--
-- Idempotent — safe to re-run. ENABLE ROW LEVEL SECURITY is a no-op if already
-- on. DROP POLICY IF EXISTS guards the CREATE POLICY against re-run errors.

BEGIN;

-- ownership_completions: SERVER-ONLY (no browser reads). RLS on, no anon policy.
ALTER TABLE ownership_completions ENABLE ROW LEVEL SECURITY;

-- ownership_qa_sampling: browser reads pending/recent rows. anon SELECT only.
ALTER TABLE ownership_qa_sampling ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon read ownership_qa_sampling" ON ownership_qa_sampling;
CREATE POLICY "anon read ownership_qa_sampling" ON ownership_qa_sampling
  FOR SELECT TO anon USING (true);

-- flow_alerts: browser reads open alerts. anon SELECT only.
ALTER TABLE flow_alerts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon read flow_alerts" ON flow_alerts;
CREATE POLICY "anon read flow_alerts" ON flow_alerts
  FOR SELECT TO anon USING (true);

-- ownership_task_history: browser reads task history. anon SELECT only.
ALTER TABLE ownership_task_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon read ownership_task_history" ON ownership_task_history;
CREATE POLICY "anon read ownership_task_history" ON ownership_task_history
  FOR SELECT TO anon USING (true);

COMMIT;

-- Storage bucket `dashboard-data` policies are managed in the Supabase
-- dashboard (storage.objects has its own policy model). See
-- SECURITY_SUPABASE_RLS_CHECKLIST.md "Storage buckets" section for the
-- SQL Kelvin should run separately.
