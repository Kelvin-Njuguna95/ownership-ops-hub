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
BASE_ID  = "appHZdfC2sn9MLGFZ"
TABLE_ID = "tblpj9aJP4ExhYCZF"
API_URL  = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Field IDs (mirror extract_v2.FIELD_IDS for sort/filter param values).
FLD_LAST_MODIFIED      = "fld0hZzdjksTCKJ09"
FLD_CREATED            = "fldZL6JmYMlFIhCLl"
FLD_START_TAGGING      = "fld7fm1PknPk1UueW"
FLD_QA_STATUS_TS       = "fld9f125Y5wZt1Ctk"
FLD_DONE_SELECTED_TIME = "fldbcTW2CD2HjejGN"

# Page caps. Per the post-truncation audit (see ``audit_today_metrics.md``):
# the active working set on a busy day is ~5,500+ records for tagged_today
# alone. 100 pages × 100/page = 10,000 record ceiling per fetch — comfortably
# above any historical day, and any cap-hit aborts the cycle (exit 2) so
# truncation can never silently under-report metrics. Bumping the ceiling
# higher in a real squeeze is preferable to letting bad data ship.
RECENT_PAGE_CAP        = 200  # Fetch A: ownership-team work in past 24h (bumped from 100 — exceeded 10k on 2026-05-18)
INTAKE_PAGE_CAP        = 30   # Fetch B: today's whole-table intake (cache-only, header KPI uses metadata count)
BOQA_PAGE_CAP          = 30   # Fetch C: current BO QA queue
TAGGED_PAGE_CAP        = 100  # Fetch D: tagged today
DONE_PAGE_CAP          = 100  # Fetch E: done OR valid today
QA_REVIEWED_PAGE_CAP   = 100  # Fetch F: qa_status_ts today
WW_QA_BACKLOG_PAGE_CAP = 30   # Fetch G: assigned-to-WW-QA-but-not-reviewed


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


