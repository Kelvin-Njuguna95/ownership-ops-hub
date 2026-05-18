#!/usr/bin/env python3
"""Upload the current dashboard data state to Supabase Storage.

Reads `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from `.env.local`,
then uploads (with upsert) the following to the `dashboard-data` bucket:

  daily_aggregates.json          → daily_aggregates.json
  config/roster.json             → config/roster.json
  ww_audit_log.json              → ww_audit_log.json
  .poll_work/snapshots/*.json    → snapshots/<filename>
  .poll_work/recent_p*.json      → cache/<filename>
  .poll_work/intake_p*.json      → cache/<filename>
  .poll_work/boqa_p*.json        → cache/<filename>

After uploads, writes and uploads `last_sync.json` with the run timestamp
and a file count. Failures on individual files are reported but do not
abort the run — partial uploads are better than none.

Run manually after each polling cycle. Phase F2 will automate this.
"""
import os
import sys
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. "
          "pip install requests --break-system-packages (or use a virtualenv)",
          file=sys.stderr)
    sys.exit(1)
try:
    from dotenv import load_dotenv
except ImportError:
    # python-dotenv is optional — CI provides env vars directly. Make
    # `.env.local` loading a no-op when the package isn't installed.
    def load_dotenv(_path=None):
        return False

HERE = Path(__file__).resolve().parent
EAT  = timezone(timedelta(hours=3))
BUCKET = "dashboard-data"


def upload(session, url, key, path, dest, content_type="application/json"):
    """POST a single file to the Storage REST API with upsert. Returns (ok, size)."""
    try:
        data = path.read_bytes()
    except OSError as e:
        print(f"  [skip] {dest}: {e}")
        return False, 0
    r = session.post(
        f"{url}/storage/v1/object/{BUCKET}/{dest}",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
            "x-upsert": "true",
        },
        data=data,
        timeout=60,
    )
    if 200 <= r.status_code < 300:
        return True, len(data)
    print(f"  [FAIL {r.status_code}] {dest}: {r.text[:140]}")
    return False, 0


def main():
    load_dotenv(HERE / ".env.local")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env.local",
              file=sys.stderr)
        print("Copy .env.local.example to .env.local and fill in your keys.",
              file=sys.stderr)
        sys.exit(1)

    work = HERE / ".poll_work"
    plan = []  # list of (local_path, remote_path)
    for top_level in ("daily_aggregates.json", "ww_audit_log.json"):
        p = HERE / top_level
        if p.exists():
            plan.append((p, top_level))
    roster = HERE / "config" / "roster.json"
    if roster.exists():
        plan.append((roster, "config/roster.json"))

    folder_mappings = [
        (work / "snapshots", "snapshots/", "*.json"),
        (work, "cache/", "recent_p*.json"),
        (work, "cache/", "intake_p*.json"),
        (work, "cache/", "boqa_p*.json"),
    ]
    for local_dir, remote_prefix, glob in folder_mappings:
        if not local_dir.exists():
            continue
        for f in sorted(local_dir.glob(glob)):
            plan.append((f, f"{remote_prefix}{f.name}"))

    if not plan:
        print("Nothing to upload (no files found).")
        sys.exit(0)

    print(f"Uploading {len(plan)} files to {url}/storage/v1/object/{BUCKET}/")
    t0 = time.time()
    session = requests.Session()
    folder_counts = {"root": 0, "config": 0, "snapshots": 0, "cache": 0}
    folder_bytes  = {"root": 0, "config": 0, "snapshots": 0, "cache": 0}
    failed = 0
    for local, dest in plan:
        ok, size = upload(session, url, key, local, dest)
        bucket = "root"
        if dest.startswith("config/"):    bucket = "config"
        elif dest.startswith("snapshots/"): bucket = "snapshots"
        elif dest.startswith("cache/"):   bucket = "cache"
        if ok:
            folder_counts[bucket] += 1
            folder_bytes[bucket]  += size
        else:
            failed += 1

    # last_sync.json — write locally, then upload
    sync_payload = {
        "timestamp": datetime.now(EAT).isoformat(),
        "files_uploaded": sum(folder_counts.values()),
        "files_failed": failed,
        "per_folder": {k: {"count": folder_counts[k], "bytes": folder_bytes[k]}
                       for k in folder_counts},
    }
    last_sync_path = HERE / "last_sync.json"
    last_sync_path.write_text(json.dumps(sync_payload, indent=2))
    ok, _ = upload(session, url, key, last_sync_path, "last_sync.json")
    if not ok:
        failed += 1

    elapsed = time.time() - t0
    total_bytes = sum(folder_bytes.values())
    print()
    print(f"Summary ({elapsed:.1f}s, {total_bytes/1024:.1f} KB total):")
    for k in ("root", "config", "snapshots", "cache"):
        if folder_counts[k]:
            print(f"  {k:10s} {folder_counts[k]:>4} files  {folder_bytes[k]/1024:>8.1f} KB")
    print(f"  last_sync.json {'uploaded' if ok else 'FAILED'}")
    if failed:
        print(f"  {failed} file(s) failed — see [FAIL] lines above")
        sys.exit(2)


if __name__ == "__main__":
    main()
