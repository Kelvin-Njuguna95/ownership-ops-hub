-- Audit: every browser-read table must allow SELECT to anon AND authenticated.
-- Run after any RLS/security change. Any row with ok=false is a latent "blank
-- panel for signed-in (or signed-out) users" bug.
WITH browser_tables(t) AS (VALUES
  ('ownership_completions'),('ownership_qa_sampling'),('flow_alerts'),('ownership_task_history'))
SELECT bt.t AS table_name,
       bool_or(p.polcmd IN ('r','*') AND ('anon'         = ANY (p.polroles::regrole[]::text[]) OR 'public' = ANY (p.polroles::regrole[]::text[]))) AS anon_can_read,
       bool_or(p.polcmd IN ('r','*') AND ('authenticated'= ANY (p.polroles::regrole[]::text[]) OR 'public' = ANY (p.polroles::regrole[]::text[]))) AS auth_can_read
FROM browser_tables bt
LEFT JOIN pg_class c ON c.relname = bt.t
LEFT JOIN pg_policy p ON p.polrelid = c.oid
GROUP BY bt.t
ORDER BY bt.t;
-- Expect anon_can_read = true AND auth_can_read = true for all four rows.
