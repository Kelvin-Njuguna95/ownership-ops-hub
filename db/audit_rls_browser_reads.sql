-- Audit: every browser-read table must allow SELECT to anon AND authenticated.
-- Run after any RLS/security change. Any row with false is a latent "blank panel"
-- bug — signed-out users if anon_can_read=false, signed-in if auth_can_read=false.
-- A `TO public` policy stores polroles={0} (the PUBLIC pseudo-role, OID 0), which
-- covers BOTH anon and authenticated — detect it via 0, not the string 'public'
-- ('0'::regrole renders as '-', so string-matching 'public' false-negatives).
WITH browser_tables(t) AS (VALUES
  ('ownership_completions'),('ownership_qa_sampling'),('flow_alerts'),('ownership_task_history'))
SELECT bt.t AS table_name,
  bool_or(p.polcmd IN ('r','*') AND (0::oid = ANY(p.polroles) OR 'anon'::regrole::oid          = ANY(p.polroles))) AS anon_can_read,
  bool_or(p.polcmd IN ('r','*') AND (0::oid = ANY(p.polroles) OR 'authenticated'::regrole::oid = ANY(p.polroles))) AS auth_can_read
FROM browser_tables bt
LEFT JOIN pg_class c ON c.relname = bt.t
LEFT JOIN pg_policy p ON p.polrelid = c.oid
GROUP BY bt.t
ORDER BY bt.t;
-- Expect anon_can_read = true AND auth_can_read = true for ALL FOUR rows.
