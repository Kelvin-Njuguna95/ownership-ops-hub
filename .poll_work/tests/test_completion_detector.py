"""Tests for completion_detector — Flow Framework v2 classification,
completed_by attribution, row builders, and first-write-wins semantics.

Network calls (Airtable GET, Supabase INSERT/PATCH) are stubbed via
unittest.mock — the detector is exercised against fake HTTP responses,
no real services hit.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import completion_detector as det  # noqa: E402
from completion_detector import (  # noqa: E402
    DONE,
    FLD_ADD_NEW_COMPANY,
    FLD_ASSIGNEE,
    FLD_COMPANY_ID_AND_NAME,
    FLD_DEAD_VESSEL,
    FLD_IMO,
    FLD_LAST_MODIFIED_BY,
    FLD_QA_ASSIGNEE,
    FLD_QA_STATUS,
    FLD_REQUESTED_BY,
    FLD_ROLE,
    FLD_VERIFICATION_STATUS,
    NEED_TO_BE_UPDATE,
    SELECTED_FOR_BO_QA,
    STUCK_HOURS,
    TAGGED,
    VALID,
    build_completion_row,
    build_sampling_row,
    build_alert_row,
    classify,
    is_complete,
    needs_tagging_row,
    resolve_completed_by,
    route_record,
    supabase_insert_completions,
)


def _fields(**kw):
    """Build a fields dict using extract_v2-style logical names mapped to FIELD_IDS."""
    keymap = {
        "imo":                 FLD_IMO,
        "assignee":            FLD_ASSIGNEE,
        "qa_assignee":         FLD_QA_ASSIGNEE,
        "qa_status":           FLD_QA_STATUS,
        "last_modified_by":    FLD_LAST_MODIFIED_BY,
        "verification_status": FLD_VERIFICATION_STATUS,
        "company_id_and_name": FLD_COMPANY_ID_AND_NAME,
        "dead_vessel":         FLD_DEAD_VESSEL,
        "add_new_company":     FLD_ADD_NEW_COMPANY,
        "role":                FLD_ROLE,
        "requested_by":        FLD_REQUESTED_BY,
    }
    return {keymap[k]: v for k, v in kw.items()}


# ============================================================================
# Pre-Flow detection rule (kept verbatim from v1).
# ============================================================================
class TestPreFlowCompletionRule(unittest.TestCase):
    """The pre-Flow rule still applies to records in `tagged` or
    `need to be update` — first-contact detection with flow=NULL."""

    def test_tagged_with_company_linked(self):
        f = _fields(verification_status=TAGGED, company_id_and_name=["recCo1"])
        self.assertTrue(is_complete(f))

    def test_tagged_with_dead_vessel(self):
        f = _fields(verification_status=TAGGED, dead_vessel=True)
        self.assertTrue(is_complete(f))

    def test_tagged_with_neither(self):
        self.assertFalse(is_complete(_fields(verification_status=TAGGED)))

    def test_need_to_be_update_with_add_new_company(self):
        f = _fields(verification_status=NEED_TO_BE_UPDATE, add_new_company="New Co")
        self.assertTrue(is_complete(f))

    def test_need_to_be_update_without_add_new_company(self):
        self.assertFalse(is_complete(_fields(verification_status=NEED_TO_BE_UPDATE)))

    def test_other_states_not_pre_flow_complete(self):
        for vs in (DONE, VALID, SELECTED_FOR_BO_QA, "waiting"):
            self.assertFalse(is_complete(_fields(verification_status=vs,
                                                  company_id_and_name=["recCo1"])))


# ============================================================================
# Flow Framework v2 classification.
# ============================================================================
class TestClassification(unittest.TestCase):
    """The 5-state classification matrix per Kelvin's spec."""

    # ---- Done/Valid ----
    def test_done_with_no_qa_classified_as_flow_a(self):
        # vs in (Done, Valid) + qa_assignee empty + qa_status empty → Flow A
        for vs in (DONE, VALID):
            target, flow = classify(_fields(verification_status=vs))
            self.assertEqual(target, "completion")
            self.assertEqual(flow, "A", f"vs={vs}: expected A")

    def test_done_with_qa_assignee_and_qa_status_classified_as_flow_c(self):
        for vs in (DONE, VALID):
            f = _fields(verification_status=vs,
                        qa_assignee={"id": "q1", "name": "QA1"},
                        qa_status="approve")
            target, flow = classify(f)
            self.assertEqual(target, "completion")
            self.assertEqual(flow, "C", f"vs={vs}: expected C")

    def test_done_with_qa_assignee_but_no_qa_status_alerts_not_classified(self):
        f = _fields(verification_status=DONE,
                    qa_assignee={"id": "q1", "name": "QA1"})
        target, detail = classify(f)
        self.assertEqual(target, "alert")
        self.assertEqual(detail, "missing_qa_status")

    def test_done_with_qa_status_but_no_qa_assignee_alerts_missing_assignee(self):
        # Rare edge case — qa_status set but no reviewer recorded.
        f = _fields(verification_status=VALID, qa_status="approve")
        target, detail = classify(f)
        self.assertEqual(target, "alert")
        self.assertEqual(detail, "missing_qa_assignee")

    # ---- Selected for BO QA ----
    def test_selected_for_bo_qa_with_assignee_classified_as_flow_b(self):
        f = _fields(verification_status=SELECTED_FOR_BO_QA,
                    qa_assignee={"id": "q1", "name": "QA1"})
        target, flow = classify(f)
        self.assertEqual(target, "sampling")
        self.assertIsNone(flow)

    def test_selected_for_bo_qa_without_assignee_alerts(self):
        f = _fields(verification_status=SELECTED_FOR_BO_QA)
        target, detail = classify(f)
        self.assertEqual(target, "alert")
        self.assertEqual(detail, "missing_qa_assignee")

    # ---- Pre-Flow states ----
    def test_tagged_with_completion_rule_routes_to_pre_flow(self):
        f = _fields(verification_status=TAGGED, company_id_and_name=["recX"])
        target, _ = classify(f)
        self.assertEqual(target, "pre_flow")

    def test_tagged_without_completion_rule_skipped(self):
        f = _fields(verification_status=TAGGED)
        target, _ = classify(f)
        self.assertEqual(target, "skip")

    def test_need_to_be_update_with_rule_routes_to_pre_flow(self):
        f = _fields(verification_status=NEED_TO_BE_UPDATE, add_new_company="X")
        target, _ = classify(f)
        self.assertEqual(target, "pre_flow")

    # ---- Other states ----
    def test_waiting_skipped(self):
        self.assertEqual(classify(_fields(verification_status="waiting")), ("skip", None))

    def test_unknown_vs_skipped(self):
        self.assertEqual(classify(_fields(verification_status="anything")), ("skip", None))

    def test_null_vs_skipped(self):
        self.assertEqual(classify(_fields()), ("skip", None))