def _paginate(headers, params, file_prefix, page_cap, label, on_truncate="error"):
    """Page through the Airtable API and save each page as
    .poll_work/<prefix>_p<N>.json. Returns (n_pages_saved, n_records_total,
    hit_cap_with_more_remaining).

    ``on_truncate`` is "error" (default) or "warn":
      - "error": exit(2) with a loud message when cap is hit with more
        records pending. Default for today-scoped fetches where a silent
        truncation produces wrong KPI numbers.
      - "warn": log a warning but keep the partial data. Used for fetch B
        (intake) where the headline KPI comes from metadata.totalRecordCount
        and the cache is intentionally bounded.
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
    if truncated and on_truncate == "error":
        print(f"\n!! TRUNCATION ERROR: {label} hit cap of {page_cap} pages "
              f"({n_records} records) with more remaining. Today-scoped "
              f"metrics would silently under-report. Bump the page cap in "
              f"poll_airtable.py and retry.\n", file=sys.stderr)
        sys.exit(2)
    return n_pages, n_records, truncated


def main():
    pat = os.environ.get("AIRTABLE_PAT")
    if not pat:
        print("AIRTABLE_PAT env var required (Airtable personal access token "
              "with read access to base appHZdfC2sn9MLGFZ)", file=sys.stderr)
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
    # Fetch A captures BOTH:
    #   (a) records modified in past 24h with an ownership-team assignee — the original
    #       "team's recent work" intent.
    #   (b) records created in past 24h regardless of assignee — fresh client uploads
    #       that haven't been picked up yet. Without this, brand-new tasks are invisible
    #       on the Tasks page until someone gets assigned (see 2026-05-18 incident:
    #       4,662 CargoChangeIntel18May2026 records uploaded at 22:34 EAT, all with
    #       empty assignee, missed by every fetch until next-day pickup).
    filter_a = (
        "AND("
        "IS_AFTER({last_modified}, DATEADD(NOW(), -1, 'days')), "
        "OR("
        f"{_build_roster_or_clause()}, "
        "IS_AFTER({Created}, DATEADD(NOW(), -1, 'days'))"
        ")"
        ")"
    )
    params_a = {
        **common,
        "filterByFormula":      filter_a,
        "sort[0][field]":       FLD_LAST_MODIFIED,
        "sort[0][direction]":   "desc",
    }
    n_pages_a, n_rec_a, _ = _paginate(headers, params_a, "recent", RECENT_PAGE_CAP, "Fetch A")
    print(f"  → {n_pages_a} pages, {n_rec_a} records")

    # ---- Fetch B: today's table intake ----
    # Airtable's TODAY() is UTC-based. Comparing {Created} (UTC) against TODAY() (UTC)
    # silently drops records created in the 21:00-23:59 UTC window (= 00:00-02:59 EAT),
    # which are "today in EAT" but "yesterday in UTC". Shift BOTH sides into EAT
    # (UTC+3) before comparing dates so the filter matches the operational day.
    #
    # NOTE: this fetch uses on_truncate="warn" — its headline KPI comes from
    # metadata.totalRecordCount (the whole-table count Airtable returns on
    # page 1), so the cache being bounded at 30 pages is intentional.
    print("\n=== Fetch B: today's table intake (intake_p*.json) ===")
    filter_b = "IS_SAME(DATEADD({Created}, 3, 'hours'), DATEADD(NOW(), 3, 'hours'), 'day')"
    params_b = {
        **common,
        "filterByFormula":      filter_b,
        "sort[0][field]":       FLD_CREATED,
        "sort[0][direction]":   "desc",
    }
    n_pages_b, n_rec_b, trunc_b = _paginate(headers, params_b, "intake", INTAKE_PAGE_CAP, "Fetch B", on_truncate="warn")
    print(f"  → {n_pages_b} pages, {n_rec_b} records"
          f"{' (cache bounded at cap; KPI uses metadata.totalRecordCount)' if trunc_b else ''}")

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
    n_pages_c, n_rec_c, _ = _paginate(headers, params_c, "boqa", BOQA_PAGE_CAP, "Fetch C")
    print(f"  → {n_pages_c} pages, {n_rec_c} records")

    # ---- Fetch D: records with start_tagging today (EAT) ----
    # The authoritative source for tagged_today + every derived metric
    # (per-team, per-agent, sanctions/non-sanctions splits, sampling %, etc.)
    # Replaces the previous practice of filtering Fetch A's 2000-record cache
    # by start_tagging — which silently under-reported when more than ~2000
    # ownership-team records were touched in past 24h.
    print("\n=== Fetch D: tagged today (tagged_today_p*.json) ===")
    filter_d = "IS_SAME(DATEADD({start tagging date}, 3, 'hours'), DATEADD(NOW(), 3, 'hours'), 'day')"
    params_d = {
        **common,
        "filterByFormula":      filter_d,
        "sort[0][field]":       FLD_START_TAGGING,
        "sort[0][direction]":   "desc",
    }
    n_pages_d, n_rec_d, _ = _paginate(headers, params_d, "tagged_today", TAGGED_PAGE_CAP, "Fetch D")
    print(f"  → {n_pages_d} pages, {n_rec_d} records")

    # ---- Fetch E: records done today OR valid today (EAT) ----
    # Split into two fetches (E1 + E2) and union in the cache. The
    # combined OR formula silently truncates because Valid Selected Time
    # is a FORMULA field (per Airtable schema); Airtable's OR over a
    # formula field returns far fewer rows than each clause alone (verified:
    # individual fetches return 124 + 3,241 = 3,315 union, OR returns 83).
    # The aggregator dedupes by record id across all fetch files, so
    # writing them separately gives the correct union without any
    # in-script dedup logic.
    print("\n=== Fetch E1: Done Selected Time today (done_today_p*.json) ===")
    filter_e1 = "IS_SAME(DATEADD({Done Selected Time}, 3, 'hours'), DATEADD(NOW(), 3, 'hours'), 'day')"
    params_e1 = {
        **common,
        "filterByFormula":      filter_e1,
        "sort[0][field]":       FLD_DONE_SELECTED_TIME,
        "sort[0][direction]":   "desc",
    }
    n_pages_e1, n_rec_e1, _ = _paginate(headers, params_e1, "done_today", DONE_PAGE_CAP, "Fetch E1")
    print(f"  → {n_pages_e1} pages, {n_rec_e1} records")

    print("\n=== Fetch E2: Valid Selected Time today (valid_today_p*.json) ===")
    filter_e2 = "IS_SAME(DATEADD({Valid Selected Time}, 3, 'hours'), DATEADD(NOW(), 3, 'hours'), 'day')"
    params_e2 = {
        **common,
        "filterByFormula":      filter_e2,
        # No sort — sorting on a formula field is unreliable, and dedup at
        # aggregator level makes ordering irrelevant for cache writes.
    }
    n_pages_e2, n_rec_e2, _ = _paginate(headers, params_e2, "valid_today", DONE_PAGE_CAP, "Fetch E2")
    print(f"  → {n_pages_e2} pages, {n_rec_e2} records")
    n_rec_e = n_rec_e1 + n_rec_e2
    n_pages_e = n_pages_e1 + n_pages_e2

    # ---- Fetch F: records QA-reviewed today (qa_status_ts EAT today) ----
    # Drives qa_inspected_today, qa_changed_today, reject_rate, per-QA today
    # reviews. Switching the gate from "tagged_today AND has qa_status" to
    # "qa_status_ts today" correctly captures cross-day reviews (record
    # tagged yesterday, QA'd today).
    print("\n=== Fetch F: QA-reviewed today (qa_reviewed_today_p*.json) ===")
    filter_f = "IS_SAME(DATEADD({QA_status_ts}, 3, 'hours'), DATEADD(NOW(), 3, 'hours'), 'day')"
    params_f = {
        **common,
        "filterByFormula":      filter_f,
        "sort[0][field]":       FLD_QA_STATUS_TS,
        "sort[0][direction]":   "desc",
    }
    n_pages_f, n_rec_f, _ = _paginate(headers, params_f, "qa_reviewed_today", QA_REVIEWED_PAGE_CAP, "Fetch F")
    print(f"  → {n_pages_f} pages, {n_rec_f} records")

    # ---- Fetch G: WW QA backlog (assigned to WW QA but not yet reviewed) ----
    # Different semantics from the legacy ww_qa_backlog computation
    # (which used vs == "Selected for WW QA"). This filter:
    #   {WW QA assignee} != BLANK() AND {WW QA} = BLANK()
    # captures records assigned to a WW QA reviewer who hasn't recorded a
    # decision yet — the true backlog from the WW QA's perspective.
    print("\n=== Fetch G: WW QA backlog (ww_qa_backlog_p*.json) ===")
    filter_g = 'AND(NOT({WW QA assignee} = BLANK()), {WW QA} = BLANK())'
    params_g = {
        **common,
        "filterByFormula":      filter_g,
        "sort[0][field]":       FLD_START_TAGGING,
        "sort[0][direction]":   "desc",
    }
    n_pages_g, n_rec_g, _ = _paginate(headers, params_g, "ww_qa_backlog", WW_QA_BACKLOG_PAGE_CAP, "Fetch G")
    print(f"  → {n_pages_g} pages, {n_rec_g} records")

    elapsed = time.time() - t0
    total_pages = n_pages_a + n_pages_b + n_pages_c + n_pages_d + n_pages_e + n_pages_f + n_pages_g
    total_recs  = n_rec_a + n_rec_b + n_rec_c + n_rec_d + n_rec_e + n_rec_f + n_rec_g
    print(f"\nSummary: A={n_rec_a} B={n_rec_b} C={n_rec_c} "
          f"D={n_rec_d} E={n_rec_e} F={n_rec_f} G={n_rec_g} | "
          f"{total_pages} pages, {total_recs} records, {elapsed:.1f}s")


if __name__ == "__main__":
    main()
