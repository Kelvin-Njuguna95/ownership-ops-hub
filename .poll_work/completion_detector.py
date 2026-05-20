#!/usr/bin/env python3
"""Detect newly-completed ownership records and stamp them in Supabase.

Flow Framework v2 (feat-flow-framework-v2):

  - Flow A: vs in (Done, Valid) + qa_assignee EMPTY + qa_status EMPTY
            → ownership_completions, flow='A'
  - Flow B: vs == "Selected for BO QA " + qa_assignee FILLED
            → ownership_qa_sampling (in-progress, NOT a completion)
  - Flow C: vs in (Done, Valid) + qa_assignee FILLED + qa_status FILLED
            → ownership_completions, flow='C'
  - Alerts: data-integrity mismatches → flow_alerts table
      missing_qa_assignee: vs SBO without reviewer; or Done/Valid + qa_status w/o assignee
      missing_qa_status:   Done/Valid + qa_assignee filled + qa_status blank
      stuck_in_sampling:   ownership_qa_sampling row older than 24h not yet completed
  - Pre-Flow (vs in tagged / need to be update): existing rule, flow=NULL on insert
    (these are first-contact records; they get a flow value if/when caught
    in a later terminal state, via the PATCH-on-NULL UPSERT path below.)

Total Completions = Flow A + Flow C (B excluded — in-progress, not done).

First-write-wins semantics:
  - ``completed_at`` is the detector's clock at first observation of a record
    in any qualifying state. Never overwritten.
  - ``flow`` is upserted: if a previous detection wrote the row with flow=NULL
    (pre-Flow path) and a later detection classifies it as A/C, the flow
    column is PATCHed in place. PostgREST filter ``&flow=is.null`` prevents
    overwriting an already-set flow.
  - flow_alerts (record_id, alert_type) is UNIQUE. ``resolved_at`` is NULL
    while the condition holds; cleared back to NULL if a previously-resolved
    record bounces back into the bad state; set to NOW() when the condition
    no longer applies.

Airtable is strictly read-only — only GET requests against the table.
Supabase writes use the service_role key (server-side only, never in the
browser bundle).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------------
# Airtable choice literals — kept as module-level constants so a rename
# in Airtable can be fixed by editing one place. Note the trailing space
# on "Selected for BO QA " — that's the literal choice name in Airtable.
# And the "be" in "need to be update" — same reason.
# ----------------------------------------------------------------------
TAGGED              = "tagged"
NEED_TO_BE_UPDATE   = "need to be update"
SELECTED_FOR_BO_QA  = "Selected for BO QA "  # trailing space, intentional
DONE                = "Done"
VALID               = "Valid"

# Stuck-in-QA-sampling threshold per the v2 spec.
STUCK_HOURS = 24

# Airtable base / table — same as poll_airtable.py
BASE_ID  = "REDACTED_BASE_ID"
TABLE_ID = "tblpj9aJP4ExhYCZF"
AIRTABLE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Field IDs we read. Mirror of extract_v2.FIELD_IDS — duplicated here
# (small list) rather than imported to keep this script standalone and
# resistant to extract_v2 refactors.
FLD_IMO                    = "fldqWGr2XDH9BRmtE"
FLD_ASSIGNEE               = "fldT4xElSgcdnqTmy"
FLD_QA_ASSIGNEE            = "fldtQ5HCuU45HOcg4"
FLD_QA_STATUS              = "fldpTTs63XmNYNPww"
FLD_LAST_MODIFIED_BY       = "fldpz9XuDm5xRblSL"
FLD_VERIFICATION_STATUS    = "fldYSXHGwZvxXK7s6"
FLD_COMPANY_ID_AND_NAME    = "fldaMBqa6bEANUPpn"
FLD_COMPANY_NAME_LOOKUP    = "flda5zj1ne1BuJhOm"
FLD_DEAD_VESSEL            = "fldK9xjvBASgXIKlm"
FLD_ADD_NEW_COMPANY        = "fld2wp1Q0GQJjbYdA"
FLD_ROLE                   = "fldnBNNkH7w4rS3fG"
FLD_REQUESTED_BY           = "fldlPkvV6BiE7glLZ"

# Cap pages so a runaway day can't loop forever. The expanded v2 filter
# pulls 4 more states than v1 (was tagged + need-to-be-update; now adds
# SBO + Done + Valid). Done/Valid bulk easily hits 5k+ on a busy day.
PAGE_CAP = 100


# ----------------------------------------------------------------------
# Shape helpers — Airtable REST returns several types in different shapes
# than the Cowork MCP wrapper used to. Mirror the dual-shape logic in
# extract_v2._name / _id (PR #6) so this script handles both.
# ----------------------------------------------------------------------

def _name(val):
    """Read 'name'-like value from a cell. Handles str / dict / list-of-either."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name")
    if isinstance(val, list) and val:
        return _name(val[0])
    return None


