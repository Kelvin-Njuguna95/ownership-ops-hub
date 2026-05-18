# Today-scoped metric audit — pre-refactor inventory

Generated as the Step 1 deliverable for `fix-systemic-today-metric-truncation`.
Source surveyed: `deploy/index.html` + `.poll_work/aggregate_v2.py`.

## Premise verification (run before the audit)

Direct Airtable query (no cap, 55 pages) vs current dashboard for 2026-05-18:

| Team | Airtable ground truth | Dashboard | Delta | Truncated? |
|---|---:|---:|---:|---|
| Simba | 996 | 996 | 0 | no — most-recently-modified slice, fully captured |
| Tembo | 993 | 162 | +831 | **YES** |
| Pweza | 753 | 181 | +572 | **YES** |
| Kobe | 1,348 | 252 | +1,096 | **YES** |
| Nyati | 1,387 | 762 | +625 | **YES** |
| **TOTAL** | **5,477** | **2,353** | **+3,124** | **57% under-report on the headline KPI** |

Root cause: `poll_airtable.py` Fetch A caps at `RECENT_PAGE_CAP = 20` pages × `pageSize=100` = 2,000 records, sorted by `last_modified` desc. On a 5,477-record day, the 3,477 oldest-modified records are dropped from cache → every today-scoped metric computed off the cache under-reports.

---

## Per-page audit

### Page 1 — Overview

| Metric (display) | Aggregator key | Today field | Source fetch | Truncation risk |
|---|---|---|---|---|
| Records uploaded today (whole table) | `relations_support_intake_today` | `Created` | B (intake_p*) | no (whole-table fetch, separate cap) |
| Tagged today (5 teams) | `tagged_today` | `start_tagging` | A | **YES** |
| Done today (5 teams) | `done_today` | `done_selected_time` OR `valid_selected_time` | A | **YES** |
| Properly completed today | `properly_completed_today` | derived from `tagged_today` set | A | **YES** |
| Dead vessels today | `dead_vessels_today` | derived from `tagged_today` set | A | **YES** |
| BO QA backlog | `bo_qa_backlog` | none (current state) | C | no |
| WW QA backlog | `ww_qa_backlog` | none (current state) | A/C | no |
| Sampling % (sanctions + non-sanctions) | `sampling_*_pct` | derived from `tagged_today` cohort | A | **YES** |
| Reject % | `reject_rate` | derived from `qa_inspected_today` | A | **YES** |
| Flow A/B/C today | `flow_{a,b,c}_today` | `start_tagging` for the today gate | A | **YES** |
| Total completions today | `total_completions_today` | `flow_a_today + flow_c_today` | A | **YES** |
| Silent changes today | `silent_changes_today` | legacy block (not v2) | legacy | n/a |
| Unique IMOs (cache) | `unique_imos` | none (cache-wide) | A | not today-scoped |

### Page 2 — Agent Scorecard

| Metric | Aggregator key | Today field | Source | Truncation |
|---|---|---|---|---|
| Tagged today | `by_agent[].tagged_today` | `start_tagging` | A | **YES** |
| QA reviewed | `by_agent[].qa_inspected_today` | derived from tagged_today + qa_status set | A | **YES** |
| Reject % | `by_agent[].reject_rate` | derived | A | **YES** |
| Properly completed today | `by_agent[].properly_completed_today` | derived | A | **YES** |
| Dead vessels today | `by_agent[].dead_vessels_today` | derived | A | **YES** |
| Sampling % (sanctions + non) | `by_agent[].sampling_*_pct` | derived | A | **YES** |
| Reminder backlog | `by_agent[].reminder_open` | `reminder` (any date) | A | not today-scoped |
| Unique IMOs | `by_agent[].unique_imos` | none | A | not today-scoped |

### Page 3 — Hourly Output

