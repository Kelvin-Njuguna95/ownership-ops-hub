#!/usr/bin/env python3
"""One-off backfill: correct 17 drifted ownership_completions rows (2026-05-20).

Context
-------
Before PR #44 the detector attributed ownership_completions.completed_by via
``last_modified_by -> qa_assignee -> assignee``. For these 17 records James
Maina was the tagger (assignee) but Selah Nabiswa last-edited them, so they
were credited to "Selah Nabiswa". PR #44 fixed attribution for NEW rows; this
script corrects the 17 already-written rows in place.

The 17 ``airtable_record_id`` values are copied verbatim from the audit doc:
``docs/hourly_audit_james_maina_2026-05-20.md`` (Step 3, "Affected record IDs").

Safety
------
- Touches ONLY the ``completed_by`` column, ONLY the ``ownership_completions``
  table, ONLY the 17 hardcoded record ids. Never DELETEs.
- The corrected value is DERIVED per-row from that row's own ``raw_payload``
  (first assignee — same rule as the detector's ``resolve_completed_by``), not
  hardcoded — so the script is self-verifying. ``EXPECTED_TAGGER`` is used only
  as a sanity gate: a row whose derived tagger isn't that is skipped with a
  warning.
- Idempotent: a row already showing the derived tagger is skipped, so a second
  run (or a run after the live cron already corrected a row) is a no-op.
- Dry-run by default. ``--apply`` performs the PATCHes. Run the dry-run, have it
  reviewed, THEN run ``--apply`` yourself.

Usage
-----
    set -a && . ./.env.local && set +a
    python3 db/backfill_james_attribution_2026-05-20.py            # dry-run
    python3 db/backfill_james_attribution_2026-05-20.py --apply    # writes
"""
import os
import sys

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

TABLE = "ownership_completions"
# Airtable assignee field id (collaborator) inside raw_payload — mirrors
# completion_detector.FLD_ASSIGNEE. The tagger is the FIRST element.
FLD_ASSIGNEE = "fldT4xElSgcdnqTmy"

# Sanity gate ONLY — per the audit all 17 must resolve to this tagger.
# (The value WRITTEN is the per-row derived name, not this literal.)
EXPECTED_TAGGER = "JAMES MAINA"

# The 17 drifted record ids — verbatim from docs/hourly_audit_james_maina_2026-05-20.md (Step 3).
RECORD_IDS = [
    "recqntpMAEpDl6tbS", "receNPWzmI1w39sbl", "recMyh6nXnCmRQ4NE", "reclVsXKDT4bPYZbU",
    "recCoCZBkCn4XPhRn", "rec29nYVgvpGl4Sch", "rech4u4MBuEZ1c9PM", "recwyGnfQ9QroDSsL",
    "recNP121qQgFNEzMa", "recMeBhQnkRMwVsP6", "recNQLfoLeXqWkLIv", "recNajaoGQbTgjx3m",
    "recz35lf2iiD3VDQG", "reczDauUmBdfhZ98R", "rec7izaSYTWZz5wTf", "rec1quZVqilZA1VdT",
    "rec0fWqhtV4rw1Paw",
]
assert len(RECORD_IDS) == 17 and len(set(RECORD_IDS)) == 17, "expected 17 unique ids"


def _name(val):
    """First collaborator name from an Airtable cell. Mirrors the detector's
    _name(): handles str / dict / list-of-either, returning the FIRST."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name")
    if isinstance(val, list) and val:
        return _name(val[0])
    return None


def derive_tagger(raw_payload):
    """The tagger = first assignee in the row's own raw_payload (or None)."""
    if not isinstance(raw_payload, dict):
        return None
    return _name(raw_payload.get(FLD_ASSIGNEE))


def main():
    apply = "--apply" in sys.argv[1:]
    try:
        base = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    except KeyError as e:
        print(f"Missing env var {e}. Run: set -a && . ./.env.local && set +a", file=sys.stderr)
        sys.exit(1)

    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    url = f"{base}/rest/v1/{TABLE}"
    in_list = ",".join(RECORD_IDS)

    # Single GET for all 17 (filtered to exactly the known ids).
    r = requests.get(url, headers=h, params={
        "select": "airtable_record_id,completed_by,raw_payload",
        "airtable_record_id": f"in.({in_list})",
    }, timeout=60)
    if r.status_code != 200:
        print(f"GET failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
        sys.exit(1)
    by_id = {row["airtable_record_id"]: row for row in r.json()}

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Backfill {TABLE}.completed_by  [{mode}] ===")
    print(f"{'record_id':20} {'current completed_by':22} {'derived tagger':16} action")
    print("-" * 78)

    to_update = []   # (record_id, derived)
    skipped = 0
    for rid in RECORD_IDS:
        row = by_id.get(rid)
        if row is None:
            print(f"{rid:20} {'(row not found)':22} {'-':16} skip(not in table)")
            skipped += 1
            continue
        cur = row.get("completed_by")
        derived = derive_tagger(row.get("raw_payload"))

        # Gate (c): derived must be non-empty.
        if not derived:
            print(f"{rid:20} {str(cur):22} {'(none)':16} skip(no assignee in raw_payload)")
            skipped += 1
            continue
        # Sanity gate: derived must be the expected tagger for this backfill.
        if derived.strip().upper() != EXPECTED_TAGGER:
            print(f"{rid:20} {str(cur):22} {derived:16} skip(WARN: derived != {EXPECTED_TAGGER})")
            skipped += 1
            continue
        # Gate (b): idempotent — already correct.
        if cur == derived:
            print(f"{rid:20} {str(cur):22} {derived:16} skip(already correct)")
            skipped += 1
            continue
        print(f"{rid:20} {str(cur):22} {derived:16} WILL UPDATE")
        to_update.append((rid, derived))

    print("-" * 78)
    print(f"{len(to_update)} to update, {skipped} skipped.")

    if not apply:
        print("\nDRY-RUN only. Re-run with --apply to write these changes.")
        return

    # --- APPLY: PATCH each row individually, filtered to its exact id. ---
    print("\nApplying…")
    done = 0
    for rid, derived in to_update:
        pr = requests.patch(
            url,
            headers={**h, "Content-Type": "application/json", "Prefer": "return=representation"},
            params={"airtable_record_id": f"eq.{rid}"},
            json={"completed_by": derived},
            timeout=60,
        )
        if pr.status_code not in (200, 204):
            print(f"  PATCH {rid} FAILED: {pr.status_code} {pr.text[:200]}", file=sys.stderr)
            continue
        done += 1

    # Verification re-GET.
    r2 = requests.get(url, headers=h, params={
        "select": "airtable_record_id,completed_by",
        "airtable_record_id": f"in.({in_list})",
    }, timeout=60)
    after = {row["airtable_record_id"]: row.get("completed_by") for row in r2.json()}
    print("\n=== Verification (post-apply) ===")
    for rid in RECORD_IDS:
        print(f"{rid:20} completed_by = {after.get(rid)!r}")

    print(f"\nBackfilled {done} rows, skipped {skipped}.")


if __name__ == "__main__":
    main()
