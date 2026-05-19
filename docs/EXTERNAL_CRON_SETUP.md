# External cron setup (cron-job.org)

The `Poll Airtable → Supabase` workflow is triggered by an external scheduler at [cron-job.org](https://cron-job.org), not by GitHub Actions' `schedule:` event. GitHub silently drops scheduled events on free-tier public repos (audit on 2026-05-19: only ~4 of ~60 expected firings executed over 22 hours). The `schedule:` block was removed in PR #24; `workflow_dispatch` remains so manual triggers and the external scheduler both work.

This document covers the two cron-job.org jobs that need to be configured:

1. **Poll trigger** — fires every 5 min during business hours, POSTs to the dispatch API.
2. **Staleness watchdog** — fires every 30 min during business hours, GETs `last_sync.json` and alerts if it's older than 45 min.

## Prerequisites

- cron-job.org account (free tier supports both jobs).
- A GitHub fine-grained PAT scoped to **`Kelvin-Njuguna95/ownership-ops-hub` only**, with **`Actions: Read and write`** permission. No other scopes. Save the token in cron-job.org's "Authentication" → "Bearer Token" field so it's not in the URL or logs.
  - Generate at: https://github.com/settings/personal-access-tokens/new
  - Resource owner: `Kelvin-Njuguna95`. Repository access: "Only select repositories" → `ownership-ops-hub`. Expiration: 1 year (calendar reminder to rotate).

## Job 1 — Poll trigger

| Field | Value |
| --- | --- |
| Title | `ownership-ops-hub: poll trigger` |
| URL | `https://api.github.com/repos/Kelvin-Njuguna95/ownership-ops-hub/actions/workflows/poll.yml/dispatches` |
| Request method | `POST` |
| Schedule | Every 5 min, `06:00–23:00` EAT, Mon–Sat. In cron-job.org's UI: set timezone to `Africa/Nairobi`, then check Mon–Sat boxes, hours 6–23, minutes `0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55`. Tighter than the previous 15-min cadence so a single failed run only opens a 5-min visible-gap instead of 15. Free tier supports `*/5` (cron-job.org's free plan allows 1-min minimum). |
| Request headers | `Accept: application/vnd.github+json`<br>`X-GitHub-Api-Version: 2022-11-28` |
| Authentication | Bearer token (paste the PAT from Prerequisites) |
| Request body | `{"ref":"main"}` |
| Body content type | `application/json` |
| Notifications | Enable email on failure (GitHub returns 401/403/404 if the PAT is wrong or revoked). |

**Expected response:** GitHub returns `204 No Content` on success. cron-job.org will flag anything else as a failure.

**Sanity-check from terminal before saving:**

```bash
curl -i -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/Kelvin-Njuguna95/ownership-ops-hub/actions/workflows/poll.yml/dispatches \
  -d '{"ref":"main"}'
```

Should return `HTTP/2 204` with no body. Then verify the run shows up:

```bash
gh run list --workflow="Poll Airtable → Supabase" --limit 3
```

## Job 2 — Staleness watchdog

Detects the next time the trigger silently breaks (PAT revoked, GitHub Actions outage, Supabase upload failing without surfacing). Independent path — does not query GitHub, only Supabase Storage.

| Field | Value |
| --- | --- |
| Title | `ownership-ops-hub: staleness watchdog` |
| URL | `https://isccbmgjgtdosiccstcp.supabase.co/storage/v1/object/public/dashboard-data/last_sync.json` |
| Request method | `GET` |
| Schedule | Every 30 min, `06:30–23:30` EAT, Mon–Sat (offset from Job 1 so we never check at the exact instant of a write). |
| Response validation | Under "Notifications" → "Notify on": **enable** "Notify if response does not contain expected text". Expected text: `2026-` (year prefix — crude but catches a totally stale file from a prior year if the bucket is wiped). |
| Threshold check | cron-job.org does not natively parse JSON timestamps, so the recency check is enforced indirectly: if the watchdog has alerted in the past 45 min AND Job 1 has not succeeded since, manually investigate. For a true threshold check, escalate to a tiny serverless function (out of scope for this setup). |
| Notifications | Enable email on failure AND on response-validation mismatch. |

**Acceptable degradation:** the year-prefix check only catches catastrophic staleness. The real freshness signal lives in the dashboard — kelvin sees the cache age in the UI. The watchdog is the second line of defense.

## Disabling the old GitHub Actions schedule

Already done in PR #24 by removing the `schedule:` block from `.github/workflows/poll.yml`. The workflow still appears as "active" in the Actions tab because `workflow_dispatch` is the active trigger now.

## Rotating the PAT

GitHub fine-grained PATs expire. Set a calendar reminder ~2 weeks before expiry. To rotate:

1. Generate new PAT (same scope: `ownership-ops-hub` only, `Actions: Read and write`).
2. Update the Bearer Token field on Job 1 in cron-job.org.
3. Sanity-check with the `curl -i` command above — expect `204`.
4. Delete the old PAT from GitHub settings.

## Failure modes and where they surface

| Failure | Surfaces as | Mitigation |
| --- | --- | --- |
| PAT revoked / expired | cron-job.org email "Job failed: 401" | Rotate PAT (above) |
| Repo renamed / deleted | cron-job.org email "Job failed: 404" | Update URL on Job 1 |
| GitHub Actions runner outage | Workflow runs queued forever; Job 1 still returns 204 | Watchdog (Job 2) catches stale `last_sync.json`; check status.github.com |
| Airtable API down | Workflow runs but Poll step fails; Sync step never executes; `last_sync.json` stays stale | Watchdog catches stale file; check airtable.statuspage.io |
| Supabase Storage write fails | Workflow shows success but cache not updated | Investigate Sync step logs; check status.supabase.com |
| cron-job.org account suspended / outage | No dispatches fire; no failure email (the failure detector is the thing that's down) | Quarterly manual verification: `gh run list ... --limit 10 --json event` should show every recent run as `workflow_dispatch` from the external IP range |
