-- 008_anon_read_ownership_completions.sql — security phase 5 follow-up / hotfix
--
-- Fixes a regression introduced by 007_enable_rls.sql (security PR #158):
-- 007 enabled RLS on ownership_completions with NO anon SELECT policy, on the
-- premise (SECURITY_SUPABASE_RLS_CHECKLIST.md) that the table is "server-only"
-- and "not read by the browser". That premise is wrong — deploy/index.html's
-- loadCompletions() reads ownership_completions DIRECTLY via the anon key:
--
--     fetchAll("ownership_completions", "completed_by", "completed_at")  // index.html
--
-- That read feeds BOTH:
--   • Overview → "Agent productivity today" scorecard (per-agent tagged totals
--     come from STATE.completions, not the snapshot's by_agent), and
--   • the Hourly Output heatmap (records each agent moved waiting→tagged per hour).
--
-- With RLS on and no anon policy the browser silently received [] (HTTP 200,
-- zero rows) for this table, blanking both panels on 2026-06-01 — even though
-- the table held tens of thousands of rows and the poll/snapshot were healthy.
--
-- Fix: grant anon SELECT, matching the read-only sibling tables already covered
-- by 007 (ownership_qa_sampling, ownership_task_history, flow_alerts). This
-- table carries only operational completion metadata
-- (airtable_record_id, completed_by, completed_at) — the same sensitivity class
-- as those siblings. No anon write policy is added, so the publishable key still
-- cannot INSERT/UPDATE/DELETE; the server pipeline keeps using service_role.
--
-- Idempotent — safe to re-run.

BEGIN;

ALTER TABLE ownership_completions ENABLE ROW LEVEL SECURITY;  -- already on; no-op
DROP POLICY IF EXISTS "anon read ownership_completions" ON ownership_completions;
CREATE POLICY "anon read ownership_completions" ON ownership_completions
  FOR SELECT TO anon USING (true);

COMMIT;
