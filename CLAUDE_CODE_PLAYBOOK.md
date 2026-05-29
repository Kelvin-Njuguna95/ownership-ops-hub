# Claude Code Build Playbook — CLIENT_A QA Intelligence

This is your step-by-step guide for building the system using Claude Code in your terminal. Every prompt below is copy-pasteable. Each one produces a verifiable result before you move to the next.

The build follows `ARCHITECTURE_V6.md` Phase 1 (Week 1 — MVP). After Phase 1 ships, swap to Phase 2 prompts.

---

## 0. Prerequisites (manual, before any Claude Code)

Do these once, in order. They take ~45 minutes total.

### 0.1 Local tooling

```bash
# Node.js 20+ (check with node -v)
# If needed: brew install node@20

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

# GitHub CLI (optional but helpful)
brew install gh
gh auth login

# Verify Claude Code
claude --version
```

### 0.2 Accounts you need open in browser tabs

1. **Supabase** — https://supabase.com — sign in, create new project `client-a-qa-staging`. Note the project URL, anon key, service role key from Settings → API.
2. **GitHub** — create empty repo `client-a-qa-dashboard` under your org or personal account. Don't initialize with README; we'll push from local.
3. **Vercel** — https://vercel.com — sign in, but don't import yet (we'll do it from the CLI after first push).
4. **Airtable** — https://airtable.com/create/tokens — create Personal Access Token scoped to `REDACTED_BASE_ID`, permissions `data.records:read` and `schema.bases:read`. Copy the token.
5. **Slack** — https://api.slack.com/apps — create webhook for a new channel `#ww-qa-alerts`. Copy the webhook URL.

### 0.3 Environment file

Create `~/.env.client-a-qa` on your machine (kept out of any repo):

```bash
# Database
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGc...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGc...
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.xxxxx.supabase.co:5432/postgres

# Airtable
AIRTABLE_PAT=pat...
AIRTABLE_BASE_ID=REDACTED_BASE_ID
AIRTABLE_TABLE_ID=tblpj9aJP4ExhYCZF

# Alerts
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Auth
ALLOWED_EMAIL_DOMAIN=impactoutsourcing.co.ke

# Cron security
CRON_SECRET=<generate with: openssl rand -hex 32>
```

### 0.4 Create the local project directory

```bash
mkdir -p ~/code/client-a-qa-dashboard
cd ~/code/client-a-qa-dashboard
git init
gh repo create client-a-qa-dashboard --private --source=. --remote=origin
```

### 0.5 Copy the architecture docs into the repo

```bash
mkdir -p docs/architecture
cp ~/Documents/QA\ Hourly\ Analysis/ARCHITECTURE*.md docs/architecture/
cp ~/Documents/QA\ Hourly\ Analysis/POLL_PROCEDURE.md docs/architecture/ 2>/dev/null || true
cp ~/Documents/QA\ Hourly\ Analysis/ww_audit_log.json docs/fixtures/cowork_audit_log.json 2>/dev/null || mkdir -p docs/fixtures && cp ~/Documents/QA\ Hourly\ Analysis/ww_audit_log.json docs/fixtures/cowork_audit_log.json
```

These docs travel with the code so Claude Code can read them on every prompt.

---

## 1. Initialize the repo with `CLAUDE.md`

The first file in any Claude-Code project. Claude reads it on every conversation start.

```bash
cd ~/code/client-a-qa-dashboard
claude
```

Inside Claude Code, paste this prompt:

