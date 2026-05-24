-- 004_task_comment_distribution
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR
-- that makes aggregate_v2.py write per-task comment counts (same rule as
-- 001/002/003). PostgREST rejects an INSERT that names a column the table
-- does not have, so the aggregator's task-ledger write would start failing
-- on every cycle if the code merged before this column existed.
--
-- Purpose: the Reports section's Tasks workbook shows a per-task breakdown of
-- the Airtable Comment field (the 11 SOP case-scenario values). Until now the
-- aggregator computed a comment breakdown only at the whole-day level and
-- never stored it per task. This column is the permanent per-task store.
--
-- No backfill: per-task comment counts cannot be reconstructed for tasks that
-- were recorded before this ships (the raw per-record comment is not retained
-- anywhere queryable). Existing rows stay NULL; new rows populate going forward.
-- The existing `GRANT SELECT ON ownership_task_history TO anon, authenticated`
-- already covers this new column — no new grant needed.

ALTER TABLE ownership_task_history ADD COLUMN comment_distribution jsonb;
