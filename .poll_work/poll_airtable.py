#!/usr/bin/env python3
"""Phase F2 standalone Airtable poller.

Hits the Airtable REST API directly (plain ``requests``, no Cowork MCP),
runs the three filtered fetches documented in POLL_PROCEDURE.md, and writes
each page to a JSON file in the shape the existing aggregator expects:

  {"records": [...], "metadata": {"totalRecordCount": N}, "nextCursor": "..."}

Outputs:
  .poll_work/recent_p*.json   — Fetch A (ownership-team work, past 24h)
  .poll_work/intake_p*.json   — Fetch B (today's table intake)
  .poll_work/boqa_p*.json     — Fetch C (current BO QA queue)

Airtable is strictly read-only — this script only issues GET requests.
Designed to run in CI: reads ``AIRTABLE_PAT`` from env, exits non-zero
on any HTTP error.
"""
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. pip install requests", file=sys.stderr)
    sys.exit(1)

HERE     = Path(__file__).resolve().parent
ROOT     = HERE.parent
BASE_ID  = "REDACTED_BASE_ID"
TABLE_ID = "tblpj9aJP4ExhYCZF"
API_URL  = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Field IDs (mirror extract_v2.FIELD_IDS for the few fields this script
# references directly — sort + filter param values).
FLD_LAST_MODIFIED = "fld0hZzdjksTCKJ09"
FLD_CREATED       = "fldZL6JmYMlFIhCLl"
FLD_START_TAGGING = "fld7fm1PknPk1UueW"

# Cap recent pages at 20 so a freak ops-spike day can't run away (cf.
# POLL_PROCEDURE.md note — "drain in well under 10 pages on a busy day").
RECENT_PAGE_CAP = 20
# Intake cap matches the procedure doc — top 3,000 records of today's intake.
INTAKE_PAGE_CAP = 30
# BO QA queue typically < 1,500 records; 30 pages = 3,000 is a generous ceiling.
BOQA_PAGE_CAP   = 30


def _wipe(glob):
    """Remove existing files matching the glob so a fresh page run can
    overwrite the previous cycle's output without leaving stragglers."""
    for p in HERE.glob(glob):
        try:
            p.unlink()
        except OSError:
            pass


def _build_roster_or_clause():
    """Build the OR(FIND(name, ARRAYJOIN({assignee}, ", "))…) clause from
    config/roster.json. Mirrors the inline formula in POLL_PROCEDURE.md but
    auto-updates when the roster changes — no manual edit needed.

    Includes every members[].name, every alias, every qa.name, and every
    non-null ww_qa.name across all 5 teams.
    """
    roster_path = ROOT / "config" / "roster.json"
    roster = json.loads(roster_path.read_text())["teams"]
    names = set()
    for info in roster.values():
        for member in info.get("members", []):
            if member.get("name"):
                names.add(member["name"])
            for alias in (member.get("aliases") or []):
                if alias:
                    names.add(alias)
        qa = info.get("qa")
        if isinstance(qa, dict) and qa.get("name"):
            names.add(qa["name"])
        ww = info.get("ww_qa")
        if isinstance(ww, dict) and ww.get("name"):
            names.add(ww["name"])
    finds = [f'FIND("{n}", ARRAYJOIN({{assignee}}, ", "))' for n in sorted(names)]
    return "OR(" + ", ".join(finds) + ")"


def _paginate(headers, params, file_prefix, page_cap, label):
    """Page through the Airtable API and save each page as
    .poll_work/<prefix>_p<N>.json. Returns (n_pages_saved, n_records_total,
    hit_cap_with_more_remaining).
    """
    _wipe(f"{file_prefix}_p*.json")
    n_pages    = 0
    n_records  = 0
    offset     = None
    truncated  = False
    while n_pages < page_cap:
        q = dict(params)
        if offset:
            q["offset"] = offset
        r = requests.get(API_URL, headers=headers, params=q, timeout=60)
        if r.status_code != 200:
            print(f"  [FAIL {r.status_code}] {label} page {n_pages + 1}: "
                  f"{r.text[:200]}", file=sys.stderr)
            sys.exit(2)
        body    = r.json()
        records = body.get("records", []) or []
        offset  = body.get("offset")
        n_pages += 1
        n_records += len(records)
        # totalRecordCount: best-effort from records actually loaded. Airtable's
        # REST API doesn't return a true table-wide count. If we hit the cap
        # AND offset is still set, more records exist beyond — boost the count
        # past 3,000 so the dashboard's intake_partial banner fires.
        if n_pages >= page_cap and offset:
            truncated = True
        page_doc = {
            "records":  records,
            "metadata": {"totalRecordCount": max(n_records, 3001 if truncated else n_records)},
            "nextCursor": offset,
        }
        (HERE / f"{file_prefix}_p{n_pages}.json").write_text(
            json.dumps(page_doc, indent=2)
        )
        if not offset:
            break
    return n_pages, n_records, truncated


def main():
    pat = os.environ.get("AIRTABLE_PAT")
    if not pat:
        print("AIRTABLE_PAT env var required (Airtable personal access token "
              "with read access to base REDACTED_BASE_ID)", file=sys.stderr)
        sys.exit(1)
    headers = {"Authorization": f"Bearer {pat}"}
    common = {
        "pageSize": "100",
        "returnFieldsByFieldId": "true",
    }
    t0 = time.time()
    print(f"Phase F2 poller — read-only against {API_URL}")

    # ---- Fetch A: ownership-team work, past 24h ----
    print("\n=== Fetch A: ownership-team work (recent_p*.json) ===")
    filter_a = (
        "AND("
        "IS_AFTER({last_modified}, DATEADD(NOW(), -1, 'days')), "
        f"{_build_roster_or_clause()}"
        ")"
    )
    params_a = {
        **common,
        "filterByFormula":      filter_a,
        "sort[0][field]":       FLD_LAST_MODIFIED,
        "sort[0][direction]":   "desc",
    }
    n_pages_a, n_rec_a, trunc_a = _paginate(headers, params_a, "recent", RECENT_PAGE_CAP, "Fetch A")
    print(f"  → {n_pages_a} pages, {n_rec_a} records"
          f"{' (TRUNCATED — hit cap with more remaining)' if trunc_a else ''}")

    # ---- Fetch B: today's table intake ----
    print("\n=== Fetch B: today's table intake (intake_p*.json) ===")
    filter_b = "IS_SAME({Created}, TODAY(), 'day')"
    params_b = {
        **common,
        "filterByFormula":      filter_b,
        "sort[0][field]":       FLD_CREATED,
        "sort[0][direction]":   "desc",
    }
    n_pages_b, n_rec_b, trunc_b = _paginate(headers, params_b, "intake", INTAKE_PAGE_CAP, "Fetch B")
    print(f"  → {n_pages_b} pages, {n_rec_b} records"
          f"{' (TRUNCATED — hit cap with more remaining)' if trunc_b else ''}")

    # ---- Fetch C: current BO QA queue ----
    # Keep the trailing space on "Selected for BO QA " — it's the literal
    # Airtable choice name and other tools depend on it.
    print("\n=== Fetch C: current BO QA queue (boqa_p*.json) ===")
    filter_c = '{verification_status} = "Selected for BO QA "'
    params_c = {
        **common,
        "filterByFormula":      filter_c,
        "sort[0][field]":       FLD_START_TAGGING,
        "sort[0][direction]":   "desc",
    }
    n_pages_c, n_rec_c, trunc_c = _paginate(headers, params_c, "boqa", BOQA_PAGE_CAP, "Fetch C")
    print(f"  → {n_pages_c} pages, {n_rec_c} records"
          f"{' (TRUNCATED — hit cap with more remaining)' if trunc_c else ''}")

    elapsed = time.time() - t0
    print(f"\nTotal: {n_pages_a + n_pages_b + n_pages_c} page files "
          f"({n_rec_a + n_rec_b + n_rec_c} records) in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