def _first_link(val):
    """Return True if a multipleRecordLinks field is non-empty (either shape)."""
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    return bool(val)


# ----------------------------------------------------------------------
# Pre-Flow completion rule (kept verbatim from v1). Records currently in
# "tagged" or "need to be update" are first-contact captures — they get
# inserted into ownership_completions with flow=NULL. The flow column is
# upserted later if a subsequent cycle catches them in a terminal state.
# ----------------------------------------------------------------------

def is_complete(fields):
    """Pre-Flow detection rule for first-contact records."""
    vs = _name(fields.get(FLD_VERIFICATION_STATUS))
    if vs == TAGGED:
        return _first_link(fields.get(FLD_COMPANY_ID_AND_NAME)) or bool(fields.get(FLD_DEAD_VESSEL))
    if vs == NEED_TO_BE_UPDATE:
        return bool(fields.get(FLD_ADD_NEW_COMPANY))
    return False


# ----------------------------------------------------------------------
# Flow Framework v2 classification.
# ----------------------------------------------------------------------

# Routing decision shape:
#   ("completion", "A" | "C")  → insert/upsert to ownership_completions
#   ("sampling", None)         → insert to ownership_qa_sampling
#   ("alert", alert_type)      → insert to flow_alerts
#   ("pre_flow", None)         → existing first-contact rule (flow=NULL)
#   ("skip", None)             → not a flow-relevant state

def classify(fields):
    """Classify a record per the Flow Framework v2 rules."""
    vs          = _name(fields.get(FLD_VERIFICATION_STATUS))
    qa_assignee = _name(fields.get(FLD_QA_ASSIGNEE))
    qa_status   = _name(fields.get(FLD_QA_STATUS))

    if vs in (DONE, VALID):
        if not qa_assignee and not qa_status:
            return ("completion", "A")
        if qa_assignee and qa_status:
            return ("completion", "C")
        if qa_assignee and not qa_status:
            return ("alert", "missing_qa_status")
        # qa_status filled but no qa_assignee — rare; flag the inverse.
        return ("alert", "missing_qa_assignee")

    if vs == SELECTED_FOR_BO_QA:
        if qa_assignee:
            return ("sampling", None)
        return ("alert", "missing_qa_assignee")

    if vs in (TAGGED, NEED_TO_BE_UPDATE):
        # Defer to the existing first-contact rule.
        return ("pre_flow", None) if is_complete(fields) else ("skip", None)

    return ("skip", None)


# ----------------------------------------------------------------------
# completed_by attribution — the TAGGER (assignee) → last_modified_by → qa_assignee.
# `completed_by` feeds the Hourly Output heatmap, which is a *tagging output*
# chart: each row must be credited to whoever moved the record waiting→tagged,
# i.e. the assignee. _name() on the collaborator list returns the FIRST
# assignee, so a multi-assignee record credits its primary (first) tagger.
# The old last_modified_by → qa_assignee chain is kept ONLY as a fallback for
# the rare record with no assignee, so the NOT-NULL column is never blank.
# Names in NON_HUMAN_LAST_MODIFIED are skipped (Automations bot etc.).
# (Was: last_modified_by → qa_assignee → assignee, which drifted tagging
# credit to QA reviewers / last editors — see
# docs/hourly_audit_james_maina_2026-05-20.md.)
# ----------------------------------------------------------------------

NON_HUMAN_LAST_MODIFIED = {"Automations"}


