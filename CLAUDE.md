# Operational notes for Claude sessions on this repo

Auto-loaded into context when working in `/Users/macbookpro/Documents/QA Hourly Analysis/`. Keep this short — it's a list of footguns to avoid, not architecture documentation. For architecture, read `ARCHITECTURE.md`. For polling flow, read `POLL_PROCEDURE.md`. For the daily verdict workflow, read `DAILY_VERDICT_PROCEDURE.md`.

---

## ⚠️ Supabase REST: paginate via `.range()` — `db-max-rows` defaults to 1000

**Any code reading from Supabase REST (`sb.from(...).select(...)` or direct HTTP) MUST paginate via `.range(from, to)` if the result set could exceed 1,000 rows.** Supabase enforces a project-level `db-max-rows` setting that caps every response at 1,000 by default. Setting `.limit(50000)` does NOT override this. The cap fails silently — the request returns 200 with the first 1,000 rows and no indication that the tail was dropped.

This has bitten us twice:

1. **PR #7's Phase D verification** — Python script using `?limit=50000` returned 1,000 rows when the table had 1,046. Worked around in the verification script via Range headers; bug noted but the fix was never propagated to the actual dashboard code.
2. **PR #11** — Dashboard `loadCompletions` issued a single `.limit(50000)` call. As soon as the table exceeded 1,000 rows for today, hour 18+ silently disappeared from Hourly Output. Same root cause, different code path. Fixed by paginating in 1000-row chunks via `.range()`.

### Correct pattern (supabase-js)

```javascript
const PAGE_SIZE = 1000;
const MAX_PAGES = 50;  // 50k-row ceiling; bump if you genuinely need more
const all = [];
for (let page = 0; page < MAX_PAGES; page++) {
  const from = page * PAGE_SIZE;
  const to   = from + PAGE_SIZE - 1;
  const { data, error } = await sb.from("ownership_completions")
    .select("...")
    .gte("...", ...)
    .order("...", { ascending: true })  // stable ordering essential for pagination
    .range(from, to);                    // .range() uses Range header, bypasses db-max-rows
  if (error) { console.error(error); return; }
  if (!data || !data.length) break;
  all.push(...data);
  if (data.length < PAGE_SIZE) break;   // last page reached
}
```

### Correct pattern (Python / requests)

```python
rows, offset = [], 0
while True:
    r = requests.get(url,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Range": f"{offset}-{offset+999}", "Range-Unit": "items"},
        params={"select": "...", "and": "..."})
    if r.status_code not in (200, 206): break
    batch = r.json()
    rows.extend(batch)
    if len(batch) < 1000: break
    offset += 1000
```

The Range header is the mechanism that bypasses `db-max-rows` — both supabase-js's `.range()` and a raw `Range: 0-999` header on `requests.get` work the same way under the hood.

### When you can skip pagination

Only when you're certain the result set is bounded below 1,000 by the query itself (e.g. `.eq("id", X).limit(1)`). For anything date-ranged or roster-filtered on this dataset, paginate.

---

## Other persistent gotchas (one-liners)

- **Airtable is strictly read-only.** Never call any `create_*`, `update_*`, or `delete_*` against the `relations_support` base. Local JSON cache files in `.poll_work/` are fine to write.
- **EAT vs UTC.** Airtable formulas use `DATEADD({field}, 3, 'hours')` to shift to EAT before date comparisons; Python uses `datetime.now(EAT).date()` where `EAT = timezone(timedelta(hours=3))`. Airtable's `NOW()` and `TODAY()` are UTC-based.
- **`Valid Selected Time` is a formula field.** Airtable's `OR(IS_SAME(..., formula_field), IS_SAME(..., real_field))` silently returns the wrong count. Split into two separate fetches and dedupe at the aggregator. (See PR #8.)
- **Airtable singleSelect / multipleRecordLinks shapes differ** between the legacy Cowork MCP wrapper (`{id, name}` dicts) and the raw REST API used by `poll_airtable.py` (plain strings / `["recXXX"]` lists). `extract_v2._name()` and `_id()` handle both. Don't reintroduce dict-only assumptions.
- **`last_modified_by == "Automations"`** is Airtable's automation bot, not a human. `completion_detector.py` filters these out via `NON_HUMAN_LAST_MODIFIED` and falls through to `qa_assignee → assignee`.
- **`.env.local` has `AIRTABLE_PAT` + Supabase service key.** Gitignored. Never commit. Service-role key bypasses RLS — only use server-side.
- **PostgREST INSERT with `Prefer: resolution=ignore-duplicates`** requires `?on_conflict=<col>` in the URL or it's a no-op (returns 409 on the first dup). (See PR #9.)
- **GitHub Actions cron runs in UTC.** `*/15 3-17 * * 1-6` = every 15 min, 03:00–17:00 UTC = 06:00–20:00 EAT, Mon–Sat. Sundays off.
- **Do NOT auto-merge PRs.** Kelvin reviews everything; standing instruction throughout this project.

