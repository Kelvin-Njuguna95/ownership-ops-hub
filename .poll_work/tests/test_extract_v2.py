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
    "done_selected_time", "valid_selected_time", "qa_status_ts", "company_id", "company_name",
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
            "valid_selected_time": "2026-05-12T15:00:00.000Z",
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
        self.assertEqual(info["valid_selected_time"], "2026-05-12T15:00:00.000Z")
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

    def test_accepts_raw_airtable_rest_shape_with_fields_key(self):
        """The raw Airtable REST API returns records with a ``fields`` key.
        The Cowork MCP wrapper returns ``cellValuesByFieldId``. extract() must
        handle both — the poller writes ``fields``-shaped records to cache."""
        rest_rec = {
            "id": "rec123",
            "createdTime": "2026-05-18T09:00:00.000Z",
            "fields": {
                FIELD_IDS["imo"]: "9999999",
                FIELD_IDS["assignee"]: [{"id": "u1", "name": "Hellen Vigehi"}],
                FIELD_IDS["verification_status"]: {"id": "x", "name": "tagged"},
            },
        }
        info = extract(rest_rec)
        self.assertEqual(info["imo"], "9999999")
        self.assertEqual(info["assignee"], "Hellen Vigehi")
        self.assertEqual(info["assignees"], ["Hellen Vigehi"])
        self.assertEqual(info["verification_status"], "tagged")

    def test_cell_values_takes_precedence_over_fields_when_both_present(self):
        """If both keys exist (shouldn't happen in practice), prefer the
        Cowork shape so existing tests keep their guarantees."""
        rec = {
            "cellValuesByFieldId": {FIELD_IDS["imo"]: "from-cell"},
            "fields":              {FIELD_IDS["imo"]: "from-fields"},
        }
        self.assertEqual(extract(rec)["imo"], "from-cell")

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