def resolve_completed_by(fields):
    """Return (name, source). `completed_by` means THE TAGGER (first assignee).

    Resolution order: assignee (first in the list) → last_modified_by →
    qa_assignee. The last two are fallbacks only when the record has no
    assignee at all, so this NOT-NULL column is never blank. ``Automations``
    is skipped as a non-human last-modifier.
    """
    n = _name(fields.get(FLD_ASSIGNEE))
    if n:
        return n, "assignee"
    n = _name(fields.get(FLD_LAST_MODIFIED_BY))
    if n and n not in NON_HUMAN_LAST_MODIFIED:
        return n, "last_modified_by"
    n = _name(fields.get(FLD_QA_ASSIGNEE))
    if n:
        return n, "qa_assignee"
    return None, None


# ----------------------------------------------------------------------
# Row builders for each target table.
# ----------------------------------------------------------------------

def build_completion_row(rec, now_utc, flow):
    """ownership_completions row. ``flow`` is 'A', 'C', or None for pre-Flow."""
    fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
    completed_by, source = resolve_completed_by(fields)
    if not completed_by:
        return None, None
    row = {
        "airtable_record_id":  rec["id"],
        "imo":                 fields.get(FLD_IMO),
        "role":                _name(fields.get(FLD_ROLE)),
        "verification_status": _name(fields.get(FLD_VERIFICATION_STATUS)),
        "company_id_and_name": _name(fields.get(FLD_COMPANY_NAME_LOOKUP))
                               or _name(fields.get(FLD_COMPANY_ID_AND_NAME)),
        "add_a_new_company":   fields.get(FLD_ADD_NEW_COMPANY),
        "completed_by":        completed_by,
        "completed_at":        now_utc.isoformat(),
        "requested_by":        fields.get(FLD_REQUESTED_BY),
        "raw_payload":         fields,
        "flow":                flow,
    }
    return row, source


def build_sampling_row(rec, now_utc):
    """ownership_qa_sampling row. Returns None if qa_assignee is missing
    (caller should have classified that as an alert, not a sampling)."""
    fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
    qa_assignee = _name(fields.get(FLD_QA_ASSIGNEE))
    if not qa_assignee:
        return None
    return {
        "airtable_record_id": rec["id"],
        "imo":                fields.get(FLD_IMO),
        "role":               _name(fields.get(FLD_ROLE)),
        "qa_assignee":        qa_assignee,
        "sampled_at":         now_utc.isoformat(),
        "raw_payload":        fields,
    }


def build_alert_row(rec, alert_type):
    """flow_alerts row. resolved_at omitted → defaults to NULL (open)."""
    fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
    return {
        "airtable_record_id":  rec["id"],
        "alert_type":          alert_type,
        "verification_status": _name(fields.get(FLD_VERIFICATION_STATUS)),
        "qa_assignee":         _name(fields.get(FLD_QA_ASSIGNEE)),
        "qa_status":           _name(fields.get(FLD_QA_STATUS)),
        "raw_payload":         fields,
    }


# ----------------------------------------------------------------------
# Airtable fetch
# ----------------------------------------------------------------------

