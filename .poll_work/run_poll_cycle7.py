#!/usr/bin/env python3
"""Run polling cycle 7 for the brave-confident-mendel session."""
import json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, Counter

HERE = Path("/sessions/brave-confident-mendel/mnt/QA Hourly Analysis")
WORK = HERE / ".poll_work"
EAT = timezone(timedelta(hours=3))

sys.path.insert(0, str(WORK))
from extract_v2 import extract, resolve_qa, SELECTED_FOR_BO_QA  # noqa: E402

def now_eat(): return datetime.now(EAT)
def parse_iso(s):
    if s is None: return None
    if s.endswith('Z'): s = s[:-1] + '+00:00'
    return datetime.fromisoformat(s)
def to_eat_date(s):
    dt = parse_iso(s); return dt.astimezone(EAT).date() if dt else None
def to_eat_hour(s):
    dt = parse_iso(s); return dt.astimezone(EAT).hour if dt else None

# Load state
audit_path = HERE / "ww_audit_log.json"
audit = json.loads(audit_path.read_text())
config = audit["config"]

# Roster chain: ROSTER_PATH env → private-notes/roster.json → config/roster.example.json
# (placeholder). Final fallback is the ww_audit_log.json deprecation shim.
roster_path = Path(os.environ.get("ROSTER_PATH") or (HERE / "private-notes" / "roster.json"))
if not roster_path.exists():
    roster_path = HERE / "config" / "roster.example.json"
if roster_path.exists():
    roster = json.loads(roster_path.read_text())["teams"]
else:
    roster = audit["roster"]

pending_baselines = audit.get("pending_baselines", {})
qa_events = audit.get("qa_events", [])
agent_completion_events = audit.get("agent_completion_events", [])
polling_state = audit["polling_state"]

today_eat = now_eat().date().isoformat()
captured_at = now_eat().isoformat()
print(f"today_eat={today_eat}, captured_at={captured_at}")

daily_records_path = HERE / f"daily_records_{today_eat}.json"
daily_records = json.loads(daily_records_path.read_text()) if daily_records_path.exists() else {}
print(f"daily_records (loaded): {len(daily_records)}")
print(f"pending_baselines (loaded): {len(pending_baselines)}")
print(f"qa_events (loaded): {len(qa_events)}")

# roster_lookup
roster_lookup = {}
for team_name, info in roster.items():
    qa = info.get("qa")
    if qa and qa.get("name"):
        roster_lookup[qa["name"].strip().lower()] = {"team": team_name, "role": "qa", "canonical": qa["name"]}
    for member in info.get("members", []):
        canonical = member["name"]
        for n in [canonical] + member.get("aliases", []):
            roster_lookup[n.strip().lower()] = {"team": team_name, "role": "agent", "canonical": canonical}

# Load only the NEW page files (recent_p*, boqa_p*) — not the legacy recent_001 etc.
recent_records = []
bo_qa_records = []
for p in sorted(WORK.glob("recent_p*.json")):
    recent_records.extend(json.loads(p.read_text()).get("records", []))
for p in sorted(WORK.glob("boqa_p*.json")):
    bo_qa_records.extend(json.loads(p.read_text()).get("records", []))
print(f"recent records loaded: {len(recent_records)}, bo_qa records loaded: {len(bo_qa_records)}")

# extract() lives in extract_v2.py — shared with the aggregator.

# Combine all records (dedup by id)
all_recs = {}
for r in recent_records: all_recs[r["id"]] = r
for r in bo_qa_records: all_recs[r["id"]] = r
print(f"unique records to process: {len(all_recs)}")

new_baselines = 0; new_qa_events = 0; new_completion_events = 0; silent_changes = 0
added_to_daily = 0; updated_in_daily = 0; skipped_not_today = 0; skipped_not_ownership = 0
qa_attribution_fallback_count = 0  # incremented when qa_assignee is blank and we fall back to last_modified_by

existing_qa_event_keys = {(e["record_id"], e.get("qa_action_at")) for e in qa_events}
existing_completion_keys = {(e["record_id"], e.get("event_type"), e.get("at")) for e in agent_completion_events}

