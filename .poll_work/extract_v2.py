"""Field extraction for relations_support records.

Single source of truth for Airtable field-ID lookups, shared by the poller
and the aggregator. Always read by field ID, never by display name — display
names in this base have casing / punctuation drift (e.g. 'add a new company',
'WW QA assignee') and other tools depend on them, so do not rename in Airtable.
"""

FIELD_IDS = {
    "imo": "fldqWGr2XDH9BRmtE",
    "assignee": "fldT4xElSgcdnqTmy",
    "qa_assignee": "fldtQ5HCuU45HOcg4",
    "ww_qa_assignee": "fldXyIOPWBTYvowv7",
    "qa_status": "fldpTTs63XmNYNPww",
    "ww_qa": "fldlsbwX7KnDRPn5j",
    "last_modified": "fld0hZzdjksTCKJ09",
    "last_modified_by": "fldpz9XuDm5xRblSL",
    "start_tagging_date": "fld7fm1PknPk1UueW",
    "done_selected_time": "fldbcTW2CD2HjejGN",
    "valid_selected_time": "fldu7c6IVtQ7MucWP",
    "qa_status_ts": "fld9f125Y5wZt1Ctk",
    "verification_status": "fldYSXHGwZvxXK7s6",
    "status": "flda5KtnUWmkFijJz",
    "company_id_and_name": "fldaMBqa6bEANUPpn",
    # Dedicated lookup fields on top of company_id_and_name. The raw Airtable
    # REST API returns the link field as a list of bare record IDs
    # (`["recXXX"]`), so company_id / company_name can't be recovered from it
    # in the F2 poller path. These lookups follow the link and expose the
    # linked record's primary-key id (number) and name (string) directly.
    "company_id_lookup":   "fld8BxCITngW9PDtX",
    "company_name_lookup": "flda5zj1ne1BuJhOm",
    "is_change": "fldGbrzM7z05OM6wb",
    "created": "fldZL6JmYMlFIhCLl",
    "comment": "fldr79tP2GV0OztrZ",
    "reminder": "fldmHBZbg57FWvrcS",
    "source_flow": "fldp2zKCY3TfysT1C",
    "add_new_company": "fld2wp1Q0GQJjbYdA",
    "requested_by": "fldlPkvV6BiE7glLZ",
    "valid_done_by_bo": "fldph8vgQ40dYQlXt",
    "role": "fldnBNNkH7w4rS3fG",
    "start_date": "fldUNsja6NUuDtJvN",
    "dead_vessel": "fldK9xjvBASgXIKlm",
}

# verification_status choice 'Selected for BO QA ' has a trailing space — preserve exactly.
SELECTED_FOR_BO_QA = "Selected for BO QA "
SELECTED_FOR_WW_QA = "Selected for WW QA"

# Canonical 11 comment values (the design doc had 12 in error).
COMMENT_VALUES = (
    "suspected zombie vessel",
    "IMO searched but not found",
    "IMO not found, has positional data",
    "IMO Never Existed, No positional data",
    "IMO not found on Nexis/Equasis, No positional data",
    "IMO not found on Nexis/Equasis/WW",
    "IMO found, No positional data on WW",
    "No Longer updated by (LRF) IHSF",
    "Role not found",
    "Cancelled before construction",
    "Document Not Available",
)

# The five comment values that mean the vessel's IMO could not be located. When
# one is set, a blank company id/name is correct (no IMO => nothing to attach), so
# the record is legitimately complete — NOT a company gap. Sliced from
# COMMENT_VALUES (indices 1–5) so the two stay in sync; asserted below to catch any
# reordering of COMMENT_VALUES.
NO_IMO_FOUND_COMMENTS = frozenset(COMMENT_VALUES[1:6])
assert NO_IMO_FOUND_COMMENTS == {
    "IMO searched but not found",
    "IMO not found, has positional data",
    "IMO Never Existed, No positional data",
    "IMO not found on Nexis/Equasis, No positional data",
    "IMO not found on Nexis/Equasis/WW",
}, "NO_IMO_FOUND_COMMENTS drifted from COMMENT_VALUES — re-check the slice indices"