def fetch_flow_records(pat):
    """GET pages of records in any of the 5 Flow-relevant states."""
    headers = {"Authorization": f"Bearer {pat}"}
    formula = (
        "OR("
        f'{{verification_status}}="{TAGGED}",'
        f'{{verification_status}}="{NEED_TO_BE_UPDATE}",'
        f'{{verification_status}}="{SELECTED_FOR_BO_QA}",'
        f'{{verification_status}}="{DONE}",'
        f'{{verification_status}}="{VALID}"'
        ")"
    )
    params = {
        "pageSize":              "100",
        "returnFieldsByFieldId": "true",
        "filterByFormula":       formula,
    }
    out = []
    offset = None
    pages = 0
    while pages < PAGE_CAP:
        q = dict(params)
        if offset:
            q["offset"] = offset
        r = requests.get(AIRTABLE_URL, headers=headers, params=q, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Airtable {r.status_code}: {r.text[:200]}")
        body = r.json()
        out.extend(body.get("records", []) or [])
        offset = body.get("offset")
        pages += 1
        if not offset:
            break
    if pages >= PAGE_CAP and offset:
        raise RuntimeError(f"fetch_flow_records hit {PAGE_CAP}-page cap with more pending — bump cap")
    return out


# ----------------------------------------------------------------------
# Supabase operations
# ----------------------------------------------------------------------

def _sb_headers(service_key, extra=None):
    h = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _sb_post(url, headers, body):
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase POST {r.status_code} {url}: {r.text[:300]}")
    return r.json() if r.text else []


def _sb_patch(url, headers, body):
    r = requests.patch(url, headers=headers, data=json.dumps(body), timeout=60)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Supabase PATCH {r.status_code} {url}: {r.text[:300]}")
    return r.json() if r.text else []


def _sb_get_paginated(url, headers, params):
    """GET with Range pagination (per CLAUDE.md: db-max-rows defaults to 1000)."""
    out = []
    offset = 0
    while True:
        h = dict(headers)
        h["Range"]      = f"{offset}-{offset+999}"
        h["Range-Unit"] = "items"
        r = requests.get(url, headers=h, params=params, timeout=60)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"Supabase GET {r.status_code}: {r.text[:300]}")
        batch = r.json()
        out.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
        if offset > 100_000:
            raise RuntimeError("_sb_get_paginated runaway — >100k rows")
    return out


def supabase_insert_completions(supabase_url, service_key, rows):
    """Bulk INSERT to ownership_completions with first-write-wins on
    completed_at, then bulk PATCH the flow column for any duplicate rows
    whose existing flow is NULL. Returns dict with counts.

    The PATCH path uses PostgREST's in.() filter to update many rows in
    one round-trip, grouped by intended flow value. Replaces a per-row
    PATCH loop that scaled poorly: 3000+ duplicates × ~150ms each
    = ~7-8 min wall time on a busy day, timing out the workflow's 10-min
    cap. The batched version makes ~10 PATCH calls per cycle regardless
    of duplicate count.

    URL-length guard: PostgREST's default proxy URL cap is ~8KB. With
    ~17-char Airtable record IDs and %2C-encoded comma separators
    (~20 chars per ID), 100 IDs per chunk → ~2KB URL — comfortably under.
    """
    if not rows:
        return {"inserted": 0, "flow_upserted": 0, "duplicates_no_flow": 0}
    from collections import defaultdict as _defaultdict

    base = supabase_url.rstrip("/")
    insert_url = f"{base}/rest/v1/ownership_completions?on_conflict=airtable_record_id"
    headers = _sb_headers(service_key, {
        "Prefer": "resolution=ignore-duplicates,return=representation",
    })
    inserted = _sb_post(insert_url, headers, rows)
    inserted_ids = {r["airtable_record_id"] for r in inserted}

    # Group duplicates by intended flow value. Rows without a flow value
    # (pre-Flow path) don't need PATCH at all — skip.
    dup_by_flow = _defaultdict(list)
    duplicates_no_flow = 0
    for row in rows:
        if row["airtable_record_id"] in inserted_ids:
            continue
        flow = row.get("flow")
        if not flow:
            duplicates_no_flow += 1
            continue
        dup_by_flow[flow].append(row["airtable_record_id"])

    flow_upserted = 0
    CHUNK = 100  # ~2KB URL with %2C-encoded commas; safely under PostgREST default
    for flow_value, rec_ids in dup_by_flow.items():
        for i in range(0, len(rec_ids), CHUNK):
            chunk = rec_ids[i:i + CHUNK]
            ids_clause = ",".join(chunk)
            patch_url = (f"{base}/rest/v1/ownership_completions"
                         f"?airtable_record_id=in.({ids_clause})"
                         f"&flow=is.null")
            # &flow=is.null filter ensures we don't overwrite an already-set
            # flow value — only NULL rows get upgraded. Server-side filter
            # so safe even if our list contains rows whose flow was set
            # since we built the batch.
            result = _sb_patch(
                patch_url,
                _sb_headers(service_key, {"Prefer": "return=representation"}),
                {"flow": flow_value},
            )
            flow_upserted += len(result)

    return {
        "inserted":           len(inserted),
        "flow_upserted":      flow_upserted,
        "duplicates_no_flow": duplicates_no_flow,
    }


