#!/usr/bin/env python3
"""Phase F2 pre-poll state hydration.

Pulls the latest state files from the Supabase Storage ``dashboard-data``
bucket into the local working tree so the next poll cycle has continuity.
Without this, each CI run would start with an empty local state and
overwrite the remote files with thinner content (losing the legacy
aggregate block, dropping historical snapshots from the local view, etc.).

Downloads:
  daily_aggregates.json         → ./daily_aggregates.json
  ww_audit_log.json             → ./ww_audit_log.json
  last_sync.json                → ./last_sync.json
  snapshots/<YYYY-MM-DD>.json   → ./.poll_work/snapshots/<YYYY-MM-DD>.json

Supabase is read-only here — only GET / POST(list) requests are issued.
"""
import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

HERE   = Path(__file__).resolve().parent
ROOT   = HERE.parent
BUCKET = "dashboard-data"
RETRIES = 5    # attempts per request before giving up
TIMEOUT = 60   # per-request timeout (s)


def _request_with_retry(session, method, url, **kwargs):
    """HTTP call with exponential backoff on transient failures. The hydrate
    step runs before the poll; a single transient Supabase Storage timeout used
    to fail the entire cycle. Retries on network exceptions and on 429/5xx;
    returns the Response for any other status (incl. 200/404) so callers keep
    their existing 200/404 handling. Honours Retry-After on 429."""
    import time
    last = None
    for attempt in range(RETRIES):
        try:
            r = session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.exceptions.RequestException as e:
            last = e
            wait = 2 ** attempt
            print(f"  transient {type(e).__name__} on {method} {url.rsplit('/', 1)[-1]}; "
                  f"retry in {wait}s (attempt {attempt + 1}/{RETRIES})", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            print(f"  [{r.status_code}] on {method} {url.rsplit('/', 1)[-1]}; "
                  f"retry in {wait}s (attempt {attempt + 1}/{RETRIES})", file=sys.stderr)
            time.sleep(wait)
            last = RuntimeError(f"{r.status_code}: {r.text[:160]}")
            continue
        return r            # 200, 404, etc. → hand back to caller unchanged
    raise RuntimeError(f"{method} {url} failed after {RETRIES} attempts: {last}")


def _get(session, url, key, remote_path, local_path):
    """Download a single object from the public-bucket URL to local_path.
    Returns (ok, size_bytes). 404 → silently skip (file may not exist yet)."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    r = _request_with_retry(
        session, "GET",
        f"{url}/storage/v1/object/public/{BUCKET}/{remote_path}",
    )
    if r.status_code == 200:
        local_path.write_bytes(r.content)
        return True, len(r.content)
    if r.status_code == 404:
        return False, 0
    print(f"  [{r.status_code}] {remote_path}: {r.text[:160]}", file=sys.stderr)
    return False, 0


def _list_prefix(session, url, key, prefix):
    """List objects under a Supabase Storage prefix. Returns a list of names
    (filenames only, prefix stripped). Service-role key required for list().

    Paginated: the list endpoint caps each response at ``limit`` objects, and
    snapshots/ grows one file per day — a single page would silently truncate
    at 1,000. Keep requesting with offset += 1000 until a short page."""
    names = []
    offset = 0
    while True:
        r = _request_with_retry(
            session, "POST",
            f"{url}/storage/v1/object/list/{BUCKET}",
            headers={
                "Authorization": f"Bearer {key}",
                "apikey":        key,
                "Content-Type":  "application/json",
            },
            json={"prefix": prefix, "limit": 1000, "offset": offset},
        )
        if r.status_code != 200:
            print(f"  list({prefix}) [{r.status_code}]: {r.text[:200]}", file=sys.stderr)
            return names
        page = r.json()
        names.extend(o["name"] for o in page if o.get("name"))
        if len(page) < 1000:   # short page = last page (raw count, pre-filter)
            return names
        offset += 1000


def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in env",
              file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    print(f"Downloading state from {url}/storage/v1/object/public/{BUCKET}/")
    n_ok    = 0
    n_miss  = 0
    n_bytes = 0

    # Root-level state files
    for name in ("daily_aggregates.json", "ww_audit_log.json", "last_sync.json"):
        ok, size = _get(session, url, key, name, ROOT / name)
        if ok:
            n_ok += 1
            n_bytes += size
            print(f"  ✓ {name} ({size} bytes)")
        else:
            n_miss += 1
            print(f"  · {name} (not present on remote)")

    # Snapshots — list + download each
    snap_dir = HERE / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    names = _list_prefix(session, url, key, "snapshots")
    snap_n_ok = 0
    snap_bytes = 0
    for n in names:
        ok, size = _get(session, url, key, f"snapshots/{n}", snap_dir / n)
        if ok:
            snap_n_ok += 1
            snap_bytes += size
    print(f"  ✓ {snap_n_ok}/{len(names)} snapshots/*.json ({snap_bytes / 1024:.1f} KB)")
    n_ok    += snap_n_ok
    n_bytes += snap_bytes

    print(f"\nDownloaded {n_ok} files, {n_miss} root files missing, "
          f"{n_bytes / 1024:.1f} KB total")


if __name__ == "__main__":
    main()
