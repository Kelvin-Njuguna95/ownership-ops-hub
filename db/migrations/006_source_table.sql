-- 006_source_table
-- Run in the dashboard Supabase project's SQL Editor BEFORE merging the PR
-- that updates completion_detector.py for relations_io (Phase B3) — same rule
-- as 001-005. The B3 detector INSERTs a `source_table` value on every row.
-- PostgREST rejects a write naming a column the table lacks, which would crash
-- the detector every poll cycle if the code merged before this ran.
--
-- ⚠️ This MUST run in the dashboard project — Supabase project ref
--    isccbmgjgtdosiccstcp (the one whose tables include ownership_completions,
--    ownership_qa_sampling, flow_alerts). It is NOT the finance / treasury
--    project.
--
-- Purpose: all five Ownership teams now work two Airtable tables —
-- relations_support and relations_io. `source_table` records which table each
-- row came from, so the Hourly Output tracker can combine both and the BO QA
-- views can compare them. Every existing row was polled from relations_support,
-- so the DEFAULT backfills them correctly. NOT NULL guarantees every future row
-- is attributed. The CHECK rejects any value other than the two known tables.
-- The existing GRANT SELECT on each table already covers the new column.

ALTER TABLE ownership_completions
    ADD COLUMN IF NOT EXISTS source_table text NOT NULL DEFAULT 'relations_support'
    CHECK (source_table IN ('relations_support', 'relations_io'));

ALTER TABLE ownership_qa_sampling
    ADD COLUMN IF NOT EXISTS source_table text NOT NULL DEFAULT 'relations_support'
    CHECK (source_table IN ('relations_support', 'relations_io'));

ALTER TABLE flow_alerts
    ADD COLUMN IF NOT EXISTS source_table text NOT NULL DEFAULT 'relations_support'
    CHECK (source_table IN ('relations_support', 'relations_io'));
