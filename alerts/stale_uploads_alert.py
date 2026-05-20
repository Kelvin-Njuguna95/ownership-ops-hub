#!/usr/bin/env python3
"""Daily Slack digest of client uploads sitting unclaimed > 3 days.

A record is "unclaimed" when nobody has moved it waiting→tagged — its
verification_status is ``waiting`` OR blank (both occur for fresh client
uploads; both have a task name and no assignee). Such records age out of the
dashboard's Fetch A 24h / 7-day windows, so there's no proactive warning. This
posts a once-daily digest to Slack (#windward-team-leaders) of unclaimed
uploads older than 3 days, grouped by task (requested_by).

Read-only against Airtable — only GET requests. Mirrors the poll pipeline:
workflow_dispatch-only GitHub Actions workflow, triggered by cron-job.org.

Env:
  AIRTABLE_PAT          (required)
  SLACK_WEBHOOK_URL     incoming-webhook URL (preferred — channel-bound)
    --- or, if no webhook ---
  SLACK_BOT_TOKEN       bot token (xoxb-…) + SLACK_CHANNEL (default below)
  SLACK_CHANNEL         channel for the bot-token path (default windward-team-leaders)

Usage:
  python3 alerts/stale_uploads_alert.py            # compose + POST to Slack
  python3 alerts/stale_uploads_alert.py --dry-run  # compose + print, no POST
"""
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

EAT = timezone(timedelta(hours=3))
STALE_DAYS = 3
DEFAULT_CHANNEL = "windward-team-leaders"
MAX_TASK_LINES = 15  # cap the per-task lines; remainder summarised as "+X more"

# Airtable base / table — same as poll_airtable.py / completion_detector.py.
BASE_ID  = "REDACTED_BASE_ID"
TABLE_ID = "tblpj9aJP4ExhYCZF"
AIRTABLE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Field IDs (mirror extract_v2.FIELD_IDS).
FLD_VERIFICATION_STATUS = "fldYSXHGwZvxXK7s6"
FLD_CREATED             = "fldZL6JmYMlFIhCLl"
FLD_REQUESTED_BY        = "fldlPkvV6BiE7glLZ"

PAGE_CAP = 200  # 200 × 100 = 20k-record ceiling; raise if the backlog ever exceeds it.

# Unclaimed = waiting OR blank. The age clause uses the poller's EAT-shift idiom
# (DATEADD(..., 3, 'hours')); for a fixed >3-day age the +3h cancels on both
# sides, but it's kept for parity with poll_airtable.py and to read clearly.
FILTER_FORMULA = (
    "AND("
    'OR({verification_status} = "waiting", {verification_status} = BLANK()),'
    "IS_BEFORE("
    "DATEADD({Created}, 3, 'hours'),"
    "DATEADD(DATEADD(NOW(), 3, 'hours'), -3, 'days')"
    ")"
    ")"
)


def _name(val):
    """First 'name'-like value from an Airtable cell (str / dict / list)."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name")
    if isinstance(val, list) and val:
        return _name(val[0])
    return None


def _parse_iso(s):
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def fetch_stale_unclaimed(pat):
    """Read-only, paginated GET of unclaimed records older than STALE_DAYS.
    Only the three fields we need are requested, to keep payloads small."""
    headers = {"Authorization": f"Bearer {pat}"}
    params = {
        "pageSize":              "100",
        "returnFieldsByFieldId": "true",
        "filterByFormula":       FILTER_FORMULA,
        # Restrict payload to the fields we group/age on (smaller + faster).
        "fields[]":              [FLD_VERIFICATION_STATUS, FLD_CREATED, FLD_REQUESTED_BY],
    }
    out = []
    offset = None
    for _page in range(PAGE_CAP):
        q = dict(params)
        if offset:
            q["offset"] = offset
        body = None
        for attempt in range(3):  # retry transient timeouts / 5xx
            try:
                r = requests.get(AIRTABLE_URL, headers=headers, params=q, timeout=90)
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise RuntimeError(f"Airtable request failed after retries: {e}")
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                body = r.json()
                break
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"Airtable {r.status_code}: {r.text[:200]}")
        out.extend(body.get("records", []) or [])
        offset = body.get("offset")
        if not offset:
            break
    else:
        raise RuntimeError(f"fetch hit {PAGE_CAP}-page cap with more pending — raise PAGE_CAP")
    return out


def summarise(records, now_utc):
    """Group records by task (requested_by). Returns (total, [task dicts]) sorted
    by count desc, then oldest age desc. Blank requested_by → '(no task name)'."""
    by_task = defaultdict(lambda: {"count": 0, "oldest_days": 0})
    for rec in records:
        f = rec.get("fields", {})
        task = (_name(f.get(FLD_REQUESTED_BY)) or "").strip() or "(no task name)"
        created = _parse_iso(f.get(FLD_CREATED))
        age_days = int((now_utc - created).total_seconds() // 86400) if created else 0
        t = by_task[task]
        t["count"] += 1
        t["oldest_days"] = max(t["oldest_days"], age_days)
    tasks = [{"name": k, **v} for k, v in by_task.items()]
    tasks.sort(key=lambda t: (-t["count"], -t["oldest_days"], t["name"]))
    return len(records), tasks


def compose_message(total, tasks, now_eat):
    """Build the Slack message text (works for webhook and chat.postMessage)."""
    if total == 0:
        return f":white_check_mark: No uploads sitting unclaimed >{STALE_DAYS} days. (checked {now_eat:%Y-%m-%d %H:%M} EAT)"
    shown = tasks[:MAX_TASK_LINES]
    lines = [
        f":warning: *Stale unclaimed uploads* — *{total}* record{'s' if total != 1 else ''} "
        f"across *{len(tasks)}* task{'s' if len(tasks) != 1 else ''} sitting >{STALE_DAYS} days "
        f"unclaimed (status `waiting`/blank, never tagged). _As of {now_eat:%Y-%m-%d %H:%M} EAT._",
        "",
    ]
    for t in shown:
        lines.append(f"• {t['name']} — {t['count']} record{'s' if t['count'] != 1 else ''}, oldest {t['oldest_days']}d")
    remaining = len(tasks) - len(shown)
    if remaining > 0:
        hidden = sum(t["count"] for t in tasks[MAX_TASK_LINES:])
        lines.append(f"+ {remaining} more task{'s' if remaining != 1 else ''} ({hidden} record{'s' if hidden != 1 else ''})")
    return "\n".join(lines)


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


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    pat = os.environ.get("AIRTABLE_PAT")
    if not pat:
        print("Missing env: AIRTABLE_PAT", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    now_eat = now_utc.astimezone(EAT)

    records = fetch_stale_unclaimed(pat)
    total, tasks = summarise(records, now_utc)
    message = compose_message(total, tasks, now_eat)

    if dry_run:
        print("=== DRY RUN — composed Slack message (not posted) ===\n")
        print(message)
        print(f"\n=== ({total} stale records across {len(tasks)} tasks) ===")
        return

    target = post_to_slack(message)
    print(f"Posted stale-uploads digest to Slack ({target}): {total} records, {len(tasks)} tasks.")


if __name__ == "__main__":
    main()