class TestRawRestShapes(unittest.TestCase):
    """Phase F2 raw Airtable REST API returns several field types in shapes
    that differ from the Cowork MCP wrapper. Until this fix, ~13 metrics in
    the dashboard silently zeroed out because extract() only handled the
    Cowork-shaped dicts.

    Field-by-field shape audit:
      - singleSelect           Cowork: {"id":..., "name":...}      Raw REST: "value"
      - singleCollaborator     Cowork: {"id":..., "name":...}      Raw REST: {"id":..., "name":..., "email":...}
      - multipleCollaborators  Cowork: [{...}]                     Raw REST: [{...}]   (works in both)
      - multipleRecordLinks    Cowork: [{"id":..., "name":...}]    Raw REST: ["recXXX"]
      - multipleLookupValues   Cowork: [value, ...]                Raw REST: [value, ...]   (numbers / strings)

    extract() now reads through shape-agnostic _name / _id helpers, and reads
    company_id / company_name from their dedicated lookup fields with the
    link field as fallback. Tests cover both shapes for every affected field.
    """

    # ---- singleSelect: verification_status, qa_status, ww_qa, status, comment, role ----

    def test_single_select_verification_status_raw_rest_string(self):
        rec = _rec({"verification_status": "tagged"})
        self.assertEqual(extract(rec)["verification_status"], "tagged")

    def test_single_select_verification_status_cowork_dict(self):
        rec = _rec({"verification_status": {"id": "sel123", "name": "tagged"}})
        self.assertEqual(extract(rec)["verification_status"], "tagged")

    def test_single_select_preserves_trailing_space_raw_rest(self):
        # The 'Selected for BO QA ' choice has a real trailing space in
        # Airtable — must survive across the raw-REST path too.
        rec = _rec({"verification_status": "Selected for BO QA "})
        info = extract(rec)
        self.assertEqual(info["verification_status"], "Selected for BO QA ")
        self.assertTrue(info["verification_status"].endswith(" "))

    def test_single_select_qa_status_both_shapes(self):
        self.assertEqual(extract(_rec({"qa_status": "approve"}))["qa_status"], "approve")
        self.assertEqual(extract(_rec({"qa_status": {"id": "x", "name": "approve"}}))["qa_status"],
                         "approve")

    def test_single_select_role_raw_rest_string(self):
        self.assertEqual(extract(_rec({"role": "OWNER"}))["role"], "OWNER")

    def test_single_select_role_garbage_dropped_raw_rest(self):
        # 'role' singleSelect has a self-referential garbage choice (literal
        # string "role"); must be dropped on raw REST just like on Cowork dict.
        self.assertIsNone(extract(_rec({"role": "role"}))["role"])

    def test_single_select_comment_raw_rest_string(self):
        rec = _rec({"comment": "suspected zombie vessel"})
        self.assertEqual(extract(rec)["comment"], "suspected zombie vessel")

    def test_single_select_status_garbage_dropped_both_shapes(self):
        self.assertIsNone(extract(_rec({"status": "Leopard"}))["status"])
        self.assertIsNone(extract(_rec({"status": {"id": "x", "name": "Leopard"}}))["status"])

    def test_single_select_ww_qa_raw_rest_string(self):
        self.assertEqual(extract(_rec({"ww_qa": "change"}))["ww_qa"], "change")

    # ---- singleCollaborator (was working — regression-proof) ----

    def test_single_collaborator_qa_assignee_raw_rest_dict(self):
        rec = _rec({"qa_assignee": {"id": "q1", "name": "Zuleikha Musa",
                                    "email": "redacted@example.com"}})
        self.assertEqual(extract(rec)["qa_assignee"], "Zuleikha Musa")

    def test_single_collaborator_last_modified_by_raw_rest_dict(self):
        rec = _rec({"last_modified_by": {"id": "m1", "name": "Mod Person", "email": "m@x"}})
        self.assertEqual(extract(rec)["last_modified_by"], "Mod Person")

    # ---- multipleCollaborators (was working — regression-proof) ----

    def test_multiple_collaborators_raw_rest_list_of_dicts(self):
        rec = _rec({"assignee": [
            {"id": "u1", "name": "Alice", "email": "a@x"},
            {"id": "u2", "name": "Bob",   "email": "b@x"},
        ]})
        info = extract(rec)
        self.assertEqual(info["assignee"], "Alice")
        self.assertEqual(info["assignees"], ["Alice", "Bob"])

    # ---- multipleRecordLinks: company_id_and_name ----

    def test_multiple_record_links_raw_rest_list_of_strings_fallback(self):
        # When lookups aren't populated, extract falls back to the link field.
        # Raw REST returns the link as bare record-IDs ["recXXX"]. Both
        # company_id and company_name resolve to that ID — degraded but truthy.
        rec = _rec({"company_id_and_name": ["rec1Vjo9ZNx7RG21V"]})
        info = extract(rec)
        self.assertEqual(info["company_id"], "rec1Vjo9ZNx7RG21V")
        self.assertEqual(info["company_name"], "rec1Vjo9ZNx7RG21V")

    def test_multiple_record_links_cowork_list_of_dicts_fallback(self):
        # Cowork shape: link returns [{"id":..., "name": "12345 - Acme"}].
        # Fallback gives the real composite name.
        rec = _rec({"company_id_and_name": [{"id": "recCo1", "name": "12345 - Acme"}]})
        info = extract(rec)
        self.assertEqual(info["company_id"], "recCo1")
        self.assertEqual(info["company_name"], "12345 - Acme")

    # ---- multipleLookupValues: the new primary source for company_id / company_name ----

    def test_lookup_company_id_numeric_value_in_list(self):
        # company_id_lookup follows the link to the linked record's primary
        # numeric ID (e.g. 82782 — a real value from today's cache).
        rec = _rec({"company_id_lookup": [82782]})
        self.assertEqual(extract(rec)["company_id"], 82782)

    def test_lookup_company_name_string_value_in_list(self):
        rec = _rec({"company_name_lookup": ["Fujian Highton Development Co Ltd"]})
        self.assertEqual(extract(rec)["company_name"], "Fujian Highton Development Co Ltd")

    def test_lookup_company_name_unknown_value(self):
        # Real value seen in today's cache — must extract verbatim.
        rec = _rec({"company_name_lookup": ["Unknown"]})
        self.assertEqual(extract(rec)["company_name"], "Unknown")

    def test_lookup_fields_win_over_link_field_fallback(self):
        # When both the lookup AND the link field are present, lookups win.
        rec = _rec({
            "company_id_lookup":   [82782],
            "company_name_lookup": ["Real Co Ltd"],
            "company_id_and_name": ["recFallbackXXX"],
        })
        info = extract(rec)
        self.assertEqual(info["company_id"], 82782)
        self.assertEqual(info["company_name"], "Real Co Ltd")

    def test_lookup_empty_list_falls_back_to_link(self):
        # Lookup present but empty list (no linked record yet): fall back.
        rec = _rec({
            "company_id_lookup":   [],
            "company_name_lookup": [],
            "company_id_and_name": [{"id": "recCo1", "name": "Acme"}],
        })
        info = extract(rec)
        self.assertEqual(info["company_id"], "recCo1")
        self.assertEqual(info["company_name"], "Acme")

    def test_lookup_none_falls_back_to_link(self):
        rec = _rec({
            "company_id_and_name": [{"id": "recCo1", "name": "Acme"}],
            # company_id_lookup / company_name_lookup absent entirely
        })
        info = extract(rec)
        self.assertEqual(info["company_id"], "recCo1")
        self.assertEqual(info["company_name"], "Acme")

    # ---- End-to-end: a record shaped exactly like what poll_airtable.py captures ----

    def test_realistic_raw_rest_record_extracts_every_dict_shape_field(self):
        """A record mirroring the live raw-REST payload audit. Every
        dict-shape field should extract cleanly — pre-fix this returned
        None for 8 of these fields."""
        rec = {
            "id": "rec123",
            "createdTime": "2026-05-18T09:00:00.000Z",
            "fields": {
                FIELD_IDS["imo"]:                  "9999999",
                FIELD_IDS["assignee"]:             [{"id": "u1", "name": "Elvis Mwanzia",
                                                    "email": "redacted@example.com"}],
                FIELD_IDS["qa_assignee"]:          {"id": "q1", "name": "Zuleikha Musa",
                                                    "email": "redacted@example.com"},
                FIELD_IDS["verification_status"]:  "tagged",
                FIELD_IDS["qa_status"]:            "approve",
                FIELD_IDS["ww_qa"]:                "change",
                FIELD_IDS["role"]:                 "OWNER",
                FIELD_IDS["comment"]:              "suspected zombie vessel",
                FIELD_IDS["status"]:               "Tagged",
                FIELD_IDS["company_id_lookup"]:    [82782],
                FIELD_IDS["company_name_lookup"]:  ["Acme Shipping"],
                FIELD_IDS["company_id_and_name"]:  ["recCo1"],   # raw-REST link shape
                FIELD_IDS["start_tagging_date"]:   "2026-05-18T06:05:00.000Z",
                FIELD_IDS["start_date"]:           "2024-10-22T00:00:00.000Z",
                FIELD_IDS["dead_vessel"]:          False,
            },
        }
        info = extract(rec)
        self.assertEqual(info["imo"], "9999999")
        self.assertEqual(info["assignee"], "Elvis Mwanzia")
        self.assertEqual(info["assignees"], ["Elvis Mwanzia"])
        self.assertEqual(info["qa_assignee"], "Zuleikha Musa")
        self.assertEqual(info["verification_status"], "tagged")
        self.assertEqual(info["qa_status"], "approve")
        self.assertEqual(info["ww_qa"], "change")
        self.assertEqual(info["role"], "OWNER")
        self.assertEqual(info["comment"], "suspected zombie vessel")
        self.assertEqual(info["status"], "Tagged")
        self.assertEqual(info["company_id"], 82782)
        self.assertEqual(info["company_name"], "Acme Shipping")
        self.assertEqual(info["start_tagging"], "2026-05-18T06:05:00.000Z")
        self.assertEqual(info["start_date"], "2024-10-22T00:00:00.000Z")
        self.assertFalse(info["dead_vessel"])