def supabase_insert_samplings(supabase_url, service_key, rows):
    if not rows:
        return 0
    url = f"{supabase_url.rstrip('/')}/rest/v1/ownership_qa_sampling?on_conflict=airtable_record_id"
    headers = _sb_headers(service_key, {
        "Prefer": "resolution=ignore-duplicates,return=representation",
    })
    inserted = _sb_post(url, headers, rows)
    return len(inserted)


def supabase_upsert_alerts(supabase_url, service_key, rows):
    """INSERT alerts (first-write-wins on first_seen_at) AND clear resolved_at
    on any existing alert that's still firing (was resolved, condition came back).
    Returns (new_inserts, reopened_count)."""
    if not rows:
        return (0, 0)
    base = supabase_url.rstrip("/")
    insert_url = f"{base}/rest/v1/flow_alerts?on_conflict=airtable_record_id,alert_type"
    headers = _sb_headers(service_key, {
        "Prefer": "resolution=ignore-duplicates,return=representation",
    })
    inserted = _sb_post(insert_url, headers, rows)
    inserted_keys = {(r["airtable_record_id"], r["alert_type"]) for r in inserted}

    reopened = 0
    for row in rows:
        key = (row["airtable_record_id"], row["alert_type"])
        if key in inserted_keys:
            continue
        # Existing row — clear resolved_at if it was set (record bounced back).
        patch_url = (f"{base}/rest/v1/flow_alerts"
                     f"?airtable_record_id=eq.{row['airtable_record_id']}"
                     f"&alert_type=eq.{row['alert_type']}"
                     f"&resolved_at=not.is.null")
        result = _sb_patch(patch_url, _sb_headers(service_key, {"Prefer": "return=representation"}),
                           {"resolved_at": None})
        if result:
            reopened += 1
    return (len(inserted), reopened)


def supabase_resolve_alerts(supabase_url, service_key, current_keys, now_utc):
    """Mark resolved any open alert whose (record_id, alert_type) is no longer
    in the current cycle's set. ``current_keys`` is a set of (rid, type) tuples.
    Returns count of newly-resolved alerts."""
    base = supabase_url.rstrip("/")
    list_url = f"{base}/rest/v1/flow_alerts"
    headers = _sb_headers(service_key)
    open_alerts = _sb_get_paginated(list_url, headers, {
        "select":      "id,airtable_record_id,alert_type",
        "resolved_at": "is.null",
    })
    resolved = 0
    for a in open_alerts:
        key = (a["airtable_record_id"], a["alert_type"])
        if key in current_keys:
            continue
        patch_url = f"{base}/rest/v1/flow_alerts?id=eq.{a['id']}"
        _sb_patch(patch_url, _sb_headers(service_key, {"Prefer": "return=minimal"}),
                  {"resolved_at": now_utc.isoformat()})
        resolved += 1
    return resolved