for rid, rec in all_recs.items():
    info = extract(rec)
    if not info["assignee"]: continue
    look = roster_lookup.get(info["assignee"].strip().lower())
    if not look or look["role"] != "agent":
        skipped_not_ownership += 1; continue
    canonical = look["canonical"]; team = look["team"]

    st = to_eat_date(info["start_tagging"]) if info["start_tagging"] else None
    if st and st.isoformat() == today_eat:
        if rid not in daily_records:
            added_to_daily += 1
        else:
            updated_in_daily += 1
        daily_records[rid] = {
            "agent": canonical, "team": team,
            "verification_status": info["verification_status"],
            "qa_status": info["qa_status"],
            "start_tagging": info["start_tagging"],
            "qa_status_ts": info["qa_status_ts"],
            "company_id": info["company_id"], "imo": info["imo"],
            "last_modified": info["last_modified"],
        }
    elif st: skipped_not_today += 1

    # completion events: when a record enters "tagged" or "Selected for BO QA "
    if info["verification_status"] in ("tagged", SELECTED_FOR_BO_QA):
        ev_type = "tagged" if info["verification_status"] == "tagged" else "selected_for_bo_qa"
        at_ts = info["start_tagging"] or info["last_modified"]
        key = (rid, ev_type, at_ts)
        if key not in existing_completion_keys:
            agent_completion_events.append({
                "record_id": rid, "imo": info["imo"], "agent": canonical, "team": team,
                "company_name": info["company_name"], "company_id": info["company_id"],
                "event_type": ev_type, "at": at_ts, "captured_at": captured_at,
            })
            existing_completion_keys.add(key); new_completion_events += 1

    # baselines + qa_events
    if info["verification_status"] == SELECTED_FOR_BO_QA and rid not in pending_baselines:
        pending_baselines[rid] = {
            "imo": info["imo"], "agent": canonical, "team": team,
            "agent_company_id": info["company_id"], "agent_company_name": info["company_name"],
            "tagged_at": info["start_tagging"], "captured_at": captured_at, "seeded": False,
        }
        new_baselines += 1
    elif rid in pending_baselines and info["verification_status"] != SELECTED_FOR_BO_QA:
        b = pending_baselines[rid]
        is_match = b.get("agent_company_name") == info["company_name"]
        ev_type = "qa_approved" if is_match else "qa_changed"
        silent = (not is_match) and (info["qa_status"] != "changed")
        key = (rid, info["qa_status_ts"] or info["last_modified"])
        if key not in existing_qa_event_keys:
            if silent: silent_changes += 1
            qa_name, used_fallback = resolve_qa(info)
            if used_fallback:
                qa_attribution_fallback_count += 1
            qa_events.append({
                "record_id": rid, "imo": info["imo"], "agent": b["agent"],
                "qa": qa_name,
                "qa_attribution_source": "last_modified_by" if used_fallback else "qa_assignee",
                "team": b["team"],
                "agent_company_name": b.get("agent_company_name"),
                "qa_company_name": info["company_name"],
                "qa_status_value": info["qa_status"],
                "verification_status": info["verification_status"],
                "qa_action_at": info["qa_status_ts"] or info["last_modified"],
                "event_type": ev_type, "silent_change": silent, "captured_at": captured_at,
            })
            existing_qa_event_keys.add(key); new_qa_events += 1
        del pending_baselines[rid]

print(f"\nadded_to_daily: {added_to_daily}, updated_in_daily: {updated_in_daily}, skipped_not_today: {skipped_not_today}, skipped_not_ownership: {skipped_not_ownership}")
print(f"new_baselines: {new_baselines}, new_qa_events: {new_qa_events}, new_completion_events: {new_completion_events}, silent: {silent_changes}")
print(f"qa_attribution_fallback (qa_assignee blank → used last_modified_by): {qa_attribution_fallback_count} of {new_qa_events} new qa_events")
print(f"daily_records total now: {len(daily_records)}, pending_baselines total now: {len(pending_baselines)}")

# Aggregates
prod_min = config["thresholds"]["daily_productivity_minimum"]
samp_min = config["thresholds"]["qa_sampling_minimum_pct"]
by_agent = defaultdict(lambda: {"team":None,"tagged_today":0,"in_bo_qa":0,"qa_inspected":0,"qa_approved":0,"qa_changed":0,"need_to_be_update":0,"skipped_done_or_valid":0,"hourly":defaultdict(int)})
for rid, r in daily_records.items():
    a = r["agent"]; by_agent[a]["team"] = r["team"]; by_agent[a]["tagged_today"] += 1
    vs, qs = r.get("verification_status"), r.get("qa_status")
    if vs == SELECTED_FOR_BO_QA: by_agent[a]["in_bo_qa"] += 1
    elif vs == "need to be update": by_agent[a]["need_to_be_update"] += 1
    elif qs == "approve": by_agent[a]["qa_inspected"] += 1; by_agent[a]["qa_approved"] += 1
    elif qs == "changed": by_agent[a]["qa_inspected"] += 1; by_agent[a]["qa_changed"] += 1
    elif vs in ("Done","Valid"): by_agent[a]["skipped_done_or_valid"] += 1
    h = to_eat_hour(r["start_tagging"])
    if h is not None: by_agent[a]["hourly"][h] += 1

