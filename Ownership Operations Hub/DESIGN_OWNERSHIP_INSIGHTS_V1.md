# Ownership Operations Hub — Insights Expansion (Design v1)

**Author:** Cowork audit for Kelvin
**Date:** 2026-05-17
**Status:** Design ready for Claude Code implementation

---

## 1. Why this exists

The current app shows hourly output, the BO-QA queue, productivity (280/day), the 15% sampling floor, silent changes, and daily verdicts. It only reads ~9 of the ~17 Airtable fields it has IDs for, ignores the WW QA stage entirely, never reads the `Comment` column (which is the spine of the SOPs), and infers QA identity from `last_modified_by` instead of `qa_assignee`. The SOPs describe a much richer operation than the app currently sees.

The goal: turn the app into a single pane of glass that answers, for any day, "what did every BO agent, BO QA, WW QA, and team do — and were they following the SOP?"

---

## 2. Operating model (from SOPs)

The working table is `relations_support` in base `appHZdfC2sn9MLGFZ`. Each row is one (IMO × Role). Roles are: Registered Owner, Beneficial Owner, Management/Ship Manager, Technical Manager, Operator, Commercial Controller/Charterer, ISM Manager.

The lifecycle:

```
Waiting  →  Tagged  →  Selected for BO QA  →  (Done | Need to be update)
                    ↘                       ↘
                      Selected for WW QA  →  Done / Valid
```

