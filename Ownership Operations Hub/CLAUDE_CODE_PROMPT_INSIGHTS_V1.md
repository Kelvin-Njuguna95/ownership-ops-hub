# Claude Code prompt — Ownership Insights v1 (Phase A + Phase B scaffolding)

> Paste this whole block into Claude Code in the `/Users/macbookpro/Documents/QA Hourly Analysis/` repo. Do NOT run it in Cowork. Cowork is auditor only.

---

You are working in `/Users/macbookpro/Documents/QA Hourly Analysis/`. The live app is here — `render_dashboard.py`, `dashboard.html`, `ww_audit_log.json`, `daily_aggregates.json`, `.poll_work/run_poll_cycle7.py`, etc. The `Ownership Operations Hub` subfolder contains the design doc `DESIGN_OWNERSHIP_INSIGHTS_V1.md` — read it first.

**Important context.** This is an Airtable-backed dashboard for Impact Outsourcing's Vessel Ownership QA operation. The single source of truth is Airtable base `appHZdfC2sn9MLGFZ`, table `relations_support` (`tblpj9aJP4ExhYCZF`). Five teams in scope: Simba, Tembo, Pweza, Kobe, Nyati. Do not touch other teams.

The current app reads only 9 fields and misses the Comment column, WW QA stage, daily intake, reminders, reasons-for-change, source-of-truth mix, the new-company moderation queue, role-level data, and per-role-task `Requested By`. This sprint fixes that.

## Goal

Implement Phase A end-to-end and lay scaffolding for Phase B (see `Ownership Operations Hub/DESIGN_OWNERSHIP_INSIGHTS_V1.md` §5).

## Tasks

### 1. Verify the Airtable schema first

Before adding any field IDs, run a schema fetch against `relations_support` and confirm the field display names below. Save the schema result to `Ownership Operations Hub/airtable_schema_snapshot.json`. If any of these display names do not exist exactly, stop and ask before guessing. Names to confirm:

```
qa_assignee, ww_qa_assignee, ww_qa, status, done_selected_time, is_change, created,
Comment, reminder, reason_for_change, source_flow, Add new company,
Requested By, Valid, Role, start_date
```

Use the Airtable MCP (`mcp__893e91e9-...__get_table_schema`) or the meta API — do not scrape the UI.

### 2. Expand `config.field_ids` in `ww_audit_log.json`

Add every confirmed field above to `config.field_ids`. Keep the existing entries. Do not rename any existing keys (the renderer reads them by name).

Also add `config.verification_status_choices` entries for any missing values, in particular `Selected for WW QA` if it exists. Preserve the trailing space on `"Selected for BO QA "` exactly — it is the literal Airtable option name.

### 3. Update the poller (`.poll_work/run_poll_cycle7.py`)

In `extract()`, pull every new field. Add a unit test or repl snippet under `.poll_work/tests/test_extract_v2.py` that confirms a sample record round-trips all the new keys.

Switch QA attribution everywhere from `last_modified_by` to `qa_assignee`. Add a fallback to `last_modified_by` only when `qa_assignee` is blank, and log a counter of how often the fallback fires so we can spot data-quality issues.

### 4. New aggregations

In a new file `.poll_work/aggregate_v2.py`, compute the following per `(date, team, agent, qa, ww_qa)` and write to `daily_aggregates.json` under a new top-level key `aggregates_v2`:

- counts_by_verification_status: dict[status → int]
- counts_by_qa_status: dict[status → int]
- comment_distribution: dict[comment_value → int]  (the 12 dropdown values from the SOPs)
- source_flow_distribution: dict[source → int]
- add_new_company_open: int  (rows where `Add new company` non-empty AND `verification_status` != Done)
- reminder_open: int
- reminder_overdue: int  (reminder date < today)
- reason_for_change_missing: int  (is_change == true AND reason_for_change blank)
- daily_intake: int  (count of records with `created` == today)
- per_role_volume: dict[role → int]
- ww_qa_throughput: int
- ww_qa_change_rate: float
- sampling_actual_pct: float  (in_bo_qa / tagged_today)
- sampling_target_pct: float = 25.0
- reject_rate: float  (qa_changed / qa_inspected)
- reject_threshold: float = 30.0
- lead_time_seconds: {created_to_tagged_p50, created_to_tagged_p90, tagged_to_bo_qa_p50, tagged_to_bo_qa_p90, bo_qa_to_done_p50, bo_qa_to_done_p90}

