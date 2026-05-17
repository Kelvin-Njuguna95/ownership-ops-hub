# Tasks tracking

**What a task is.** A task is a single value of the `requested_by` column on a `relations_support` record — e.g. `CargoChangeIntel17May2026_task6`. Operations uploads records to the table in named batches; every record in the same batch shares one `requested_by` value, so tasks are the natural unit of work to track.

**Where the Tasks page reads its data.** The top-of-page table is populated from `aggregates_v2.tasks_all` in `daily_aggregates.json` — pre-aggregated from the local `.poll_work/recent_p*.json` cache, no Airtable round-trip. The per-task detail drawer's "Fetch all records" button (and only that button) calls Airtable live via `mcp__...__list_records_for_table` filtered by `{requested_by} = "<task name>"`, read-only.

**The 5 flag rules.**
- **incomplete** — at least one record is still in `waiting`, `tagged`, `Selected for BO QA `, `Selected for WW QA`, or `need to be update`.
- **stuck** — no record in the task had `last_modified` in the past 24h. Entire task is dormant.
- **company-gap** — any record marked `Done` or `Valid` but with no `company_id` AND `dead_vessel` unchecked. SOP violation: completed without linking a company and without flagging the vessel as dead.
- **unassigned** — any record has an empty assignees list.
- **high-waiting** — `waiting` is strictly greater than 50% of the task's total records.

**Cache window caveat.** The summary view is bounded by the polling cache (records modified in the past 24h, capped at the 20-page fetch — see `TECH_DEBT.md` item 1). Tasks created weeks ago will only show the records that have recently moved through them; older records sit outside the window. To see every record for a task regardless of when it was last touched, open the detail drawer and click "Fetch all records".
