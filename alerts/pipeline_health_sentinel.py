#!/usr/bin/env python3
"""Pipeline health sentinel — Slack alerts for the two failure modes that lose data.

From the 2026-06-11 data-durability audit: the only real data loss came from an
18.7-hour poll outage nobody noticed (2026-05-29 17:50 → 05-30 12:30 EAT) plus a
snapshot that froze mid-afternoon. Both are detectable the same day. This runs two
independent checks and posts to Slack (same channel/path as stale_uploads_alert.py)
when something is wrong; it stays quiet and exits 0 when healthy.

  1. snapshot   — end-of-day freshness. Plain GET of the PUBLIC bucket's
                  snapshots/<today-EAT>.json (no keys). Alerts if it's missing or
                  its computed_at is earlier than 18:30 EAT (froze early — the
                  2026-05-29 failure mode). Intended ~19:30 EAT, Mon–Sat.
  2. blindwindow — intraday liveness. GitHub API for the poll.yml workflow's last
                  SUCCESSFUL run; alerts if it's older than 60 min (scheduler death
                  or a failure streak — the 2026-05-29/30 outage). Intended a few
                  times in business hours (e.g. 09:30 / 13:30 / 17:30 EAT, Mon–Sat).

Sundays are skipped (agents are off, per the roster work-schedule). Healthy → exit 0;
any fired alert → loud log + Slack + exit 1; an operational error (can't reach the
bucket / GitHub, no Slack credential) → exit 2.

Read-only everywhere: GETs to the public bucket and the GitHub API; the only write
is the Slack POST. Mirrors the poll pipeline: workflow_dispatch-only GitHub Actions
workflow, triggered by cron-job.org (GitHub `schedule:` events are dropped on
free-tier public repos — see poll.yml / docs/EXTERNAL_CRON_SETUP.md).

Env:
  Slack (one of, only needed when an alert actually fires):
    SLACK_WEBHOOK_URL   incoming-webhook URL (preferred — channel-bound)
    SLACK_BOT_TOKEN     bot token (xoxb-…) + optional SLACK_CHANNEL (default below)
  Blind-window check (auto-set inside GitHub Actions):
    GITHUB_TOKEN        token with actions:read on this repo
    GITHUB_REPOSITORY   "owner/repo"
  Optional overrides (have public defaults — handy for tests):
    STORAGE_PUBLIC_URL  public bucket base (default = the dashboard's bucket)
    GITHUB_API_URL      GitHub API base (default https://api.github.com)

Usage:
  python3 alerts/pipeline_health_sentinel.py --check snapshot
  python3 alerts/pipeline_health_sentinel.py --check blindwindow
  python3 alerts/pipeline_health_sentinel.py --check both          # default
  python3 alerts/pipeline_health_sentinel.py --check both --dry-run # compose + print, no POST
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta, time as dtime

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EAT = timezone(timedelta(hours=3))
DEFAULT_CHANNEL = "windward-team-leaders"   # same channel as stale_uploads_alert.py

# Snapshot-freshness threshold. The final daily snapshot normally finalises
# ~23:5x EAT. On 2026-05-29 a poll outage froze it at computed_at = 17:54, and the
# Weekly/trend snapshots under-reported the evening for that day. A complete day's
# snapshot must therefore be stamped no earlier than this cutoff when we check at
# ~19:30. (Audit: private-notes/report-data-audit-2026-06-11.md; CHANGELOG 2026-06-12.)
SNAPSHOT_MIN_COMPUTED_EAT = dtime(18, 30)

# Blind-window threshold. The poll fires every ~10 min via cron-job.org; short gaps
# are harmless (the detector's 24h look-back recovers them — proven 2026-06-10, a
# 12-failure day with zero data lost). But a multi-hour business-window blackout
# loses data permanently (proven 2026-05-30: ~325 captured vs ~6,000 typical). 60 min
# is comfortably longer than one missed cycle yet far shorter than a damaging outage.
BLIND_WINDOW_MAX_AGE_MIN = 60

POLL_WORKFLOW_FILE = "poll.yml"   # the workflow whose liveness we watch
DEFAULT_STORAGE_PUBLIC = (
    "https://isccbmgjgtdosiccstcp.supabase.co/storage/v1/object/public/dashboard-data"
)  # public bucket (same URL embedded in deploy/index.html — not a secret)
DEFAULT_GITHUB_API = "https://api.github.com"

# Exit codes: 0 healthy/skipped, 1 alert fired, 2 operational error.
EXIT_HEALTHY, EXIT_ALERT, EXIT_ERROR = 0, 1, 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_iso(s):
    """Parse an ISO-8601 timestamp (handles trailing 'Z'); None on failure."""
    if not s or not isinstance(s, str):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _fmt_age(minutes):
    """'73 min (1h13m)' style age label."""
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes} min ({minutes // 60}h{minutes % 60:02d}m)"


def storage_public_url():
    return os.environ.get("STORAGE_PUBLIC_URL", DEFAULT_STORAGE_PUBLIC).rstrip("/")


# ---------------------------------------------------------------------------
# Check 1 — snapshot freshness (end-of-day)
# ---------------------------------------------------------------------------
def fetch_snapshot(date_str):
    """GET the public bucket's snapshots/<date>.json. Returns the parsed dict,
    or None when the object is missing (404). Raises on any other failure."""
    url = f"{storage_public_url()}/snapshots/{date_str}.json"
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"snapshot GET {date_str}: HTTP {r.status_code} {r.text[:160]}")
    try:
        return r.json()
    except ValueError as e:
        raise RuntimeError(f"snapshot GET {date_str}: bad JSON ({e})")


def check_snapshot(now_eat, snapshot):
    """Pure logic. Returns (healthy: bool, message: str|None).

    `snapshot` is the parsed dict, or None when missing. Message is the Slack
    text when unhealthy, else None."""
    date_str = now_eat.date().isoformat()
    stamp = f"_Checked {now_eat:%Y-%m-%d %H:%M} EAT._"

    if snapshot is None:
        return False, (
            f":rotating_light: *Pipeline health — no end-of-day snapshot for {date_str}.* "
            f"`snapshots/{date_str}.json` is missing from the bucket — the aggregator "
            f"hasn't written today's snapshot, so the pipeline may be dark. "
            f"Weekly/trend/date-picker views for {date_str} will have no data. {stamp}"
        )

    computed = _parse_iso(snapshot.get("computed_at"))
    if computed is None:
        return False, (
            f":rotating_light: *Pipeline health — today's snapshot ({date_str}) has no usable "
            f"`computed_at`.* Raw value: `{snapshot.get('computed_at')!r}`. Can't confirm it "
            f"finalised; treat as suspect. {stamp}"
        )

    computed_eat = computed.astimezone(EAT)
    cutoff = datetime.combine(now_eat.date(), SNAPSHOT_MIN_COMPUTED_EAT, tzinfo=EAT)
    if computed_eat < cutoff:
        gap_min = int((cutoff - computed_eat).total_seconds() // 60)
        return False, (
            f":warning: *Pipeline health — today's snapshot ({date_str}) froze early.* "
            f"Last aggregator run `computed_at` = *{computed_eat:%H:%M} EAT* — "
            f"*{gap_min} min* before the {SNAPSHOT_MIN_COMPUTED_EAT:%H:%M} cutoff. The evening "
            f"will be under-reported in the Weekly report, trend strips and the date-picker "
            f"view for {date_str} (the 2026-05-29 failure mode). {stamp}"
        )

    return True, None


# ---------------------------------------------------------------------------
# Check 2 — blind window (intraday liveness)
# ---------------------------------------------------------------------------
def fetch_latest_poll_success():
    """Most-recent SUCCESSFUL poll.yml run timestamp (UTC, aware), or None when
    there are no successful runs. Raises on missing creds / API error."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY not set — cannot query poll.yml runs.")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set — cannot query poll.yml runs.")
    api = os.environ.get("GITHUB_API_URL", DEFAULT_GITHUB_API).rstrip("/")
    url = f"{api}/repos/{repo}/actions/workflows/{POLL_WORKFLOW_FILE}/runs"
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params={"status": "success", "per_page": "1", "exclude_pull_requests": "true"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GitHub runs API: HTTP {r.status_code} {r.text[:160]}")
    runs = (r.json() or {}).get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    ts = _parse_iso(run.get("run_started_at") or run.get("created_at"))
    if ts is None:
        raise RuntimeError(f"GitHub runs API: run has no parseable timestamp ({run.get('id')})")
    return ts.astimezone(timezone.utc)


def check_blind_window(now_utc, last_success_utc):
    """Pure logic. Returns (healthy: bool, message: str|None).

    `last_success_utc` is the most-recent successful poll.yml run (UTC) or None."""
    now_eat = now_utc.astimezone(EAT)
    stamp = f"_Checked {now_eat:%Y-%m-%d %H:%M} EAT._"

    if last_success_utc is None:
        return False, (
            f":rotating_light: *Pipeline health — no successful `poll.yml` run found.* "
            f"The poll pipeline appears to have never succeeded (or the window is empty). "
            f"Records completed-and-absorbed now are being lost. {stamp}"
        )

    age_min = (now_utc - last_success_utc).total_seconds() / 60.0
    if age_min > BLIND_WINDOW_MAX_AGE_MIN:
        last_eat = last_success_utc.astimezone(EAT)
        return False, (
            f":rotating_light: *Pipeline health — poll pipeline is blind.* Last successful "
            f"`poll.yml` run was *{_fmt_age(age_min)} ago* (at *{last_eat:%H:%M} EAT*), past the "
            f"{BLIND_WINDOW_MAX_AGE_MIN}-min threshold. Short gaps recover via the 24h look-back, "
            f"but a multi-hour business-window blackout loses data permanently (2026-05-30). "
            f"Check cron-job.org and the runner. {stamp}"
        )

    return True, None


# ---------------------------------------------------------------------------
# Slack (reuses the stale_uploads_alert.py channel + credential fallback)
# ---------------------------------------------------------------------------
def post_to_slack(message):
    """POST to Slack via webhook (preferred) or bot token. Raises on failure."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        r = requests.post(webhook, json={"text": message}, timeout=30)
        if r.status_code != 200 or r.text.strip() != "ok":
            raise RuntimeError(f"Slack webhook POST failed: {r.status_code} {r.text[:200]}")
        return "webhook"
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        channel = os.environ.get("SLACK_CHANNEL", DEFAULT_CHANNEL)
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"channel": channel, "text": message},
            timeout=30,
        )
        data = r.json() if r.text else {}
        if r.status_code != 200 or not data.get("ok"):
            raise RuntimeError(f"Slack chat.postMessage failed: {r.status_code} {data.get('error', r.text[:200])}")
        return f"bot→#{channel}"
    raise RuntimeError(
        "No Slack credential. Set SLACK_WEBHOOK_URL (incoming webhook) or "
        "SLACK_BOT_TOKEN (+ optional SLACK_CHANNEL)."
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_snapshot_check(now_eat):
    """Fetch + evaluate the snapshot check. Returns (healthy, message)."""
    snapshot = fetch_snapshot(now_eat.date().isoformat())
    return check_snapshot(now_eat, snapshot)


def run_blindwindow_check(now_utc):
    """Fetch + evaluate the blind-window check. Returns (healthy, message)."""
    last = fetch_latest_poll_success()
    return check_blind_window(now_utc, last)


def main(argv=None, now_utc=None):
    parser = argparse.ArgumentParser(description="Pipeline health sentinel (snapshot + blind-window).")
    parser.add_argument("--check", choices=["snapshot", "blindwindow", "both"], default="both",
                        help="which check(s) to run (default: both).")
    parser.add_argument("--dry-run", action="store_true",
                        help="compose + print any alert, do not POST to Slack.")
    args = parser.parse_args(argv)

    # now_utc is injectable for tests; default to the real clock.
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_eat = now_utc.astimezone(EAT)

    # Sundays: agents are off (roster work-schedule). Skip — no work to under-report,
    # and the pipeline is expected quiet.
    if now_eat.weekday() == 6:
        print(f"Sunday ({now_eat:%Y-%m-%d}) — agents off; sentinel skipped.")
        return EXIT_HEALTHY

    checks = ["snapshot", "blindwindow"] if args.check == "both" else [args.check]
    runners = {
        "snapshot":    lambda: run_snapshot_check(now_eat),
        "blindwindow": lambda: run_blindwindow_check(now_utc),
    }

    fired = False
    for name in checks:
        try:
            healthy, message = runners[name]()
        except Exception as e:
            # Operational failure (bucket/GitHub unreachable, bad creds). Fail loud.
            print(f"🛑 sentinel ERROR running '{name}' check: {e}", file=sys.stderr)
            return EXIT_ERROR

        if healthy:
            print(f"✓ {name}: healthy.")
            continue

        fired = True
        print(f"🚨 ALERT [{name}]:\n{message}\n")
        if args.dry_run:
            print(f"   (--dry-run: not posting to Slack)")
            continue
        try:
            target = post_to_slack(message)
            print(f"   posted to Slack ({target}).")
        except Exception as e:
            print(f"🛑 sentinel ERROR posting '{name}' alert to Slack: {e}", file=sys.stderr)
            return EXIT_ERROR

    return EXIT_ALERT if fired else EXIT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
