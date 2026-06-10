#!/usr/bin/env python3
"""One-off backfill: set ownership_task_history.source_table for historical rows
(NULL because they predate migration 010) by deriving each task's dominant table
from ownership_completions (requested_by + source_table, per migration 006).
Idempotent: only updates rows where source_table IS NULL. Reads service key from
SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY env (source .env.local first).

Note on query shape: ownership_completions is large enough that an unbounded
`select=requested_by,source_table` scan trips the project statement_timeout (500
/ 57014). So instead of pulling the whole table, we first collect the small set
of task names that still need backfilling (NULL in the ledger), then derive each
one's table from a per-task, paginated `requested_by=eq.<name>` query — every
request is bounded and fast.

Preflight: PostgREST caches the table schema; after a fresh `ALTER TABLE ... ADD
COLUMN` (migration 010) it serves 42703 ("column ... does not exist") for that
column until the cache reloads (`NOTIFY pgrst, 'reload schema';`). We check for
that up front and bail with instructions rather than firing doomed writes."""
import os, sys, json, requests
from collections import defaultdict, Counter

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REST = f"{URL}/rest/v1"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

def get_all(path, params):
    """Paginated GET via Range header (bypasses db-max-rows 1000 cap)."""
    rows, off = [], 0
    while True:
        h = dict(H); h["Range-Unit"] = "items"; h["Range"] = f"{off}-{off+999}"
        r = requests.get(f"{REST}/{path}", headers=h, params=params, timeout=60)
        if r.status_code not in (200, 206):
            print("GET error", r.status_code, r.text[:300]); sys.exit(1)
        batch = r.json(); rows.extend(batch)
        if len(batch) < 1000: break
        off += 1000
    return rows

def get_page(path, params):
    """Single bounded GET (limit honoured via params). Returns the row list."""
    r = requests.get(f"{REST}/{path}", headers=H, params=params, timeout=60)
    if r.status_code not in (200, 206):
        print("GET error", r.status_code, r.text[:300]); sys.exit(1)
    return r.json()

# 0) Preflight — confirm PostgREST can see ownership_task_history.source_table.
pf = requests.get(f"{REST}/ownership_task_history", headers=H,
                  params={"select": "source_table", "limit": 1}, timeout=60)
if pf.status_code != 200 and '"42703"' in pf.text:
    print("BLOCKED: PostgREST does not yet see ownership_task_history.source_table")
    print("  ->", pf.text[:200])
    print("  Migration 010 added the column, but PostgREST's schema cache is stale.")
    print("  In the dashboard Supabase SQL editor (project isccbmgjgtdosiccstcp) run:")
    print("      NOTIFY pgrst, 'reload schema';")
    print("  then re-run this script. No rows were written.")
    sys.exit(2)
if pf.status_code != 200:
    print("Preflight failed:", pf.status_code, pf.text[:300]); sys.exit(1)

# 1) ledger rows needing backfill (source_table IS NULL) — small, filtered, fast.
print("Reading ownership_task_history rows with NULL source_table…")
nulls = get_all("ownership_task_history", {"select": "task_name", "source_table": "is.null"})
need = sorted({(r.get("task_name") or "").strip() for r in nulls if r.get("task_name")})
print(f"  {len(nulls)} null rows across {len(need)} task names")

def dominant(counter):
    io = counter.get("relations_io", 0); sup = counter.get("relations_support", 0)
    if io and not sup: return "relations_io"
    if sup and not io: return "relations_support"
    if io > sup: return "relations_io"
    if sup > io: return "relations_support"
    return "mixed"

# 2) derive each needed task's dominant table from completions.
# Single keyset-paginated pass over ownership_completions ordered by the `id`
# primary key — each page is index-ordered and bounded, so it stays fast and
# never trips the statement_timeout the way a per-task seq-scan (unindexed
# requested_by) or an unordered OFFSET scan does on this large table.
print("Deriving dominant table per task from ownership_completions (keyset pass)…")
need_set = set(need)
by_task = defaultdict(Counter)
# `id` is a uuid PK; keyset-paginate lexically from the zero-uuid sentinel.
cursor, pages, total = "00000000-0000-0000-0000-000000000000", 0, 0
while True:
    page = get_page("ownership_completions",
                    {"select": "id,requested_by,source_table",
                     "order": "id.asc", "id": f"gt.{cursor}", "limit": "1000"})
    if not page: break
    for c in page:
        total += 1
        nm = (c.get("requested_by") or "").strip()
        st = c.get("source_table")
        if nm in need_set and st:
            by_task[nm][st] += 1
    cursor = page[-1]["id"]; pages += 1
    if len(page) < 1000: break
print(f"  scanned {total} completion rows in {pages} pages; "
      f"resolved a table for {len(by_task)} / {len(need)} tasks")

# 3) PATCH each task's NULL rows to its dominant table (idempotent: WHERE is.null).
updated = Counter(); skipped = []
for name in need:
    if name not in by_task: skipped.append(name); continue
    val = dominant(by_task[name])
    params = {"task_name": f"eq.{name}", "source_table": "is.null"}
    h = dict(H); h["Content-Type"] = "application/json"; h["Prefer"] = "return=minimal"
    r = requests.patch(f"{REST}/ownership_task_history", headers=h, params=params,
                       data=json.dumps({"source_table": val}), timeout=60)
    if r.status_code not in (200, 204):
        print("PATCH error", name, r.status_code, r.text[:200]); continue
    updated[val] += 1
print("Backfilled task counts by table:", dict(updated))
print(f"Skipped {len(skipped)} tasks with no completions rows (left NULL):", skipped[:10],
      "…" if len(skipped) > 10 else "")
