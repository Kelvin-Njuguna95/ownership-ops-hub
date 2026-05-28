#!/usr/bin/env python3
"""One-off backfill: correct observation-time bucketing in ownership_completions.

Cause C (see the PR #147 diagnosis): ``completed_at`` was stamped with the
detector's clock at first observation, not when the work actually happened.
When polls failed and a later run recovered, large batches were bulk-stamped
into a single microsecond, emptying the true hour on the Hourly Output chart
and dumping the work into the next hour. This script recomputes ``completed_at``
from each row's ``raw_payload`` using the SAME ``resolve_completion_ts`` logic
the detector now uses going forward, so history and new rows agree.

DRY-RUN BY DEFAULT — prints what it would change and writes nothing. Pass
``--apply`` to write. Idempotent: recomputes the same value each run and skips
rows already at their real time, so it is safe to re-run.

A row is flagged when its ``completed_at`` is shared by >= --min-cluster rows
(the bulk-stamp signature), OR the resolved real time differs from the stored
``completed_at`` by more than --drift-minutes — and recomputation yields a
different, real (non-fallback) timestamp.

Env (from .env.local, gitignored per CLAUDE.md):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. Reads work with either key;
  --apply PATCH needs the service-role key. The script errors gracefully if the
  vars are absent.

  Usage:
    python3 .poll_work/backfill_completed_at.py                 # dry-run, all rows
    python3 .poll_work/backfill_completed_at.py --since 2026-05-27
    python3 .poll_work/backfill_completed_at.py --apply         # write
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Share the EXACT resolution logic with the detector so history matches new rows.
from completion_detector import resolve_completion_ts  # noqa: E402

TABLE = "ownership_completions"
PAGE = 1000

# The two known bulk events from the PR #147 diagnosis — shown verbatim in the
# dry-run so reviewers can eyeball recovery.
KNOWN_BULK_EVENTS = (
    "2026-05-27T15:24:05.888033+00:00",
    "2026-05-28T12:55:17.148266+00:00",
)
# Sentinel for "resolve found no real date field" — distinguishable from any
# real ISO timestamp, so we can leave the observation clock untouched.
_NO_DATE = "\x00no-date"


def _load_env_local():
    """Populate SUPABASE_* from .env.local if not already set, so the dry-run
    runs without the caller exporting vars by hand. Existing env wins."""
    for base in (HERE, HERE.parent):
        env = base / ".env.local"
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _headers(key):
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def _get_paginated(base, key, params):
    """GET with Range pagination (db-max-rows caps responses at 1000)."""
    out, offset = [], 0
    while True:
        h = dict(_headers(key))
        h["Range"] = f"{offset}-{offset + PAGE - 1}"
        h["Range-Unit"] = "items"
        r = requests.get(f"{base}/rest/v1/{TABLE}", headers=h, params=params, timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"GET {r.status_code}: {r.text[:300]}")
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
        if offset > 2_000_000:
            raise RuntimeError("runaway pagination (>2M rows)")
    return out


def _parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _hour_key(iso):
    """UTC 'YYYY-MM-DDTHH' bucket (handles both Z and +00:00 suffixes)."""
    try:
        return _parse(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    except Exception:
        return "??"


def _minutes_between(a, b):
    try:
        return abs((_parse(b) - _parse(a)).total_seconds()) / 60.0
    except Exception:
        return 0.0


def find_affected(rows, min_cluster, drift_minutes):
    """Return [(row, new_ts)] for rows whose completed_at is detector-bucketed.

    Affected = (completed_at shared by >= min_cluster rows) OR
    (|resolved_real - stored| > drift_minutes), and recomputation gives a
    different, real timestamp. Rows with no resolvable date field, or already
    at their real time, are skipped (idempotent)."""
    cluster_counts = Counter(r["completed_at"] for r in rows)
    affected = []
    for r in rows:
        old = r["completed_at"]
        new = resolve_completion_ts(r.get("verification_status"),
                                    r.get("raw_payload") or {}, _NO_DATE)
        if new == _NO_DATE or new == old:
            continue
        in_cluster = cluster_counts[old] >= min_cluster
        big_drift = _minutes_between(old, new) > drift_minutes
        if in_cluster or big_drift:
            affected.append((r, new))
    return affected


def _print_dry_run(rows, affected, min_cluster, drift_minutes):
    print(f"  scanned {len(rows)} rows; flagged {len(affected)} for correction")

    # Per-hour movement: how many rows leave each hour and enter each hour.
    out_of, into = Counter(), Counter()
    for r, new in affected:
        out_of[_hour_key(r["completed_at"])] += 1
        into[_hour_key(new)] += 1
    moved_hours = sorted(set(out_of) | set(into))
    print("\n  Per-hour net diff (UTC):")
    print(f"    {'hour':<16}{'OUT':>7}{'IN':>7}{'NET':>7}")
    for h in moved_hours:
        o, i = out_of.get(h, 0), into.get(h, 0)
        print(f"    {h+':00':<16}{-o:>7}{i:>7}{i - o:>+7}")

    # Verbatim before/after for the two known bulk events.
    by_old = {}
    for r, new in affected:
        by_old.setdefault(r["completed_at"], []).append((r, new))
    for ev in KNOWN_BULK_EVENTS:
        grp = by_old.get(ev, [])
        print(f"\n  Bulk event {ev}: {len(grp)} rows flagged")
        for r, new in grp[:20]:
            print(f"    {r['airtable_record_id']}  vs={r.get('verification_status')!r:<22} "
                  f"{r['completed_at']}  ->  {new}")
        if len(grp) > 20:
            print(f"    ... (+{len(grp) - 20} more)")

    print("\n  DRY-RUN: no writes performed. Re-run with --apply to write.")


def _apply(base, key, affected, batch_size):
    patched, failed = 0, 0
    for i in range(0, len(affected), batch_size):
        chunk = affected[i:i + batch_size]
        for r, new in chunk:
            url = f"{base}/rest/v1/{TABLE}?id=eq.{r['id']}"
            resp = requests.patch(url, headers={**_headers(key), "Prefer": "return=minimal"},
                                  data=json.dumps({"completed_at": new}), timeout=60)
            if resp.status_code in (200, 204):
                patched += 1
            else:
                failed += 1
                print(f"  FAIL id={r['id']}: {resp.status_code} {resp.text[:150]}",
                      file=sys.stderr)
        print(f"  [{datetime.now(timezone.utc).isoformat()}] "
              f"batch {i // batch_size + 1}: patched={patched} failed={failed} "
              f"of {len(affected)}")
    print(f"  DONE: patched {patched}, failed {failed}, total flagged {len(affected)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run)")
    ap.add_argument("--since", help="only rows with completed_at >= ISO date (e.g. 2026-05-27)")
    ap.add_argument("--until", help="only rows with completed_at < ISO date")
    ap.add_argument("--min-cluster", type=int, default=10,
                    help="rows sharing one completed_at to count as a bulk-stamp (default 10)")
    ap.add_argument("--drift-minutes", type=int, default=30,
                    help="resolved-vs-stored gap (min) that flags a non-clustered row (default 30)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="PATCH rows per progress batch in --apply mode (default 100)")
    args = ap.parse_args()

    _load_env_local()
    base = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
              "(export them or add them to .env.local).", file=sys.stderr)
        sys.exit(1)
    base = base.rstrip("/")

    params = {"select": "id,airtable_record_id,verification_status,completed_at,raw_payload",
              "order": "completed_at.asc"}
    if args.since and args.until:
        params["and"] = f"(completed_at.gte.{args.since},completed_at.lt.{args.until})"
    elif args.since:
        params["completed_at"] = f"gte.{args.since}"
    elif args.until:
        params["completed_at"] = f"lt.{args.until}"

    print(f"[{datetime.now(timezone.utc).isoformat()}] backfill_completed_at  "
          f"mode={'APPLY' if args.apply else 'DRY-RUN'}  since={args.since} until={args.until}  "
          f"min_cluster={args.min_cluster} drift_min={args.drift_minutes}")

    rows = _get_paginated(base, key, params)
    affected = find_affected(rows, args.min_cluster, args.drift_minutes)

    if not args.apply:
        _print_dry_run(rows, affected, args.min_cluster, args.drift_minutes)
        return

    print(f"  scanned {len(rows)} rows; APPLYING {len(affected)} corrections "
          f"in batches of {args.batch_size}")
    _apply(base, key, affected, args.batch_size)


if __name__ == "__main__":
    main()
