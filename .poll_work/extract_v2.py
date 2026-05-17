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
    "qa_status_ts": "fld9f125Y5wZt1Ctk",
    "verification_status": "fldYSXHGwZvxXK7s6",
    "status": "flda5KtnUWmkFijJz",
    "company_id_and_name": "fldaMBqa6bEANUPpn",
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

# Self-referential garbage choice on the 'role' singleSelect (sel id selyNKujjYLS03W1A).
ROLE_GARBAGE_VALUES = {"role"}

# Garbage values from old projects on the 'status' singleSelect. Ops will clean up separately.
STATUS_GARBAGE_VALUES = {"Cargill_Bulk_Carrier_2023-2", "Leopard"}


def _name(val):
    """Read .name from a singleSelect / singleCollaborator cell."""
    return val.get("name") if isinstance(val, dict) else None


def _first(val):
    """Read first element from a list-typed cell (multipleCollaborators, multipleRecordLinks)."""
    if isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict):
            return first
    return None


def extract(rec):
    """Flatten an Airtable record's cellValuesByFieldId into a dict with stable keys.

    Every key is always present. Missing → None (or False for the checkbox).
    Garbage values on role/status are mapped to None.
    """
    c = (rec.get("cellValuesByFieldId") or {})
    F = FIELD_IDS

    asg_list = c.get(F["assignee"]) or []
    assignees_all = [v.get("name") for v in asg_list if isinstance(v, dict) and v.get("name")]
    co_first = _first(c.get(F["company_id_and_name"])) or {}

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
        "qa_status_ts":        c.get(F["qa_status_ts"]),
        "company_id":          co_first.get("id"),
        "company_name":        co_first.get("name"),
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

    Detected from the requested_by field (operations convention): if the
    string contains the substring 'sanctions' (case-insensitive), it's a
    sanctions task — which needs 50% QA sampling — otherwise it's a
    standard task at 15%. Empty / None → False (treated as non-sanctions).
    """
    if not requested_by:
        return False
    return "sanctions" in str(requested_by).lower()
