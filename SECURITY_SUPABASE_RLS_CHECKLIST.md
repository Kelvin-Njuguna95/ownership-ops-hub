# Supabase RLS audit — Ownership Ops Hub

Project ref: `isccbmgjgtdosiccstcp`
Generated: 2026-05-30

**How to use:** open the Supabase SQL editor for the project, paste each verification
block, and check the result against the noted expectation. If any check fails, run the
remediation block directly below it.

> **Heads-up from the codebase audit:** the migrations under `db/migrations/`
> (`001`–`006`) create all four tables but contain **no `ENABLE ROW LEVEL SECURITY`
> and no `CREATE POLICY` statements**. So unless RLS was turned on manually in the
> Supabase dashboard, every table below is likely shipping with **RLS OFF**, which
> means the public `anon` key can read *and write* the table directly from any browser.
> Treat the "Verify RLS is ON" step as the priority check for all four tables.

**Threat model (why this matters):** the dashboard is a static page that ships the
`anon` key to the browser (`deploy/index.html`). Anyone who opens devtools has that
key. With RLS off, the anon key is a full read/write grant. The goal of this audit is:
browser-facing tables expose **read-only** anon access; the heavy writes happen
server-side under the `service_role` key (which bypasses RLS), so write policies are
never needed for `anon`.

---

## Tables

Four tables were found in the codebase (`db/migrations/001`–`006`, browser reads in
`deploy/index.html`, server writes via `/rest/v1/...` in the Python pipeline).

| Table | Browser reads | Server writes | Anon access should be |
|-------|:---:|:---:|---|
| `ownership_completions` | — | ✓ | none (server-only) |
| `ownership_qa_sampling` | ✓ | ✓ | read-only |
| `flow_alerts` | ✓ | ✓ | read-only |
| `ownership_task_history` | ✓ | ✓ | read-only |

---

### `ownership_completions`

Used by: **server only** — written by `completion_detector.py` via `/rest/v1/ownership_completions`. Not read by the browser (`deploy/index.html` reads it from the cached snapshots in Storage, not from this table).

Verify RLS is ON:
```sql
SELECT relrowsecurity FROM pg_class WHERE relname = 'ownership_completions';
-- expect: t
```

Verify policies (server-only table: ideally NO anon policy — service_role bypasses RLS):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_completions'::regclass;
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_completions ENABLE ROW LEVEL SECURITY;
```

For a server-only table, RLS ON with **NO policies** is the correct, locked-down end
state — the `service_role` key the pipeline uses bypasses RLS entirely, while `anon`
gets nothing. Do **not** add an anon SELECT policy here unless the browser starts
reading this table directly.

---

### `ownership_qa_sampling`

Used by: **both** — browser reads the pending queue and the last-8-days window
(`deploy/index.html`: `sb.from("ownership_qa_sampling")` filtered on `reviewed_at is null`
and `sampled_at >= eightDaysAgo`); server writes via `/rest/v1/ownership_qa_sampling`.

Verify RLS is ON:
```sql
SELECT relrowsecurity FROM pg_class WHERE relname = 'ownership_qa_sampling';
-- expect: t
```

Verify policies (anon should be read-only — a single SELECT policy, no INSERT/UPDATE/DELETE for anon):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_qa_sampling'::regclass;
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_qa_sampling ENABLE ROW LEVEL SECURITY;
```

If the table is browser-readable and missing an anon SELECT policy:
```sql
CREATE POLICY "anon read" ON ownership_qa_sampling
  FOR SELECT TO anon USING (true);
```

---

### `flow_alerts`

Used by: **both** — browser reads alerts (`deploy/index.html`: `sb.from("flow_alerts")`);
server writes via `/rest/v1/flow_alerts`.

Verify RLS is ON:
```sql
SELECT relrowsecurity FROM pg_class WHERE relname = 'flow_alerts';
-- expect: t
```

Verify policies (anon should be read-only — a single SELECT policy, no INSERT/UPDATE/DELETE for anon):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'flow_alerts'::regclass;
```

If RLS is OFF — remediation:
```sql
ALTER TABLE flow_alerts ENABLE ROW LEVEL SECURITY;
```

If the table is browser-readable and missing an anon SELECT policy:
```sql
CREATE POLICY "anon read" ON flow_alerts
  FOR SELECT TO anon USING (true);
```

---

### `ownership_task_history`

Used by: **both** — browser reads the daily history (`deploy/index.html`:
`sb.from("ownership_task_history")`); server writes via `/rest/v1/ownership_task_history`.

Verify RLS is ON:
```sql
SELECT relrowsecurity FROM pg_class WHERE relname = 'ownership_task_history';
-- expect: t
```

Verify policies (anon should be read-only — a single SELECT policy, no INSERT/UPDATE/DELETE for anon):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_task_history'::regclass;
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_task_history ENABLE ROW LEVEL SECURITY;
```

If the table is browser-readable and missing an anon SELECT policy:
```sql
CREATE POLICY "anon read" ON ownership_task_history
  FOR SELECT TO anon USING (true);
```

---

## Storage buckets

One bucket was found in the codebase (`dashboard-data`, referenced in
`sync_to_supabase.py`, `.poll_work/download_state.py`, `db/backfill_task_history_from_snapshots.py`,
and `deploy/index.html`).

### bucket `dashboard-data`

Used by: **both** — the browser fetches the aggregates/snapshots over the public object
path (`deploy/index.html`: `STORAGE_PUBLIC = .../storage/v1/object/public/dashboard-data`)
and lists objects (`sb.storage.from("dashboard-data").list(...)`); the server uploads
snapshots/cache/aggregates with the `service_role` key (`sync_to_supabase.py`).

Because the browser reads it over the **public** object path, this bucket must be
public-readable. Writes must stay restricted to `service_role` (the pipeline), never `anon`.

Verify it's public-readable but write-restricted:
```sql
SELECT name, public FROM storage.buckets WHERE name = 'dashboard-data';
-- expect for dashboard-data bucket: public = true
-- (any server-only bucket would instead expect public = false)
```

Verify Storage policies:
```sql
SELECT name, definition, action, roles
FROM storage.policies WHERE bucket_id = 'dashboard-data';
```

If bucket is public and missing an "anon SELECT only" policy:
```sql
CREATE POLICY "anon read aggregates" ON storage.objects
  FOR SELECT TO anon
  USING (bucket_id = 'dashboard-data');
```

> **Write-side check:** confirm there is **no** policy granting `anon` INSERT/UPDATE/DELETE
> on `storage.objects` for `bucket_id = 'dashboard-data'`. The pipeline uploads with the
> `service_role` key, which bypasses Storage RLS, so anon never needs write access. If
> such a policy exists, drop it — otherwise anyone could overwrite the dashboard's cache
> from the browser (the same blast radius as the snapshot-clobber failure noted in CLAUDE.md,
> but attacker-controlled).

---

## Rate limiting (no action needed — informational)

Supabase enforces 60 req/sec per IP on the anon key by default. If the dashboard ever
gets hammered, raise it in **Supabase Settings → API → Rate Limits**.

---

## When to re-audit

Quarterly. **Next: 2026-08-30.** Or whenever a new table or bucket is added — in this
repo that means any new `db/migrations/00X_*.sql`, any new `sb.from("...")` in
`deploy/index.html`, or any new `/rest/v1/...` path or `BUCKET = "..."` in the Python
pipeline. New migrations should themselves include the `ENABLE ROW LEVEL SECURITY` and
anon `CREATE POLICY` statements so this gap doesn't reopen.
