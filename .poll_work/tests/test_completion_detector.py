"""Tests for completion_detector — the completion rule, the completed_by
fallback chain, and the first-write-wins insert behavior.

Network calls (Airtable GET, Supabase INSERT) are stubbed via
unittest.mock — the detector is exercised end-to-end against fake HTTP
responses, no real services hit.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import completion_detector as det  # noqa: E402
from completion_detector import (  # noqa: E402
    FLD_ADD_NEW_COMPANY,
    FLD_ASSIGNEE,
    FLD_COMPANY_ID_AND_NAME,
    FLD_DEAD_VESSEL,
    FLD_IMO,
    FLD_LAST_MODIFIED_BY,
    FLD_QA_ASSIGNEE,
    FLD_REQUESTED_BY,
    FLD_ROLE,
    FLD_VERIFICATION_STATUS,
    NEED_TO_BE_UPDATE,
    TAGGED,
    build_row,
    is_complete,
    resolve_completed_by,
)


def _fields(**kw):
    """Build a fields dict using extract_v2-style logical names mapped to FIELD_IDS."""
    keymap = {
        "imo":                 FLD_IMO,
        "assignee":            FLD_ASSIGNEE,
        "qa_assignee":         FLD_QA_ASSIGNEE,
        "last_modified_by":    FLD_LAST_MODIFIED_BY,
        "verification_status": FLD_VERIFICATION_STATUS,
        "company_id_and_name": FLD_COMPANY_ID_AND_NAME,
        "dead_vessel":         FLD_DEAD_VESSEL,
        "add_new_company":     FLD_ADD_NEW_COMPANY,
        "role":                FLD_ROLE,
        "requested_by":        FLD_REQUESTED_BY,
    }
    return {keymap[k]: v for k, v in kw.items()}


class TestCompletionRule(unittest.TestCase):
    """The completion rule — both terminal verification_status states."""

    def test_tagged_with_company_linked_and_not_dead_is_complete(self):
        f = _fields(verification_status=TAGGED,
                    company_id_and_name=["recCo1"])
        self.assertTrue(is_complete(f))

    def test_tagged_with_dead_vessel_but_no_company_is_complete(self):
        # Dead vessel is the operational alternative to company linkage.
        f = _fields(verification_status=TAGGED, dead_vessel=True)
        self.assertTrue(is_complete(f))

    def test_tagged_with_no_company_and_not_dead_is_not_complete(self):
        # Still in flight — agent hasn't picked a company yet.
        f = _fields(verification_status=TAGGED)
        self.assertFalse(is_complete(f))

    def test_tagged_with_both_company_and_dead_is_complete(self):
        # OR semantics: either alone suffices, both together also.
        f = _fields(verification_status=TAGGED,
                    company_id_and_name=["recCo1"],
                    dead_vessel=True)
        self.assertTrue(is_complete(f))

    def test_need_to_be_update_with_add_new_company_is_complete(self):
        f = _fields(verification_status=NEED_TO_BE_UPDATE,
                    add_new_company="Proposed New Co Ltd")
        self.assertTrue(is_complete(f))

    def test_need_to_be_update_with_blank_add_new_company_is_not_complete(self):
        f = _fields(verification_status=NEED_TO_BE_UPDATE)
        self.assertFalse(is_complete(f))

    def test_need_to_be_update_with_empty_string_add_new_company_is_not_complete(self):
        f = _fields(verification_status=NEED_TO_BE_UPDATE, add_new_company="")
        self.assertFalse(is_complete(f))

    def test_waiting_is_not_complete_even_with_company(self):
        # vs="waiting" → not in a terminal state, never complete.
        f = _fields(verification_status="waiting",
                    company_id_and_name=["recCo1"])
        self.assertFalse(is_complete(f))

    def test_selected_for_bo_qa_is_not_complete(self):
        # Records in BO QA backlog — agent already finished, but vs isn't
        # one of the two terminal states the detector tracks.
        f = _fields(verification_status="Selected for BO QA ",
                    company_id_and_name=["recCo1"])
        self.assertFalse(is_complete(f))

    def test_done_is_not_complete(self):
        # Done means already fully approved by QA. Not tracked by detector —
        # only the agent-completion moment is, which is upstream of Done.
        f = _fields(verification_status="Done",
                    company_id_and_name=["recCo1"])
        self.assertFalse(is_complete(f))

    def test_null_verification_status_is_not_complete(self):
        self.assertFalse(is_complete(_fields()))

    def test_tagged_handles_raw_rest_string_vs(self):
        # singleSelect raw-REST shape is a plain string. _name unwraps it.
        f = _fields(verification_status=TAGGED, company_id_and_name=["recX"])
        self.assertTrue(is_complete(f))

    def test_tagged_handles_cowork_dict_vs(self):
        # Cowork shape: vs is a dict {id, name}. _name unwraps it.
        f = _fields(verification_status={"id": "x", "name": TAGGED},
                    company_id_and_name=["recX"])
        self.assertTrue(is_complete(f))

    def test_tagged_handles_cowork_link_dict(self):
        # multipleRecordLinks Cowork shape: list of dicts. _first_link sees non-empty.
        f = _fields(verification_status=TAGGED,
                    company_id_and_name=[{"id": "recCo1", "name": "Acme"}])
        self.assertTrue(is_complete(f))


class TestResolveCompletedBy(unittest.TestCase):
    """Fallback chain: last_modified_by → qa_assignee → assignee."""

    def test_prefers_last_modified_by(self):
        f = _fields(last_modified_by={"id": "u1", "name": "Last Mod Person"},
                    qa_assignee={"id": "q1", "name": "QA Person"},
                    assignee=[{"id": "a1", "name": "Agent Person"}])
        name, source = resolve_completed_by(f)
        self.assertEqual(name, "Last Mod Person")
        self.assertEqual(source, "last_modified_by")

    def test_falls_back_to_qa_assignee(self):
        f = _fields(qa_assignee={"id": "q1", "name": "QA Person"},
                    assignee=[{"id": "a1", "name": "Agent Person"}])
        name, source = resolve_completed_by(f)
        self.assertEqual(name, "QA Person")
        self.assertEqual(source, "qa_assignee")

    def test_falls_back_to_assignee(self):
        f = _fields(assignee=[{"id": "a1", "name": "Agent Person"}])
        name, source = resolve_completed_by(f)
        self.assertEqual(name, "Agent Person")
        self.assertEqual(source, "assignee")

    def test_returns_none_when_all_three_blank(self):
        name, source = resolve_completed_by(_fields())
        self.assertIsNone(name)
        self.assertIsNone(source)

    def test_handles_raw_rest_collaborator_with_email(self):
        # Raw REST returns collaborators with id/name/email — _name reads .name.
        f = _fields(last_modified_by={"id": "u1", "name": "Lillian Gichamba",
                                       "email": "lillian@impactoutsourcing.co.ke"})
        name, source = resolve_completed_by(f)
        self.assertEqual(name, "Lillian Gichamba")
        self.assertEqual(source, "last_modified_by")


class TestBuildRow(unittest.TestCase):
    def test_full_row_shape(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        f = _fields(
            imo="9999999",
            verification_status=TAGGED,
            company_id_and_name=[{"id": "recCo1", "name": "Acme Shipping"}],
            role="OWNER",
            requested_by="CargoTask17May",
            last_modified_by={"id": "u1", "name": "Lillian Gichamba"},
        )
        row, source = build_row({"id": "rec123", "fields": f}, now)
        self.assertEqual(source, "last_modified_by")
        self.assertEqual(row["airtable_record_id"], "rec123")
        self.assertEqual(row["imo"], "9999999")
        self.assertEqual(row["role"], "OWNER")
        self.assertEqual(row["verification_status"], TAGGED)
        self.assertEqual(row["completed_by"], "Lillian Gichamba")
        # completed_at is detector clock, not Airtable's
        self.assertEqual(row["completed_at"], "2026-05-18T12:00:00+00:00")
        self.assertEqual(row["requested_by"], "CargoTask17May")
        # raw_payload preserves the fields dict for forensics
        self.assertEqual(row["raw_payload"], f)

    def test_returns_none_when_unattributable(self):
        now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
        row, source = build_row({"id": "recX", "fields": _fields(
            verification_status=TAGGED,
            company_id_and_name=["recCo1"],
        )}, now)
        self.assertIsNone(row)
        self.assertIsNone(source)


class TestSupabaseInsertContract(unittest.TestCase):
    """Verify the detector calls Supabase with the right Prefer header so
    UNIQUE-constraint duplicates are silently skipped (first-write-wins)."""

    def test_insert_sends_ignore_duplicates_header(self):
        with patch.object(det, "requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=201, json=lambda: [], text="[]")
            det.supabase_insert("https://example.supabase.co", "fake-key", [{"airtable_record_id": "rec1"}])
            mock_req.post.assert_called_once()
            kwargs = mock_req.post.call_args.kwargs
            prefer = kwargs["headers"]["Prefer"]
            self.assertIn("resolution=ignore-duplicates", prefer)
            self.assertIn("return=representation", prefer)
            self.assertEqual(kwargs["headers"]["apikey"], "fake-key")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-key")

    def test_first_write_wins_on_duplicate(self):
        """Two detector runs see the same complete record. The first
        insert returns the row; the second returns an empty list because
        the server silently skipped it. completed_at stays whatever the
        first run stamped."""
        with patch.object(det, "requests") as mock_req:
            row = {"airtable_record_id": "rec1", "completed_at": "2026-05-18T10:00:00+00:00"}
            # First call — server returns the inserted row
            mock_req.post.return_value = MagicMock(status_code=201,
                                                    json=lambda: [row],
                                                    text='[{"airtable_record_id":"rec1"}]')
            first = det.supabase_insert("https://x.supabase.co", "k", [row])
            self.assertEqual(len(first), 1)

            # Second call (15 min later, same row) — server returns []
            mock_req.post.return_value = MagicMock(status_code=201,
                                                    json=lambda: [],
                                                    text="[]")
            second = det.supabase_insert("https://x.supabase.co", "k",
                                          [{**row, "completed_at": "2026-05-18T10:15:00+00:00"}])
            self.assertEqual(second, [],
                             "Server should silently skip the duplicate "
                             "and return no inserted rows")

    def test_empty_rows_skips_http_call(self):
        # No work to do → no POST, no error.
        with patch.object(det, "requests") as mock_req:
            result = det.supabase_insert("https://x", "k", [])
            self.assertEqual(result, [])
            mock_req.post.assert_not_called()


class TestEndToEndFlow(unittest.TestCase):
    """End-to-end: Airtable returns a mix of complete and incomplete records;
    detector should only attempt to insert the complete ones."""

    def test_only_complete_records_are_inserted(self):
        airtable_records = [
            {"id": "recA", "fields": _fields(  # complete: tagged + company
                verification_status=TAGGED,
                company_id_and_name=["recCo1"],
                last_modified_by={"id": "u1", "name": "Alice"})},
            {"id": "recB", "fields": _fields(  # NOT complete: tagged but no company, not dead
                verification_status=TAGGED,
                last_modified_by={"id": "u2", "name": "Bob"})},
            {"id": "recC", "fields": _fields(  # complete: need to be update + add_new_company
                verification_status=NEED_TO_BE_UPDATE,
                add_new_company="New Co",
                last_modified_by={"id": "u3", "name": "Carol"})},
            {"id": "recD", "fields": _fields(  # NOT complete: waiting (not a terminal state)
                verification_status="waiting",
                company_id_and_name=["recCo2"],
                last_modified_by={"id": "u4", "name": "Dave"})},
            {"id": "recE", "fields": _fields(  # complete via dead_vessel
                verification_status=TAGGED, dead_vessel=True,
                last_modified_by={"id": "u5", "name": "Eve"})},
        ]
        with patch.object(det, "requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"records": airtable_records, "offset": None},
            )
            mock_req.post.return_value = MagicMock(
                status_code=201,
                json=lambda: [{"airtable_record_id": rid} for rid in ["recA", "recC", "recE"]],
                text="[]",
            )
            records = det.fetch_terminal_records("fake-pat")
            self.assertEqual(len(records), 5)
            now = datetime.now(timezone.utc)
            rows = []
            for rec in records:
                if det.is_complete(rec["fields"]):
                    row, _ = det.build_row(rec, now)
                    if row:
                        rows.append(row)
            self.assertEqual({r["airtable_record_id"] for r in rows}, {"recA", "recC", "recE"})


if __name__ == "__main__":
    unittest.main()
