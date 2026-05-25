-- 005_qa_sampling_review_lifecycle
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR
-- that updates completion_detector.py (same rule as 001-004). The detector's
-- new PATCH names the `reviewed_at` / `qa_status` columns; PostgREST rejects a
-- write naming a column the table lacks, which would crash the detector every
-- poll cycle if the code merged before this ran.
--
-- ⚠️ This MUST run in the dashboard project — Supabase project ref
--    isccbmgjgtdosiccstcp (the one whose tables include ownership_qa_sampling,
--    ownership_completions, ownership_task_history). It is NOT the finance /
--    treasury project.
--
-- Purpose: make ownership_qa_sampling a full review-lifecycle record so the BO
-- QA Console can show per-team / per-QA backlog and review turnaround.
--   reviewed_at        — when the detector first saw a QA verdict (NULL = pending)
--   qa_status          — the verdict: 'approve' or 'changed'
--   add_a_new_company  — the add-new-company field, captured at sampling time
--   assignee           — the agent who tagged the record, captured at sampling time
-- No backfill: reviewed_at cannot be reconstructed for past reviews. Existing
-- rows keep NULL; the detector retro-stamps any still-visible ones on its next
-- cycles. The existing GRANT SELECT on the table already covers new columns.

ALTER TABLE ownership_qa_sampling ADD COLUMN IF NOT EXISTS reviewed_at       timestamptz;
ALTER TABLE ownership_qa_sampling ADD COLUMN IF NOT EXISTS qa_status         text;
ALTER TABLE ownership_qa_sampling ADD COLUMN IF NOT EXISTS add_a_new_company text;
ALTER TABLE ownership_qa_sampling ADD COLUMN IF NOT EXISTS assignee          text;

CREATE INDEX IF NOT EXISTS idx_qa_sampling_reviewed_at ON ownership_qa_sampling(reviewed_at);