> **Prompt 1 — Initialize project conventions**
>
> Read `docs/architecture/ARCHITECTURE_V6.md` end to end. Then read `ARCHITECTURE_V5.md` and `ARCHITECTURE_V4.md` for anything v6 says is unchanged.
>
> Then create a `CLAUDE.md` at the repo root that captures:
> - One-paragraph project summary
> - The five-layer architecture (ingestion / interpretation / governance / intelligence / presentation) with which tables and lib folders belong to each
> - The 10 implementation warnings from `ARCHITECTURE_V6.md` §5, restated in your own words
> - File-creation order conventions: schema first, then `lib/normalization/`, then `lib/ingestion/`, then `lib/interpretation/`, then `lib/intelligence/`, then `app/`
> - Lint rules to enforce: no `cellValuesByFieldId` access outside `lib/normalization/` or `lib/ingestion/`; no direct `qa_events` reads outside the polling library and `qa_events_effective` view consumers
> - Test conventions: unit tests live next to code (`foo.ts` and `foo.test.ts`); integration tests in `tests/integration/`; fixtures in `tests/fixtures/`
> - Commit conventions: one phase = one PR, small atomic commits within
>
> Also create `.gitignore` covering `node_modules`, `.next`, `.env*`, `.DS_Store`, `dist`, `out`.
>
> Finish with `git add . && git commit -m "chore: initialize repo with CLAUDE.md and architecture docs"`.

Verify: `cat CLAUDE.md` shows the five layers and the 10 warnings. `git log` shows one commit.

---

## 2. Day 1 morning — Schema + seed + RLS

```
> Prompt 2 — Phase 1 schema in Drizzle
>
> Goal: lay down the Phase 1 schema described in ARCHITECTURE_V6.md §1 plus the columns from V4/V5/V6 that Phase 1 needs.
>
> Tasks:
> 1. `npm init -y` and install: drizzle-orm, drizzle-kit, postgres, @supabase/supabase-js, dotenv, tsx, typescript, @types/node. Dev deps: vitest, @types/node.
> 2. Create `tsconfig.json` (strict, ESM, node16 module resolution).
> 3. Create `drizzle.config.ts` reading DATABASE_URL from env.
> 4. Create `drizzle/schema.ts` with these Phase 1 tables:
>    - projects, project_areas, teams, agents, agent_assignments, user_profiles
>    - targets, metric_definitions
>    - airtable_field_versions (Phase 2 will populate, but table exists Phase 1)
>    - records_cache, pending_baselines (both with source_system column)
>    - raw_airtable_snapshots (with source_system)
>    - qa_events with: confidence, classification_reason, classification_code (NULLABLE in Phase 1), event_fingerprint (NULLABLE), origin_transition_id (NULLABLE), replayed_from_run_id (NULLABLE), source_system, idempotency_key UNIQUE
>    - daily_aggregates (with aggregate_version, aggregate_stability, source_system, replayed_from_run_id, metric_definition_ids array)
>    - polling_runs, polling_watermarks
>    - All enums for event_kind (6 values per V4 §2), transition_kind (10 values per V4 §3), source_system (4 values per V6 §1.3)
> 5. Create `drizzle/migrations/0001_initial.sql` by running `npx drizzle-kit generate`. Verify the SQL is sane.
> 6. Create `drizzle/seed/roster.ts` that reads `docs/fixtures/cowork_audit_log.json`, extracts the roster (5 teams + 25 agents from V6's source), and writes a TypeScript seed script that INSERTs projects (1 row: client-a), project_areas (1: ownership_tagging), teams (5), agents (25), agent_assignments (effective from 2026-01-01).
> 7. Create `drizzle/seed/targets.ts` seeding: productivity_min_daily=280 (global), sampling_min_pct=15 (global), stuck_in_qa_hours=72 (global).
> 8. Create `drizzle/seed/metric_definitions.ts` with the 5 metric definitions per V4 §1.3.
> 9. Create `drizzle/seed/watermarks.ts` initializing one row in polling_watermarks (kind='airtable_last_modified', last_successful_modified_time=now()-interval '24 hours', overlap_seconds=120).
> 10. Create `scripts/db-push.ts` that runs drizzle-kit push then all seed scripts in order.
> 11. Apply RLS: create `drizzle/migrations/0002_rls.sql` with the `is_csm()` function and `csm_read_all` policy on every fact/audit table per V4 §3.
>
> Run `npm run db:push` against the Supabase staging project. Verify: `psql $DATABASE_URL -c "SELECT name FROM teams"` returns 5 rows.
>
> Commit as "feat(schema): Phase 1 Drizzle schema with seed and RLS".
```

