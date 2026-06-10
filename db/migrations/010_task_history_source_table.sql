-- 010_task_history_source_table
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR that
-- makes aggregate_v2._write_task_history write `source_table` (same rule as
-- 003/004/006). PostgREST rejects an upsert naming a column the table lacks, so
-- the per-cycle ledger write would start failing if the code merged first.
--
-- ⚠️ Dashboard project only — Supabase project ref isccbmgjgtdosiccstcp (the one
--    with ownership_completions / ownership_qa_sampling / ownership_task_history).
--    NOT the finance project.
--
-- Purpose: the Completed Tasks page (Completion archive) gains Relations IO /
-- Relations support tabs. `source_table` records which Airtable table a task's
-- records came from. Nullable, no default: historical completed tasks predate
-- this and cannot be reclassified (their records have aged out), so they stay
-- NULL and surface only under All completed / Sanctions. New/updated tasks
-- populate going forward. 'mixed' is allowed for batches split across tables.
-- Existing GRANT SELECT on ownership_task_history already covers this column.

ALTER TABLE ownership_task_history ADD COLUMN IF NOT EXISTS source_table text;
