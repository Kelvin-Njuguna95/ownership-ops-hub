# Tech Debt — Phase A follow-ups

Items surfaced during Phase A implementation that should be picked up in later sprints. **Do not fix without explicit ops sign-off.**

---

## 1. 20-page cap silently truncates ~10k records/day

`POLL_PROCEDURE.md` step 3 caps the recent-window fetch at 20 pages (2,000 records). The May 17 2026 cache refresh showed `metadata.totalRecordCount = 12,811` records matching the `isWithin pastNumberOfDays=1` filter — meaning ~10,800 records per day are silently dropped.

**Symptom:** 91% of the 2,000 cached records are assigned to out-of-scope users (upstream taggers). The 5 ownership teams' records are buried under the noise and most likely never reach the cache for the BO QA queue.

**Fix:** Add a server-side filter to the MCP call restricting `assignee` to the union of ownership-team members + their BO QAs + WW QAs. Lets the 100-records-per-page budget go to relevant rows.

**Scope:** Update `POLL_PROCEDURE.md` and the polling-agent prompt to add an `isAnyOf` filter on `fldT4xElSgcdnqTmy` (assignee) with the collaborator IDs from the roster.

---

## 2. `status` and `valid_done_by_bo` are 0% populated

Across 264 in-scope records on 2026-05-17, neither field had a single populated value. These are part of the Phase A field-pull and are wired up in `extract_v2.FIELD_IDS`, but the records don't carry them.

**Symptom:** Two of the 25 extracted fields are dead weight. The dashboard doesn't depend on them yet, but Phase B's Case Scenarios page wants `status` for stuck-record detection, and the SOPs reference `valid_done_by_bo` for monitoring.

**Fix:** Ask ops whether these fields are intentionally unused, used only on records outside the past-24h window, or filled by a workflow we're not seeing. If unused, drop them from `FIELD_IDS`.

---

## 3. WW QA stage appears unused in practice

- `ww_qa_assignee`: 0% populated across 264 records.
- `ww_qa` (approve/change verdict): 0% populated.
- `verification_status == "Selected for WW QA"`: 6 records sitting in the queue, none with an assignee.

**Symptom:** Page 4 (WW QA Console) shows an empty state. The 6-record backlog suggests the stage is being entered but never resolved — workflow may have stalled.

**Fix:** Confirm with ops whether WW QA is dormant, dead, or alive-but-handled-elsewhere. If alive, find out who the WW QA reviewers are so `config/roster.json` can be backfilled.

---

## 4. `reason_for_change` lives in `relations_io` (cross-table)

The "overwrites without reason" metric (`reason_for_change_missing`) was deferred from Phase A because the field doesn't exist on `relations_support`. The closest field is `relations_io.reason_for_the_change` (fldu7T8eOHaDe3uup, singleSelect). See `Ownership Operations Hub/airtable_schema_snapshot.json` for the snapshot.

**Fix:** Phase C — implement the cross-table join, alongside the new-company moderation queue.

---

## 5. `add_new_company` queue deserves its own moderation page

68.6% of in-scope records have non-empty `add_new_company` text. The SOPs say this column triggers **100% mandatory QA**. Right now we just count the open items as a number on Overview.

**Fix:** Phase C — build a dedicated moderation page that lists every open `add_new_company` row with the company text, assignee, age, and a one-click drill-down to the Airtable record (read-only link, not an edit).
