"""Phase A aggregator.

Pre-computes the metrics described in
``Ownership Operations Hub/DESIGN_OWNERSHIP_INSIGHTS_V1.md`` §4.2,
dimensioned per (team, agent, qa_assignee, ww_qa_assignee), and writes
them to ``daily_aggregates.json`` under a new top-level ``aggregates_v2``
key. The pre-existing ``by_agent`` / ``by_team`` / ``totals`` blocks are
preserved alongside.

``reason_for_change_missing`` is deferred to Phase C — the field lives in
the sister table ``relations_io`` (fldu7T8eOHaDe3uup), so a cross-table
join is required.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_v2 import (  # noqa: E402
    COMMENT_VALUES,
    SELECTED_FOR_BO_QA,
    SELECTED_FOR_WW_QA,
    extract,
    is_properly_completed,
    is_sanctions,
)

EAT = timezone(timedelta(hours=3))
SAMPLING_TARGET_PCT = 25.0                  # combined (legacy)
SAMPLING_TARGET_NON_SANCTIONS_PCT = 15.0    # non-sanctions tasks
SAMPLING_TARGET_SANCTIONS_PCT = 50.0        # sanctions tasks
REJECT_THRESHOLD = 30.0                     # applies to both cohorts

# verification_status values that mean the row has exited the active workflow.
# Used to decide whether an `add_new_company` row still counts as "open".
DONE_LIKE = {"Done", "Valid"}

# Per-dimension metric whitelists. by_qa and by_ww_qa drop metrics that
# don't make sense at that dimension (e.g. a QA's daily_intake is always 0).
QA_KEYS = {
    "counts_by_qa_status",                    # approve / changed split
    "qa_inspected_today",                     # BO QA throughput (combined)
    "qa_changed_today",
    "qa_inspected_today_sanctions",
    "qa_inspected_today_non_sanctions",
    "qa_changed_today_sanctions",
    "qa_changed_today_non_sanctions",
    "tagged_today_sanctions",                 # cohort context for sampling %
    "tagged_today_non_sanctions",
    "sampling_actual_pct",                    # combined 25% target (legacy)
    "sampling_target_pct",
    "sampling_non_sanctions_pct",             # 15% target
    "sampling_target_non_sanctions_pct",
    "sampling_sanctions_pct",                 # 50% target
    "sampling_target_sanctions_pct",
    "reject_rate",                            # combined
    "reject_rate_sanctions",
    "reject_rate_non_sanctions",
    "reject_threshold",
}

WW_QA_KEYS = {
    "ww_qa_throughput",
    "ww_qa_change_rate",
    "ww_qa_backlog",              # from Fetch G — {WW QA assignee} filled AND {WW QA} blank
}


def _parse_iso(s):
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _parse_eat_date(s):
    dt = _parse_iso(s)
    return dt.astimezone(EAT).date() if dt else None


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def _pct(num, den):
    return round((num / den) * 100, 1) if den else 0.0


def _percentile(values, p):
    """Linear-interpolated percentile of a list of floats. Returns int seconds (or None)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return int(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return int(s[lo])
    return int(s[lo] + (s[hi] - s[lo]) * (k - lo))


def _lead_times(records):
    """Compute lead-time percentiles across the three transitions.

    Skips records where the relevant timestamp pair is incomplete or
    where the destination timestamp precedes the source (clock skew).
    """
    c2t, t2b, b2d = [], [], []
    for info in records:
        created = _parse_iso(info.get("created"))
        tagged = _parse_iso(info.get("start_tagging"))
        bo_qa_ts = _parse_iso(info.get("qa_status_ts"))
        done = _parse_iso(info.get("done_selected_time"))
        if created and tagged and tagged >= created:
            c2t.append((tagged - created).total_seconds())
        if tagged and bo_qa_ts and bo_qa_ts >= tagged:
            t2b.append((bo_qa_ts - tagged).total_seconds())
        if bo_qa_ts and done and done >= bo_qa_ts:
            b2d.append((done - bo_qa_ts).total_seconds())
    return {
        "created_to_tagged_p50": _percentile(c2t, 50),
        "created_to_tagged_p90": _percentile(c2t, 90),
        "tagged_to_bo_qa_p50":   _percentile(t2b, 50),
        "tagged_to_bo_qa_p90":   _percentile(t2b, 90),
        "bo_qa_to_done_p50":     _percentile(b2d, 50),
        "bo_qa_to_done_p90":     _percentile(b2d, 90),
    }


def _lead_time_today(records, today_eat):
    """Lead time (start_tagging → completion) for records completed today.

    The three-transition ``_lead_times`` block above is computed over the
    whole in-scope cache and split at BO QA, so it can't answer "how long
    did the work finished *today* take, end to end". This helper fills that
    gap for the Pipeline & Lead Time page.

    "Completed today" mirrors the ``done_today`` metric exactly: a record
    whose ``done_selected_time`` OR ``valid_selected_time`` lands on
    ``today_eat`` (EAT). The lead-time sample is the elapsed seconds from
    ``start_tagging`` to that completion timestamp. Samples missing
    ``start_tagging``, or where completion precedes tagging (clock skew),
    are dropped from the percentile pool but still counted as completions —
    so ``count`` reconciles with ``done_today`` while p50/p90 stay clean.

    Returns ``{p50, p90, count, by_team: {<team>: {p50, p90, count}},
    by_cohort: {"sanctions"|"non_sanctions": {p50, p90, count}}}``.
    Percentiles are int seconds (or ``None`` when there are no samples).
    A record's cohort is ``is_sanctions(requested_by)`` — sanctions tasks
    run 100% QA per SOP, so splitting lead time by cohort surfaces whether
    that extra QA materially slows completion. Teams/cohorts with zero
    completions today are omitted (same empty convention as ``by_team``).
    """
    def _stats(recs):
        samples, count = [], 0
        for info in recs:
            comp = None
            for field in ("done_selected_time", "valid_selected_time"):
                ts = _parse_iso(info.get(field))
                if ts and ts.astimezone(EAT).date() == today_eat:
                    comp = ts if comp is None else min(comp, ts)
            if comp is None:
                continue
            count += 1
            tagged = _parse_iso(info.get("start_tagging"))
            if tagged and comp >= tagged:
                samples.append((comp - tagged).total_seconds())
        return {
            "p50":   _percentile(samples, 50),
            "p90":   _percentile(samples, 90),
            "count": count,
        }

    out = _stats(records)
    by_team = {}
    for team, recs in _group(records, lambda r: r.get("team")).items():
        s = _stats(recs)
        if s["count"]:
            by_team[team] = s
    out["by_team"] = by_team
    by_cohort = {}
    cohort_of = lambda r: "sanctions" if is_sanctions(r.get("requested_by")) else "non_sanctions"
    for cohort, recs in _group(records, cohort_of).items():
        s = _stats(recs)
        if s["count"]:
            by_cohort[cohort] = s
    out["by_cohort"] = by_cohort
    return out


def compute_metrics(records, today_eat):
    """Compute the full metric block for a slice of records.

    ``today_eat`` is an EAT-local date — used to decide what counts as
    "today" for daily_intake, tagged_today, reminder_overdue, etc.
    """
    vs_counts = Counter()
    qa_counts = Counter()
    comment_counts = Counter({v: 0 for v in COMMENT_VALUES})
    source_counts = Counter()
    role_counts = Counter()

    add_new_company_open = 0
    reminder_open = 0
    reminder_overdue = 0
    team_routed_intake_today = 0
    tagged_today = 0
    done_today = 0
    in_bo_qa_today = 0
    sampled_today_ids = set()
    sampled_today_sanctions_ids = set()
    sampled_today_non_sanctions_ids = set()
    qa_inspected_today = 0
    qa_changed_today = 0
    need_to_be_update_today = 0
    ww_qa_throughput = 0
    ww_qa_changed = 0
    ww_qa_backlog = 0
    bo_qa_backlog = 0
    # Sanctions / non-sanctions split — operations runs the two cohorts to
    # different sampling targets (15% vs 50%). Task type detected from
    # requested_by via extract_v2.is_sanctions().
    tagged_today_sanctions = 0
    tagged_today_non_sanctions = 0
    in_bo_qa_today_sanctions = 0
    in_bo_qa_today_non_sanctions = 0
    qa_inspected_today_sanctions = 0
    qa_inspected_today_non_sanctions = 0
    qa_changed_today_sanctions = 0
    qa_changed_today_non_sanctions = 0
    need_to_be_update_today_sanctions = 0
    need_to_be_update_today_non_sanctions = 0
    # Ops definition of "properly completed" = start_date filled AND
    # (company_id filled OR dead_vessel=True). Dead vessel is the
    # operational alternative when no company exists to link.
    properly_completed_today = 0
    dead_vessels_today = 0
    # Flow A/B/C — Windward parity. A record can be in Flow B AND Flow C
    # simultaneously (reviewed AND completed-after-QA), so track independently.
    flow_a_in_cache = 0   # Done/Valid AND qa_assignee blank   (completed without QA)
    flow_b_in_cache = 0   # qa_assignee+qa_status both filled  (QA reviewed)
    flow_c_in_cache = 0   # Done/Valid AND has_qa              (completed AFTER QA)
    flow_a_today = 0
    flow_b_today = 0
    flow_c_today = 0
    # Working hours window — zero-fill so the dashboard always renders a
    # complete 6→23 row even on quiet hours.
    hourly_buckets = {h: 0 for h in range(6, 24)}

    for idx, info in enumerate(records):
        vs = info.get("verification_status")
        qs = info.get("qa_status")
        if vs:
            vs_counts[vs] += 1
        if qs:
            qa_counts[qs] += 1

        # Flow A/B/C — keep cache-wide counters for weekly/downstream users,
        # and separate today-scoped counters for the Overview dashboard.
        is_completed = vs in ("Done", "Valid")
        has_qa = bool(info.get("qa_assignee")) and bool(qs)
        is_tagged_today = _parse_eat_date(info.get("start_tagging")) == today_eat
        if is_completed and not info.get("qa_assignee"):
            flow_a_in_cache += 1
            if is_tagged_today:
                flow_a_today += 1
        if has_qa:
            flow_b_in_cache += 1
            if is_tagged_today:
                flow_b_today += 1
        if is_completed and has_qa:
            flow_c_in_cache += 1
            if is_tagged_today:
                flow_c_today += 1

        cm = info.get("comment")
        if cm:
            comment_counts[cm] += 1

        sf = info.get("source_flow")
        if sf:
            source_counts[sf] += 1

        role = info.get("role")
        if role:
            role_counts[role] += 1

        # add_new_company_open (broader definition per fix-add-new-company-
        # metric-broader-definition): any record tagged today with the
        # add_new_company column filled. Mirrors the Airtable view "filter
        # by start_tagging today AND add_new_company non-empty". Replaces
        # the legacy 3-condition rule (need-to-be-update + no company_id)
        # which under-counted (11 vs the live Airtable count of 96).
        # Same metric key for backward dashboard compatibility; the
        # user-facing label is updated in deploy/index.html.
        if (info.get("add_new_company")
                and _parse_eat_date(info.get("start_tagging")) == today_eat):
            add_new_company_open += 1

        rem_date = _parse_date(info.get("reminder"))
        if rem_date:
            reminder_open += 1
            if rem_date < today_eat:
                reminder_overdue += 1

        if _parse_eat_date(info.get("created")) == today_eat:
            team_routed_intake_today += 1

        if any(_parse_eat_date(info.get(field)) == today_eat
               for field in ("done_selected_time", "valid_selected_time")):
            done_today += 1

        if _parse_eat_date(info.get("start_tagging")) == today_eat:
            san = is_sanctions(info.get("requested_by"))
            tagged_today += 1
            if san: tagged_today_sanctions += 1
            else:   tagged_today_non_sanctions += 1
            if is_properly_completed(info):
                properly_completed_today += 1
            if info.get("dead_vessel") is True:
                dead_vessels_today += 1
            sampled = False
            if vs == SELECTED_FOR_BO_QA:
                sampled = True
                in_bo_qa_today += 1
                if san: in_bo_qa_today_sanctions += 1
                else:   in_bo_qa_today_non_sanctions += 1
            elif vs == "need to be update":
                sampled = True
                need_to_be_update_today += 1
                if san: need_to_be_update_today_sanctions += 1
                else:   need_to_be_update_today_non_sanctions += 1
            if qs in ("approve", "changed"):
                sampled = True
                qa_inspected_today += 1
                if san: qa_inspected_today_sanctions += 1
                else:   qa_inspected_today_non_sanctions += 1
                if qs == "changed":
                    qa_changed_today += 1
                    if san: qa_changed_today_sanctions += 1
                    else:   qa_changed_today_non_sanctions += 1
            if sampled:
                sampled_today_ids.add(idx)
                if san:
                    sampled_today_sanctions_ids.add(idx)
                else:
                    sampled_today_non_sanctions_ids.add(idx)

        # Hourly Tagging Output — independent of the today-scoped block above.
        # A record contributes to hour H for an agent on the EAT day of its
        # start_tagging iff ALL:
        #   - company_id AND company_name are both truthy — the agent has
        #     linked a real company via company_id_and_name. extract_v2 reads
        #     these from the linked record (.id / .name); both are None when
        #     the link is empty (matches the lookup-field "empty array" semantic).
        #   - start_date is non-null — evidence the agent recorded the vessel's
        #     business-date entry. The VALUE isn't compared to today; the gate
        #     is just "filled". (start_date is a vessel business date — build
        #     year, inception — not a tagging-session timestamp.)
        #   - start_tagging parses; its EAT date is today AND its EAT hour
        #     is 6..23. This is the agent-driven tagging-session timestamp
        #     (same field tagged_today uses).
        #   - assignee in roster — already enforced upstream in aggregate()
        #     before records reach compute_metrics.
        # Exclusions:
        #   - dead_vessel checkbox ticked — those aren't tagging output.
        #   - add_new_company non-empty — 100% QA workflow, separate metric.
        # NOTE: verification_status is deliberately NOT gated. "tagged" is a
        # transient state — records pass through it in seconds en route to
        # "Selected for BO QA" / "Valid" / "Done" / "need to be update", so
        # by poll time the day's work has already moved downstream. Gating on
        # current verification_status would zero out the histogram. The
        # company_id+company_name pair IS the agent-completion evidence.
        if (info.get("company_id")
                and info.get("company_name")
                and info.get("start_date")
                and not info.get("dead_vessel")
                and not info.get("add_new_company")):
            dt = _parse_iso(info.get("start_tagging"))
            if dt:
                dt_eat = dt.astimezone(EAT)
                if dt_eat.date() == today_eat and 6 <= dt_eat.hour <= 23:
                    hourly_buckets[dt_eat.hour] += 1

        ww = info.get("ww_qa")
        if ww in ("approve", "change"):
            ww_qa_throughput += 1
            if ww == "change":
                ww_qa_changed += 1

        if vs == SELECTED_FOR_BO_QA:
            bo_qa_backlog += 1
        # ww_qa_backlog: per the fix-systemic-today-metric-truncation refactor,
        # this now means "record appears in Fetch G" — i.e. {WW QA assignee}
        # is filled AND {WW QA} is blank. The legacy definition
        # (vs == "Selected for WW QA") counted records the operator had
        # routed but excluded records actually sitting in a WW QA's queue
        # awaiting their decision. The Fetch-G semantic is what WW QAs and
        # the user-facing "WW QA backlog" tile actually mean.
        if "G" in info.get("_sources", ()):
            ww_qa_backlog += 1

    return {
        "counts_by_verification_status": dict(vs_counts),
        "counts_by_qa_status":           dict(qa_counts),
        "comment_distribution":          dict(comment_counts),
        "source_flow_distribution":      dict(source_counts),
        "per_role_volume":               dict(role_counts),
        "add_new_company_open":          add_new_company_open,
        # Alias under a clearer name for new dashboard tiles / future
        # per-team rendering. Same value as add_new_company_open above.
        "add_new_company_today":         add_new_company_open,
        "reminder_open":                 reminder_open,
        "reminder_overdue":              reminder_overdue,
        "team_routed_intake_today":      team_routed_intake_today,
        "tagged_today":                  tagged_today,
        "hourly_buckets":                hourly_buckets,
        "done_today":                    done_today,
        "properly_completed_today":      properly_completed_today,
        "dead_vessels_today":            dead_vessels_today,
        "in_bo_qa_today":                in_bo_qa_today,
        "qa_inspected_today":            qa_inspected_today,
        "qa_changed_today":              qa_changed_today,
        "need_to_be_update_today":       need_to_be_update_today,
        # Sanctions / non-sanctions cohort counts (today)
        "tagged_today_sanctions":              tagged_today_sanctions,
        "tagged_today_non_sanctions":          tagged_today_non_sanctions,
        "in_bo_qa_today_sanctions":            in_bo_qa_today_sanctions,
        "in_bo_qa_today_non_sanctions":        in_bo_qa_today_non_sanctions,
        "qa_inspected_today_sanctions":        qa_inspected_today_sanctions,
        "qa_inspected_today_non_sanctions":    qa_inspected_today_non_sanctions,
        "qa_changed_today_sanctions":          qa_changed_today_sanctions,
        "qa_changed_today_non_sanctions":      qa_changed_today_non_sanctions,
        "need_to_be_update_today_sanctions":   need_to_be_update_today_sanctions,
        "need_to_be_update_today_non_sanctions": need_to_be_update_today_non_sanctions,
        "ww_qa_throughput":              ww_qa_throughput,
        "ww_qa_change_rate":             _pct(ww_qa_changed, ww_qa_throughput),
        "bo_qa_backlog":                 bo_qa_backlog,
        "ww_qa_backlog":                 ww_qa_backlog,
        # Flow framework (Windward parity)
        "flow_a_in_cache":               flow_a_in_cache,
        "flow_b_in_cache":               flow_b_in_cache,
        "flow_c_in_cache":               flow_c_in_cache,
        "flow_a_today":                  flow_a_today,
        "flow_b_today":                  flow_b_today,
        "flow_c_today":                  flow_c_today,
        "total_completions":             flow_a_in_cache + flow_c_in_cache,
        "total_completions_today":       flow_a_today + flow_c_today,
        "multi_assignee_count":          sum(1 for info in records if len(info.get("assignees", [])) > 1),
        "unique_imos":                   len({info["imo"] for info in records if info.get("imo")}),
        # Sampling: numerator is the set-union of records that are in BO QA,
        # already QA-reviewed, or bounced back for rework today. The combined
        # metric stays for backward-compat. The two cohort metrics (15%
        # non-sanctions, 50% sanctions) are the operational truth.
        "sampling_actual_pct":           _pct(
            len(sampled_today_ids),
            tagged_today,
        ),
        "sampling_target_pct":           SAMPLING_TARGET_PCT,
        "sampling_non_sanctions_pct":    _pct(
            len(sampled_today_non_sanctions_ids),
            tagged_today_non_sanctions,
        ),
        "sampling_target_non_sanctions_pct": SAMPLING_TARGET_NON_SANCTIONS_PCT,
        "sampling_sanctions_pct":        _pct(
            len(sampled_today_sanctions_ids),
            tagged_today_sanctions,
        ),
        "sampling_target_sanctions_pct": SAMPLING_TARGET_SANCTIONS_PCT,
        "reject_rate":                   _pct(qa_changed_today, qa_inspected_today),
        "reject_rate_sanctions":         _pct(qa_changed_today_sanctions, qa_inspected_today_sanctions),
        "reject_rate_non_sanctions":     _pct(qa_changed_today_non_sanctions, qa_inspected_today_non_sanctions),
        "reject_threshold":              REJECT_THRESHOLD,
        "lead_time_seconds":             _lead_times(records),
    }


def _slim(metrics, keep):
    return {k: v for k, v in metrics.items() if k in keep}


def _group(records, key_fn):
    g = defaultdict(list)
    for r in records:
        k = key_fn(r)
        if k:
            g[k].append(r)
    return g


# verification_status values that mean a record is still in flight (not done/valid).
INCOMPLETE_STATUSES = {
    "waiting", "tagged", SELECTED_FOR_BO_QA, SELECTED_FOR_WW_QA, "need to be update",
}
# Canonical 7-key status_distribution emitted on every task (zero-filled).
STATUS_KEYS = (
    "waiting", "tagged", SELECTED_FOR_BO_QA, "Done", "Valid", SELECTED_FOR_WW_QA, "need to be update",
)


def _normalize_ownership(ownership_assignees):
    """Build the {lowercase_name: {team, canonical}} lookup. Same defensive
    accept-both-shapes pattern aggregate() uses."""
    norm = {}
    for k, v in ownership_assignees.items():
        key = k.strip().lower()
        if isinstance(v, dict):
            norm[key] = {"team": v["team"], "canonical": v.get("canonical", k)}
        else:
            norm[key] = {"team": v, "canonical": k}
    return norm


def compute_task_breakdowns(records, today_eat, ownership_assignees):
    """Per-task lifecycle view. Groups all in-cache records by requested_by
    (blank → "(no task name)") and emits a rich dict per task with counts,
    multi-team agent attribution, and 5 operational flags.

    `records` is the full extracted list (in-scope + out-of-scope) — Tasks
    are a whole-table concern, not bound to the 5 ownership teams.

    Returns a list sorted by date_last_modified desc (nulls last).
    """
    norm = _normalize_ownership(ownership_assignees)
    cutoff_24h = datetime.now(EAT) - timedelta(hours=24)

    by_task = defaultdict(list)
    for r in records:
        req = (r.get("requested_by") or "").strip() or "(no task name)"
        by_task[req].append(r)

    out = []
    for name, recs in by_task.items():
        total = len(recs)
        status = {k: 0 for k in STATUS_KEYS}
        for info in recs:
            vs = info.get("verification_status")
            if vs in status:
                status[vs] += 1

        properly_completed = sum(1 for info in recs if is_properly_completed(info))
        with_company       = sum(1 for info in recs if info.get("company_id"))
        without_company    = total - with_company
        dead_vessels       = sum(1 for info in recs if info.get("dead_vessel") is True)
        with_reminder      = sum(1 for info in recs if info.get("reminder"))
        # Per-task QA coverage — % of completed records that had a QA review.
        completed_task     = sum(1 for info in recs if info.get("verification_status") in ("Done", "Valid"))
        qa_reviewed_task   = sum(1 for info in recs
                                 if info.get("qa_assignee") and info.get("qa_status"))
        qa_coverage_pct    = round((qa_reviewed_task / max(completed_task, 1)) * 100, 1)

        # Dates
        created_dts  = [d for d in (_parse_iso(info.get("created")) for info in recs) if d]
        last_mod_dts = [d for d in (_parse_iso(info.get("last_modified")) for info in recs) if d]
        date_first_seen    = min(created_dts).isoformat()  if created_dts  else None
        date_last_modified = max(last_mod_dts).isoformat() if last_mod_dts else None

        # Agents worked — multi-assignee aware. Each assignee on each record
        # gets credit for that record.
        agent_counts = Counter()
        for info in recs:
            for asg in (info.get("assignees") or []):
                key = (asg or "").strip().lower()
                canonical = norm.get(key, {}).get("canonical", asg)
                if canonical:
                    agent_counts[canonical] += 1
        agents_worked = []
        for agent, cnt in agent_counts.most_common():
            team = norm.get(agent.strip().lower(), {}).get("team")
            agents_worked.append({"name": agent, "team": team, "records": cnt})

        # Teams worked — count DISTINCT records per team (a record co-assigned
        # to Alice/Simba and Bob/Tembo counts once for Simba and once for Tembo).
        team_record_ids = defaultdict(set)
        for idx, info in enumerate(recs):
            seen_teams = set()
            for asg in (info.get("assignees") or []):
                t = norm.get((asg or "").strip().lower(), {}).get("team")
                if t:
                    seen_teams.add(t)
            for t in seen_teams:
                team_record_ids[t].add(idx)
        teams_worked = [
            {"team": team, "records": len(ids)}
            for team, ids in sorted(team_record_ids.items(), key=lambda x: (-len(x[1]), x[0]))
        ]

        # Flags
        flags = []
        if any(info.get("verification_status") in INCOMPLETE_STATUSES for info in recs):
            flags.append("incomplete")
        # "stuck" — NO record had last_modified in past 24h.
        if not last_mod_dts or all(d < cutoff_24h for d in last_mod_dts):
            flags.append("stuck")
        # "company-gap" — any record marked Done/Valid but with no company AND not a dead vessel.
        for info in recs:
            if info.get("verification_status") in ("Done", "Valid") \
               and not info.get("company_id") \
               and info.get("dead_vessel") is not True:
                flags.append("company-gap")
                break
        if any(not (info.get("assignees") or []) for info in recs):
            flags.append("unassigned")
        if total > 0 and status["waiting"] / total > 0.5:
            flags.append("high-waiting")

        out.append({
            "name": name,
            "is_sanctions": is_sanctions(name),
            "total_records_in_cache": total,
            "date_first_seen": date_first_seen,
            "date_last_modified": date_last_modified,
            "status_distribution": status,
            "properly_completed": properly_completed,
            "with_company": with_company,
            "without_company": without_company,
            "dead_vessels": dead_vessels,
            "with_reminder": with_reminder,
            "completed": completed_task,
            "qa_reviewed": qa_reviewed_task,
            "qa_coverage_pct": qa_coverage_pct,
            "agents_worked": agents_worked,
            "teams_worked": teams_worked,
            "flags": flags,
        })

    out.sort(key=lambda t: t["date_last_modified"] or "", reverse=True)
    return out


def _percentile_hours(values_hours, p):
    """Linear-interpolated percentile on a list of hour values. Returns float, 2dp."""
    if not values_hours:
        return 0.0
    s = sorted(values_hours)
    if len(s) == 1:
        return round(s[0], 2)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return round(s[lo], 2)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def compute_qa_reviewers(records):
    """Per-QA aggregations across all in-cache records.

    A 'review' = qa_assignee filled AND qa_status filled. Review time =
    qa_status_ts minus start_tagging_date in hours. Records with missing
    timestamps or negative deltas are dropped from the time calculation
    but still counted as reviews.
    """
    by_qa = defaultdict(list)
    for info in records:
        if info.get("qa_assignee") and info.get("qa_status"):
            by_qa[info["qa_assignee"]].append(info)

    out = []
    for name, recs in by_qa.items():
        n = len(recs)
        approvals = sum(1 for r in recs if r.get("qa_status") == "approve")
        changes   = sum(1 for r in recs if r.get("qa_status") == "changed")
        times_h = []
        for r in recs:
            t1 = _parse_iso(r.get("start_tagging"))
            t2 = _parse_iso(r.get("qa_status_ts"))
            if t1 and t2 and t2 >= t1:
                times_h.append((t2 - t1).total_seconds() / 3600.0)
        avg_h = round(sum(times_h) / len(times_h), 2) if times_h else 0.0
        out.append({
            "name": name,
            "reviews": n,
            "approvals": approvals,
            "changes": changes,
            "approval_pct": round((approvals / n) * 100, 1) if n else 0.0,
            "avg_review_time_hours":    avg_h,
            "median_review_time_hours": _percentile_hours(times_h, 50),
            "p90_review_time_hours":    _percentile_hours(times_h, 90),
        })
    out.sort(key=lambda x: -x["reviews"])
    return out


def compute_not_yet_finalized(records, today_eat, ownership_assignees, cap=500):
    """Records currently in an incomplete state, with age and IMO-cohort context.

    Returns (list, truncated_bool). Sorted by days_open desc, capped at `cap`.
    open_roles_count = number of OTHER records sharing this IMO and still open
    (per the spec — current record excluded from the count).
    """
    norm = _normalize_ownership(ownership_assignees)

    # First pass: gather IMO → open record count
    imo_to_open = defaultdict(int)
    open_records = []
    for info in records:
        if info.get("verification_status") in INCOMPLETE_STATUSES:
            open_records.append(info)
            imo = info.get("imo")
            if imo:
                imo_to_open[imo] += 1

    out = []
    for info in open_records:
        base = info.get("start_tagging") or info.get("created")
        base_date = _parse_eat_date(base)
        days_open = (today_eat - base_date).days if base_date else 0
        asg = info.get("assignee")
        team = norm.get((asg or "").strip().lower(), {}).get("team")
        imo = info.get("imo")
        other_open = max(0, imo_to_open.get(imo, 0) - 1)  # exclude self
        out.append({
            "imo": imo,
            "role": info.get("role"),
            "assignee": asg,
            "team": team,
            "verification_status": info.get("verification_status"),
            "qa_assignee": info.get("qa_assignee"),
            "qa_status": info.get("qa_status"),
            "days_open": days_open,
            "open_roles_count": other_open,
        })
    out.sort(key=lambda r: -r["days_open"])
    truncated = len(out) > cap
    return out[:cap], truncated


def compute_qa_done_not_finalized(not_yet_finalized_list):
    """Subset of not_yet_finalized where the record has been QA-reviewed
    (qa_assignee + qa_status both filled) but verification_status is still
    incomplete. Operational red flag — QA touched it, ops hasn't closed it."""
    return [r for r in not_yet_finalized_list
            if r.get("qa_assignee") and r.get("qa_status")]


def compute_add_new_company_records(records, today_eat, ownership_assignees, cap=500):
    """Records with add_new_company filled — drilldown for the Overview
    add_new_company_open count. SOP requires 100% QA on every such record;
    the page exposes a "needing QA" filter (qa_assignee or qa_status empty).

    Returns (list, truncated_bool). Sorted by days_open desc, capped at `cap`.
    Includes records in any verification_status — the page surfaces the full
    new-company pipeline, not just open ones (closed-but-missing-QA is also a
    failure mode worth seeing).
    """
    norm = _normalize_ownership(ownership_assignees)

    out = []
    for info in records:
        company = (info.get("add_new_company") or "").strip()
        if not company:
            continue
        base = info.get("start_tagging") or info.get("created")
        base_date = _parse_eat_date(base)
        days_open = (today_eat - base_date).days if base_date else 0
        asg = info.get("assignee")
        team = norm.get((asg or "").strip().lower(), {}).get("team")
        out.append({
            "requested_by":        info.get("requested_by"),
            "imo":                 info.get("imo"),
            "role":                info.get("role"),
            "assignee":            asg,
            "team":                team,
            "add_new_company":     company,
            "days_open":           days_open,
            "qa_assignee":         info.get("qa_assignee"),
            "qa_status":           info.get("qa_status"),
            "verification_status": info.get("verification_status"),
        })
    out.sort(key=lambda r: -r["days_open"])
    truncated = len(out) > cap
    return out[:cap], truncated


def aggregate(records, today_eat, ownership_assignees):
    """Build the aggregates_v2 block.

    Parameters
    ----------
    records: list[dict]
        Extracted info dicts (output of extract_v2.extract).
    today_eat: datetime.date
        Today in EAT.
    ownership_assignees: dict[str, str | dict]
        Map of assignee name → team. Two accepted shapes:
          - ``{name: team_str}``                       (test fixtures)
          - ``{name: {"team": ..., "canonical": ...}}``  (production, via _build_ownership_assignees)
        Lookup is case-insensitive. The canonical name (the roster's
        spelling of the member, not Airtable's raw casing or alias) is
        stamped onto each in-scope record's ``assignee`` field so that
        downstream grouping produces canonical keys.
    """
    # Normalize to {lowercase_key: {"team": ..., "canonical": ...}}. Accepts both
    # the rich shape and the legacy {name: team} shape (where canonical defaults
    # to the original key as-passed).
    norm_ownership = {}
    for k, v in ownership_assignees.items():
        key = k.strip().lower()
        if isinstance(v, dict):
            norm_ownership[key] = {"team": v["team"], "canonical": v.get("canonical", k)}
        else:
            norm_ownership[key] = {"team": v, "canonical": k}

    in_scope = []
    for r in records:
        match = None
        for asg in r.get("assignees", []):
            match = norm_ownership.get(asg.strip().lower()) if asg else None
            if match:
                break
        if not match:
            continue
        # Stamp team AND overwrite assignee with the matched canonical spelling so
        # by_agent / by_team grouping keys match the roster, not Airtable raw text.
        r = dict(r, team=match["team"], assignee=match["canonical"])
        in_scope.append(r)

    # Whole-table intake metric — NOT scoped to ownership teams. Counts every
    # record created today regardless of assignee. Surfaces "what was uploaded
    # to the table today" vs the per-team team_routed_intake_today metric which
    # only fires when ops routes same-day to the 5 teams.
    relations_support_intake_today = sum(
        1 for r in records if _parse_eat_date(r.get("created")) == today_eat
    )

    # Tasks uploaded today (whole-table, grouped by requested_by). Empty / null
    # requested_by collapses into a single "(no task name)" bucket.
    task_counts = Counter()
    for r in records:
        if _parse_eat_date(r.get("created")) != today_eat:
            continue
        req = (r.get("requested_by") or "").strip() or "(no task name)"
        task_counts[req] += 1
    tasks_today = [
        {"name": name, "records": n, "is_sanctions": is_sanctions(name)}
        for name, n in task_counts.most_common()
    ]

    totals = compute_metrics(in_scope, today_eat)
    totals["relations_support_intake_today"] = relations_support_intake_today
    totals["tasks_today"] = tasks_today
    totals["tasks_today_count"] = len(tasks_today)

    tasks_all = compute_task_breakdowns(records, today_eat, ownership_assignees)
    qa_reviewers = compute_qa_reviewers(in_scope)
    not_yet_finalized, nyf_truncated = compute_not_yet_finalized(in_scope, today_eat, ownership_assignees)
    qa_done_not_finalized = compute_qa_done_not_finalized(not_yet_finalized)
    add_new_company_records, anc_truncated = compute_add_new_company_records(in_scope, today_eat, ownership_assignees)

    return {
        "date":                       today_eat.isoformat(),
        "computed_at":                datetime.now(EAT).isoformat(),
        "sampling_target_pct":        SAMPLING_TARGET_PCT,
        "reject_threshold":           REJECT_THRESHOLD,
        "totals":                     totals,
        # Pipeline & Lead Time page — end-to-end (start_tagging → completion)
        # lead time for records completed today, overall + per ownership team.
        # Computed over in_scope so count reconciles with totals.done_today.
        "lead_time_today":            _lead_time_today(in_scope, today_eat),
        "tasks_all":                  tasks_all,
        "qa_reviewers":               qa_reviewers,
        "not_yet_finalized":          not_yet_finalized,
        "not_yet_finalized_truncated": nyf_truncated,
        "qa_done_not_finalized":      qa_done_not_finalized,
        "add_new_company_records":    add_new_company_records,
        "add_new_company_records_truncated": anc_truncated,
        "by_team":  {k: compute_metrics(v, today_eat)
                     for k, v in _group(in_scope, lambda r: r.get("team")).items()},
        "by_agent": {k: compute_metrics(v, today_eat)
                     for k, v in _group(in_scope, lambda r: r.get("assignee")).items()},
        "by_qa":    {k: _slim(compute_metrics(v, today_eat), QA_KEYS)
                     for k, v in _group(in_scope, lambda r: r.get("qa_assignee")).items()},
        "by_ww_qa": {k: _slim(compute_metrics(v, today_eat), WW_QA_KEYS)
                     for k, v in _group(in_scope, lambda r: r.get("ww_qa_assignee")).items()},
    }


def _load_records(work_dir):
    """Load raw page files, dedup by record id, extract.

    Seven page-file sets (per POLL_PROCEDURE.md + the fix-systemic-today-
    metric-truncation refactor — see ``audit_today_metrics.md``):

      - recent_p*.json            — Fetch A (ownership-team work, past 24h)
      - intake_p*.json            — Fetch B (today's table intake)
      - boqa_p*.json              — Fetch C (current BO QA queue)
      - tagged_today_p*.json      — Fetch D (today's tagged work, authoritative)
      - done_today_p*.json        — Fetch E (today's done/valid, authoritative)
      - qa_reviewed_today_p*.json — Fetch F (today's QA reviews, authoritative)
      - ww_qa_backlog_p*.json     — Fetch G (assigned-but-not-reviewed WW QA)

    Same record can appear in more than one — dedup by ``id``. Each
    extracted record is tagged with ``_sources`` (set of fetch letters)
    so downstream metrics can route from their authoritative source where
    semantics demand it (e.g. ww_qa_backlog now means "in Fetch G", not
    "vs == Selected for WW QA").
    """
    raw     = {}
    sources = {}
    fetch_map = {
        "recent_p*.json":            "A",
        "intake_p*.json":            "B",
        "boqa_p*.json":              "C",
        "tagged_today_p*.json":      "D",
        "done_today_p*.json":        "E",   # E1 — Done Selected Time today
        "valid_today_p*.json":       "E",   # E2 — Valid Selected Time today (formula field; split to avoid OR-truncation)
        "qa_reviewed_today_p*.json": "F",
        "ww_qa_backlog_p*.json":     "G",
    }
    for pattern, letter in fetch_map.items():
        for p in sorted(work_dir.glob(pattern)):
            for r in json.loads(p.read_text()).get("records", []):
                raw[r["id"]] = r
                sources.setdefault(r["id"], set()).add(letter)
    out = []
    for rid, r in raw.items():
        info = extract(r)
        info["_sources"] = sources.get(rid, set())
        out.append(info)
    return out


def _intake_total_from_metadata(work_dir):
    """Read metadata.totalRecordCount from the LAST intake_p*.json file.

    The poller writes a running cumulative count on each page (see
    poll_airtable.py — n_records is the running total), so page 1's
    metadata is the partial count after page 1 (~100), and only the
    last page carries the full cumulative count. Lexicographic sort
    orders intake_p1.json before intake_p10.json, so we natural-sort
    by the integer page number embedded in the filename to pick the
    actual last page.

    Returns (total, partial_flag). partial_flag is True iff total > 3000
    (the cache page cap), indicating downstream consumers should warn.
    """
    files = list(work_dir.glob("intake_p*.json"))
    if not files:
        return 0, False
    def _page_num(p):
        m = re.search(r"intake_p(\d+)\.json$", p.name)
        return int(m.group(1)) if m else 0
    last_page = max(files, key=_page_num)
    try:
        meta = json.loads(last_page.read_text()).get("metadata", {})
        total = int(meta.get("totalRecordCount", 0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0, False
    return total, total > 3000


def _build_ownership_assignees(roster):
    """Map lowercased name → {team, canonical}.

    Canonical is the roster member's ``name`` field. Aliases resolve to
    the parent member's canonical name. Lookups use a normalized lowercase
    key so Airtable variants like 'Hellen vigehi' or 'FAITH KHALAI' match
    the roster's canonical 'Hellen Vigehi' / 'Faith Khalai'.
    """
    out = {}
    for team, info in roster.items():
        for member in info.get("members", []):
            canonical = member["name"]
            out[canonical.strip().lower()] = {"team": team, "canonical": canonical}
            for alias in member.get("aliases", []):
                out[alias.strip().lower()] = {"team": team, "canonical": canonical}
    return out


def _load_roster(here):
    """Roster lives in config/roster.json. Falls back to the deprecation shim
    in ww_audit_log.json.roster if the file isn't there yet."""
    roster_path = here / "config" / "roster.json"
    if roster_path.exists():
        return json.loads(roster_path.read_text())["teams"]
    return json.loads((here / "ww_audit_log.json").read_text())["roster"]


# ---------------------------------------------------------------------------
# Daily snapshot persistence (Phase E)
# ---------------------------------------------------------------------------
# Each aggregate run writes a copy of the aggregates_v2 block to
# .poll_work/snapshots/<YYYY-MM-DD>.json. Multiple runs on the same day
# overwrite — the latest end-of-day state is what persists. The Weekly Report
# reads these snapshots to render true multi-day rollups.
#
# Append-friendly: Phase E2 can add rotation (delete >90 days) and gzip.
# Each snapshot is ~1–3 MB so daily plain JSON is fine for now.

def _save_snapshot(aggs_v2, work_dir):
    """Persist a copy of the aggregates_v2 block to .poll_work/snapshots/<date>.json."""
    snap_dir = work_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    date = aggs_v2.get("date") or datetime.now(EAT).date().isoformat()
    (snap_dir / f"{date}.json").write_text(json.dumps(aggs_v2, indent=2))


def load_snapshots(work_dir, start_date, end_date):
    """Return ``[(date, aggs_v2), ...]`` for snapshots in [start_date, end_date] inclusive.

    Missing days are silently skipped. Result is sorted by date ascending.
    """
    snap_dir = work_dir / "snapshots"
    if not snap_dir.exists():
        return []
    out = []
    cur = start_date
    while cur <= end_date:
        f = snap_dir / f"{cur.isoformat()}.json"
        if f.exists():
            try:
                out.append((cur, json.loads(f.read_text())))
            except json.JSONDecodeError:
                pass
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


def compute_weekly_rollup(snapshots, roster=None):
    """Roll a list of per-day aggregates_v2 snapshots into a weekly summary.

    Snapshots: list of (date, aggs_v2) — output of ``load_snapshots``.
    Roster: optional dict of teams (config/roster.json shape). If omitted,
    `agents_not_working` will be empty (caller didn't provide the universe
    of expected agents to compare against).
    """
    if not snapshots:
        return {
            "date_range": {"start": None, "end": None},
            "days_with_data": 0,
            "totals": {},
            "per_day": [],
            "per_team_rollup": {},
            "agents_not_working": [],
        }

    snapshots_sorted = sorted(snapshots, key=lambda x: x[0])
    days = [d for d, _ in snapshots_sorted]
    start, end = days[0], days[-1]

    # Per-day breakdown
    per_day = []
    for day, aggs in snapshots_sorted:
        totals = aggs.get("totals", {}) or {}
        by_agent = aggs.get("by_agent", {}) or {}
        active_agents = sum(
            1 for m in by_agent.values()
            if (m.get("tagged_today", 0) or 0) + (m.get("total_completions_today", m.get("total_completions", 0)) or 0) > 0
        )
        per_day.append({
            "date": day.isoformat(),
            "tagged":             totals.get("tagged_today", 0) or 0,
            "done":               totals.get("done_today", 0) or 0,
            "flow_a":             totals.get("flow_a_in_cache", totals.get("flow_a_count", 0)) or 0,
            "flow_b":             totals.get("flow_b_in_cache", totals.get("flow_b_count", 0)) or 0,
            "flow_c":             totals.get("flow_c_in_cache", totals.get("flow_c_count", 0)) or 0,
            "total_completions":  totals.get("total_completions", 0) or 0,
            "active_agents":      active_agents,
            "by_team": {
                team: (m.get("tagged_today", 0) or 0)
                for team, m in (aggs.get("by_team", {}) or {}).items()
            },
        })

    # Totals
    sum_tagged = sum(d["tagged"] for d in per_day)
    sum_done   = sum(d["done"]   for d in per_day)
    sum_fa     = sum(d["flow_a"] for d in per_day)
    sum_fb     = sum(d["flow_b"] for d in per_day)
    sum_fc     = sum(d["flow_c"] for d in per_day)
    sum_comp   = sum(d["total_completions"] for d in per_day)
    # Unique IMOs: set-union not possible from aggregates alone.
    # Sum-of-each-day's-unique_imos is a defensible approximation.
    sum_unique = sum((aggs.get("totals", {}) or {}).get("unique_imos", 0) or 0
                     for _, aggs in snapshots_sorted)
    sum_reviews = sum(sum(r.get("reviews", 0) for r in (aggs.get("qa_reviewers", []) or []))
                      for _, aggs in snapshots_sorted)
    sum_changes = sum(sum(r.get("changes", 0) for r in (aggs.get("qa_reviewers", []) or []))
                      for _, aggs in snapshots_sorted)
    avg_active = round(sum(d["active_agents"] for d in per_day) / len(per_day), 1) if per_day else 0.0

    totals = {
        "tagged":             sum_tagged,
        "done":               sum_done,
        "flow_a":             sum_fa,
        "flow_b":             sum_fb,
        "flow_c":             sum_fc,
        "total_completions":  sum_comp,
        "unique_imos_sum":    sum_unique,
        "qa_reviews":         sum_reviews,
        "qa_changes":         sum_changes,
        "active_agents_avg":  avg_active,
    }

    # Per-team rollup
    per_team_rollup = {}
    # Collect agent → total tagged across days
    agent_tagged = defaultdict(int)
    agent_team   = {}
    for _, aggs in snapshots_sorted:
        for agent, m in (aggs.get("by_agent", {}) or {}).items():
            agent_tagged[agent] += m.get("tagged_today", 0) or 0
    # Determine team for each agent from roster (if provided)
    if roster:
        for team, info in roster.items():
            for member in info.get("members", []):
                agent_team[member["name"]] = team
                for alias in member.get("aliases", []):
                    agent_team[alias] = team

    # Sum per team across days
    for team in (roster or {}).keys() if roster else set():
        team_records = 0
        team_unique  = 0
        for _, aggs in snapshots_sorted:
            tm = (aggs.get("by_team", {}) or {}).get(team)
            if not tm:
                continue
            team_records += tm.get("tagged_today", 0) or 0
            team_unique  += tm.get("unique_imos", 0) or 0
        # Top performer in this team
        team_members = [m["name"] for m in (roster.get(team, {}).get("members") or [])]
        team_member_set = set(team_members)
        ranked = sorted(((a, n) for a, n in agent_tagged.items() if a in team_member_set),
                        key=lambda x: -x[1])
        top = {"name": ranked[0][0], "records": ranked[0][1]} if ranked and ranked[0][1] > 0 else None
        per_team_rollup[team] = {
            "records": team_records,
            "unique_imos_sum": team_unique,
            "top_performer": top,
        }

    # Agents not working
    agents_not_working = []
    if roster:
        for team, info in roster.items():
            for member in info.get("members", []):
                missing_dates = []
                for day, aggs in snapshots_sorted:
                    by_agent = aggs.get("by_agent", {}) or {}
                    rec = by_agent.get(member["name"])
                    if not rec or (rec.get("tagged_today", 0) or 0) + (rec.get("total_completions_today", rec.get("total_completions", 0)) or 0) == 0:
                        missing_dates.append(day.isoformat())
                if missing_dates:
                    agents_not_working.append({
                        "name": member["name"],
                        "team": team,
                        "days_missed": len(missing_dates),
                        "missing_dates": missing_dates,
                    })
        agents_not_working.sort(key=lambda x: (-x["days_missed"], x["team"], x["name"]))

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "days_with_data": len(per_day),
        "totals": totals,
        "per_day": per_day,
        "per_team_rollup": per_team_rollup,
        "agents_not_working": agents_not_working,
    }


def _compute_flow_framework_counts(work_dir, ownership_assignees):
    """Classify in-scope records tagged today (Fetch D, cache-based) by
    their CURRENT verification_status + qa_assignee + qa_status state.
    Each record contributes to exactly one of four buckets so the sum
    reconciles with tagged_today_total:

      flow_a_today: classify() → ("completion", "A")  — Done/Valid, no QA
      flow_b_today: classify() → ("sampling", _)     — in BO QA sampling pool
      flow_c_today: classify() → ("completion", "C")  — Done/Valid + qa + status
      flow_d_today: anything else — pre_flow, alert (missing assignee/status), skip

    A + B + C + D == count of in-scope Fetch D records == tagged_today.

    Note the semantic shift from PR #13's source: those counts came from
    Supabase first-detection events (records DETECTED today). These counts
    come from current Airtable state (records TAGGED today). The latter
    reconciles cleanly with the headline KPI; the former matched detector
    activity but left a 1,193-record gap (records in tagged / need-to-be-
    update or flagged states).

    The Supabase tables (ownership_completions / ownership_qa_sampling)
    are still populated by the detector — they remain the source for the
    hourly heatmap. Only the Overview cards switched source.

    alerts_* counts come from a separate Supabase query — they include
    integrity issues from prior days too, not just today's tagged scope.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from completion_detector import classify as _classify, FLD_ASSIGNEE  # noqa: E402

    # Build a case-insensitive roster lookup (mirrors aggregate()'s
    # multi-assignee, alias-aware roster matching).
    norm = {}
    for name, val in ownership_assignees.items():
        norm[name.strip().lower()] = val

    flow_a = flow_b = flow_c = flow_d = 0
    in_scope = 0

    for p in sorted(work_dir.glob("tagged_today_p*.json")):
        try:
            page = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for rec in page.get("records", []):
            fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
            # Roster filter — multi-assignee aware (ANY assignee in roster qualifies).
            asg_list = fields.get(FLD_ASSIGNEE) or []
            if not isinstance(asg_list, list):
                continue
            in_roster = False
            for asg in asg_list:
                name = asg.get("name") if isinstance(asg, dict) else asg
                if name and name.strip().lower() in norm:
                    in_roster = True
                    break
            if not in_roster:
                continue
            in_scope += 1
            target, detail = _classify(fields)
            if target == "completion" and detail == "A":
                flow_a += 1
            elif target == "completion" and detail == "C":
                flow_c += 1
            elif target == "sampling":
                flow_b += 1
            else:
                # alert, pre_flow, skip — the "in-flight / not yet
                # classified" bucket. Flow D is the reconciliation slop
                # that makes A+B+C+D == tagged_today.
                flow_d += 1

    counts = {
        "flow_a_today":            flow_a,
        "flow_b_today":            flow_b,
        "flow_c_today":            flow_c,
        "flow_d_today":            flow_d,
        # B and D excluded from Total Completions per spec:
        #   B = in-progress (in sampling pool)
        #   D = in-flight (not yet sampled / not yet finalized)
        "total_completions_today": flow_a + flow_c,
    }
    # Sanity assertion — protected by tests; here as a runtime guardrail.
    assert flow_a + flow_b + flow_c + flow_d == in_scope, (
        f"Flow A+B+C+D ({flow_a+flow_b+flow_c+flow_d}) != in_scope ({in_scope})")
    return counts


def _fetch_alerts_counts():
    """Read open alert counts from Supabase flow_alerts table. Returns {}
    if env vars missing (keeps aggregator runnable in local dev without
    the Supabase env)."""
    import os
    try:
        import requests
    except ImportError:
        return {}
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return {}

    def _count(extra_params):
        params = dict(extra_params)
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Prefer": "count=exact", "Range": "0-0", "Range-Unit": "items"}
        try:
            r = requests.get(f"{url.rstrip('/')}/rest/v1/flow_alerts",
                              headers=headers, params={**params, "select": "airtable_record_id"},
                              timeout=15)
            if r.status_code not in (200, 206):
                return 0
            cr = r.headers.get("Content-Range", "")
            if "/" in cr:
                try:
                    return int(cr.split("/")[1])
                except ValueError:
                    return 0
            return 0
        except Exception as e:
            print(f"  WARN _fetch_alerts_counts: {e}")
            return 0

    a1 = _count({"alert_type": "eq.missing_qa_assignee", "resolved_at": "is.null"})
    a2 = _count({"alert_type": "eq.missing_qa_status",   "resolved_at": "is.null"})
    a3 = _count({"alert_type": "eq.stuck_in_sampling",   "resolved_at": "is.null"})
    return {
        "alerts_missing_qa_assignee": a1,
        "alerts_missing_qa_status":   a2,
        "alerts_stuck_in_sampling":   a3,
        "alerts_open_total":          a1 + a2 + a3,
    }


def main():
    here = Path(__file__).resolve().parent.parent
    work = here / ".poll_work"
    ownership_assignees = _build_ownership_assignees(_load_roster(here))

    today_eat = datetime.now(EAT).date()
    records = _load_records(work)
    aggs = aggregate(records, today_eat, ownership_assignees)

    # Pick the larger of cache-counted intake (which sums Fetch A backfill +
    # Fetch B today-creates) and Fetch B's metadata totalRecordCount.
    # Background: when Fetch B truncates at the 30-page cap, poll_airtable.py
    # writes a sentinel 3001 to metadata that just means "at least 3001" — not
    # the true count. Meanwhile Fetch A's 24h window (with PR #31's fresh-upload
    # backfill) frequently catches records Fetch B missed, so the combined cache
    # count can be larger and more accurate. Both are lower bounds; max is the
    # best estimate. partial=True only when metadata's lower bound exceeds what's
    # in cache (so the truncation banner only fires when we're actually missing
    # data we haven't already backfilled via Fetch A).
    intake_metadata, _meta_truncated = _intake_total_from_metadata(work)
    cache_count = aggs["totals"]["relations_support_intake_today"]
    intake_total = max(cache_count, intake_metadata)
    intake_partial = intake_metadata > cache_count
    aggs["totals"]["relations_support_intake_today"] = intake_total
    aggs["totals"]["intake_partial"] = intake_partial

    # Flow Framework v3 — cache-based A/B/C/D classification of records
    # tagged today. A+B+C+D == tagged_today (reconciles with the headline
    # KPI). See _compute_flow_framework_counts() docstring for the semantic
    # shift from v2.
    flow_counts = _compute_flow_framework_counts(work, ownership_assignees)
    aggs["totals"].update(flow_counts)
    # Alerts come separately from Supabase flow_alerts — they include
    # integrity issues from prior days too, not just today's tagged scope.
    alerts_counts = _fetch_alerts_counts()
    if alerts_counts:
        aggs["totals"].update(alerts_counts)
    sanity = flow_counts["flow_a_today"] + flow_counts["flow_b_today"] + \
             flow_counts["flow_c_today"] + flow_counts["flow_d_today"]
    print(f"  Flow v3 totals: A={flow_counts['flow_a_today']} "
          f"B={flow_counts['flow_b_today']} C={flow_counts['flow_c_today']} "
          f"D={flow_counts['flow_d_today']} | sum={sanity} "
          f"(should equal tagged_today={aggs['totals'].get('tagged_today', '?')})"
          + (f" | alerts open: {alerts_counts.get('alerts_open_total', 0)}"
             if alerts_counts else ""))

    agg_path = here / "daily_aggregates.json"
    existing = json.loads(agg_path.read_text()) if agg_path.exists() else {}
    existing["aggregates_v2"] = aggs
    agg_path.write_text(json.dumps(existing, indent=2))

    # Phase E — persist a per-day snapshot so the Weekly Report can render
    # historical days as cycles accumulate.
    _save_snapshot(aggs, work)

    print(f"aggregates_v2 written: {len(records)} records, "
          f"{len(aggs['by_team'])} teams, {len(aggs['by_agent'])} agents, "
          f"{len(aggs['by_qa'])} QAs, {len(aggs['by_ww_qa'])} WW QAs, "
          f"intake_total={intake_total} (partial={intake_partial}) | "
          f"snapshot saved to .poll_work/snapshots/{aggs['date']}.json")


if __name__ == "__main__":
    main()
