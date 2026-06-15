# Report Metrics Dictionary — Ownership Hub Excel exports

**Scope:** the three Report Builder workbooks (`_buildTasksWorkbook`,
`_buildAgentsWorkbook`, `_buildQAsWorkbook`) in `deploy/index.html`, fed by
`_loadReportData` → `_agentAggregate` / `_qaAggregate`.

**Why this doc exists:** the same concept ("QA reviewed" / "QA changed") is
computed **three different ways from two different time-bases**, so for one
week the three workbooks disagree:

| Figure (same week) | Value | Where | Source / basis |
|---|---:|---|---|
| QAs "Total reviewed" | **18,713** | `_buildQAsWorkbook` | Σ `t.qaPeak.qa_reviewers[].reviewed` — task-lifetime peak (qaPeak day) |
| Tasks "Total QA reviewed" | **11,525** | `_buildTasksWorkbook` | Σ `t.peak.qa_reviewed` — task-lifetime peak (peak-records day, a *different* day) |
| QAs "Total records sampled" | **14,036** | `_buildQAsWorkbook` | `ownership_qa_sampling` row count, range-scoped by `sampled_at` |
| Agents scorecard "QA-checked" Σ | (4th) | `_buildAgentsWorkbook` | Σ `t.peak.agents_worked[].qa_checked` — task-lifetime peak (peak day) |

The three "reviewed" numbers can never agree because two are task-lifetime
peaks pulled from **different snapshot days** and the third is a range-scoped
event count.

---

## Time-bases in play

- **event-in-range** — a row in an append-only event table whose event
  timestamp falls in `[start, end]` (EAT day bounds → UTC via
  `T00:00:00+03:00`). Clean, additive, clipped to the window.
- **task-lifetime-peak** — a single snapshot row chosen from a task's whole
  history: `peak` = day with max `total_records`; `qaPeak` = day with max
  `qa_reviewed` (see `groupTaskHistory`, ~line 2369–2370). A task is *included*
  if any of its history rows touch the window, but the chosen snapshot's
  scalars are **NOT clipped** to the window — they reflect that whole day, which
  may sit outside `[start, end]`. This is the root inflation.

---

## Source tables / grains

| Table | Grain | Key columns | Event timestamp |
|---|---|---|---|
| `ownership_completions` | one row per tagged record | `airtable_record_id`, `completed_by` (the **tagger**, not a QA), `completed_at`, `flow` (`A`=done no-QA, `C`=done after-QA, `NULL`=tagged-not-done) | `completed_at` |
| `ownership_qa_sampling` | one row per record sent to BO QA | `airtable_record_id`, `qa_assignee`, `sampled_at`, `reviewed_at`, `qa_status` (`approve`/`changed`) | `sampled_at` (sent), `reviewed_at` (verdict) |
| `ownership_task_history` | one row per task per snapshot day | `total_records`, `completed`, `qa_reviewed`, `qa_changed`, `dead_vessels`, `agents_worked[]`, `qa_reviewers[]`, `status_distribution`, … | `snapshot_date` (daily) |

Note: `ownership_completions.completed_by` is the **tagger** (per
`resolve_completed_by`), so the Agents workbook's "records tagged" is really a
tagging-output count; `flow A/C` mark which of those reached done.

---

## Per-metric inventory (current state)

### Tasks workbook (`_buildTasksWorkbook`)
| Cell | Source | Grain | Time-basis | Incl. BO QA? |
|---|---|---|---|---|
| Tasks active / completed / open | `data.tasks` (history touches range) | task | window-touch | — |
| Total records | Σ `t.peak.total_records` | task | **lifetime-peak** | n/a |
| Total dead vessels | Σ `t.peak.dead_vessels` | task | **lifetime-peak** | n/a |
| Total with reminder | Σ `t.peak.with_reminder` | task | **lifetime-peak** | n/a |
| Total completed records | Σ `t.peak.completed` | task | **lifetime-peak** | done |
| **Total QA reviewed** | Σ `t.peak.qa_reviewed` | task | **lifetime-peak (peak day)** | yes |
| Total QA changed | Σ `t.peak.qa_changed` | task | **lifetime-peak (peak day)** | yes |
| Overall QA coverage % | `_pct(Σpeak.qa_reviewed, Σpeak.completed)` | task | lifetime-peak | — |
| Median TAT | `median(latest.tat_hours)` of completed | task | final | — |
| Per-task rows (QA reviewed / changed / coverage) | `t.peak.*` | task | lifetime-peak | yes |

### Agents workbook (`_buildAgentsWorkbook`)
| Cell | Source | Grain | Time-basis |
|---|---|---|---|
| Total records tagged / daily / hourly / Flow A / Flow C | `ownership_completions` via `_agentAggregate` | record | **event-in-range** (`completed_at`) |
| Scorecard **QA-checked / QA-changed / Reject%** | Σ `t.peak.agents_worked[].qa_checked` / `.qa_changed` | person×task | **lifetime-peak (peak day)** |