by_agent_out = {}
for a, s in by_agent.items():
    sampled = s["in_bo_qa"] + s["qa_inspected"] + s["need_to_be_update"]
    rate = (sampled / s["tagged_today"] * 100) if s["tagged_today"] else 0
    by_agent_out[a] = {
        "team": s["team"], "tagged_today": s["tagged_today"],
        "in_bo_qa": s["in_bo_qa"], "qa_inspected": s["qa_inspected"],
        "qa_approved": s["qa_approved"], "qa_changed": s["qa_changed"],
        "need_to_be_update": s["need_to_be_update"],
        "skipped_done_or_valid": s["skipped_done_or_valid"],
        "sampling_rate_pct": round(rate, 1),
        "productivity_met": s["tagged_today"] >= prod_min,
        "hourly": {str(k): v for k, v in sorted(s["hourly"].items())},
    }

by_team = defaultdict(lambda: {"tagged_today":0,"in_bo_qa":0,"qa_inspected":0,"qa_approved":0,"qa_changed":0,"need_to_be_update":0,"skipped_done_or_valid":0})
for a, s in by_agent_out.items():
    t = s["team"]
    for k in by_team[t]: by_team[t][k] += s.get(k, 0)
by_team_out = {}
for t, s in by_team.items():
    sampled = s["in_bo_qa"] + s["qa_inspected"] + s["need_to_be_update"]
    rate = (sampled / s["tagged_today"] * 100) if s["tagged_today"] else 0
    by_team_out[t] = {**s, "sampling_rate_pct": round(rate, 1)}

totals = {
    "tagged_today": sum(s["tagged_today"] for s in by_agent_out.values()),
    "in_bo_qa": sum(s["in_bo_qa"] for s in by_agent_out.values()),
    "qa_inspected": sum(s["qa_inspected"] for s in by_agent_out.values()),
    "silent_changes_today": sum(1 for e in qa_events if e.get("silent_change") and (e.get("captured_at","") or "").startswith(today_eat)),
}

aggregates = {
    "date": today_eat, "computed_at": captured_at,
    "thresholds": {"productivity_min": prod_min, "sampling_min_pct": samp_min},
    "by_agent": by_agent_out, "by_team": by_team_out, "totals": totals,
}

# pending_queue from fresh BO QA fetch
queue_groups = defaultdict(lambda: {"count":0, "oldest_h": None})
queue_count = 0; queue_by_team = Counter()
for r in bo_qa_records:
    info = extract(r)
    if not info["assignee"]: continue
    look = roster_lookup.get(info["assignee"].strip().lower())
    if not look or look["role"] != "agent": continue
    team, canonical = look["team"], look["canonical"]
    qa_name = roster[team]["qa"]["name"]
    key = (team, qa_name, canonical)
    queue_groups[key]["count"] += 1; queue_count += 1; queue_by_team[team] += 1
    h = to_eat_hour(info["start_tagging"])
    if h is not None:
        cur = queue_groups[key]["oldest_h"]
        if cur is None or h < cur: queue_groups[key]["oldest_h"] = h

by_team_qa_agent = sorted(
    [{"team":k[0],"qa":k[1],"agent":k[2],"count":v["count"],"oldest_h":v["oldest_h"]} for k,v in queue_groups.items()],
    key=lambda x:(x["team"], -x["count"])
)
pending_queue = {
    "captured_at": captured_at, "count": queue_count, "by_team": dict(queue_by_team),
    "by_team_qa_agent": by_team_qa_agent,
    "note": None,
}

# Save
polling_state["last_run_at"] = captured_at
polling_state["last_successful_run_at"] = captured_at
polling_state["total_polls"] = polling_state.get("total_polls", 0) + 1
polling_state["last_error"] = None
polling_state["last_window_start"] = captured_at

audit["pending_baselines"] = pending_baselines
audit["qa_events"] = qa_events
audit["agent_completion_events"] = agent_completion_events
audit["polling_state"] = polling_state

daily_records_path.write_text(json.dumps(daily_records, indent=2))
(HERE / "daily_aggregates.json").write_text(json.dumps(aggregates, indent=2))
(HERE / "pending_queue.json").write_text(json.dumps(pending_queue, indent=2))
audit_path.write_text(json.dumps(audit, indent=2))

print(f"\nFINAL daily_records={len(daily_records)}, pending_baselines={len(pending_baselines)}, pending_queue={queue_count}")
print(f"SUMMARY: {new_baselines} baselines, {new_completion_events} completions, {new_qa_events} qa_events ({silent_changes} silent)")