Use timestamps `created` → `start_tagging_date` → `qa_status_ts` → `done_selected_time` for the lead-time computation. Skip records missing intermediate timestamps rather than erroring.

Add a regression test in `.poll_work/tests/test_aggregate_v2.py` that feeds a 20-record fixture and asserts each metric.

### 5. Rebuild `dashboard.html` as a multi-page UI

Replace the single-tab layout with a left-nav of seven pages (see design doc §4.3). Use `window.cowork.callMcpTool` for the live Airtable fetch — keep that path. Load Chart.js and Grid.js from CDN (these two only — no other CDN libs).

Implement these pages **fully** in this sprint:

- **Overview** — KPI cards (daily intake, tagged today, done today, BO QA backlog, WW QA backlog, sampling % vs 25%, reject % vs 30%, silent changes today).
- **Agent Scorecard** — agent picker on top, then: hourly heatmap (existing), productivity vs 280, reject %, comment-mix horizontal bar, reminder backlog table, role-mix pie. Drill-down to records. (No peer-review widget — that workflow has been retired.)
- **BO QA Console** — daily worklist (Tagged records grouped by Assignee, with 5/5/5 stratification helper showing top/mid/bottom by record count), separate "Add new company — 100% QA required" table, today's reviews split approve/changed.
- **Daily Intake & Assignments** — bar chart of daily intake by team (today + 7-day rolling), unassigned count, age of oldest Waiting record.

Stub these pages with a "Coming in Phase C/D" placeholder card listing the planned widgets (do not implement the data yet):

- **WW QA Console**
- **Pipeline & Lead Time**
- **Case Scenarios**

Persist the user's last-selected page and last-selected agent in `localStorage` so reloads land them where they were.

### 6. Move the roster out of state

Extract the agent / BO QA / WW QA roster from `ww_audit_log.json` into a new file `config/roster.json` with this shape:

```json
{
  "teams": {
    "Simba":  { "members": [...], "qa": "...", "ww_qa": "..." },
    "Tembo":  { "members": [...], "qa": "...", "ww_qa": "..." },
    "Pweza":  { "members": [...], "qa": "...", "ww_qa": "..." },
    "Kobe":   { "members": [...], "qa": "...", "ww_qa": "..." },
    "Nyati":  { "members": [...], "qa": "...", "ww_qa": "..." }
  }
}
```

Update every reader to load from this file. Leave a deprecation shim in `ww_audit_log.json` that mirrors the new file for one release.

### 7. Housekeeping

- Delete the stale poll scripts (`run_poll.py`, `run_poll_v2.py`, `run_poll_now.py`, `run_poll_cycle.py`) — keep only `run_poll_cycle7.py`. Verify nothing imports them first.
- Consolidate `ARCHITECTURE_V1.md` through `ARCHITECTURE_V6.md` into a single `ARCHITECTURE.md` that points at `Ownership Operations Hub/DESIGN_OWNERSHIP_INSIGHTS_V1.md` as the current source of truth.
- Run the existing test suite. Don't break anything currently green.

## Constraints

- Read-only on Airtable. Do not write back. Do not create records, update fields, or delete rows under any circumstance.
- No new CDN dependencies beyond Chart.js and Grid.js.
- Do not migrate to Next.js / Supabase in this sprint. That is Phase E. Keep the JSON-file architecture.
- Preserve the literal value `"Selected for BO QA "` with its trailing space.
- The trailing 23:00 EAT daily verdict snapshot must continue to work. Test it.

## Definition of done

1. `airtable_schema_snapshot.json` exists and matches the new field IDs.
2. `ww_audit_log.json` field_ids covers every field in §1.
3. `.poll_work/aggregate_v2.py` produces all metrics in §4 and its tests pass.
4. `dashboard.html` opens with a working left-nav, four fully implemented pages, three stub pages.
5. `config/roster.json` is the only place the roster is defined.
6. One green run of the existing test suite + the two new test files.
7. A short `CHANGELOG.md` entry summarising what shipped.

## Out of scope

Phase C (WW QA + new-company queue full implementation), Phase D (lead-time charts beyond the aggregates), and Phase E (Next.js replatform). Those get their own prompts.

---

When you're done, write a one-paragraph summary at the end of the session of (a) what you confirmed in the schema fetch, (b) any field that didn't exist under the expected name, and (c) any aggregation you couldn't compute because the source field was missing.
