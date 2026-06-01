# Supabase RLS audit — Ownership Ops Hub

Project ref: `isccbmgjgtdosiccstcp`
Generated: 2026-05-30

> **💡 To apply in version control:** Run this file in the Supabase SQL editor once to
> enable RLS — the file lives in the repo so any new environment gets the same
> enforcement. See [`db/migrations/007_enable_rls.sql`](db/migrations/007_enable_rls.sql),
> which enables RLS and creates the anon-read policies for all four tables in a single
> idempotent transaction. The verification blocks below confirm it took effect; the
> Storage-bucket SQL still needs to be run separately (storage.objects has its own
> policy model).

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

## ⚠️ Rule: browser-read tables must allow SELECT to BOTH `anon` and `authenticated`

**Every table the dashboard reads from the browser must allow SELECT to BOTH the
`anon` (signed-out) AND `authenticated` (signed-in) roles — simplest is `TO public`,
which covers both.** The dashboard is used both signed-in and signed-out. A signed-in
Supabase session runs as the `authenticated` role, which a `TO anon`-only policy does
**not** cover (and `authenticated` is not a member of `anon`), so a `TO anon`-only
policy blanks every browser panel for signed-in users. **Verify reads as BOTH roles,
not just anon.** Writes stay browser-blocked (no write policy; `service_role` bypasses
RLS), so the read-only intent is preserved.

> **2026-06-01 role-scope correction:** `007_enable_rls.sql` created all SELECT
> policies `TO anon` only (and `ownership_completions` got none — partially fixed by
> `008`). Signed-out users worked, but **signed-in users saw blank panels** on every
> browser-read table. Fixed durably by
> [`db/migrations/009_public_read_browser_tables.sql`](db/migrations/009_public_read_browser_tables.sql)
> (`TO public`). Run [`db/audit_rls_browser_reads.sql`](db/audit_rls_browser_reads.sql)
> after any RLS change to catch a regression — see the Verification section.

---

## Tables

Four tables were found in the codebase (`db/migrations/001`–`006`, browser reads in
`deploy/index.html`, server writes via `/rest/v1/...` in the Python pipeline).

All four tables are read by the browser, so each must be **read (public)** — SELECT
to both `anon` and `authenticated` (see the rule above).

| Table | Browser reads | Server writes | Browser access should be |
|-------|:---:|:---:|---|
| `ownership_completions` | ✓ | ✓ | read (public — anon + authenticated) |
| `ownership_qa_sampling` | ✓ | ✓ | read (public — anon + authenticated) |
| `flow_alerts` | ✓ | ✓ | read (public — anon + authenticated) |
| `ownership_task_history` | ✓ | ✓ | read (public — anon + authenticated) |

---

### `ownership_completions`

Used by: **both** — written by `completion_detector.py` via `/rest/v1/ownership_completions`, AND read directly by the browser. `deploy/index.html`'s `loadCompletions()` calls `fetchAll("ownership_completions", "completed_by", "completed_at")` (paginated `sb.from("ownership_completions")`) to build the Overview "Agent productivity today" scorecard and the Hourly Output heatmap. **Correction (2026-06-01):** an earlier revision of this checklist classified the table as server-only; that was wrong, and migration `007_enable_rls.sql` enabled RLS with no anon policy on that basis, blanking both panels. Fixed in `db/migrations/008_anon_read_ownership_completions.sql`.

Verify RLS is ON:
```sql
SELECT relrowsecurity FROM pg_class WHERE relname = 'ownership_completions';
-- expect: t
```

Verify policies (browser-readable table: a single SELECT policy covering BOTH `anon` and
`authenticated` — i.e. `TO public` — and no INSERT/UPDATE/DELETE for either browser role):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_completions'::regclass;
-- expect a SELECT (polcmd='r') policy whose polroles includes {public} (or both anon + authenticated)
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_completions ENABLE ROW LEVEL SECURITY;
```

If the SELECT policy is missing or `TO anon`-only (the 2026-06-01 role-scope regression):
```sql
DROP POLICY IF EXISTS "anon read ownership_completions" ON ownership_completions;
CREATE POLICY "read ownership_completions" ON ownership_completions
  FOR SELECT TO public USING (true);
