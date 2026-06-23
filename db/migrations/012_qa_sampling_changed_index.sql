-- 012_qa_sampling_changed_index
-- Run in the dashboard Supabase project's SQL Editor. Safe to run anytime
-- (it does not gate any code merge — it only makes an existing query fast).
--
-- ⚠️ Dashboard project only — Supabase project ref isccbmgjgtdosiccstcp (the one
--    with ownership_completions / ownership_qa_sampling / ownership_task_history).
--    NOT the finance project.
--
-- Purpose: the Critical Errors page loads every BO-QA "changed" verdict since
-- 2026-05-25, filtered by qa_status='changed' and a reviewed_at range, ordered by
-- airtable_record_id. The only existing index is on reviewed_at alone, so the
-- planner scanned the whole month-wide reviewed_at window, filtered qa_status in
-- memory, then sorted by airtable_record_id — tripping statement_timeout (57014)
-- as the table grew. This partial index covers exactly that query: it indexes only
-- the (sparse) 'changed' rows, in airtable_record_id order, so the scan is tiny and
-- needs no sort. reviewed_at is the trailing column so the date-range filter is
-- index-covered too.

CREATE INDEX IF NOT EXISTS idx_qa_sampling_changed_recid
  ON ownership_qa_sampling (airtable_record_id, reviewed_at)
  WHERE qa_status = 'changed';
