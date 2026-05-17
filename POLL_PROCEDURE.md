# Polling procedure v2 — aggregation-based (handles real volume)

The polling task runs every 15 min between 06:00 and 22:59 EAT. It does **incremental aggregation** — instead of storing every Airtable record, it maintains a per-record state cache for today and recomputes small summary aggregates on every cycle. This scales to your real volume (~6,700+ records/day).

## Files involved

- **`ww_audit_log.json`** — source of roster, config, thresholds. Also holds `pending_baselines` and `qa_events` (small, for silent-change detection).
- **`daily_records_<YYYY-MM-DD>.json`** — per-record state cache for today. One file per day. Each entry: `{agent, team, verification_status, qa_status, start_tagging, qa_status_ts, company_id, imo}` keyed by record_id. Size ~1-2 MB max.
- **`daily_aggregates.json`** — small summary read by the dashboard. Holds today's per-agent + per-team counts and hourly buckets.
- **`pending_queue.json`** — small file with currently-pending records (verification_status = "Selected for BO QA" AND assignee in ownership teams).

## Steps each cycle

### 1. Determine today's date in EAT

`today_eat` = current date in `Africa/Nairobi` (YYYY-MM-DD).

If `daily_records_<today_eat>.json` doesn't exist (new day), create it as `{}`. Don't carry yesterday's data into today.

### 2. Load state

- Read `ww_audit_log.json` → get config, roster, thresholds, pending_baselines, polling_state
- Read `daily_records_<today_eat>.json` → today's record state cache (dict keyed by record_id)
- Build `roster_lookup`: name (lowercase, including aliases) → `{team, role, canonical}`. Only ownership teams (Simba/Tembo/Pweza/Kobe/Nyati).

### 3. Fetch from Airtable — THREE targeted fetches

The old single past-24h fetch was a fire-hose — `relations_support` has ~12,800 records matching that filter on a busy day, and the 20-page cap silently dropped ~10,000. Split into three narrow fetches:

**Common args for all three:**
- Tool: `mcp__893e91e9-3198-489a-8427-1a4eceeb50bd__list_records_for_table`
- `baseId`: `appHZdfC2sn9MLGFZ`, `tableId`: `tblpj9aJP4ExhYCZF`
- `fieldIds`: **omit** — let Airtable return all fields (the aggregator picks via `.poll_work/extract_v2.py::FIELD_IDS`).
- `pageSize`: `100`

---

#### Fetch A — ownership-team work

Records modified in the past 24h whose `assignee` collaborator field includes one of the 5 ownership teams' canonical names or aliases (members + QAs; WW QAs once `config/roster.json` has them).