```

RLS ON with **only** a public SELECT policy is the correct end state: the `service_role`
key the pipeline uses bypasses RLS for writes, while both browser roles can read but not write.

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

Verify policies (a single SELECT policy covering BOTH `anon` and `authenticated` — `TO public`):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_qa_sampling'::regclass;
-- expect a SELECT (polcmd='r') policy whose polroles includes {public} (or both anon + authenticated)
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_qa_sampling ENABLE ROW LEVEL SECURITY;
```

If the SELECT policy is missing or `TO anon`-only:
```sql
DROP POLICY IF EXISTS "anon read ownership_qa_sampling" ON ownership_qa_sampling;
CREATE POLICY "read ownership_qa_sampling" ON ownership_qa_sampling
  FOR SELECT TO public USING (true);
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

Verify policies (a single SELECT policy covering BOTH `anon` and `authenticated` — `TO public`):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'flow_alerts'::regclass;
-- expect a SELECT (polcmd='r') policy whose polroles includes {public} (or both anon + authenticated)
```

If RLS is OFF — remediation:
```sql
ALTER TABLE flow_alerts ENABLE ROW LEVEL SECURITY;
```

If the SELECT policy is missing or `TO anon`-only:
```sql
DROP POLICY IF EXISTS "anon read flow_alerts" ON flow_alerts;
CREATE POLICY "read flow_alerts" ON flow_alerts
  FOR SELECT TO public USING (true);
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

Verify policies (a single SELECT policy covering BOTH `anon` and `authenticated` — `TO public`):
```sql
SELECT polname, polcmd, polroles::regrole[], pg_get_expr(polqual, polrelid) AS using_expr
FROM pg_policy WHERE polrelid = 'ownership_task_history'::regclass;
-- expect a SELECT (polcmd='r') policy whose polroles includes {public} (or both anon + authenticated)
```

If RLS is OFF — remediation:
```sql
ALTER TABLE ownership_task_history ENABLE ROW LEVEL SECURITY;
```

If the SELECT policy is missing or `TO anon`-only:
```sql
DROP POLICY IF EXISTS "anon read ownership_task_history" ON ownership_task_history;
CREATE POLICY "read ownership_task_history" ON ownership_task_history
  FOR SELECT TO public USING (true);
```

---

## Verification — browser-read role coverage (run after ANY RLS change)

The single most important check: every browser-read table must allow SELECT to BOTH
`anon` and `authenticated`. The committed audit
[`db/audit_rls_browser_reads.sql`](db/audit_rls_browser_reads.sql) checks all four at
once. Run it in the SQL editor (or via the service key). Note: a `TO public` policy
stores `polroles = {0}` (the PUBLIC pseudo-role, OID 0), which covers both `anon` and
`authenticated` — so the audit detects it **by OID 0**, not the string `'public'`
(`'0'::regrole` renders as `'-'`, so string-matching `'public'` would false-negative):

```sql
WITH browser_tables(t) AS (VALUES
  ('ownership_completions'),('ownership_qa_sampling'),('flow_alerts'),('ownership_task_history'))
SELECT bt.t AS table_name,
  bool_or(p.polcmd IN ('r','*') AND (0::oid = ANY(p.polroles) OR 'anon'::regrole::oid          = ANY(p.polroles))) AS anon_can_read,
  bool_or(p.polcmd IN ('r','*') AND (0::oid = ANY(p.polroles) OR 'authenticated'::regrole::oid = ANY(p.polroles))) AS auth_can_read
FROM browser_tables bt
LEFT JOIN pg_class c ON c.relname = bt.t
LEFT JOIN pg_policy p ON p.polrelid = c.oid
GROUP BY bt.t
ORDER BY bt.t;
-- Expect anon_can_read = true AND auth_can_read = true for ALL FOUR rows.
```

Any row with `false` is a latent "blank panel" bug — for signed-out users if
`anon_can_read = false`, or signed-in users if `auth_can_read = false` (the 2026-06-01
regression). **Also reproduce real usage**: read each table via REST as `authenticated`
(reuse a signed-in access token), or load the production dashboard **while signed in**
and confirm Overview agent productivity, Hourly Output, the QA pages, Completed Tasks,
and team alerts all render — testing only the anon path is what let the regression
through.

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