def detect_stuck_in_sampling(supabase_url, service_key, now_utc):
    """Find ownership_qa_sampling rows older than STUCK_HOURS whose
    airtable_record_id has NOT yet completed (not in ownership_completions
    with flow=A or flow=C). Returns list of dicts suitable for flow_alerts."""
    base = supabase_url.rstrip("/")
    cutoff = (now_utc - timedelta(hours=STUCK_HOURS)).isoformat()
    old_samples = _sb_get_paginated(
        f"{base}/rest/v1/ownership_qa_sampling",
        _sb_headers(service_key),
        {"select": "airtable_record_id,qa_assignee,sampled_at,raw_payload",
         "sampled_at": f"lt.{cutoff}"},
    )
    if not old_samples:
        return []
    # Check which of those have NOT completed.
    ids = [s["airtable_record_id"] for s in old_samples]
    # PostgREST `in.()` with a long list works up to URL-length limits;
    # chunk to be safe.
    completed_ids = set()
    CHUNK = 200
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        in_clause = ",".join(chunk)
        completed = _sb_get_paginated(
            f"{base}/rest/v1/ownership_completions",
            _sb_headers(service_key),
            {"select": "airtable_record_id",
             "airtable_record_id": f"in.({in_clause})",
             "flow": "in.(A,C)"},
        )
        completed_ids.update(c["airtable_record_id"] for c in completed)
    alerts = []
    for s in old_samples:
        if s["airtable_record_id"] in completed_ids:
            continue
        alerts.append({
            "airtable_record_id":  s["airtable_record_id"],
            "alert_type":          "stuck_in_sampling",
            "verification_status": SELECTED_FOR_BO_QA,
            "qa_assignee":         s["qa_assignee"],
            "qa_status":           None,
            "raw_payload":         s.get("raw_payload") or {},
        })
    return alerts


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    pat = os.environ.get("AIRTABLE_PAT")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key  = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    missing = [n for n, v in (("AIRTABLE_PAT", pat), ("SUPABASE_URL", supabase_url),
                              ("SUPABASE_SERVICE_ROLE_KEY", service_key)) if not v]
    if missing:
        print(f"Missing env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    now_utc = datetime.now(timezone.utc)
    print(f"completion_detector (Flow v2) — {now_utc.isoformat()}")

    records = fetch_flow_records(pat)
    print(f"  Fetched {len(records)} records from Airtable")

    # Bucket records by classification target.
    completion_rows = []
    sampling_rows   = []
    alert_rows      = []
    skipped         = {"skip": 0, "no_attribution": 0}
    by_class        = {"A": 0, "B": 0, "C": 0, "pre_flow": 0,
                       "missing_qa_assignee": 0, "missing_qa_status": 0}

    for rec in records:
        fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
        target, detail = classify(fields)
        if target == "skip":
            skipped["skip"] += 1
            continue
        if target == "completion":
            row, _ = build_completion_row(rec, now_utc, flow=detail)
            if not row:
                skipped["no_attribution"] += 1
                continue
            completion_rows.append(row)
            by_class[detail] += 1
        elif target == "sampling":
            row = build_sampling_row(rec, now_utc)
            if not row:
                skipped["no_attribution"] += 1
                continue
            sampling_rows.append(row)
            by_class["B"] += 1
        elif target == "alert":
            alert_rows.append(build_alert_row(rec, detail))
            by_class[detail] += 1
        elif target == "pre_flow":
            row, _ = build_completion_row(rec, now_utc, flow=None)
            if not row:
                skipped["no_attribution"] += 1
                continue
            completion_rows.append(row)
            by_class["pre_flow"] += 1

    # Stuck-in-sampling detection — query the existing sampling table.
    stuck_alerts = detect_stuck_in_sampling(supabase_url, service_key, now_utc)
    alert_rows.extend(stuck_alerts)
    by_class.setdefault("stuck_in_sampling", 0)
    by_class["stuck_in_sampling"] = len(stuck_alerts)

    # Write to the three tables.
    comp_result = supabase_insert_completions(supabase_url, service_key, completion_rows)
    samp_new    = supabase_insert_samplings(supabase_url, service_key, sampling_rows)
    alert_new, alert_reopened = supabase_upsert_alerts(supabase_url, service_key, alert_rows)

    # Resolve open alerts whose condition no longer applies.
    current_alert_keys = {(a["airtable_record_id"], a["alert_type"]) for a in alert_rows}
    resolved_count = supabase_resolve_alerts(supabase_url, service_key, current_alert_keys, now_utc)

    elapsed = time.time() - t0
    print(f"  Classified:        "
          f"A={by_class['A']} B={by_class['B']} C={by_class['C']} "
          f"pre_flow={by_class['pre_flow']} skipped={skipped['skip']}")
    print(f"  completions:       new={comp_result['inserted']} "
          f"flow_upserted={comp_result['flow_upserted']} "
          f"dup_no_flow={comp_result['duplicates_no_flow']}")
    print(f"  qa_sampling:       new={samp_new}")
    print(f"  alerts:            "
          f"missing_qa_assignee={by_class['missing_qa_assignee']} "
          f"missing_qa_status={by_class['missing_qa_status']} "
          f"stuck={by_class['stuck_in_sampling']} "
          f"(new_inserts={alert_new} reopened={alert_reopened} resolved={resolved_count})")
    print(f"  No attribution:    {skipped['no_attribution']}")
    print(f"  Elapsed:           {elapsed:.1f}s")


if __name__ == "__main__":
    main()