---

## ⚠️ Backfills and historical snapshots — handle with care

Backfilling a historical day's snapshot has bitten this project twice. Both root causes:

### 1. `sync_to_supabase.py` re-uploads ALL local snapshots

The current script uploads every file under `.poll_work/snapshots/` regardless of date. If a one-off local aggregator run regenerates an OLD snapshot file (e.g. `snapshots/2026-05-18.json`) against a stale cache, the sync **happily clobbers the correct historical snapshot on Supabase Storage** with the regenerated wrong one.

Concrete failure mode (2026-05-19): a local backfill aggregator ran with `today_eat=2026-05-18` against ~hours-old cache files. It correctly fixed the `add_new_company_open` metric (16 → 84) but inadvertently regressed `bo_qa_backlog` per-team (Pweza/Tembo dropped from 165/179 → 0/0) because the cache was missing records that had transitioned to SBO since the last poll. `sync_to_supabase.py` then uploaded the regenerated `snapshots/2026-05-18.json`, overwriting the cron-written one that had the correct values.

**Guardrail to add (follow-up):** `sync_to_supabase.py` should never upload a `snapshots/<date>.json` whose date is older than today (EAT). At minimum, add a printed warning if it's about to do so. Belt-and-braces: a flag like `--allow-historical-snapshots` that defaults off.

**Until that ships, manual rule:** before running `sync_to_supabase.py` after any local aggregator run, check `ls .poll_work/snapshots/` — if there's a snapshot file for a date that's not today, delete it from the local copy before syncing.

### 2. Backfills must re-poll Airtable when the local cache is stale

Cache files in `.poll_work/` (`recent_p*.json`, `tagged_today_p*.json`, etc.) get refreshed every cron cycle. Between cron cycles they're stale. Running the aggregator against a stale cache produces wrong values for current-state metrics:

- `bo_qa_backlog` — current SBO count; drifts as records move in/out of SBO
- `flow_a/b/c/d_today` — classifies by CURRENT verification_status; drifts as records advance through states
- `add_new_company_open` — stable once a record is tagged with `add_new_company`, but new records keep appearing through the day

**If you need to backfill a historical day's snapshot:**

- **Either** re-poll Airtable first to refresh the cache (`set -a && . ./.env.local && set +a && python3 .poll_work/poll_airtable.py`). This works only if the historical day's records are still within the live filters' lookback windows (Fetch A is past-24h, Fetch D is "tagged today" so won't pull yesterday).
- **Or** write only the specific metric you're backfilling. Load the existing `snapshots/<date>.json`, mutate just the field you're fixing, write back. Don't replace the whole snapshot.
- **Or — preferred:** don't backfill. Document the gap in the changelog and let the next day's correct snapshot take over. Trying to reconstruct point-in-time current-state metrics retroactively will always be lossy.

### 3. `_save_snapshot` writes today's snapshot every aggregator run

The cron's normal behavior writes only `snapshots/<today_eat>.json`. So a fresh workflow run will never accidentally touch historical snapshots — only manual local runs with overridden `today_eat` can do that. The guardrail above is specifically for the manual-override path.
