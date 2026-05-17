# Sampling rule — non-sanctions vs sanctions

Ops runs two QA sampling targets, not one:

- **Non-sanctions tasks need ≥15% QA sampling.**
- **Sanctions tasks need ≥50% QA sampling.**

Sampled = `in_bo_qa_today + qa_inspected_today + need_to_be_update_today`, divided by `tagged_today`, in the matching cohort.

**Task type detection.** Looks at the `requested_by` field on each record (e.g. `CargoChangeIntel17May2026_task6`, `CargoSanctionsCheck17Apr2026`). If `requested_by` contains the substring `"sanctions"` (case-insensitive) it's a sanctions task; otherwise it's non-sanctions. Empty / null `requested_by` is treated as non-sanctions. The check lives in `.poll_work/extract_v2.py::is_sanctions()`.

**Where it surfaces.** Overview: two side-by-side sampling KPIs, each colored against its own target. Overview per-team table: two pill columns. Agent Scorecard: two cohort KPIs. BO QA Console: tagged-today headline splits Sanctions / Non-sanctions. Aggregator emits cohort counters at totals, by_team, and by_agent dimensions (see `aggregate_v2.QA_KEYS`).

The combined `sampling_actual_pct` (25% target) is kept in the data block for backward compatibility but no longer headlines anywhere.