# Self-referential garbage choice on the 'role' singleSelect (sel id selyNKujjYLS03W1A).
ROLE_GARBAGE_VALUES = {"role"}

# Garbage values from old projects on the 'status' singleSelect. Ops will clean up separately.
STATUS_GARBAGE_VALUES = {"Cargill_Bulk_Carrier_2023-2", "Leopard"}


def _name(val):
    """Read a 'name' value from a cell, transparent across both record shapes.

    The Cowork MCP wrapper expanded singleSelect / singleCollaborator cells
    into dicts (``{"id": ..., "name": ...}``) and linked-record / lookup cells
    into lists of dicts. The raw Airtable REST API returns the same logical
    values in flatter shapes: singleSelects as plain strings, linked-records
    as lists of bare record-ID strings, lookups as lists of plain values.

    Handles, in priority order:
      - None              → None
      - str               → the string (raw REST singleSelect / lookup-of-text)
      - dict              → ``val.get("name")`` (Cowork singleSelect / collaborator)
      - list (non-empty)  → recurse on the first element (multipleCollaborators,
                            multipleRecordLinks under either shape, lookup arrays)
      - anything else (number, bool, etc.) → None
    """
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name")
    if isinstance(val, list) and val:
        return _name(val[0])
    return None


def _id(val):
    """Read an 'id' value from a cell, transparent across both record shapes.

    Mirror of :func:`_name` for the id side of the pair. Accepts numbers
    (lookup fields often return numeric record IDs like 82782) and bare
    record-ID strings (raw REST link fields return ``["recXXX"]``).
    """
    if val is None:
        return None
    if isinstance(val, (str, int, float)):
        return val if val != "" else None
    if isinstance(val, dict):
        return val.get("id")
    if isinstance(val, list) and val:
        return _id(val[0])
    return None