### QAs workbook (`_buildQAsWorkbook`)
| Cell | Source | Grain | Time-basis |
|---|---|---|---|
| Total records sampled / daily / hourly | `ownership_qa_sampling` via `_qaAggregate` (`q.total`) | record | **event-in-range** (`sampled_at`) |
| **Total reviewed** | Σ `t.qaPeak.qa_reviewers[].reviewed` | person×task | **lifetime-peak (qaPeak day)** |
| Total changed | Σ `t.qaPeak.qa_reviewers[].changed` | person×task | **lifetime-peak (qaPeak day)** |
| Overall reject rate % | `_pct(Σreviewed, Σchanged)` | person | lifetime-peak |
| Scorecard reviewed/approved/changed | `t.qaPeak.qa_reviewers[]` | person×task | lifetime-peak |

---

## The fix: one canonical, range-scoped, record-grain definition

Add `_reportMetrics(data)` (memoised on `data`) computing the shared QA numbers
**one way only**, all event-in-range, record grain:

| Canonical metric | Definition (proposed) | Event table / basis |
|---|---|---|
| `completedInRange` | count of `ownership_completions` rows with `flow ∈ {A,C}` (i.e. actually done) | `completed_at` in range |
| `qaReviewedInRange` | count of `ownership_qa_sampling` rows with a verdict (`qa_status ∈ {approve,changed}`) | `reviewed_at` in range |
| `qaChangedInRange` | count of `ownership_qa_sampling` rows with `qa_status = changed` | `reviewed_at` in range |
| `recordsSampledInRange` | count of `ownership_qa_sampling` rows | `sampled_at` in range |
| `qaCoveragePct` | `_pct(qaReviewedInRange, completedInRange − deadAdj)` | derived |

**All three workbooks' headline "reviewed" cells** (QAs "Total reviewed", Tasks
"Total QA reviewed", and the Agents scorecard QA total) read
`qaReviewedInRange` → they tie out by construction. Task-lifetime peak figures
stay **only** as separate, explicitly-labelled columns ("… (task lifetime)").

A build-time tie-out self-check asserts the three headline numbers match across
workbooks; on divergence → `console.warn` + a visible "⚠ metric mismatch" note
on the report cards (mirrors the existing `completionsPartial` pattern).

---

## ⚠ Decisions needed before / during implementation

1. **Reviewed time-basis = `reviewed_at` (verdict), not `sampled_at`.**
   Matches the existing `loadQaReviewHours` precedent (a "review" = a verdict).
   Consequence: `recordsSampledInRange` (by `sampled_at`) and
   `qaReviewedInRange` (by `reviewed_at`) are **different cohorts** — a record
   sampled in week 1 but reviewed in week 2 lands in each week's respective
   metric. This requires `_loadReportData` to also fetch reviewed events
   (a second `ownership_qa_sampling` page-read clipped by `reviewed_at`, pulling
   `qa_status`). *Default chosen: `reviewed_at`.* Confirm.

2. **`completedInRange` denominator = `flow ∈ {A,C}`** (done records), not all
   tagging rows (`flow NULL` = tagged-not-yet-done). Confirm; alternative is
   all completion rows.

3. **Dead-vessel exclusion from the coverage denominator is NOT possible at
   event grain.** `ownership_completions` carries no dead-vessel flag; the field
   exists only at task-lifetime grain (`peak.dead_vessels`). Options:
   - (a) denominator = `completedInRange`, no dead-vessel exclusion — flag the
     limitation in the report footnote; **recommended** until the event table
     carries the flag;
   - (b) approximate by subtracting Σ `t.peak.dead_vessels` — **mixes grains**
     (lifetime-peak task count vs in-range record count) and can over-subtract.

   Per the task instruction the default is "exclude + note", but since the flag
   is absent at this grain, the honest default is **(a) do not subtract, and
   note it**. Confirm whether to add a dead-vessel column to
   `ownership_completions` later.

4. **Per-person scorecards** (who reviewed how much) only exist at task-lifetime
   grain today (`qaPeak.qa_reviewers[]`). The event tables have `qa_assignee`
   on sampling rows, so per-QA reviewed-in-range *is* derivable from
   `reviewed_at`+`qa_status`+`qa_assignee`. Proposal: switch the per-QA
   scorecard "reviewed/changed" to the event-in-range basis too (so the column
   sums to `qaReviewedInRange`), and keep the lifetime-peak numbers as clearly
   labelled "(task lifetime)" columns. Confirm.

---

## Label renames (so every column states its basis)

- "Total QA reviewed" → **"QA reviewed (records, in-range)"** (headline) +
  separate **"QA reviewed (task lifetime)"** where the peak figure is still shown.
- "Total reviewed" (QAs) → **"QA reviewed (records, in-range)"**.
- Agents scorecard "QA-checked" → keep as **"QA-checked (task lifetime)"** for
  the per-agent peak column; headline QA total uses the in-range value.
- Coverage cells annotated with the denominator basis and the dead-vessel
  caveat (decision 3).
