# Architecture

The current source of truth for the data model and dashboard scope is **`Ownership Operations Hub/DESIGN_OWNERSHIP_INSIGHTS_V1.md`**. This file is the operating description — what's here today, how it fits together.

## What this is

A JSON-file polling app that watches Impact Outsourcing's Vessel Ownership QA operation in Airtable base `REDACTED_BASE_ID`, table `relations_support`. Five teams: Simba, Tembo, Pweza, Kobe, Nyati. Polls every 15 min between 06:00 and 22:59 EAT.

## File map

### Configuration
- `ww_audit_log.json` — config (field IDs, choice IDs, thresholds), polling state, qa_events, pending_baselines, daily_verdicts. Also holds a deprecation shim of the roster.
- `config/roster.json` — **source of truth** for the 5 ownership teams' roster (members, BO QA, WW QA). Readers fall back to the shim if this file is missing.

### Polling pipeline (`.poll_work/`)
- `run_poll_cycle7.py` — the polling loop. Reads cached Airtable page files (`recent_p*.json`, `boqa_p*.json`), updates `daily_records_<date>.json`, maintains baselines + qa_events for silent-change detection, recomputes the legacy `daily_aggregates.json` block.
- `extract_v2.py` — single source of truth for which Airtable field IDs the app reads (`FIELD_IDS`) and for record extraction. Shared by the poller and aggregator.
- `aggregate_v2.py` — Phase A aggregator. Computes per-(team, agent, qa, ww_qa) metrics described in the design doc §4.2 and writes them under `aggregates_v2` in `daily_aggregates.json`.
- `tests/test_extract_v2.py`, `tests/test_aggregate_v2.py` — unit + fixture-driven regression tests.

### Dashboard
- `dashboard.html` — live dashboard. Calls Airtable through `window.cowork.callMcpTool`. Being replaced by a 7-page multi-nav UI in Phase A/B.
- `render_dashboard.py` → `dashboard_static.html` — a static render of the same data, used as a fallback / artifact.

### Documentation
- `Ownership Operations Hub/DESIGN_OWNERSHIP_INSIGHTS_V1.md` — current design, KPIs, page layout.
- `Ownership Operations Hub/airtable_schema_snapshot.json` — last-fetched Airtable schema with field IDs, types, and singleSelect choice IDs.
- `POLL_PROCEDURE.md` — the procedure a polling agent follows each 15-min cycle. Drives the MCP calls that produce the page files in `.poll_work/`.
- `DAILY_VERDICT_PROCEDURE.md` — the 23:00 EAT daily snapshot procedure.
- `CLAUDE_CODE_PLAYBOOK.md` — workflow tips for ops.

## Data flow

```
Airtable (relations_support)
        │
        ▼ list_records_for_table (via MCP, every 15 min, no fields whitelist)
.poll_work/recent_p*.json + boqa_p*.json
        │
        ▼ run_poll_cycle7.py
daily_records_<date>.json   ww_audit_log.json (qa_events, baselines, polling_state)
        │
        ├──▶ aggregate_v2.py ─▶ daily_aggregates.json.aggregates_v2
        │
        └──▶ legacy block    ─▶ daily_aggregates.json.{by_agent, by_team, totals}
                                       │
                                       ▼
                              dashboard.html (live)
                              dashboard_static.html (via render_dashboard.py)
```

## History

This file used to be one of seven (`ARCHITECTURE.md` through `ARCHITECTURE_V6.md`) — each a competing draft of where the project might go. They were exploratory and contradicted each other. As of 2026-05-17 they are collapsed into this one page; current scope and rollout are owned by `DESIGN_OWNERSHIP_INSIGHTS_V1.md`. The Phase E target (Next.js + Supabase + Vercel) is real but out of scope for the JSON-file app described here.