| Metric | Source | Truncation |
|---|---|---|
| Hourly Tagging Output by Agent | `STATE.completions` from `ownership_completions` Supabase table (PR #7) | **no — already migrated** |

Excluded from this refactor's scope.

### Page 4 — QA Performance

| Metric | Aggregator key | Today field | Source | Truncation |
|---|---|---|---|---|
| Active QAs | `qa_reviewers.length` | none (cache-wide) | A | not today-scoped |
| Total reviews | `qa_reviewers[].reviews` | none | A | not today-scoped |
| Approval %, avg/median/p90 response | derived | none | A | not today-scoped |

All metrics on this page are all-time across the cache window (no "today" filter). Cache-window truncation is a separate concern out of scope here.

### Page 5 — BO QA Console

| Metric | Aggregator key | Today field | Source | Truncation |
|---|---|---|---|---|
| Tagged today (header repeat) | `tagged_today` | `start_tagging` | A | **YES** |
| BO QA backlog | `bo_qa_backlog` | none | C | no |
| `add_new_company` open | `add_new_company_open` | none (current state) | A/C | no |
| Today's reviews | `qa_inspected_today` | derived | A | **YES** |

Also: the page issues a LIVE Airtable fetch (`liveBoQa`) for the worklist itself — not from cache, so unaffected by this refactor.

### Page 6 — Daily Intake

| Metric | Aggregator key | Today field | Source | Truncation |
|---|---|---|---|---|
| Records uploaded today (whole table) | `relations_support_intake_today` | `Created` (whole table count from metadata) | B | no |
| Distinct tasks uploaded today | `tasks_today_count` | `Created` | B | no |
| Routed to 5 teams today | `team_routed_intake_today` | `Created` AND assignee in roster | A | **YES** (assignee scoping happens off A's truncated cache) |
| Tagged today (5 teams) | `tagged_today` | `start_tagging` | A | **YES** |
| 7-day intake (cache trend) | `STATE.cacheRecords` filtered by created date | `Created` | A | **YES** for today's slice |

### Page 7 — Tasks

All metrics here are all-time per-task aggregates across the cache window. Cache-window truncation is a separate concern. **No today-scoped metrics flagged for this refactor.**

### Page 8 — WW QA Console

| Metric | Aggregator key | Today field | Source | Truncation |
|---|---|---|---|---|
| WW QA backlog | `ww_qa_backlog` | none (currently `vs == "Selected for WW QA"`) | A/C | no |
| WW QA throughput | `ww_qa_throughput` | none (cache-wide) | A | not today-scoped |
| WW change rate | `ww_qa_change_rate` | derived | A | not today-scoped |

**Spec note:** the user's Fetch G definition is different from the current backlog computation:
- Current: `verification_status == "Selected for WW QA"`
- Spec'd Fetch G: `{ww_qa_assignee} != "" AND {ww_qa} = ""` (assigned to WW QA but not yet reviewed)

These are semantically different. The refactor will switch to the spec'd definition.

### Page 9 — Pipeline & Lead Time

All metrics are all-time percentiles across the cache window. **No today-scoped metrics flagged.** (Cache-window truncation could skew these by biasing toward recently-modified records, but that's out of scope here.)

### Page 10 — Case Scenarios

Comment distribution is cache-wide (all-time). **No today-scoped metrics flagged.**

### Page 11 — Weekly Report

Rolls up per-day snapshots. Each historical snapshot was written when the aggregator ran on that day, using that day's cache — so historical days have the same truncation bias baked in. Future snapshots (after this refactor) will be accurate. Historical days remain as-is (no backfill in scope).

| Metric | Source | Truncation today | Truncation historical |
|---|---|---|---|
| Total records / Daily avg / Per-team records / Top performer / Agents not working | snapshot-derived | fixed by refactor | baked-in, won't be retroactively fixed |
| Flow A/B/C totals | snapshot-derived | fixed by refactor | baked-in |
| Unique IMOs / Active agents avg | snapshot-derived | not truncation-risk | n/a |

---

## Summary — by today-definition

### Group 1: `start_tagging today` → **NEW Fetch D**

Aggregator keys:
- `tagged_today`, `tagged_today_sanctions`, `tagged_today_non_sanctions`
- `by_team[].tagged_today`, `by_agent[].tagged_today`
- `properly_completed_today`, `dead_vessels_today`
- `flow_a_today`, `flow_b_today`, `flow_c_today`, `total_completions_today`
- `sampling_non_sanctions_pct`, `sampling_sanctions_pct`, `sampling_actual_pct`
- `in_bo_qa_today`, `need_to_be_update_today` (derived from tagged_today set)
- `qa_inspected_today`, `qa_changed_today`, `reject_rate` and their sanctions/non-sanctions splits (gated on tagged_today set in current code; may move to Fetch F)

### Group 2: `done_selected_time` or `valid_selected_time` today → **NEW Fetch E**

Aggregator keys:
- `done_today`
- (Flow A/B/C: currently gated on `start_tagging today` AND `vs in (Done, Valid)`. Per spec, Fetch E becomes the source for the "completed today" gate; cross-joined with tagged_today set as needed.)

### Group 3: `qa_status_ts today` → **NEW Fetch F**

Aggregator keys:
- `qa_inspected_today`, `qa_inspected_today_sanctions`, `qa_inspected_today_non_sanctions`
- `qa_changed_today`, `qa_changed_today_sanctions`, `qa_changed_today_non_sanctions`
- `reject_rate`, `reject_rate_sanctions`, `reject_rate_non_sanctions`
- per-QA today-scoped reviews

(Currently these are gated on `start_tagging today`, which under-counts when a QA reviewed today a record tagged yesterday. Fetch F switches the gate to the actual review timestamp.)

### Group 4: `Created today` → **EXISTING Fetch B** (unchanged)

Aggregator keys:
- `relations_support_intake_today` (already from metadata.totalRecordCount, accurate)
- `tasks_today_count`, `tasks_today`
- `team_routed_intake_today` — currently sourced from A's truncated cache; will move to Fetch B's full set + roster check

### Group 5: current-state queues (no date filter)

- `bo_qa_backlog` → **EXISTING Fetch C** (`vs == "Selected for BO QA "`)
- `add_new_company_open` → from current-state cache (Fetch A or C; unchanged)
- `reminder_open`, `reminder_overdue` → from cache (any date with reminder field; truncation-risk but not today-scoped)
- `ww_qa_backlog` → **NEW Fetch G** with spec'd definition (`ww_qa_assignee filled AND ww_qa blank`)

### Group 6: `completed_at` (Hourly Output) → **EXISTING ownership_completions** Supabase table

- Unchanged. Already migrated in PR #7.

### Group 7: all-time / cache-wide (not today-scoped — out of scope for this refactor)

- Lead time percentiles, QA reviewer aggregates, task breakdowns, comment distribution, role volume, source flow distribution, status counts.

---

## Refactor implication: dedupe across fetches

A single record can appear in multiple fetches:
- Tagged today + done today + QA-reviewed today → appears in D, E, F.
- Currently in BO QA + tagged today → appears in C, D.

`_load_records()` already dedupes by record id across A/B/C. The refactor extends that to A/B/C/D/E/F/G. Each metric reads from the appropriate filtered fetch independently; the underlying record set is unified for per-record operations (lookups, lead-time computations).