- **Output:** paginate to `.poll_work/recent_p*.json` (overwriting previous cycle's files).
- **Page cap:** drop it. With this filter the result set will drain in well under 10 pages on a busy day; if a freak day ever exceeds 30 pages, that's a real signal to investigate, not to truncate.
- **Sort:** `{fieldId: "fld0hZzdjksTCKJ09", direction: "desc"}` (last_modified desc).
- **`filterByFormula`:**

```
AND(
  IS_AFTER({last_modified}, DATEADD(NOW(), -1, 'days')),
  OR(
    FIND("Melanie Wanjiku",      ARRAYJOIN({assignee}, ", ")),
    FIND("Merline Akinyi",       ARRAYJOIN({assignee}, ", ")),
    FIND("Bet Merline Akinyi",   ARRAYJOIN({assignee}, ", ")),
    FIND("Caroline Murugi",      ARRAYJOIN({assignee}, ", ")),
    FIND("Stephen Muindi",       ARRAYJOIN({assignee}, ", ")),
    FIND("Teresa Mbuthia",       ARRAYJOIN({assignee}, ", ")),
    FIND("Stephen Kimari",       ARRAYJOIN({assignee}, ", ")),
    FIND("Lewis Nganga",         ARRAYJOIN({assignee}, ", ")),
    FIND("Flavian Etyang",       ARRAYJOIN({assignee}, ", ")),
    FIND("Anne Nzisa",           ARRAYJOIN({assignee}, ", ")),
    FIND("Annabel Grace",        ARRAYJOIN({assignee}, ", ")),
    FIND("Annabel Muriithi",     ARRAYJOIN({assignee}, ", ")),
    FIND("Beatrice Mutheu",      ARRAYJOIN({assignee}, ", ")),
    FIND("Zuleikha Musa",        ARRAYJOIN({assignee}, ", ")),
    FIND("Veronica Muthiora",    ARRAYJOIN({assignee}, ", ")),
    FIND("Ashley Nyambura",      ARRAYJOIN({assignee}, ", ")),
    FIND("Lillian Gichamba",     ARRAYJOIN({assignee}, ", ")),
    FIND("lillian Gichamba",     ARRAYJOIN({assignee}, ", ")),
    FIND("Timothy Kamanja",      ARRAYJOIN({assignee}, ", ")),
    FIND("Ashley Wairimu",       ARRAYJOIN({assignee}, ", ")),
    FIND("Stanley Munyambu",     ARRAYJOIN({assignee}, ", ")),
    FIND("Faith John",           ARRAYJOIN({assignee}, ", ")),
    FIND("Solomon Muturi",       ARRAYJOIN({assignee}, ", ")),
    FIND("Faith Khalai",         ARRAYJOIN({assignee}, ", ")),
    FIND("Selah Nabiswa",        ARRAYJOIN({assignee}, ", ")),
    FIND("Wilson Karani",        ARRAYJOIN({assignee}, ", ")),
    FIND("James Maina",          ARRAYJOIN({assignee}, ", ")),
    FIND("JAMES MAINA",          ARRAYJOIN({assignee}, ", ")),
    FIND("Hellen Vigehi",        ARRAYJOIN({assignee}, ", ")),
    FIND("Hellen vigehi",        ARRAYJOIN({assignee}, ", ")),
    FIND("Elvis Mwanzia",        ARRAYJOIN({assignee}, ", "))
  )
)
```

Maintenance: when `config/roster.json` changes (new member, new alias, ww_qa filled), regenerate this OR-block and paste it back here verbatim. Tools (incl. the polling agent) should NOT build the formula at runtime — keep it inline and explicit so the filter is auditable from this doc alone.

---

#### Fetch B — today's table intake

Whole-table count of records created today, any assignee. Drives the Daily Intake page.

- **Output:** paginate to `.poll_work/intake_p*.json` (overwriting previous cycle's files).
- **Page cap:** 30 (3,000 records max; daily intake can spike to 10k+ during automation batches, so 3,000 is a deliberate ceiling — see note below).
- **Sort:** `{fieldId: "fldZL6JmYMlFIhCLl", direction: "desc"}` (Created desc — most recent first).
- **`filterByFormula`:**

```
IS_SAME({Created}, TODAY(), 'day')
```

**How the aggregator reads this fetch — important:**

- `aggregates_v2.totals.relations_support_intake_today` comes from **page 1's `metadata.totalRecordCount`**, NOT from counting records in the cache. This gives the accurate headline number even when intake exceeds the 30-page cap.
- The cache (`intake_p*.json`) itself only holds the first 3,000 records of today's intake (most-recent-first). Per-task summary counts on the Tasks page may under-report for very large tasks.
- When `metadata.totalRecordCount > 3000`, the aggregator sets `aggregates_v2.totals.intake_partial = true`. The dashboard's Daily Intake page shows an amber banner in this case.
- The per-task drill-down "Fetch all records" button on the Tasks page bypasses the cache entirely and gives the truth on demand for any individual task.

---

#### Fetch C — BO QA queue (unchanged)

Records currently in `verification_status = "Selected for BO QA "` (trailing space). Drives the BO QA Console.

- **Output:** paginate to `.poll_work/boqa_p*.json` (overwriting previous cycle's files).
- **Page cap:** 30.
- **Sort:** `{fieldId: "fld7fm1PknPk1UueW", direction: "desc"}` (start tagging date desc).
- **`filterByFormula`:**

```
{verification_status} = "Selected for BO QA "
```

### 4. Update today's record cache

Process the **union** of records returned by Fetch A and Fetch B, deduplicated by record id (Fetch C records are also folded in for backlog tracking but never gate the cache write). For each record:

- Extract `assignee` (first item of fldT4xElSgcdnqTmy collaborator field) — get `.name`. (Multi-assignee records also extract the full `assignees` list per `extract_v2.extract()`.)
- Look up in `roster_lookup`. **If not an ownership-team agent, skip.**
- Extract `start_tagging` (fld7fm1PknPk1UueW). **If date in EAT ≠ today_eat, skip** (we only track today's work in today's cache).
- Update/insert entry in `daily_records[<record_id>]` with all the current fields.

### 5. Process baselines and qa_events (silent-change tracking, unchanged)

For each record in this poll:
- If `verification_status` = `"Selected for BO QA "` AND record_id NOT in `pending_baselines` → add baseline (frozen agent pick)
- If record_id IS in `pending_baselines` AND `verification_status` ≠ `"Selected for BO QA "` → write a `qa_event` to `ww_audit_log.qa_events`, comparing baseline company vs current company. Set `silent_change: true` if changed but `qa_status` = `approve`. Remove from `pending_baselines`.

### 6. Compute aggregates from daily_records

After all records processed, iterate `daily_records` and build:

```json
{
  "date": "<today_eat>",
  "computed_at": "<this run's EAT timestamp>",
  "thresholds": {"productivity_min": 280, "sampling_min_pct": 15},
  "by_agent": {
    "<canonical_agent_name>": {
      "team": "Kobe",
      "tagged_today": 314,
      "in_bo_qa": 11,
      "qa_inspected": 47,       // QA_status set (approve OR changed)
      "qa_approved": 40,
      "qa_changed": 7,
      "skipped_done_or_valid": 256,  // currently Done/Valid + no QA_status
      "sampling_rate_pct": 18.5,      // (in_bo_qa + qa_inspected + need_to_be_update) / tagged_today * 100
      "productivity_met": true,        // tagged_today >= 280
      "hourly": {"6": 0, "7": 10, "8": 45, "9": 60, ...}  // bucket by hour of start_tagging
    }
  },
  "by_team": {
    "Kobe": {"tagged_today": 1200, "in_bo_qa": 50, "qa_inspected": 200, "qa_approved": 180, "qa_changed": 20, "skipped": 950, "sampling_rate_pct": 21}
  },
  "totals": {
    "tagged_today": 6741,
    "in_bo_qa": 339,
    "qa_inspected": 800,
    "silent_changes_today": 0
  }
}
```

Hourly bucket uses `start_tagging` (EAT hour). Sampling formula matches what's described in `ww_audit_log.json` README.

### 7. Update pending_queue.json

Run a SEPARATE small query: records where `verification_status = "Selected for BO QA "` (uses choice ID `selHROyMcSsu160lS`). Paginate. Filter to ownership-team agents. Store as:

```json
{
  "captured_at": "...",
  "count": 339,
  "by_team_qa_agent": [
    {"team": "Kobe", "qa": "Zuleikha Musa", "agent": "Lillian Gichamba", "count": 11, "oldest_h": 8}
  ]
}
```

### 8. Save all files

- Write `daily_records_<today_eat>.json`
- Write `daily_aggregates.json`
- Write `pending_queue.json`
- Update `polling_state` in `ww_audit_log.json` (last_run_at, total_polls, last_error)
- Write `ww_audit_log.json`

### 9. Re-render the standalone dashboard

```bash
cd "/Users/macbookpro/Documents/QA Hourly Analysis" && python3 render_dashboard.py
```

The renderer reads `daily_aggregates.json` + `pending_queue.json` + `ww_audit_log.json` and writes `dashboard_static.html`.

### 10. Log summary

`<N> records cached today, <T> tagged across teams, <P> pending, <S> silent changes, agents below 280: <B>`.

## Error handling

If any step fails:
- Write error to `polling_state.last_error`
- Do NOT advance `last_successful_run_at`
- Next poll will redo the 30-min window naturally

## Notes on the bootstrap day

On the first day this runs, `daily_records_<today>.json` builds up across the day's polls. Mid-day metrics will be incomplete (only records modified since the polling task started). By end of day, the cache reflects everything. From day 2 onwards, the 06:00 poll has a clean slate and builds up complete data.

If you need a backfill, run a "manual sweep" — same logic but with a full-day filter window. The polling task can be triggered manually via "Run now" in the Scheduled sidebar.
