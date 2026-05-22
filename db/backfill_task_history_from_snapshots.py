#!/usr/bin/env python3
"""One-time backfill — recover past tasks into ownership_task_history from the
dashboard's own daily snapshots.

PR #70 taught the aggregator to record every task into the
ownership_task_history ledger going forward. Tasks that completed and aged out
of the rolling poll cache BEFORE PR #70 shipped were never written to it.

The dashboard has saved a full daily snapshot to Supabase Storage
(snapshots/<date>.json) since 2026-05-17. Each snapshot already contains a
complete `tasks_all` list. This script harvests those snapshots and writes one
ledger row per task per snapshot day.

Safety:
  * Reads ONLY from Supabase Storage. Does not touch Airtable.
  * Skips today's snapshot — the live pipeline (PR #70) owns today's rows.
    Harvested rows are always past-dated; the aggregator only writes
    today-dated rows; the two can never collide.
  * Inserts with Prefer: resolution=ignore-duplicates — never overwrites.
  * Idempotent — running it twice inserts nothing the second time.

One-time script, committed for the record; NOT wired into the cron.
Env required: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

EAT = timezone(timedelta(hours=3))
HARVEST_DAYS_BACK = 30      # how far back to look for snapshot files
BATCH = 500                 # rows per Supabase POST

# Supabase Storage bucket holding daily_aggregates.json + snapshots/.
# Matches the literal used by .poll_work/download_state.py and
# sync_to_supabase.py (both set BUCKET = "dashboard-data").
STORAGE_BUCKET = "dashboard-data"


def download_snapshot(sb_url, sb_key, date_str):
    """Download snapshots/<date>.json from Supabase Storage. Returns the parsed
    dict, or None if the file does not exist.

    Uses the public-object read path (/storage/v1/object/public/{bucket}/...),
    matching .poll_work/download_state.py — the dashboard-data bucket is public,
    so this is the proven download pattern. Auth headers are passed too (a
    harmless no-op on a public object, and a fallback if access ever tightens).
    """
    url = (f"{sb_url.rstrip('/')}/storage/v1/object/public/"
           f"{STORAGE_BUCKET}/snapshots/{date_str}.json")
    headers = {"apikey": sb_key, "Authorization": f"Bearer {sb_key}"}
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code == 200:
        return r.json()
    if r.status_code in (400, 404):
        return None
    print(f"  WARN snapshot {date_str}: HTTP {r.status_code} {r.text[:200]}")
    return None


def task_to_row(t, snapshot_date, computed_at):
    """Map a snapshot tasks_all entry to an ownership_task_history row. Uses
    .get() throughout so older snapshots with a thinner task shape (e.g. no
    qa_reviewers, which PR #70 added) simply yield NULLs."""
    return {
        "task_name":           t.get("name"),
        "snapshot_date":       snapshot_date,
        "computed_at":         computed_at,
        "is_sanctions":        t.get("is_sanctions"),
        "total_records":       t.get("total_records_in_cache"),
        "date_first_seen":     t.get("date_first_seen"),
        "date_last_modified":  t.get("date_last_modified"),
        "valid_pct":           t.get("valid_pct"),
        "is_completed":        t.get("is_completed"),
        "end_time":            t.get("end_time"),
        "tat_hours":           t.get("tat_hours"),
        "status_distribution": t.get("status_distribution"),
        "properly_completed":  t.get("properly_completed"),
        "with_company":        t.get("with_company"),
        "without_company":     t.get("without_company"),
        "dead_vessels":        t.get("dead_vessels"),
        "with_reminder":       t.get("with_reminder"),
        "completed":           t.get("completed"),
        "qa_reviewed":         t.get("qa_reviewed"),
        "qa_changed":          t.get("qa_changed"),
        "qa_coverage_pct":     t.get("qa_coverage_pct"),
        "agents_worked":       t.get("agents_worked"),
        "teams_worked":        t.get("teams_worked"),
        "qa_reviewers":        t.get("qa_reviewers"),
        "flags":               t.get("flags"),
        "source":              "snapshot-harvest",
    }


def main():
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (sb_url and sb_key):
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    if STORAGE_BUCKET.startswith("<<<"):
        print("ERROR: set STORAGE_BUCKET to the real bucket name first")
        sys.exit(1)

    today = datetime.now(EAT).date()
    today_iso = today.isoformat()

    rows = []
    snaps_found = 0
    # Start at 1 day back so today's snapshot is never harvested.
    for back in range(1, HARVEST_DAYS_BACK + 1):
        ds = (today - timedelta(days=back)).isoformat()
        snap = download_snapshot(sb_url, sb_key, ds)
        if not snap:
            continue
        snaps_found += 1
        snap_date = snap.get("date") or ds
        if snap_date == today_iso:
            continue   # defensive — never harvest a today-dated snapshot
        computed_at = snap.get("computed_at")
        kept = 0
        for t in (snap.get("tasks_all") or []):
            name = t.get("name")
            if not name or name == "(no task name)":
                continue
            rows.append(task_to_row(t, snap_date, computed_at))
            kept += 1
        print(f"  {ds}: {kept} tasks")

    print(f"\n{snaps_found} snapshot(s) found, {len(rows)} task-rows to insert")
    if not rows:
        print("Nothing to harvest.")
        return

    endpoint = (f"{sb_url.rstrip('/')}/rest/v1/ownership_task_history"
                f"?on_conflict=task_name,snapshot_date")
    headers = {
        "apikey":        sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type":  "application/json",
        # ignore-duplicates: never overwrite an existing row. return=
        # representation makes PostgREST return only the rows actually
        # inserted, so we can count true new inserts.
        "Prefer":        "resolution=ignore-duplicates,return=representation",
    }
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        r = requests.post(endpoint, headers=headers,
                          data=json.dumps(batch), timeout=120)
        if r.status_code not in (200, 201):
            print(f"ERROR: Supabase HTTP {r.status_code}: {r.text[:400]}")
            sys.exit(1)
        n = len(r.json())
        inserted += n
        print(f"  batch {i // BATCH + 1}: {n} new "
              f"({len(batch) - n} already present)")

    print(f"\nDone. {inserted} row(s) recovered into ownership_task_history; "
          f"{len(rows) - inserted} already present (idempotent).")


if __name__ == "__main__":
    main()