# ============================================================================
# completed_by attribution chain — the TAGGER (assignee) wins.
# completed_by feeds the Hourly Output (tagging) heatmap, so credit goes to
# the assignee, falling back to last_modified_by → qa_assignee only when the
# record has no assignee. (Reversed from v1's last_modified_by-first chain —
# see docs/hourly_audit_james_maina_2026-05-20.md.)
# ============================================================================
class TestResolveCompletedBy(unittest.TestCase):
    def test_prefers_assignee_over_last_modified_by_and_qa(self):
        # The whole point of the fix: assignee (tagger) wins even when a
        # different person last-modified the record or is the QA reviewer.
        f = _fields(last_modified_by={"id": "u1", "name": "Last Mod"},
                    qa_assignee={"id": "q1", "name": "QA"},
                    assignee=[{"id": "a1", "name": "Agent"}])
        self.assertEqual(resolve_completed_by(f), ("Agent", "assignee"))

    def test_last_modified_by_differing_from_assignee_credits_assignee(self):
        # Regression for the James→Selah drift: Selah last-edited James's
        # tagged record; credit must stay with James (the assignee).
        f = _fields(assignee=[{"id": "j", "name": "JAMES MAINA"}],
                    last_modified_by={"id": "s", "name": "Selah Nabiswa"})
        self.assertEqual(resolve_completed_by(f), ("JAMES MAINA", "assignee"))

    def test_multi_assignee_credits_first(self):
        # Tagger = the FIRST assignee in the list.
        f = _fields(assignee=[{"id": "a1", "name": "First Tagger"},
                              {"id": "a2", "name": "Second"}])
        self.assertEqual(resolve_completed_by(f), ("First Tagger", "assignee"))

    def test_falls_back_to_last_modified_by_when_no_assignee(self):
        f = _fields(last_modified_by={"id": "u1", "name": "Last Mod"},
                    qa_assignee={"id": "q1", "name": "QA"})
        self.assertEqual(resolve_completed_by(f), ("Last Mod", "last_modified_by"))

    def test_falls_back_to_qa_assignee_when_no_assignee_or_lmb(self):
        f = _fields(qa_assignee={"id": "q1", "name": "QA"})
        self.assertEqual(resolve_completed_by(f), ("QA", "qa_assignee"))

    def test_none_when_all_blank(self):
        self.assertEqual(resolve_completed_by(_fields()), (None, None))

    def test_automations_skipped_in_fallback(self):
        # No assignee → fallback chain; Automations last-modifier is skipped.
        f = _fields(last_modified_by={"id": "s", "name": "Automations"},
                    qa_assignee={"id": "q1", "name": "Real Person"})
        self.assertEqual(resolve_completed_by(f), ("Real Person", "qa_assignee"))