Daily flow per the SOPs:
1. Team Lead uploads / assigns records (Assignee filled, status = Waiting).
2. BO Agent works the queue, fills the 7-role fields, sets `Comment` from the fixed dropdown (12 case scenarios), optionally sets `reminder`, flips status to `Tagged`.
3. BO QA each morning groups Tagged records by Assignee, picks **25%** stratified 5/5/5 (top/mid/bottom of the agent's batch) PLUS 100% of `Add new company` rows, fills `QA Assignee` (which moves the row to `Selected for BO QA`), and marks remaining 75% as `Done`.
4. BO QA writes `approve` or `changed` into `qa_status`; if any agent's batch is >30% wrong, the whole batch is returned for redo.
5. WW Ownership Expert performs the same review at the `Selected for WW QA` stage.
6. Monitoring tasks use the `Valid` checkbox.

KPIs the SOPs imply: 25% sampling, ≤30% reject rate, 100% QA on new-company rows, 2-week / 3-strike rule on Document Not Available, two corroborating sources for Commercial Controller, peer-to-peer coverage.

---

## 3. Current app — what it does and what it misses

### Reads today
`imo`, `assignee`, `last_modified_by`, `start_tagging_date`, `last_modified`, `company_id_and_name`, `verification_status`, `qa_status`, `qa_status_ts`.

### Configured but never read
`qa_assignee`, `ww_qa_assignee`, `ww_qa`, `status`, `done_selected_time`, `is_change`, `created`.

### Not even in the config
`Comment`, `reminder`, `reason_for_change`, `source_flow`, `Add new company`, `Requested By`, `Valid`, `Role`, `start_date` (the role-level one).

### Tables not touched
`companies` (duplicate / lesser-IMO check).

### Result
The app is blind to comments, reminders, reasons-for-change, source-of-truth mix, WW QA, the new-company queue, role-level mix, daily intake volume, and lead-time across the funnel.

---

## 4. Target architecture

Keep the existing JSON-file polling app for v1 (it works), but evolve it into a multi-page dashboard with seven views. The migration to Next.js + Supabase described in older `ARCHITECTURE_V*.md` drafts comes after this insight expansion proves the data model.

### 4.1 Data layer — expand the pull

Extend `config.field_ids` and `extract()` in `.poll_work/run_poll_cycle7.py` to also pull: `qa_assignee`, `ww_qa_assignee`, `ww_qa`, `status`, `done_selected_time`, `is_change`, `created`, `Comment`, `reminder`, `reason_for_change`, `source_flow`, `Add new company`, `Requested By`, `Valid`, `Role`, `start_date`.

The poll cycle stays the same: every 15 min, 06:00–22:59 EAT, with the 23:00 EAT daily verdict snapshot.

### 4.2 Aggregations — pre-compute, don't recompute in browser

Add a new `daily_aggregates.json` writer that, per (date × agent × team × QA × role), pre-computes:

- Counts by `verification_status` (waiting, tagged, selected_for_bo_qa, selected_for_ww_qa, done, valid, need_to_be_update).
- Counts by `qa_status` (approve, changed, blank).
- Comment distribution (12 scenarios × counts).
- Source-flow distribution.
- `Add new company` queue (non-empty + not yet done).
- Reminder cohort (open reminders, broken down by age bucket and strike count).
- Reason-for-change presence/absence on `is_change = true` rows.
- Funnel lead times: created → start_tagging_date → qa_status_ts → done_selected_time, with p50/p90.
- Daily intake volume per team (count of new `created` records today).
- Per-role volume per agent.
- WW QA throughput and change rate.
- Sampling compliance: actual sample % vs 25% target.
- Reject rate per agent (changed / qa_inspected) vs 30% threshold.

### 4.3 Pages (multi-tab dashboard)

| # | Page | Audience | Key cards |
|---|------|----------|-----------|
| 1 | **Overview** | Leadership | Daily intake, tagged today, done today, BO QA backlog, WW QA backlog, sampling % vs 25%, reject % vs 30%, silent changes |
| 2 | **Agent Scorecard** | TL + agent | Per-agent: tagged, hourly heatmap, productivity vs 280, reject %, comment mix, reminder backlog, role mix |
| 3 | **BO QA Console** | BO QA | Daily worklist with 5/5/5 stratification helper, 100% `Add new company` queue, today's reviews split approve/changed, per-QA throughput, per-agent reject % |
| 4 | **WW QA Console** | WW Ownership Expert | `Selected for WW QA` queue by age, today's WW changes vs approves, silent-change at WW stage |
| 5 | **Pipeline & Lead Time** | Ops lead | Funnel chart Waiting→Tagged→BO QA→Done, p50/p90 hours per stage, stuck records (in-stage > X days) |
| 6 | **Case Scenarios** | QA leads | Comment dropdown distribution per agent and per day, anomaly flags (e.g. "Document Not Available" with no `reminder` set) |
| 7 | **Daily Intake & Assignments** | Team Lead | New `created` records today per team, unassigned count, age of the oldest Waiting record |

Existing tabs (Hourly Output, Pending QA, Productivity, Sampling, Silent Changes, Daily Verdicts) fold into Pages 1–5 — nothing is lost.

### 4.4 Rendering

`dashboard.html` already uses `window.cowork.callMcpTool` against the Airtable MCP for live fetch — keep this path. Replace the single-tab UX with a left-nav of the seven pages. Each page is a self-contained section that reads the same aggregated JSON, so adding pages later is just adding sections.

Chart.js stays. Add Grid.js for sortable tables (worklists, queues).

### 4.5 Roster & config

Move the roster (Simba, Tembo, Pweza, Kobe, Nyati + BO QAs + WW QAs) out of `ww_audit_log.json` into a separate `config/roster.json` so it can be edited without touching state.

### 4.6 Where this goes next

Once the seven pages are live and the aggregates are stable, the same JSON schema becomes a Supabase table set and the pages become a Next.js app per the existing `ARCHITECTURE_V*.md` drafts. Auth and per-team RLS land then.

---

## 5. Phased rollout

**Phase A — Data expansion (the unblocker).** Add every unread field to the poll. Switch QA attribution from `last_modified_by` to `qa_assignee`. Land "Daily intake" KPI from `created`. This alone fixes the accuracy of every existing tab.

**Phase B — Comment & reminder analytics.** Land Pages 6 and 7 and the comment-distribution aggregations.

**Phase C — WW QA + new-company queue.** Land Page 4 and the `Add new company` mandatory-QA queue inside Page 3.

**Phase D — Lead-time & pipeline.** Land Page 5 (funnel + p50/p90).

**Phase E — Replatform** to Next.js + Supabase + Vercel.

Phases A–D run on the existing JSON-file app. The Claude Code prompt in `CLAUDE_CODE_PROMPT_INSIGHTS_V1.md` covers Phase A end-to-end and scaffolds Phases B–D.

---

## 6. Open questions to answer before Phase B

1. Are `Comment`, `reminder`, `Add new company`, `Requested By`, `Valid`, `Role`, `source_flow`, `reason_for_change`, `start_date` the exact Airtable field names, or do they have different display names in the base? (Need to run a schema fetch.)
2. Is there a single "current role-holder" row per (IMO × Role), or do role changes create new rows? Affects how lead-time and role-mix are computed.
3. Who is on the WW QA roster? The current app has no list.
4. Should the 30% reject rule trigger a Slack alert, or just a dashboard flag?
