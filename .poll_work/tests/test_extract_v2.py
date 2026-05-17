"""Unit tests for extract_v2."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extract_v2 import (
    FIELD_IDS,
    SELECTED_FOR_BO_QA,
    extract,
    is_properly_completed,
    is_sanctions,
    resolve_qa,
)


def _rec(data):
    """Build a record dict from a {logical_name: value} mapping."""
    return {"cellValuesByFieldId": {FIELD_IDS[k]: v for k, v in data.items()}}


EXPECTED_KEYS = {
    "imo", "assignee", "assignees", "qa_assignee", "ww_qa_assignee", "last_modified_by",
    "start_tagging", "start_date", "created", "last_modified",
    "done_selected_time", "qa_status_ts", "company_id", "company_name",
    "verification_status", "qa_status", "ww_qa", "status", "is_change",
    "comment", "reminder", "source_flow", "add_new_company",
    "requested_by", "valid_done_by_bo", "role", "dead_vessel",
}


class TestExtract(unittest.TestCase):
    def test_round_trips_all_keys(self):
        rec = _rec({
            "imo": "9601675",
            "assignee": [{"id": "u1", "name": "Lillian Gichamba"}],
            "qa_assignee": {"id": "q1", "name": "Zuleikha Musa"},
            "ww_qa_assignee": {"id": "w1", "name": "WW Person"},
            "last_modified_by": {"id": "m1", "name": "Last Modifier"},
            "start_tagging_date": "2026-05-12T11:24:00.000Z",
            "start_date": "2024-01-15T00:00:00.000Z",
            "created": "2026-05-12T09:00:00.000Z",
            "last_modified": "2026-05-12T12:00:00.000Z",
            "done_selected_time": "2026-05-12T14:00:00.000Z",
            "qa_status_ts": "2026-05-12T13:00:00.000Z",
            "company_id_and_name": [{"id": "recCo1", "name": "12345 - Acme Shipping"}],
            "verification_status": {"id": "selX", "name": SELECTED_FOR_BO_QA},
            "qa_status": {"id": "selA", "name": "approve"},
            "ww_qa": {"id": "selW", "name": "change"},
            "status": {"id": "selS", "name": "Tagged"},
            "is_change": "true",
            "comment": {"id": "selC", "name": "suspected zombie vessel"},
            "reminder": "2026-05-20",
            "source_flow": "Equasis",
            "add_new_company": "ABC Co\nXYZ Co",
            "requested_by": "Ops Lead",
            "valid_done_by_bo": True,
            "role": {"id": "selR", "name": "OWNER"},
            "dead_vessel": True,
        })

        info = extract(rec)

        self.assertEqual(set(info.keys()), EXPECTED_KEYS)
        self.assertEqual(info["imo"], "9601675")
        self.assertEqual(info["assignee"], "Lillian Gichamba")
        self.assertEqual(info["assignees"], ["Lillian Gichamba"])
        self.assertEqual(info["qa_assignee"], "Zuleikha Musa")
        self.assertEqual(info["ww_qa_assignee"], "WW Person")
        self.assertEqual(info["last_modified_by"], "Last Modifier")
        self.assertEqual(info["start_tagging"], "2026-05-12T11:24:00.000Z")
        self.assertEqual(info["start_date"], "2024-01-15T00:00:00.000Z")
        self.assertEqual(info["created"], "2026-05-12T09:00:00.000Z")
        self.assertEqual(info["done_selected_time"], "2026-05-12T14:00:00.000Z")
        self.assertEqual(info["company_id"], "recCo1")
        self.assertEqual(info["company_name"], "12345 - Acme Shipping")
        self.assertEqual(info["verification_status"], SELECTED_FOR_BO_QA)
        self.assertEqual(info["qa_status"], "approve")
        self.assertEqual(info["ww_qa"], "change")
        self.assertEqual(info["status"], "Tagged")
        self.assertEqual(info["is_change"], "true")
        self.assertEqual(info["comment"], "suspected zombie vessel")
        self.assertEqual(info["reminder"], "2026-05-20")
        self.assertEqual(info["source_flow"], "Equasis")
        self.assertEqual(info["add_new_company"], "ABC Co\nXYZ Co")
        self.assertEqual(info["requested_by"], "Ops Lead")
        self.assertTrue(info["valid_done_by_bo"])
        self.assertEqual(info["role"], "OWNER")
        self.assertTrue(info["dead_vessel"])

    def test_empty_record_yields_all_keys_with_none(self):
        info = extract({})
        self.assertEqual(set(info.keys()), EXPECTED_KEYS)
        for key, val in info.items():
            if key in ("valid_done_by_bo", "dead_vessel"):
                self.assertFalse(val)
            elif key == "assignees":
                self.assertEqual(val, [], "empty assignees should be []")
            else:
                self.assertIsNone(val, f"{key} should default to None, got {val!r}")

    def test_multi_assignee(self):
        rec = _rec({"assignee": [
            {"id": "u1", "name": "Alice"},
            {"id": "u2", "name": "Bob"},
            {"id": "u3", "name": "Carol"},
        ]})
        info = extract(rec)
        self.assertEqual(info["assignee"], "Alice", "primary stays as first item")
        self.assertEqual(info["assignees"], ["Alice", "Bob", "Carol"])

    def test_no_assignee(self):
        info = extract({"cellValuesByFieldId": {}})
        self.assertIsNone(info["assignee"])
        self.assertEqual(info["assignees"], [])

    def test_garbage_role_value_dropped(self):
        rec = _rec({"role": {"id": "selyNKujjYLS03W1A", "name": "role"}})
        self.assertIsNone(extract(rec)["role"])

    def test_real_role_values_preserved(self):
        for role_value in ("OWNER", "BENEFICIAL_OWNER", "MANAGEMENT", "OPERATOR",
                           "COMMERCIAL_CONTROLLER", "TECHNICAL_MANAGER", "ISM_MANAGER"):
            rec = _rec({"role": {"id": "x", "name": role_value}})
            self.assertEqual(extract(rec)["role"], role_value)

    def test_garbage_status_values_dropped(self):
        for bad in ("Leopard", "Cargill_Bulk_Carrier_2023-2"):
            rec = _rec({"status": {"id": "x", "name": bad}})
            self.assertIsNone(extract(rec)["status"], f"{bad} should be dropped")

    def test_real_status_values_preserved(self):
        rec = _rec({"status": {"id": "x", "name": "Tagged"}})
        self.assertEqual(extract(rec)["status"], "Tagged")

    def test_preserves_selected_for_bo_qa_trailing_space(self):
        rec = _rec({"verification_status": {"id": "x", "name": "Selected for BO QA "}})
        self.assertEqual(extract(rec)["verification_status"], "Selected for BO QA ")
        self.assertTrue(extract(rec)["verification_status"].endswith(" "))

    def test_valid_done_by_bo_is_bool(self):
        self.assertFalse(extract(_rec({}))["valid_done_by_bo"])
        self.assertTrue(extract(_rec({"valid_done_by_bo": True}))["valid_done_by_bo"])


class TestResolveQA(unittest.TestCase):
    def test_uses_qa_assignee_when_present(self):
        name, fb = resolve_qa({"qa_assignee": "Zuleikha", "last_modified_by": "Other"})
        self.assertEqual(name, "Zuleikha")
        self.assertFalse(fb)

    def test_falls_back_when_qa_assignee_blank(self):
        name, fb = resolve_qa({"qa_assignee": None, "last_modified_by": "Other"})
        self.assertEqual(name, "Other")
        self.assertTrue(fb)

    def test_blank_when_both_missing(self):
        name, fb = resolve_qa({"qa_assignee": None, "last_modified_by": None})
        self.assertIsNone(name)
        self.assertFalse(fb)

    def test_empty_string_qa_assignee_falls_back(self):
        name, fb = resolve_qa({"qa_assignee": "", "last_modified_by": "Other"})
        self.assertEqual(name, "Other")
        self.assertTrue(fb)


class TestIsProperlyCompleted(unittest.TestCase):
    """Ops definition: start_date filled AND (company_id filled OR dead_vessel=True)."""

    def test_both_filled(self):
        info = {"start_date": "2024-01-15", "company_id": "recCo1", "dead_vessel": False}
        self.assertTrue(is_properly_completed(info))

    def test_only_start_date(self):
        info = {"start_date": "2024-01-15", "company_id": None, "dead_vessel": False}
        self.assertFalse(is_properly_completed(info))

    def test_start_date_plus_dead_vessel(self):
        info = {"start_date": "2024-01-15", "company_id": None, "dead_vessel": True}
        self.assertTrue(is_properly_completed(info))

    def test_only_company(self):
        # company filled but start_date missing → not properly completed
        info = {"start_date": None, "company_id": "recCo1", "dead_vessel": False}
        self.assertFalse(is_properly_completed(info))

    def test_none(self):
        info = {"start_date": None, "company_id": None, "dead_vessel": False}
        self.assertFalse(is_properly_completed(info))

    def test_dead_vessel_alone(self):
        # dead_vessel=True is the alternative to company, NOT to start_date
        info = {"start_date": None, "company_id": None, "dead_vessel": True}
        self.assertFalse(is_properly_completed(info))


class TestIsSanctions(unittest.TestCase):
    def test_non_sanctions_task_name(self):
        self.assertFalse(is_sanctions("CargoChangeIntel17Apr2026_task2"))

    def test_sanctions_in_name(self):
        self.assertTrue(is_sanctions("CargoSanctionsCheck17Apr2026"))

    def test_case_insensitive(self):
        self.assertTrue(is_sanctions("CargoSANCTIONS"))
        self.assertTrue(is_sanctions("sanctions"))

    def test_none_and_empty(self):
        self.assertFalse(is_sanctions(None))
        self.assertFalse(is_sanctions(""))


if __name__ == "__main__":
    unittest.main()