def extract(rec):
    """Flatten an Airtable record into a dict with stable keys.

    Accepts both record shapes:
      - ``cellValuesByFieldId`` — what the Cowork MCP wrapper returns.
      - ``fields``              — what the raw Airtable REST API returns
        (via ``poll_airtable.py``) when ``returnFieldsByFieldId=true``.
    Both are dicts of {field_id: value}, so the rest of the function is
    schema-agnostic. Per-field shapes differ between the two paths — see
    :func:`_name` / :func:`_id` for the unification logic.

    Every key is always present in the output. Missing → None (or False
    for the checkbox fields). Garbage values on role/status are mapped to None.
    """
    c = (rec.get("cellValuesByFieldId") or rec.get("fields") or {})
    F = FIELD_IDS

    # Multi-assignee — list under both shapes, but element type differs
    # (dict for collaborators in both, string only in some lookup shapes).
    # _name handles both transparently.
    asg_raw = c.get(F["assignee"])
    if asg_raw is None:
        asg_list = []
    elif isinstance(asg_raw, list):
        asg_list = asg_raw
    else:
        asg_list = [asg_raw]
    assignees_all = []
    for v in asg_list:
        n = _name(v)
        if n:
            assignees_all.append(n)

    # company_id / company_name: prefer the dedicated lookup fields (canonical
    # numeric id + real company name); fall back to the link field. On the
    # Cowork MCP shape the link returns ``[{"id":"recCo1","name":"Acme"}]`` so
    # fallback gives a sensible answer; on raw REST without lookups configured
    # the fallback yields the bare record-id string for BOTH fields — a
    # degraded edge case that doesn't fire in production (lookups auto-populate
    # from the link).
    co_id   = _id(c.get(F["company_id_lookup"]))   or _id(c.get(F["company_id_and_name"]))
    co_name = _name(c.get(F["company_name_lookup"])) or _name(c.get(F["company_id_and_name"]))

    role = _name(c.get(F["role"]))
    if role in ROLE_GARBAGE_VALUES:
        role = None

    status = _name(c.get(F["status"]))
    if status in STATUS_GARBAGE_VALUES:
        status = None

    return {
        "imo":                 c.get(F["imo"]),
        # `assignee` is the PRIMARY assignee (first in the list) — preserved for backward compat.
        # `assignees` is the full list; new code that needs multi-attribution reads this.
        "assignee":            assignees_all[0] if assignees_all else None,
        "assignees":           assignees_all,
        "qa_assignee":         _name(c.get(F["qa_assignee"])),
        "ww_qa_assignee":      _name(c.get(F["ww_qa_assignee"])),
        "last_modified_by":    _name(c.get(F["last_modified_by"])),
        "start_tagging":       c.get(F["start_tagging_date"]),
        "start_date":          c.get(F["start_date"]),
        "created":             c.get(F["created"]),
        "last_modified":       c.get(F["last_modified"]),
        "done_selected_time":  c.get(F["done_selected_time"]),
        "valid_selected_time": c.get(F["valid_selected_time"]),
        "qa_status_ts":        c.get(F["qa_status_ts"]),
        "company_id":          co_id,
        "company_name":        co_name,
        "verification_status": _name(c.get(F["verification_status"])),
        "qa_status":           _name(c.get(F["qa_status"])),
        "ww_qa":               _name(c.get(F["ww_qa"])),
        "status":              status,
        "is_change":           c.get(F["is_change"]),
        "comment":             _name(c.get(F["comment"])),
        "reminder":            c.get(F["reminder"]),
        "source_flow":         c.get(F["source_flow"]),
        "add_new_company":     c.get(F["add_new_company"]),
        "requested_by":        c.get(F["requested_by"]),
        "valid_done_by_bo":    bool(c.get(F["valid_done_by_bo"])),
        "role":                role,
        "dead_vessel":         bool(c.get(F["dead_vessel"])),
    }


def resolve_qa(info):
    """QA attribution: prefer qa_assignee, fall back to last_modified_by.

    Returns (qa_name, used_fallback). used_fallback is True iff qa_assignee
    was blank but last_modified_by populated the answer — the poller counts
    these so we can spot data-quality issues with unassigned QA reviews.
    """
    if info.get("qa_assignee"):
        return info["qa_assignee"], False
    if info.get("last_modified_by"):
        return info["last_modified_by"], True
    return None, False


def is_properly_completed(info):
    """Operations definition of 'properly completed':

    - ``start_date`` is filled, AND
    - ``company_id`` is filled OR ``dead_vessel`` is True.

    Dead vessel is the operational alternative when no company exists to link.
    Either path satisfies the second clause; ``start_date`` is mandatory.
    """
    if not info.get("start_date"):
        return False
    return bool(info.get("company_id")) or info.get("dead_vessel") is True


def is_sanctions(requested_by):
    """True iff requested_by names a sanctions task.

    Operational rule (clarified by Kelvin): the client uploads sanctions
    tasks with 'sanction'/'sanctions' in the task name. Detected from the
    requested_by field by matching the singular substring 'sanction'
    (case-insensitive) — 'sanctions' contains 'sanction', so both forms
    match. Examples: 'SanctionChangeIntel20May2026' → True,
    'CargoSanctionsCheck17Apr2026' → True, 'CargoChangeIntel20May2026' →
    False. A sanctions task needs 50% QA sampling; a standard task 15%.
    Empty / None → False (treated as non-sanctions).

    This is the single dashboard-wide definition of a sanctions task — it
    drives the QA-sampling cohort metrics, the Tasks-page badge, and the
    Pipeline & Lead Time cohort split.
    """
    if not requested_by:
        return False
    return "sanction" in str(requested_by).lower()