class TestHelpers(unittest.TestCase):
    """Direct tests of _name / _id since they're the shape-bridging core."""

    def test_name_handles_all_shapes(self):
        from extract_v2 import _name
        self.assertIsNone(_name(None))
        self.assertEqual(_name("tagged"), "tagged")
        self.assertEqual(_name({"id": "x", "name": "tagged"}), "tagged")
        self.assertEqual(_name([{"id": "x", "name": "tagged"}]), "tagged")
        self.assertEqual(_name(["tagged"]), "tagged")
        self.assertIsNone(_name([]))
        self.assertIsNone(_name(42))  # numbers aren't names
        self.assertIsNone(_name({"id": "x"}))  # dict without name

    def test_id_handles_all_shapes(self):
        from extract_v2 import _id
        self.assertIsNone(_id(None))
        self.assertEqual(_id("recXXX"), "recXXX")
        self.assertEqual(_id(82782), 82782)
        self.assertEqual(_id({"id": "recCo1", "name": "Acme"}), "recCo1")
        self.assertEqual(_id([{"id": "recCo1"}]), "recCo1")
        self.assertEqual(_id(["recXXX"]), "recXXX")
        self.assertEqual(_id([82782]), 82782)
        self.assertIsNone(_id([]))
        self.assertIsNone(_id(""))  # empty string → None, not truthy-empty


if __name__ == "__main__":
    unittest.main()
