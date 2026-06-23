-- 013_ce_reviewed_by_agent_rpc
-- Run in the dashboard Supabase project's SQL Editor (ref isccbmgjgtdosiccstcp).
-- NOT the finance project.
--
-- Purpose: loadCriticalErrors() in deploy/index.html already calls
--   sb.rpc("ce_reviewed_by_agent", { since, until })
-- to get per-agent reviewed totals (approve + changed) for the current week as
-- error-rate context, and falls back to a heavy client-side row scan when the RPC
-- is absent. The RPC was never deployed, so the slow fallback is what runs. This
-- function does the count server-side (one indexed aggregate) and returns one row
-- per assignee. Granting EXECUTE to anon/authenticated matches the table's existing
-- public SELECT grant (browser reads it directly).

CREATE OR REPLACE FUNCTION ce_reviewed_by_agent(since timestamptz, until timestamptz)
RETURNS TABLE (assignee text, n bigint)
LANGUAGE sql STABLE AS $$
  SELECT assignee, count(*) AS n
  FROM ownership_qa_sampling
  WHERE qa_status IN ('approve', 'changed')
    AND reviewed_at >= since
    AND reviewed_at <  until
  GROUP BY assignee;
$$;

GRANT EXECUTE ON FUNCTION ce_reviewed_by_agent(timestamptz, timestamptz) TO anon, authenticated;
