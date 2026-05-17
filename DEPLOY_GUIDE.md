# Deployment guide — Supabase + Vercel

The `deploy/` folder contains the production fork of the dashboard. It reads its data from Supabase Storage and gates access behind a magic-link login. The local `dashboard.html` is unchanged — it stays as the dev/test copy you can open with `python3 -m http.server`.

## What this deployment is

- **Hosting:** Vercel (static — just `deploy/index.html` + `deploy/vercel.json`).
- **Data layer:** Supabase Storage, public bucket `dashboard-data`. The dashboard fetches JSON over HTTPS — no MCP, no Cowork, no Airtable round-trips.
- **Auth:** Supabase email magic-link, restricted client-side to `@impactoutsourcing.co.ke` addresses.

## How to run a sync

After each polling cycle (or whenever you want the dashboard to reflect new data):

```bash
python3 sync_to_supabase.py
```

Requires `requests` and `python-dotenv`:

```bash
pip install requests python-dotenv --break-system-packages
# or use a venv:
python3 -m venv .venv && source .venv/bin/activate && pip install requests python-dotenv
```

The script uploads `daily_aggregates.json`, `config/roster.json`, `ww_audit_log.json`, every `.poll_work/snapshots/*.json`, and every `.poll_work/{recent,intake,boqa}_p*.json` to the `dashboard-data` bucket, then writes `last_sync.json` so the dashboard's "Synced Xm ago" pill stays honest.

## How auth works

1. User opens the Vercel URL.
2. Dashboard checks for an existing Supabase session. If absent, the login overlay covers everything.
3. User enters their email. Client-side check: if it doesn't end with `@impactoutsourcing.co.ke`, an inline error appears and no magic link is sent.
4. Valid address → `supabase.auth.signInWithOtp({ email })` fires. User gets an email with a link back to the dashboard URL.
5. Click → Supabase JS SDK auto-processes the URL hash and creates a session. Dashboard re-checks email, hides the overlay, loads data.
6. Top-right pill shows `<email> · Sign out`.

## Where the keys live

- **Anon public key** — hardcoded in `deploy/index.html` (it's designed to be public; safe to commit).
- **Service role key** — in `.env.local` only, never committed. Used by `sync_to_supabase.py` to upload. Grab it from Supabase Dashboard → Settings → API → `service_role` secret.

## Workflow

1. Local poll cycle runs (or you run it manually): refreshes `.poll_work/*` and `daily_aggregates.json`.
2. `python3 sync_to_supabase.py` pushes the new state to Supabase Storage.
3. Anyone on the team refreshes the Vercel URL — sees the new data immediately. No re-deploy needed; only the data changes.

## Known limitations

- **Client-side domain check only.** A determined user could bypass the `@impactoutsourcing.co.ke` restriction by hitting the Supabase auth endpoint directly. Server-side enforcement (a Supabase auth-hook database function rejecting non-domain emails) is Phase F2.
- **Manual sync.** Until Phase F2 you have to run `sync_to_supabase.py` by hand after each polling cycle. A cron job or GitHub Action will automate this later.
- **Anon key has read access to the public bucket.** That's by design — auth gates the UI, not the storage. Don't put secrets in the bucket.
