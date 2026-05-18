#!/usr/bin/env python3
"""Detect newly-completed ownership records and stamp them in Supabase.

Runs every poll cycle after the aggregator. Reads Airtable for records in
the two terminal verification_status states, applies the completion rule,
and writes the first-time-seen completion to the ownership_completions
table. The Supabase UNIQUE constraint on airtable_record_id + the
``Prefer: resolution=ignore-duplicates`` header give us first-write-wins:
a record's completed_at is stamped once and never updated.

The dashboard's Hourly Output page reads from this table, so the
``completed_at`` distribution = true intra-day work distribution, not the
batch-assignment clustering you get from Airtable's start_tagging field.

Airtable is strictly read-only — only GET requests against the table.
Supabase writes use the service_role key (server-side only, never in the
browser bundle).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------------
# Airtable choice literals — kept as module-level constants so a rename
# in Airtable can be fixed by editing one place. Note the "be" in
# ``need to be update`` — that's the exact choice name as it appears in
# the live cache (Airtable field choices aren't trimmed/normalised).
# ----------------------------------------------------------------------
TAGGED            = "tagged"
NEED_TO_BE_UPDATE = "need to be update"

# Airtable base / table — same as poll_airtable.py
BASE_ID  = "appHZdfC2sn9MLGFZ"
TABLE_ID = "tblpj9aJP4ExhYCZF"
AIRTABLE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Field IDs we read. Mirror of extract_v2.FIELD_IDS — duplicated here
# (small list) rather than imported to keep this script standalone and
# resistant to extract_v2 refactors.
FLD_IMO                    = "fldqWGr2XDH9BRmtE"
FLD_ASSIGNEE               = "fldT4xElSgcdnqTmy"
FLD_QA_ASSIGNEE            = "fldtQ5HCuU45HOcg4"
FLD_LAST_MODIFIED_BY       = "fldpz9XuDm5xRblSL"
FLD_VERIFICATION_STATUS    = "fldYSXHGwZvxXK7s6"
FLD_COMPANY_ID_AND_NAME    = "fldaMBqa6bEANUPpn"
FLD_COMPANY_NAME_LOOKUP    = "flda5zj1ne1BuJhOm"
FLD_DEAD_VESSEL            = "fldK9xjvBASgXIKlm"
FLD_ADD_NEW_COMPANY        = "fld2wp1Q0GQJjbYdA"
FLD_ROLE                   = "fldnBNNkH7w4rS3fG"
FLD_REQUESTED_BY           = "fldlPkvV6BiE7glLZ"

# Cap pages so a runaway day can't loop forever. Both terminal states
# combined typically yield ~1-3k records on a busy day.
PAGE_CAP = 60


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
# Completion rule — Kelvin's spec.
# ----------------------------------------------------------------------

def is_complete(fields):
    """Apply the completion rule.

    Two terminal verification_status states qualify a record as "complete":

      - ``"tagged"``: complete iff EITHER ``company_id_and_name`` is filled
        OR ``dead_vessel`` is True. Both are valid terminal outcomes — a
        record gets a real company linked, OR it's marked as a dead vessel
        (a zombie ship with no live owner; ops's escape hatch).
      - ``"need to be update"``: complete iff ``add_a_new_company`` is
        non-empty. The agent is proposing a new company not yet in the
        company table, awaiting QA approval to create it.

    Anything else → not complete (still in flight, or not an ownership
    workflow record at all).

    Note on the original spec clause "(NOT is_dead_vessel OR dead_vessel ==
    TRUE)": as written this is tautological (any A: ``NOT A OR A`` is True).
    Interpreting it as ``company_filled OR dead_vessel`` here — i.e. the
    intended meaning is that dead_vessel is an alternative completion
    signal to company linkage, not an additional gate.

    TODO(kelvin): confirm interpretation.
    """
    vs = _name(fields.get(FLD_VERIFICATION_STATUS))
    if vs == TAGGED:
        return _first_link(fields.get(FLD_COMPANY_ID_AND_NAME)) or bool(fields.get(FLD_DEAD_VESSEL))
    if vs == NEED_TO_BE_UPDATE:
        return bool(fields.get(FLD_ADD_NEW_COMPANY))
    return False


# ----------------------------------------------------------------------
# completed_by attribution — last_modified_by → qa_assignee → assignee.
# Records when fallback was used so we can spot data-quality issues with
# unassigned QA reviews.
# ----------------------------------------------------------------------

# Names in this set are non-human last_modified_by values from Airtable
# (system / automation identities). When ``last_modified_by`` matches one
# of these, treat it as null and fall through to qa_assignee → assignee
# so completion is attributed to the human who did the work — not to the
# automation that updated a derived field after the fact. Surfaced during
# dry verify: ~42% of would-insert rows credited "Automations".
NON_HUMAN_LAST_MODIFIED = {"Automations"}


def resolve_completed_by(fields):
    """Return (name, source) where source is 'last_modified_by',
    'qa_assignee', 'assignee', or None when all three are blank.

    ``last_modified_by`` values in NON_HUMAN_LAST_MODIFIED are treated as
    null and fall through to the next link in the chain.
    """
    n = _name(fields.get(FLD_LAST_MODIFIED_BY))
    if n and n not in NON_HUMAN_LAST_MODIFIED:
        return n, "last_modified_by"
    n = _name(fields.get(FLD_QA_ASSIGNEE))
    if n:
        return n, "qa_assignee"
    n = _name(fields.get(FLD_ASSIGNEE))
    if n:
        return n, "assignee"
    return None, None


def build_row(rec, now_utc):
    """Build the Supabase row from an Airtable record. Returns None if the
    record can't be attributed to anyone (completed_by is NOT NULL in the
    schema, so we skip rather than error)."""
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
        # Detector clock — not Airtable's. This is the whole point of the
        # architecture: the moment the system observed completion, NOT a
        # batch-assignment timestamp from Airtable.
        "completed_at":        now_utc.isoformat(),
        "requested_by":        fields.get(FLD_REQUESTED_BY),
        "raw_payload":         fields,
    }
    return row, source


# ----------------------------------------------------------------------
# Airtable fetch
# ----------------------------------------------------------------------

def fetch_terminal_records(pat):
    """GET pages of records currently in either terminal state. Returns
    the flat list of records (across all pages)."""
    headers = {"Authorization": f"Bearer {pat}"}
    formula = f'OR({{verification_status}}="{TAGGED}", {{verification_status}}="{NEED_TO_BE_UPDATE}")'
    params  = {
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
    return out


# ----------------------------------------------------------------------
# Supabase insert
# ----------------------------------------------------------------------

def supabase_insert(supabase_url, service_key, rows):
    """Bulk insert rows with resolution=ignore-duplicates. Returns the list
    of rows the server actually inserted (others were dups). Supabase
    returns the inserted set in the response body when Prefer=return.

    The ``?on_conflict=airtable_record_id`` query parameter is REQUIRED
    for ``Prefer: resolution=ignore-duplicates`` to take effect. Per the
    PostgREST docs (https://docs.postgrest.org/en/v12/references/api/
    preferences.html#prefer-resolution): a plain POST without on_conflict
    treats the Prefer header as a no-op and returns 409 on the first
    UNIQUE-violation. The first detector run after a fresh table works
    either way (no dups possible), so the missing param only surfaces
    starting with run #2.
    """
    if not rows:
        return []
    url = (f"{supabase_url.rstrip('/')}/rest/v1/ownership_completions"
           f"?on_conflict=airtable_record_id")
    headers = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
        # ignore-duplicates: silently skip rows that violate the UNIQUE
        # constraint on airtable_record_id. First-write-wins for completed_at.
        # return=representation so the response body lists the inserted rows.
        "Prefer":        "resolution=ignore-duplicates,return=representation",
    }
    r = requests.post(url, headers=headers, data=json.dumps(rows), timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase insert {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else []


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
    print(f"completion_detector — {now_utc.isoformat()}")

    records  = fetch_terminal_records(pat)
    checked  = len(records)
    complete = 0
    rows     = []
    fallback_counts = {"last_modified_by": 0, "qa_assignee": 0, "assignee": 0, "none": 0}

    for rec in records:
        fields = rec.get("fields") or rec.get("cellValuesByFieldId") or {}
        if not is_complete(fields):
            continue
        complete += 1
        row, source = build_row(rec, now_utc)
        if row is None:
            fallback_counts["none"] += 1
            continue
        fallback_counts[source] += 1
        rows.append(row)

    inserted = supabase_insert(supabase_url, service_key, rows)
    newly_stamped   = len(inserted)
    already_stamped = len(rows) - newly_stamped

    elapsed = time.time() - t0
    print(f"  Checked:           {checked}")
    print(f"  Complete:          {complete}")
    print(f"  Newly stamped:     {newly_stamped}")
    print(f"  Already-stamped:   {already_stamped}")
    print(f"  completed_by source breakdown: {fallback_counts}")
    print(f"  Elapsed:           {elapsed:.1f}s")


if __name__ == "__main__":
    main()
