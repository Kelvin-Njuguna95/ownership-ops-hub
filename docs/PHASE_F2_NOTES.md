# Phase F2 — Automated polling via GitHub Actions

## What this does

`.github/workflows/poll.yml` runs every 15 min during EAT business hours (Mon–Sat, 06:00–20:00 EAT). Each run:

1. Pulls existing state from Supabase Storage (`download_state.py`).
2. Hits Airtable with the three read-only fetches from `POLL_PROCEDURE.md` (`poll_airtable.py`).
3. Re-aggregates the cache into `daily_aggregates.json` + writes today's snapshot (`aggregate_v2.py`).
4. Uploads everything back to Supabase Storage (`sync_to_supabase.py`).

After step 4, the Vercel dashboard's "Synced Nm ago" pill stays under 30 minutes during business hours, with zero laptop involvement.

## Required GitHub secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**, and add three secrets:

| Name | Value | Source |
|---|---|---|
| `AIRTABLE_PAT` | Personal access token with read access to base `appHZdfC2sn9MLGFZ` (table `relations_support`) | Airtable → Account → Developer hub → Personal access tokens. Scopes: `data.records:read`. Workspaces: the CLIENT_A base. |
| `SUPABASE_URL` | `https://isccbmgjgtdosiccstcp.supabase.co` | Already in `.env.local` |
| `SUPABASE_SERVICE_ROLE_KEY` | Legacy JWT-format service_role key (starts with `eyJ...`) | Supabase Dashboard → Settings → API → `service_role` (legacy JWT, not the new `sb_secret_*` format — Storage REST rejects those) |

The workflow won't run successfully until all three are set.

## How to trigger manually

GitHub UI → **Actions → Poll Airtable → Supabase → Run workflow → main → Run workflow**. Useful for:
- Testing after changing secrets.
- Forcing a fresh sync outside the cron window.
- Re-running after a transient failure (the cron's `concurrency: poll-airtable` group skips overlapping runs but a manual dispatch still queues).

## How to read logs

GitHub UI → **Actions → Poll Airtable → Supabase**. Each run shows up as a row with timestamp + status. Click into one to see per-step output:
- "Poll Airtable" prints page counts per fetch + TRUNCATED flag if any cap was hit.
- "Aggregate" prints the totals summary.
- "Sync to Supabase Storage" prints per-folder counts + bytes + the final `last_sync.json uploaded`.

Failed runs are red. Click the failed step to see the stderr; common failures: stale `AIRTABLE_PAT`, exhausted Supabase storage quota, Airtable rate limit (429).

## How to disable

Two options:

1. **Temporarily**: GitHub UI → **Actions → Poll Airtable → Supabase → "···" → Disable workflow**. Re-enable the same way.
2. **Permanently**: delete `.github/workflows/poll.yml` and push.

Disabling does not affect manual `python3 sync_to_supabase.py` runs from your laptop — those keep working.

## Cost / quota notes

- **GitHub Actions free tier**: 2,000 minutes/month for private repos (unlimited for public). The cron fires 4×/hr × 15 hr × 6 days = 360 runs/week ≈ 1,440 runs/month. At ~2 min per run, that's ~2,880 min/month — **over the free tier on a private repo**. If the repo is public, no quota worries. If private, either upgrade to a paid plan, drop the frequency to every 30 min (cuts in half), or shorten the daily window.
- **Airtable API**: 5 requests/second per base. We do ~3 fetches × ~10 pages = ~30 requests per run. Well under the limit.
- **Supabase Storage egress** (free tier 5 GB/month). The `download_state.py` step pulls ~5 MB; `sync_to_supabase.py` pushes ~5 MB. 360 runs/week × 10 MB = ~14 GB/month. **Over the free tier** — consider Pro plan ($25/mo for 100 GB) or reducing snapshot file size.

## Known limitations

- `relations_support_intake_today` will show the cap-truncated count (top 3,000 records of today's intake) rather than the true table-wide count. The Airtable REST API doesn't return a total-record count, so the script counts records actually loaded. If we hit the cap with more remaining, the dashboard's `intake_partial` amber banner does fire (we boost `metadata.totalRecordCount` past 3,000 in that case).
- No retry on transient Airtable 5xx. A failed run waits for the next 15-min slot — usually fine but means a ~15-min freshness gap.
- No alerting on failure. Use GitHub's built-in email notifications (Settings → Notifications → Workflow runs) or wire a Slack webhook into the workflow later.
