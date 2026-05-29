# CLIENT_A Ownership QA Audit Log

This folder holds the data feeding the CLIENT_A ownership-team QA monitoring dashboard.

## Files

- `ww_audit_log.json` — single source of truth. Read by the dashboard, updated by the scheduled polling tasks.
- `archive/` — older entries pruned out of `ww_audit_log.json` after 90 days.
- `dashboard.html` — the live dashboard artifact (also accessible via Cowork).

## JSON sections

- `config` — Airtable IDs, choice IDs, thresholds. Edit `thresholds.daily_productivity_minimum` (280) or `thresholds.qa_sampling_minimum_pct` (15) to adjust the flagging logic.
- `roster` — team → QA + agents mapping. Edit when people move teams.
- `polling_state` — bookkeeping for the recurring tasks (last run, gaps, errors).
- `pending_baselines` — records currently in `Selected for BO QA`, keyed by Airtable record ID. Each holds the agent's frozen company pick so we can detect QA-side changes later.
- `agent_completion_events` — one entry per record the polling task observed entering `tagged` or `Selected for BO QA`. Drives the hourly-output chart.
- `qa_events` — one entry per record exiting `Selected for BO QA`. Includes `silent_change` flag (true when company changed but `QA_status` is still `approve`).
- `daily_verdicts` — locked at 23:00 EAT each day. Per-agent productivity (vs 280) and per-QA sampling rate (vs 15%) frozen for the day.

## Scope

Only the 5 ownership teams: TEAM_1, TEAM_5, TEAM_4, TEAM_3, TEAM_2. Other teams (TEAM_7, Mamba, TEAM_6, Ownership-Experts) are ignored.

## Source

Records pulled from Airtable base `ww-vendor` (`REDACTED_BASE_ID`), table `relations_support` (`tblpj9aJP4ExhYCZF`).
