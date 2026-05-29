# Daily verdict procedure — CLIENT_A ownership QA

Runs once a day at **23:00 EAT** (Africa/Nairobi). Locks the day's productivity and sampling compliance numbers.

## Files involved

- **State file (read + write):** `/Users/macbookpro/Documents/QA Hourly Analysis/ww_audit_log.json`

## Steps

### 1. Load state

Read `/Users/macbookpro/Documents/QA Hourly Analysis/ww_audit_log.json`. Identify today's date in `Africa/Nairobi` (YYYY-MM-DD).

Pull thresholds from `config.thresholds`:
- `daily_productivity_minimum`: 280
- `qa_sampling_minimum_pct`: 15

### 2. Compute per-agent productivity

For each agent in the roster (across all 5 ownership teams):

- Count `agent_completion_events` where `agent` matches AND `completed_at` falls within today (00:00–23:59 EAT).
- `productivity_met` = count >= 280

Group results by team.

### 3. Compute per-agent sampling rate

For each agent:

- **Reviewed today**: count `qa_events` where `agent` matches AND `qa_action_at` falls within today AND `event_type` in `["qa_approved", "qa_changed"]`.
- **Skipped today**: this is harder — we need to query Airtable for records by this agent that moved to `Done`/`Valid` today with `QA_status` empty. To keep the verdict task self-contained, **defer the skipped count to the dashboard** which queries Airtable live. In the verdict, store only:
  - `tagged_today`: count of `agent_completion_events` (from step 2)
  - `reviewed_today`: count of `qa_events` for the agent today

The dashboard will compute the exact sampling-rate denominator from live Airtable data when rendering.

### 4. Compute per-QA stats

For each QA in the roster:

- `qa_actions_today`: count `qa_events` where `qa` matches today
- `silent_changes_today`: count where `silent_change == true`
- `qa_changed_today`: count where `event_type == "qa_changed"`
- `qa_approved_today`: count where `event_type == "qa_approved"`

### 5. Write the verdict

Add to `daily_verdicts[YYYY-MM-DD]`:

```json
{
  "locked_at": "<23:00 EAT ISO timestamp>",
  "by_agent": {
    "<agent_name>": {
      "team": "<team>",
      "tagged_today": N,
      "reviewed_today": M,
      "productivity_met": true|false
    }
  },
  "by_qa": {
    "<qa_name>": {
      "team": "<team>",
      "qa_actions_today": N,
      "qa_approved_today": A,
      "qa_changed_today": C,
      "silent_changes_today": S
    }
  }
}
```

Save the file back.

### 6. Pruning

If `qa_events` has entries older than 90 days, move them to `/Users/macbookpro/Documents/QA Hourly Analysis/archive/qa_events_archive_<YYYY-MM>.json` and remove from the live state file.

If `agent_completion_events` has entries older than 90 days, do the same.

`pending_baselines` should never accumulate stale entries — if any baseline is older than 7 days, log it as a stuck record (write to `polling_state.stuck_records`) but leave it in place.