Verify:
- Supabase Table Editor shows all Phase 1 tables
- `targets` has 3 rows, `agents` has 25, `teams` has 5
- `polling_watermarks` has one row
- RLS is enabled on every fact table

---

## 3. Day 1 afternoon — Next.js + auth + normalization

```
> Prompt 3 — Next.js scaffold and Google SSO
>
> Goal: a Next.js 14 App Router app with Google SSO gated to @impactoutsourcing.co.ke. Single-user mode.
>
> Tasks:
> 1. `npx create-next-app@latest . --typescript --tailwind --app --no-eslint --import-alias "@/*"` into the current directory. Accept overwrites for tsconfig etc. but preserve our drizzle/, docs/, scripts/.
> 2. Install: @supabase/supabase-js, @supabase/ssr, zod, date-fns, date-fns-tz, lucide-react, recharts.
> 3. Add shadcn/ui: `npx shadcn-ui@latest init` (defaults: New York style, Slate, CSS variables yes). Add components: button, card, table, tabs, badge, dialog, toast, select.
> 4. Create `lib/supabase/server.ts`, `lib/supabase/browser.ts`, `lib/supabase/service.ts` per `@supabase/ssr` cookie pattern. Service role client only importable from `app/api/` routes (enforce via a comment header + later via lint rule).
> 5. Create `middleware.ts` that protects all routes except `/login`, `/auth/callback`, `/api/cron/*`. Unauthenticated users redirect to `/login`.
> 6. Create `app/login/page.tsx` with a single Google sign-in button.
> 7. Create `app/auth/callback/route.ts` handling the OAuth code exchange; reject any email not matching `process.env.ALLOWED_EMAIL_DOMAIN`.
> 8. Create `app/(dashboard)/layout.tsx` with a top nav (Overview, Pending QA, Silent Changes, placeholder for admin).
> 9. Create `app/(dashboard)/page.tsx` rendering a placeholder "Loading data..." — will fill in Day 4.
> 10. Configure Supabase Auth: enable Google provider in Supabase dashboard, redirect URLs include http://localhost:3000/auth/callback and your-vercel-url/auth/callback.
> 11. On first sign-in, automatically INSERT a `user_profiles` row with `app_role='customer_success_manager'`.
>
> Verify: `npm run dev`, visit http://localhost:3000, sign in with your @impactoutsourcing.co.ke account, see the placeholder dashboard. Sign in with a non-Impact account → rejected.
>
> Commit as "feat(auth): Next.js + Supabase Google SSO with domain restriction".
```

```
> Prompt 4 — Normalization layer (FIRST module before any polling logic)
>
> Goal: implement `lib/normalization/normalized_fields.ts` exactly per V5 §3.1. This is THE FIRST piece of business logic. Everything else depends on it.
>
> Tasks:
> 1. Create `lib/normalization/normalized_fields.ts` with:
>    - VERIFICATION_STATUS_NORMALIZED, QA_STATUS_NORMALIZED maps (handle trailing-space "Selected for BO QA ")
>    - normalizeVerificationStatus, normalizeQaStatus, normalizeCompanyName, normalizeUserName functions
>    - normalizeRecord(raw) composite function returning a NormalizedRecord type
>    - All TypeScript types exported
> 2. Create `lib/normalization/normalized_fields.test.ts` with Vitest tests covering:
>    - Trailing-space "Selected for BO QA " → "selected_for_bo_qa"
>    - Mixed-case "JAMES MAINA" / "lillian Gichamba" → lowercased for comparison
>    - "  approve  " → "approve"
>    - null/undefined → null
>    - Unknown status value → passed through verbatim (per V5 risk #22)
>    - Company name with internal whitespace → collapsed
> 3. Configure Vitest: add `vitest.config.ts`, add `"test": "vitest"` to package.json scripts.
> 4. Run `npm test`. All tests pass.
> 5. Add `tsconfig` path alias `@/lib/normalization` and verify import works from a stub file.
>
> Add a comment header to every file: `// Layer: Interpretation`. We'll enforce this with lint in Phase 2.
>
> Commit as "feat(normalization): canonical-form mapping for Airtable values with full test coverage".
```

Verify: `npm test` shows all tests pass. `cat lib/normalization/normalized_fields.ts | grep "Selected for BO QA"` shows both the space and non-space variants mapped.

---

## 4. Day 2 — Airtable client, watermark, raw snapshot writer

```
> Prompt 5 — Ingestion layer
>
> Goal: code that fetches Airtable records using watermark protection and writes them verbatim to raw_airtable_snapshots.
>
> Tasks:
> 1. Create `lib/airtable/fields.ts` with all 9 field IDs as named constants (from V6 schema). One source of truth.
> 2. Create `lib/airtable/client.ts`:
>    - `fetchRecordsModifiedSince(since: Date): AsyncIterable<AirtableRecord[]>` paginates with cursor + exponential backoff on 429s.
>    - 60-second wall-clock cap per call. Cap pages at 50 per run.
> 3. Create `lib/ingestion/watermark.ts`:
>    - `readWatermark()` returns { last_successful_modified_time, overlap_seconds }
>    - `advanceWatermark(maxModifiedTime, runId)` updates only on success
> 4. Create `lib/ingestion/writeRawSnapshots.ts` that bulk-inserts records into raw_airtable_snapshots with source_system='airtable_live'.
> 5. Create `lib/ingestion/runPoll.ts` that does steps 1-3 of the V5 polling flow:
>    - START polling_run with status='running'
>    - Compute window_from = watermark - overlap_seconds, window_to = now
>    - Fetch Airtable, write raw snapshots
>    - DO NOT advance watermark yet (that's Day 3 after events are written)
>    - UPDATE polling_run with status='partial' (since we're not done yet) and stats
> 6. Create `app/api/cron/poll-manual/route.ts` (GET) protected by CRON_SECRET that calls runPoll(). Returns JSON of the run summary.
> 7. Create `tests/integration/ingestion.test.ts` with fixtures: 5 mock Airtable records. Verify watermark behavior, raw snapshot insertion, polling_run row.
>
> Run: `curl -H "Authorization: Bearer $CRON_SECRET" http://localhost:3000/api/cron/poll-manual`
>
> Verify: 
> - polling_runs has a new row with status='partial'
> - raw_airtable_snapshots has new rows with source_system='airtable_live'
> - polling_watermarks last_successful_modified_time UNCHANGED (we haven't completed yet)
>
> Commit as "feat(ingestion): Airtable fetch + watermark + raw snapshot writer".
```

---

## 5. Day 3 — Interpretation: baselines, events, idempotency

```
> Prompt 6 — Interpretation layer for Phase 1
>
> Goal: detect baselines and write qa_events for 4 event kinds (qa_approved, qa_changed, silent_change, not_sampled). Phase 1 simplified: no state_machine_rules yet, no review_queue, confidence hardcoded 'high', classification_code='BASELINE_MATCH' or 'CLEAN_NOT_SAMPLED'.
>
> Tasks:
> 1. Create `lib/interpretation/detectBaselines.ts`:
>    - For each NormalizedRecord where verification_status='selected_for_bo_qa' AND record_id NOT IN pending_baselines: INSERT new baseline with baseline_seq=1 (or seq+1 if a prior expired baseline exists), source_system='airtable_live'.
> 2. Create `lib/interpretation/detectEvents.ts`:
>    - Compare current normalized state vs records_cache (in-memory join since record_state_history isn't Phase 1).
>    - Detect transitions:
>      - tagged → selected_for_bo_qa (already handled via baselines)
>      - selected_for_bo_qa → done/valid: lookup baseline, compare companies (normalized), emit qa_approved / qa_changed / silent_change, mark baseline status='matched_to_qa_event'.
>      - tagged → done/valid (skipping BO QA): emit not_sampled with confidence='high', classification_code='CLEAN_NOT_SAMPLED'.
>    - For each event, compute idempotency_key = sha256(record_id + qa_action_ts_iso + event_kind + baseline_seq). INSERT ON CONFLICT (idempotency_key) DO NOTHING.
>    - All Phase 1 events: confidence='high', classification_code per simple rules above, event_fingerprint NULL (Phase 2 will populate), origin_transition_id NULL (Phase 2), source_system='airtable_live'.
> 3. Create `lib/interpretation/updateRecordsCache.ts` doing UPSERT on records_cache. Resolves agent_id/team_id/qa_id via roster lookup keyed by normalized agent name.
> 4. Extend `lib/ingestion/runPoll.ts` to call detectBaselines → detectEvents → updateRecordsCache → ADVANCE WATERMARK → COMPLETE polling_run (status='success'). This is the full Phase 1 polling flow.
> 5. Add to `tests/integration/`:
>    - test_idempotency.ts: run the same fixture window twice, assert zero new events the second time
>    - test_classification.ts: feed fixtures of each transition type, assert correct event_kind written
>    - test_baseline_lifecycle.ts: baseline opens on BO QA entry, matches on exit
>
> Verify by re-running the manual-poll endpoint twice:
> - First run: writes baselines and events
> - Second run: same window, ZERO new rows (idempotency works)
> - polling_watermarks.last_successful_modified_time advances after each successful run
>
> Commit as "feat(interpretation): baseline + event detection with idempotency".
```

---

## 6. Day 4 — Daily aggregates + overview page

```
> Prompt 7 — Intelligence layer (daily aggregates)
>
> Goal: compute daily_aggregates per agent for today, render the overview dashboard.
>
> Tasks:
> 1. Create `lib/intelligence/version.ts` with `export const AGGREGATE_VERSION = 1` and a function to compute compute_logic_hash by hashing the source of lib/intelligence/computeAggregates.ts.
> 2. Create `lib/intelligence/computeAggregates.ts`:
>    - For each ownership-team agent: count events from qa_events_effective by event_kind, plus records currently in_bo_qa from records_cache.
>    - Compute hourly_buckets from start_tagging_date in records_cache.
>    - Resolve productivity_target and sampling_target_pct via the targets table (using effective_from <= date).
>    - Compute sampling_rate_pct per V4 §1.6 formula.
>    - UPSERT daily_aggregates with aggregate_version=1, aggregate_stability='stable', source_system='airtable_live', metric_definition_ids = array of metric IDs effective today.
> 3. Create `drizzle/views/0003_qa_events_effective.sql` that creates the qa_events_effective view per V6 §1.8 (Phase 1 simplification: no replay/correction joins yet — those tables don't exist in Phase 1; the view degrades to SELECT * FROM qa_events plus is_corrected=false, is_replayed=false constants).
> 4. Wire computeAggregates into runPoll as the final step before COMPLETE.
> 5. Create `lib/queries/dashboard.ts` with server functions:
>    - getDailyKPIs(date: Date)
>    - getHourlyOutput(date: Date, team?: string)
>    - getProductivityTable(date: Date, team?: string)
>    - getSamplingTable(date: Date, team?: string)
>    All read from daily_aggregates + qa_events_effective.
> 6. Build `app/(dashboard)/page.tsx` (overview):
>    - 6 KPI cards: Pending QA, Tagged today, QA actions today, Silent changes, Agents under 280, Last poll
>    - Hourly heatmap (agents × hours grid, color-coded)
>    - Team-totals line chart (Recharts)
>    - Team selector dropdown (All / TEAM_1 / TEAM_5 / TEAM_4 / TEAM_3 / TEAM_2)
>    - Date selector (defaults today, allow yesterday)
> 7. Server Components for data fetching; Client Components only for interactivity.
>
> Verify: trigger manual poll. Refresh dashboard. See real numbers. Heatmap shows hours with activity. Productivity flag fires for any agent below 280.
>
> Commit as "feat(intelligence): daily aggregates + overview dashboard".
```

---

## 7. Day 5 — Pending queue + silent changes pages

```
> Prompt 8 — Pending QA queue and silent changes pages
>
> Goal: two more dashboard pages reading from records_cache and qa_events_effective.
>
> Tasks:
> 1. Create `app/(dashboard)/queue/page.tsx`:
>    - Read records_cache WHERE verification_status='selected_for_bo_qa', grouped by team → qa → agent.
>    - Columns: Team, QA, Agent, Pending count, Oldest pending (hours ago, computed from start_tagging_date).
>    - Sortable by count or oldest.
> 2. Create `app/(dashboard)/silent-changes/page.tsx`:
>    - List qa_events_effective WHERE event_kind='silent_change' ORDER BY qa_action_at DESC LIMIT 50.
>    - Show: Time, Team, Agent, QA, Agent picked, Arrow, QA changed to, Confidence pill, Silent badge.
>    - Click row to expand and show classification_reason + drill-down (placeholder — full timeline lands Phase 3).
> 3. Create `lib/queries/queue.ts` and `lib/queries/silent-changes.ts` with the server-side queries.
> 4. Add navigation links in the layout: Overview, Pending QA Queue, Silent Changes.
> 5. Add a "Trigger poll" button visible to the CSM (calls /api/cron/poll-manual with CRON_SECRET passed via a same-origin /api/admin/trigger-poll route).
>
> Verify: Pending queue shows the 949 currently in BO QA (or whatever today's number is) grouped by team. Silent changes is empty (good — no detected dishonesty yet).
>
> Commit as "feat(dashboard): pending queue + silent changes pages".
```

---

## 8. Day 6 — Vercel deploy + production cron

```
> Prompt 9 — Deploy to Vercel and configure cron
>
> Goal: app is live at a Vercel URL with the manual-poll route accessible via the trigger button.
>
> Tasks:
> 1. Create `vercel.json` with crons: `{"path": "/api/cron/poll-manual", "schedule": "*/15 3-20 * * *"}` — note: Vercel Cron requires Pro plan. For Phase 1 we ship with the cron config but trigger manually until upgrade.
> 2. Install Vercel CLI: `npm i -g vercel`. Run `vercel login` then `vercel link` to associate this repo with a new Vercel project.
> 3. `vercel env add` for each environment variable from `~/.env.client-a-qa` to BOTH Preview and Production.
> 4. Push to GitHub: `git push -u origin main`.
> 5. `vercel --prod` to deploy.
> 6. Verify the deployed URL loads, Google SSO works (after adding the Vercel URL to Supabase Auth redirect URLs).
> 7. From the deployed URL, click "Trigger poll" and observe new rows in Supabase (use the dashboard's SQL editor to verify).
> 8. Smoke test for 1 hour: trigger 3 manual polls 15 min apart, watch daily_aggregates update.
>
> Commit as "chore: configure Vercel cron and production env".
```

Verify: production URL works, you can sign in, the trigger button writes new rows, KPIs update.

---

## 9. Day 7 — Backfill from Cowork prototype

```
> Prompt 10 — Backfill 30 days from Cowork audit log
>
> Goal: import existing qa_events from docs/fixtures/cowork_audit_log.json so the dashboard shows historical data.
>
> Tasks:
> 1. Create `scripts/backfill-cowork.ts` that:
>    - Reads docs/fixtures/cowork_audit_log.json
>    - For each qa_event entry: INSERT into qa_events with source_system='cowork_backfill', confidence='low', classification_code='AMBIGUOUS', classification_reason='Backfilled from Cowork prototype log; original interpretation logic unavailable', event_fingerprint=NULL, origin_transition_id=NULL, idempotency_key=sha256(record_id + qa_action_at + event_kind + 'backfill').
>    - For each pending_baseline still in the log: INSERT into pending_baselines with status appropriate (open if still in BO QA today, matched_to_qa_event if a corresponding event exists, missing_qa otherwise).
>    - For each agent_completion_event (if not already covered by qa_events): we ignore for now; this is Phase 3 reporting territory.
> 2. Run the script: `npx tsx scripts/backfill-cowork.ts`.
> 3. Trigger the dashboard. Confirm 30 days of historical events are visible (with the "low confidence backfill" indicator).
> 4. Recompute daily_aggregates for the last 30 days by triggering manual polls or a dedicated script `scripts/recompute-aggregates-window.ts` that loops through dates and calls computeAggregates per day.
>
> Verify the Phase 1 done-criteria from V5 §7.5:
> - [ ] Dashboard at production URL, gated to your Google account
> - [ ] 30 days of historical data visible
> - [ ] Manual cron trigger populates today's data correctly  
> - [ ] system_health_metrics not yet (that's Phase 2)
> - [ ] Slack webhook fires on a deliberate test (skip until Phase 2's alert system)
> - [ ] End-to-end test passes: `npm test` runs all unit + integration tests green
>
> Commit as "chore(backfill): 30-day historical import from Cowork log".
> Tag the commit: `git tag -a phase-1-mvp -m "Phase 1 MVP complete"`.
```

---

## 10. Tips for working with Claude Code on this build

### 10.1 Slash commands worth knowing

- `/clear` — reset context when starting a new prompt. Use at the start of each "Day" prompt above so the previous day's chatter doesn't clutter.
- `/help` — list available commands.
- `/permissions` — review what tools Claude Code has accepted; revoke any over-permissive ones.
- `/cost` — see token usage for the current session.

### 10.2 When Claude Code goes wrong

If a prompt produces broken code:

1. Don't ask Claude Code to "fix" without context. Run `npm test` or `npm run dev` first, capture the actual error.
2. Paste the error verbatim back to Claude Code with "This failed with: <error>. The relevant file is `<path>`. Read that file before suggesting a fix."
3. If three iterations don't resolve it, `git stash` and re-prompt from a clean state. Long debug threads accumulate misleading context.

### 10.3 Reading the architecture during the build

Claude Code can read any file in the repo. When you're on Day 4 and Claude says "what's the formula for sampling_rate?" — point it at `docs/architecture/ARCHITECTURE_V4.md` §1.3. Don't reproduce the formula in the prompt; reference the doc. That trains Claude Code to use the docs as the source of truth, which is what you want.

### 10.4 Phase 2 and Phase 3 prompts

After Phase 1 is tagged, this playbook gets extended with Days 8-21 prompts. Reach out (or open the playbook v2 in this same workspace folder) when Phase 1 is shipped and stable for at least 48 hours of smoke testing.

---

## 11. Today's immediate next steps

In order:

1. **Manual setup (§0)** — 45 minutes. Open the 5 browser tabs, create the accounts, populate `~/.env.client-a-qa`, install Claude Code locally.
2. **Initialize the repo** — `mkdir`, `git init`, copy docs, `gh repo create`. ~10 minutes.
3. **Launch Claude Code** in the repo directory: `claude`.
4. **Run Prompt 1** to create `CLAUDE.md`. Verify the result.
5. **Run Prompt 2** for the schema. The first 30 minutes of real building.

Stop after Prompt 2 if it's been a long day. Pick up Prompt 3 the next morning — Day 1 morning + Day 1 afternoon are two separate sessions, even though the playbook puts them in the same calendar day.

Expected total time to Phase 1 complete: **5-7 working days** if you keep two sessions per day. **10-14 days** if you do one focused session per day. Don't rush it — the foundation matters more than the speed.