# ============================================================================
# Row builders.
# ============================================================================
class TestBuildCompletionRow(unittest.TestCase):
    def test_full_row_shape_with_flow_a(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(verification_status=DONE,
                    imo="9999999",
                    role="OWNER",
                    last_modified_by={"id": "u1", "name": "Lillian Gichamba"})
        row, source = build_completion_row({"id": "rec123", "fields": f}, now, flow="A")
        self.assertEqual(source, "last_modified_by")
        self.assertEqual(row["airtable_record_id"], "rec123")
        self.assertEqual(row["flow"], "A")
        self.assertEqual(row["completed_at"], "2026-05-18T12:00:00+00:00")
        self.assertEqual(row["verification_status"], DONE)

    def test_flow_c_row(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(verification_status=VALID,
                    qa_assignee={"id": "q1", "name": "QA"},
                    qa_status="approve",
                    last_modified_by={"id": "q1", "name": "QA"})
        row, _ = build_completion_row({"id": "rec1", "fields": f}, now, flow="C")
        self.assertEqual(row["flow"], "C")

    def test_pre_flow_row_has_null_flow(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(verification_status=TAGGED,
                    company_id_and_name=["recX"],
                    last_modified_by={"id": "u1", "name": "Agent"})
        row, _ = build_completion_row({"id": "rec1", "fields": f}, now, flow=None)
        self.assertIsNone(row["flow"])

    def test_returns_none_when_unattributable(self):
        now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        row, src = build_completion_row({"id": "rec1", "fields": _fields(verification_status=DONE)}, now, flow="A")
        self.assertIsNone(row)
        self.assertIsNone(src)


class TestBuildSamplingRow(unittest.TestCase):
    def test_sampling_row(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(verification_status=SELECTED_FOR_BO_QA,
                    imo="9999",
                    qa_assignee={"id": "q1", "name": "Zuleikha"})
        row = build_sampling_row({"id": "rec1", "fields": f}, now)
        self.assertEqual(row["airtable_record_id"], "rec1")
        self.assertEqual(row["qa_assignee"], "Zuleikha")
        self.assertEqual(row["sampled_at"], "2026-05-18T12:00:00+00:00")

    def test_none_when_no_qa_assignee(self):
        now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        self.assertIsNone(build_sampling_row({"id": "rec1", "fields": _fields(verification_status=SELECTED_FOR_BO_QA)}, now))

    def test_sampling_row_captures_assignee_and_add_new_company(self):
        # A record set for BO QA carries the tagger (assignee) and the
        # add-new-company field; build_sampling_row must capture both at
        # sampling time so the row is a full review-lifecycle record.
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(verification_status=SELECTED_FOR_BO_QA,
                    imo="9999",
                    qa_assignee={"id": "q1", "name": "Zuleikha"},
                    assignee=[{"id": "a1", "name": "JAMES MAINA"}],
                    add_new_company="Acme Shipping Ltd")
        row = build_sampling_row({"id": "rec1", "fields": f}, now)
        # (1) assignee = the (first) tagger.
        self.assertEqual(row["assignee"], "JAMES MAINA")
        # (2) add_a_new_company = the raw add-new-company value.
        self.assertEqual(row["add_a_new_company"], "Acme Shipping Ltd")
        # (3) the original keys are still present and correct.
        self.assertEqual(row["airtable_record_id"], "rec1")
        self.assertEqual(row["qa_assignee"], "Zuleikha")
        self.assertEqual(row["sampled_at"], "2026-05-18T12:00:00+00:00")


class TestBuildAlertRow(unittest.TestCase):
    def test_alert_row_captures_context(self):
        f = _fields(verification_status=DONE,
                    qa_assignee={"id": "q1", "name": "QA1"})
        row = build_alert_row({"id": "rec1", "fields": f}, "missing_qa_status")
        self.assertEqual(row["airtable_record_id"], "rec1")
        self.assertEqual(row["alert_type"], "missing_qa_status")
        self.assertEqual(row["verification_status"], DONE)
        self.assertEqual(row["qa_assignee"], "QA1")
        self.assertIsNone(row["qa_status"])
        # resolved_at omitted → server default NULL
        self.assertNotIn("resolved_at", row)


# ============================================================================
# Supabase write semantics — first-write-wins, on_conflict, UPSERT.
# ============================================================================
class TestSupabaseInsertCompletions(unittest.TestCase):
    def test_url_includes_on_conflict_param(self):
        """Per CLAUDE.md: Prefer: resolution=ignore-duplicates needs
        ?on_conflict=<col> or it's a no-op (returns 409 on first dup)."""
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            mock_req.patch.return_value = MagicMock(status_code=200, json=lambda: [], text="[]")
            det.supabase_insert_completions("https://x.supabase.co", "key",
                                             [{"airtable_record_id": "rec1", "flow": "A"}])
            url = mock_req.post.call_args.args[0]
            self.assertIn("on_conflict=airtable_record_id", url)

    def test_first_write_wins_per_flow_type(self):
        """The same record stamped twice with different flow values: the
        second insert is a no-op via ignore-duplicates, but the PATCH path
        upserts the flow column when it was NULL."""
        row_with_flow = {"airtable_record_id": "rec1", "flow": "A"}
        with patch.object(det, "requests") as mock_req:
            # First call: INSERT, server returns the row as newly inserted
            mock_req.post.return_value = MagicMock(
                status_code=201, json=lambda: [row_with_flow], text="[]")
            r1 = det.supabase_insert_completions("https://x", "key", [row_with_flow])
            self.assertEqual(r1["inserted"], 1)
            self.assertEqual(r1["flow_upserted"], 0)

            # Second call: row already exists (POST returns [] = no new inserts).
            # The flow column is being re-asserted with same value 'A'.
            # PATCH path: filter flow=is.null → returns empty (existing row has flow=A).
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            mock_req.patch.return_value = MagicMock(status_code=200, json=lambda: [], text="[]")
            r2 = det.supabase_insert_completions("https://x", "key", [row_with_flow])
            self.assertEqual(r2["inserted"], 0)
            self.assertEqual(r2["flow_upserted"], 0,
                             "flow already set → PATCH filter rejects → no upsert")

    def test_upsert_flow_on_existing_null_row(self):
        """Record was previously inserted with flow=NULL (pre-Flow path),
        now caught at Done/Valid → batched PATCH should fire and upsert flow."""
        row = {"airtable_record_id": "rec1", "flow": "A"}
        with patch.object(det, "requests") as mock_req:
            # POST returns [] (already exists)
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            # PATCH returns [{"id": ...}] (row matched the flow=is.null filter)
            mock_req.patch.return_value = MagicMock(
                status_code=200, json=lambda: [{"id": "uuid", "airtable_record_id": "rec1", "flow": "A"}],
                text="[...]")
            result = det.supabase_insert_completions("https://x", "key", [row])
            self.assertEqual(result["inserted"], 0)
            self.assertEqual(result["flow_upserted"], 1)
            # Verify the PATCH URL uses the batched in.() filter + flow=is.null guard
            patch_url = mock_req.patch.call_args.args[0]
            self.assertIn("flow=is.null", patch_url)
            self.assertIn("airtable_record_id=in.(rec1)", patch_url,
                          "single-row batch should still use in.() format")

    def test_pre_flow_row_without_flow_value_does_not_patch(self):
        """A pre-Flow detection (row.flow=None) on a duplicate should NOT
        trigger a PATCH — it's just a no-op."""
        row = {"airtable_record_id": "rec1", "flow": None}
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            result = det.supabase_insert_completions("https://x", "key", [row])
            mock_req.patch.assert_not_called()
            self.assertEqual(result["duplicates_no_flow"], 1)

    def test_batched_patch_one_call_per_flow_value_per_chunk(self):
        """Performance regression guard: 250 duplicate rows split across
        2 flow values should produce 3 PATCH calls (100+100 for one flow,
        50 for the other), NOT 250 per-row PATCH calls.

        Replaces the prior O(N) per-row pattern that caused 10-min cron
        timeouts on busy days."""
        rows = (
            [{"airtable_record_id": f"recA{i}", "flow": "A"} for i in range(150)]
            + [{"airtable_record_id": f"recC{i}", "flow": "C"} for i in range(100)]
        )
        with patch.object(det, "requests") as mock_req:
            # All POST'd rows come back as duplicates (server returns [])
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            # Each PATCH returns 50 representative updates (just a count for assertion)
            mock_req.patch.return_value = MagicMock(
                status_code=200, json=lambda: [{"id": f"u{i}"} for i in range(50)], text="[]")
            det.supabase_insert_completions("https://x", "key", rows)
            # 150 A's → 2 chunks of 100+50; 100 C's → 1 chunk. Total 3 PATCH calls.
            self.assertEqual(mock_req.patch.call_count, 3,
                             f"Expected 3 batched PATCH calls, got {mock_req.patch.call_count}. "
                             f"Did per-row PATCH regress?")
            # Each PATCH URL should contain in.(...) with comma-separated IDs
            for call in mock_req.patch.call_args_list:
                url = call.args[0]
                self.assertIn("airtable_record_id=in.(", url)
                self.assertIn("flow=is.null", url)
            # And the chunk sizes are 100, 50, 100 (in some order)
            chunk_sizes = []
            for call in mock_req.patch.call_args_list:
                url = call.args[0]
                # Count IDs in the in.(...) by counting commas + 1
                m = url.split("in.(")[1].split(")")[0]
                chunk_sizes.append(m.count(",") + 1)
            self.assertEqual(sorted(chunk_sizes), [50, 100, 100])

    def test_batched_patch_chunks_at_100_to_stay_under_url_limit(self):
        """A single flow value with 250 duplicates should produce 3 chunked
        PATCH calls (100+100+50), keeping URLs under the proxy's ~8KB cap."""
        rows = [{"airtable_record_id": f"rec{i:03d}", "flow": "A"} for i in range(250)]
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            mock_req.patch.return_value = MagicMock(
                status_code=200, json=lambda: [], text="[]")
            det.supabase_insert_completions("https://x", "key", rows)
            self.assertEqual(mock_req.patch.call_count, 3)
            sizes = []
            for call in mock_req.patch.call_args_list:
                ids_part = call.args[0].split("in.(")[1].split(")")[0]
                sizes.append(ids_part.count(",") + 1)
            self.assertEqual(sorted(sizes), [50, 100, 100])
            # Sanity: URLs all under 3KB (allows %2C encoding overhead)
            for call in mock_req.patch.call_args_list:
                self.assertLess(len(call.args[0]), 3000,
                                "URL should stay well under proxy limit")


class TestStuckInSamplingDetection(unittest.TestCase):
    def test_stuck_in_sampling_threshold_24h(self):
        """Records sampled > STUCK_HOURS ago AND not yet completed should
        generate stuck_in_sampling alerts."""
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        cutoff = (now - timedelta(hours=STUCK_HOURS)).isoformat()
        with patch.object(det, "requests") as mock_req:
            # First GET: ownership_qa_sampling rows older than cutoff
            old_samples_resp = MagicMock(status_code=200, json=lambda: [
                {"airtable_record_id": "rec_stuck", "qa_assignee": "QA1",
                 "sampled_at": "2026-05-17T08:00:00+00:00", "raw_payload": {}},
                {"airtable_record_id": "rec_done",   "qa_assignee": "QA1",
                 "sampled_at": "2026-05-17T08:00:00+00:00", "raw_payload": {}},
            ])
            # Second GET: which of those have completed (only rec_done)
            completed_resp = MagicMock(status_code=200, json=lambda: [
                {"airtable_record_id": "rec_done"},
            ])
            mock_req.get.side_effect = [old_samples_resp, completed_resp]
            alerts = det.detect_stuck_in_sampling("https://x", "key", now)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["airtable_record_id"], "rec_stuck")
            self.assertEqual(alerts[0]["alert_type"], "stuck_in_sampling")
            # Verify the cutoff in the first GET's filter
            first_call_params = mock_req.get.call_args_list[0].kwargs["params"]
            self.assertIn(cutoff, first_call_params["sampled_at"])

    def test_no_stuck_when_table_empty(self):
        now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        with patch.object(det, "requests") as mock_req:
            mock_req.get.return_value = MagicMock(status_code=200, json=lambda: [])
            alerts = det.detect_stuck_in_sampling("https://x", "key", now)
            self.assertEqual(alerts, [])


class TestSamplingInsert(unittest.TestCase):
    def test_uses_on_conflict_param(self):
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            det.supabase_insert_samplings("https://x", "key",
                                          [{"airtable_record_id": "rec1", "qa_assignee": "Q"}])
            url = mock_req.post.call_args.args[0]
            self.assertIn("on_conflict=airtable_record_id", url)


class TestAlertUpsert(unittest.TestCase):
    def test_upserts_with_composite_on_conflict(self):
        """flow_alerts UNIQUE is on (airtable_record_id, alert_type)."""
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            mock_req.patch.return_value = MagicMock(status_code=200, json=lambda: [], text="[]")
            det.supabase_upsert_alerts("https://x", "key",
                                       [{"airtable_record_id": "rec1", "alert_type": "missing_qa_status"}])
            url = mock_req.post.call_args.args[0]
            self.assertIn("on_conflict=airtable_record_id,alert_type", url)

    def test_reopens_resolved_alert_when_condition_returns(self):
        """If an alert was previously resolved but the bad state has come
        back, PATCH it to clear resolved_at."""
        row = {"airtable_record_id": "rec1", "alert_type": "missing_qa_status"}
        with patch.object(det, "requests") as mock_req:
            # POST: row already exists (returns [])
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            # PATCH (clearing resolved_at): returns the patched row
            mock_req.patch.return_value = MagicMock(
                status_code=200, json=lambda: [{"id": "uuid"}], text="[...]")
            new, reopened = det.supabase_upsert_alerts("https://x", "key", [row])
            self.assertEqual(new, 0)
            self.assertEqual(reopened, 1)
            patch_url = mock_req.patch.call_args.args[0]
            self.assertIn("resolved_at=not.is.null", patch_url)


class TestAlertResolution(unittest.TestCase):
    def test_resolves_alerts_not_in_current_set(self):
        """Alerts open in DB whose (record_id, alert_type) is no longer
        in this cycle's current set → PATCH resolved_at."""
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        with patch.object(det, "requests") as mock_req:
            # GET open alerts: 2 in DB
            mock_req.get.return_value = MagicMock(status_code=200, json=lambda: [
                {"id": "uuid1", "airtable_record_id": "recA", "alert_type": "missing_qa_status"},
                {"id": "uuid2", "airtable_record_id": "recB", "alert_type": "stuck_in_sampling"},
            ])
            mock_req.patch.return_value = MagicMock(status_code=204, json=lambda: [], text="")
            # Current cycle only has recA's alert (recB has been resolved)
            current_keys = {("recA", "missing_qa_status")}
            resolved = det.supabase_resolve_alerts("https://x", "key", current_keys, now)
            self.assertEqual(resolved, 1)
            # Verify the PATCH targeted recB's id
            patch_url = mock_req.patch.call_args.args[0]
            self.assertIn("uuid2", patch_url)


# ============================================================================
# Total Completions contract.
# ============================================================================
class TestTotalCompletionsContract(unittest.TestCase):
    def test_total_completions_excludes_flow_b(self):
        """Total Completions = A + C. Flow B is in-progress (sampling),
        explicitly excluded per Kelvin's v2 spec."""
        # This is enforced by the aggregator (tested in test_aggregate_v2),
        # but documenting the contract here too so future detector changes
        # don't accidentally route Flow B records into ownership_completions.
        f_b = _fields(verification_status=SELECTED_FOR_BO_QA,
                      qa_assignee={"id": "q", "name": "Q"})
        target, _ = classify(f_b)
        self.assertEqual(target, "sampling",
                         "Flow B must NEVER classify as completion target")
        self.assertNotEqual(target, "completion")


# ============================================================================
# Capture-gap closure — every tagged-or-beyond record gets a tagging row.
# ============================================================================
class TestNeedsTaggingRow(unittest.TestCase):
    def test_tagged_or_beyond_states_need_a_row(self):
        for vs in (TAGGED, NEED_TO_BE_UPDATE, SELECTED_FOR_BO_QA, DONE, VALID):
            self.assertTrue(needs_tagging_row(_fields(verification_status=vs)), vs)

    def test_waiting_and_blank_do_not(self):
        self.assertFalse(needs_tagging_row(_fields(verification_status="waiting")))
        self.assertFalse(needs_tagging_row(_fields()))  # blank vs


class TestCaptureGapClosure(unittest.TestCase):
    NOW = datetime(2026, 5, 20, 17, 0, 0, tzinfo=timezone.utc)

    def _route(self, **kw):
        return route_record({"id": kw.pop("rid", "recX"), "fields": _fields(**kw)}, self.NOW)

    # (a) A record first observed already in BO QA now gets a tagging row.
    def test_sbo_first_seen_gets_tagging_row_and_keeps_sampling(self):
        target, _, comp, samp, alert = self._route(
            verification_status=SELECTED_FOR_BO_QA,
            assignee=[{"id": "j", "name": "JAMES MAINA"}],
            qa_assignee={"id": "q", "name": "Selah Nabiswa"})
        self.assertEqual(target, "sampling")
        self.assertIsNotNone(samp, "sampling row must still be written")
        self.assertEqual(samp["qa_assignee"], "Selah Nabiswa")
        self.assertIsNotNone(comp, "NEW: a tagging completions row is added")
        self.assertIsNone(comp["flow"], "tagging row is pre-flow (flow=NULL)")
        self.assertEqual(comp["completed_by"], "JAMES MAINA", "credited to the tagger")
        self.assertEqual(comp["verification_status"], SELECTED_FOR_BO_QA)

    def test_sbo_without_qa_assignee_alert_still_gets_tagging_row(self):
        target, detail, comp, samp, alert = self._route(
            verification_status=SELECTED_FOR_BO_QA,
            assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertEqual((target, detail), ("alert", "missing_qa_assignee"))
        self.assertIsNotNone(alert)
        self.assertIsNone(samp)
        self.assertIsNotNone(comp)
        self.assertIsNone(comp["flow"])
        self.assertEqual(comp["completed_by"], "JAMES MAINA")

    def test_done_missing_qa_status_alert_gets_tagging_row(self):
        # Done/Valid with a QA-integrity alert was also a capture gap.
        target, detail, comp, samp, alert = self._route(
            verification_status=VALID,
            assignee=[{"id": "j", "name": "JAMES MAINA"}],
            qa_assignee={"id": "q", "name": "Reviewer"})  # qa_status blank
        self.assertEqual((target, detail), ("alert", "missing_qa_status"))
        self.assertIsNotNone(alert)
        self.assertIsNotNone(comp)
        self.assertIsNone(comp["flow"])
        self.assertEqual(comp["completed_by"], "JAMES MAINA")

    def test_tagged_without_company_skip_still_gets_tagging_row(self):
        # Previously skipped (is_complete False); it's still a tagged record.
        target, _, comp, samp, alert = self._route(
            verification_status=TAGGED,
            assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertEqual(target, "skip")
        self.assertIsNotNone(comp)
        self.assertIsNone(comp["flow"])
        self.assertEqual(comp["completed_by"], "JAMES MAINA")

    # No double-rowing: completion / pre_flow targets still yield exactly one row.
    def test_flow_a_completion_not_double_rowed(self):
        target, detail, comp, samp, alert = self._route(
            verification_status=DONE, assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertEqual((target, detail), ("completion", "A"))
        self.assertIsNotNone(comp)
        self.assertEqual(comp["flow"], "A", "keeps its real flow, not overwritten with NULL")

    def test_pre_flow_single_row(self):
        target, _, comp, samp, alert = self._route(
            verification_status=TAGGED, company_id_and_name=["recCo"],
            assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertEqual(target, "pre_flow")
        self.assertIsNotNone(comp)
        self.assertIsNone(comp["flow"])

    # (c) waiting / blank produce no completions row.
    def test_waiting_produces_no_completion_row(self):
        _, _, comp, _, _ = self._route(
            verification_status="waiting", assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertIsNone(comp)

    def test_blank_vs_produces_no_completion_row(self):
        _, _, comp, _, _ = self._route(assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertIsNone(comp)

    def test_no_assignee_unattributable_yields_no_row(self):
        # A tagged-or-beyond record with no assignee/lmb/qa stays unattributable
        # (NOT-NULL invariant preserved — no row rather than a null completed_by).
        _, _, comp, _, _ = self._route(verification_status=SELECTED_FOR_BO_QA)
        self.assertIsNone(comp)

    # (b) Idempotency: a gap-fill tagging row (flow=NULL) never modifies an
    # existing row — as a duplicate it takes the no-PATCH path.
    def test_gap_fill_row_is_idempotent_no_patch_on_duplicate(self):
        _, _, comp, _, _ = self._route(
            verification_status=SELECTED_FOR_BO_QA,
            assignee=[{"id": "j", "name": "JAMES MAINA"}],
            qa_assignee={"id": "q", "name": "Selah Nabiswa"})
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            result = supabase_insert_completions("https://x", "key", [comp])
            mock_req.patch.assert_not_called()
            self.assertEqual(result["duplicates_no_flow"], 1)

    # (d) A gap-filled flow=NULL row still upgrades to A/C when the record completes.
    def test_gap_filled_row_upgrades_to_flow_a_when_completed(self):
        _, _, comp_sbo, _, _ = self._route(
            rid="recGap", verification_status=SELECTED_FOR_BO_QA,
            assignee=[{"id": "j", "name": "JAMES MAINA"}],
            qa_assignee={"id": "q", "name": "Selah Nabiswa"})
        self.assertIsNone(comp_sbo["flow"])
        # Later cycle: same record reaches Done (Flow A).
        _, detail, comp_done, _, _ = self._route(
            rid="recGap", verification_status=DONE,
            assignee=[{"id": "j", "name": "JAMES MAINA"}])
        self.assertEqual((detail, comp_done["flow"]), ("A", "A"))
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")  # dup
            mock_req.patch.return_value = MagicMock(
                status_code=200, json=lambda: [{"id": "u", "airtable_record_id": "recGap"}], text="[]")
            result = supabase_insert_completions("https://x", "key", [comp_done])
            self.assertEqual(result["flow_upserted"], 1)
            self.assertIn("flow=is.null", mock_req.patch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
