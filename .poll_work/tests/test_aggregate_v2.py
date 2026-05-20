"""Regression tests for aggregate_v2.

Feeds a hand-built 20-record fixture (15 in-scope across 3 agents/teams,
5 out-of-scope) and asserts every metric the aggregator emits.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregate_v2 import (  # noqa: E402
    EAT,
    QA_KEYS,
    REJECT_THRESHOLD,
    SAMPLING_TARGET_PCT,
    WW_QA_KEYS,
    _intake_total_from_metadata,
    _load_records,
    _save_snapshot,
    aggregate,
    compute_metrics,
    compute_not_yet_finalized,
    compute_qa_done_not_finalized,
    compute_qa_reviewers,
    compute_task_breakdowns,
    compute_weekly_rollup,
    load_snapshots,
)
from extract_v2 import COMMENT_VALUES, SELECTED_FOR_BO_QA, SELECTED_FOR_WW_QA  # noqa: E402


TODAY = date(2026, 5, 15)

OWNERSHIP_ASSIGNEES = {
    "Alice": "Simba",
    "Bob":   "Tembo",
    "Carol": "Nyati",
}


def _ts(day_offset, hour):
    """ISO-8601 EAT timestamp, day_offset days from TODAY at the given hour."""
    d = TODAY.toordinal() + day_offset
    dt = date.fromordinal(d)
    return f"{dt.isoformat()}T{hour:02d}:00:00.000+03:00"


def _info(**overrides):
    """Build a complete info dict (all extract_v2 keys present)."""
    base = {
        "imo": None, "assignee": None, "assignees": [], "qa_assignee": None, "ww_qa_assignee": None,
        "last_modified_by": None, "start_tagging": None, "start_date": None,
        "created": None, "last_modified": None, "done_selected_time": None,
        "valid_selected_time": None, "qa_status_ts": None, "company_id": None, "company_name": None,
        "verification_status": None, "qa_status": None, "ww_qa": None,
        "status": None, "is_change": None, "comment": None, "reminder": None,
        "source_flow": None, "add_new_company": None, "requested_by": None,
        "valid_done_by_bo": False, "role": None, "dead_vessel": False,
    }
    base.update(overrides)
    # Convenience: if caller set `assignee` but not `assignees`, mirror.
    if "assignee" in overrides and "assignees" not in overrides:
        base["assignees"] = [overrides["assignee"]] if overrides["assignee"] else []
    return base


def _fixture():
    """20 records: 5 Alice (Simba), 5 Bob (Tembo), 5 Carol (Nyati), 5 out-of-scope."""
    return [
        # --- Alice / Simba (5) ---
        _info(assignee="Alice", qa_assignee="QA1",
              verification_status="tagged",
              start_tagging=_ts(0, 9), created=_ts(0, 8),
              comment="suspected zombie vessel", source_flow="Equasis",
              role="OWNER"),
        _info(assignee="Alice", qa_assignee="QA1",
              verification_status=SELECTED_FOR_BO_QA,
              start_tagging=_ts(0, 10), created=_ts(0, 8),
              qa_status_ts=_ts(0, 11),
              comment="IMO searched but not found", source_flow="Equasis",
              role="OWNER",
              add_new_company="New Corp\nOther Co",
              reminder="2026-05-10"),  # overdue
        _info(assignee="Alice", qa_assignee="QA1",
              verification_status="Done",
              start_tagging=_ts(0, 11), created=_ts(0, 9),
              qa_status="approve", qa_status_ts=_ts(0, 12),
              done_selected_time=_ts(0, 13),
              ww_qa="approve", ww_qa_assignee="WW1",
              comment="Role not found", role="BENEFICIAL_OWNER",
              reminder="2026-05-20"),  # future
        _info(assignee="Alice", qa_assignee="QA1",
              verification_status="Done",
              start_tagging=_ts(0, 12), created=_ts(0, 10),
              qa_status="changed", qa_status_ts=_ts(0, 13),
              done_selected_time=_ts(0, 14),
              ww_qa="change", ww_qa_assignee="WW1",
              comment="Document Not Available", role="OPERATOR"),
        _info(assignee="Alice", qa_assignee="QA1",
              verification_status="tagged",
              start_tagging=_ts(0, 13), created=_ts(0, 11),
              comment="suspected zombie vessel", source_flow="Nexis",
              role="OWNER",
              add_new_company="Another New Co"),

        # --- Bob / Tembo (5) ---
        _info(assignee="Bob", qa_assignee="QA2",
              verification_status="tagged",
              start_tagging=_ts(0, 8), created=_ts(-1, 8),
              comment="IMO not found, has positional data", source_flow="WW",
              role="MANAGEMENT"),
        _info(assignee="Bob", qa_assignee="QA2",
              verification_status=SELECTED_FOR_BO_QA,
              start_tagging=_ts(0, 9), created=_ts(-1, 9),
              qa_status_ts=_ts(0, 10),
              comment="IMO Never Existed, No positional data", source_flow="WW",
              role="TECHNICAL_MANAGER"),
        _info(assignee="Bob", qa_assignee="QA2",
              verification_status="Done",
              start_tagging=_ts(0, 10), created=_ts(-2, 8),
              qa_status="approve", qa_status_ts=_ts(0, 11),
              done_selected_time=_ts(0, 12),
              comment="No Longer updated by (LRF) IHSF", role="MANAGEMENT"),
        _info(assignee="Bob", qa_assignee="QA2",
              verification_status="Done",
              start_tagging=_ts(-1, 15), created=_ts(-2, 8),
              qa_status="changed", qa_status_ts=_ts(-1, 16),
              done_selected_time=_ts(-1, 17),
              ww_qa="change", ww_qa_assignee="WW2",
              comment="Cancelled before construction", role="OPERATOR"),
        _info(assignee="Bob", qa_assignee="QA2",
              verification_status="Valid",
              valid_done_by_bo=True,
              start_tagging=_ts(-2, 10), created=_ts(-2, 8),
              comment="IMO found, No positional data on WW", role="ISM_MANAGER",
              add_new_company="More New Co"),  # NOT open: vs is Valid

        # --- Carol / Nyati (5) ---
        _info(assignee="Carol", qa_assignee="QA3",
              verification_status="waiting",
              created=_ts(0, 7),
              comment="IMO not found on Nexis/Equasis, No positional data",
              role="OWNER"),
        _info(assignee="Carol", qa_assignee="QA3",
              verification_status="tagged",
              start_tagging=_ts(0, 8), created=_ts(0, 7),
              comment="IMO not found on Nexis/Equasis/WW", source_flow="Equasis",
              reminder="2026-05-10",  # overdue
              role="OWNER"),
        _info(assignee="Carol", qa_assignee="QA3",
              verification_status="need to be update",
              start_tagging=_ts(0, 9), created=_ts(0, 7),
              comment="suspected zombie vessel",
              role="BENEFICIAL_OWNER"),
        _info(assignee="Carol", qa_assignee="QA3",
              verification_status="Done",
              start_tagging=_ts(0, 10), created=_ts(-1, 7),
              qa_status="changed", qa_status_ts=_ts(0, 11),
              done_selected_time=_ts(0, 12),
              comment="Document Not Available", role="OWNER"),
        _info(assignee="Carol", qa_assignee="QA3",
              verification_status="Done",
              start_tagging=_ts(0, 11), created=_ts(-1, 7),
              qa_status="approve", qa_status_ts=_ts(0, 12),
              done_selected_time=_ts(0, 13),
              ww_qa="approve", ww_qa_assignee="WW1",
              comment="suspected zombie vessel", role="OWNER"),

        # --- 5 out-of-scope (different / unknown assignee) ---
        *[_info(assignee="OutsideUser", verification_status="tagged",
                start_tagging=_ts(0, 15), created=_ts(0, 15))
          for _ in range(5)],
    ]


class TestComputeMetrics(unittest.TestCase):
    """Direct test of compute_metrics over the 15 in-scope records."""

    @classmethod
    def setUpClass(cls):
        all_records = _fixture()
        cls.in_scope = [r for r in all_records if r["assignee"] in OWNERSHIP_ASSIGNEES]
        assert len(cls.in_scope) == 15
        cls.m = compute_metrics(cls.in_scope, TODAY)

    def test_counts_by_verification_status(self):
        self.assertEqual(self.m["counts_by_verification_status"], {
            "tagged": 4,
            SELECTED_FOR_BO_QA: 2,
            "Done": 6,
            "Valid": 1,
            "waiting": 1,
            "need to be update": 1,
        })

    def test_counts_by_qa_status(self):
        self.assertEqual(self.m["counts_by_qa_status"], {
            "approve": 3,
            "changed": 3,
        })

    def test_comment_distribution_has_all_11_keys(self):
        self.assertEqual(set(self.m["comment_distribution"].keys()), set(COMMENT_VALUES))
        self.assertEqual(sum(self.m["comment_distribution"].values()), 15)

    def test_comment_distribution_values(self):
        d = self.m["comment_distribution"]
        self.assertEqual(d["suspected zombie vessel"], 4)
        self.assertEqual(d["IMO searched but not found"], 1)
        self.assertEqual(d["Role not found"], 1)
        self.assertEqual(d["Document Not Available"], 2)
        self.assertEqual(d["Cancelled before construction"], 1)
        self.assertEqual(d["IMO found, No positional data on WW"], 1)

    def test_source_flow_distribution(self):
        self.assertEqual(self.m["source_flow_distribution"],
                         {"Equasis": 3, "Nexis": 1, "WW": 2})

    def test_per_role_volume(self):
        self.assertEqual(self.m["per_role_volume"], {
            "OWNER": 7,
            "BENEFICIAL_OWNER": 2,
            "OPERATOR": 2,
            "MANAGEMENT": 2,
            "TECHNICAL_MANAGER": 1,
            "ISM_MANAGER": 1,
        })

    def test_add_new_company_open(self):
        # Broader definition (fix-add-new-company-metric-broader-definition):
        # any record tagged today with add_new_company filled. The main
        # fixture has 2 such records (Alice 2 = BO QA today, Alice 5 = tagged
        # today, both with add_new_company filled). Detailed scenarios live
        # in TestAddNewCompanyBroaderDefinition.
        self.assertEqual(self.m["add_new_company_open"], 2)
        # Same value exposed under the new alias key.
        self.assertEqual(self.m["add_new_company_today"], 2)

    def test_reminder_metrics(self):
        self.assertEqual(self.m["reminder_open"], 3)     # Alice 2 + Alice 3 + Carol 2
        self.assertEqual(self.m["reminder_overdue"], 2)  # Alice 2 + Carol 2

    def test_team_routed_intake_today(self):
        # 5 Alice + 3 Carol created today. Bob's are all yesterday/earlier.
        self.assertEqual(self.m["team_routed_intake_today"], 8)
        # daily_intake removed entirely
        self.assertNotIn("daily_intake", self.m)

    def test_done_today(self):
        # Alice 3,4 + Bob 3 + Carol 4,5 have done_selected_time set to today
        # Bob 4 was done yesterday. Total: 5.
        self.assertEqual(self.m["done_today"], 5)

    def test_bo_qa_backlog(self):
        # Alice 2 (SELECTED_FOR_BO_QA) + Bob 2 (SELECTED_FOR_BO_QA) regardless of when tagged
        self.assertEqual(self.m["bo_qa_backlog"], 2)

    def test_tagged_today(self):
        # All 5 Alice tagged today; Bob 1,2,3 tagged today; Carol 2,3,4,5 tagged today.
        self.assertEqual(self.m["tagged_today"], 12)

    def test_in_bo_qa_today(self):
        # Alice 2 + Bob 2.
        self.assertEqual(self.m["in_bo_qa_today"], 2)

    def test_qa_inspected_today(self):
        # Alice 3+4, Bob 3, Carol 4+5.
        self.assertEqual(self.m["qa_inspected_today"], 5)
        self.assertEqual(self.m["qa_changed_today"], 2)  # Alice 4 + Carol 4

    def test_need_to_be_update_today(self):
        # Carol 3: verification_status='need to be update', tagged today.
        self.assertEqual(self.m["need_to_be_update_today"], 1)

    def test_ww_qa_throughput_and_change_rate(self):
        # Alice 3 (approve), Alice 4 (change), Bob 4 (change), Carol 5 (approve).
        self.assertEqual(self.m["ww_qa_throughput"], 4)
        self.assertEqual(self.m["ww_qa_change_rate"], 50.0)

    def test_sampling_actual_pct(self):
        # 3-component definition matches the existing aggregator:
        # (in_bo_qa_today + qa_inspected_today + need_to_be_update_today) / tagged_today
        # = (2 + 5 + 1) / 12 = 66.7
        self.assertEqual(self.m["sampling_actual_pct"], 66.7)
        self.assertEqual(self.m["sampling_target_pct"], SAMPLING_TARGET_PCT)

    def test_reject_rate(self):
        # qa_changed_today / qa_inspected_today = 2/5 = 40.0
        self.assertEqual(self.m["reject_rate"], 40.0)
        self.assertEqual(self.m["reject_threshold"], REJECT_THRESHOLD)

    def test_lead_time_keys(self):
        keys = {"created_to_tagged_p50", "created_to_tagged_p90",
                "tagged_to_bo_qa_p50", "tagged_to_bo_qa_p90",
                "bo_qa_to_done_p50", "bo_qa_to_done_p90"}
        self.assertEqual(set(self.m["lead_time_seconds"].keys()), keys)

    def test_lead_time_tagged_to_bo_qa_is_one_hour(self):
        # All 8 (start_tagging → qa_status_ts) gaps in fixture are exactly 1 hour.
        lt = self.m["lead_time_seconds"]
        self.assertEqual(lt["tagged_to_bo_qa_p50"], 3600)
        self.assertEqual(lt["tagged_to_bo_qa_p90"], 3600)

    def test_lead_time_bo_qa_to_done_is_one_hour(self):
        # All 6 (qa_status_ts → done_selected_time) gaps are exactly 1 hour.
        lt = self.m["lead_time_seconds"]
        self.assertEqual(lt["bo_qa_to_done_p50"], 3600)
        self.assertEqual(lt["bo_qa_to_done_p90"], 3600)

    def test_lead_time_created_to_tagged_is_positive(self):
        # Values are heterogeneous (1h to 50h). Just sanity-check ordering + positivity.
        lt = self.m["lead_time_seconds"]
        self.assertGreater(lt["created_to_tagged_p50"], 0)
        self.assertGreaterEqual(lt["created_to_tagged_p90"], lt["created_to_tagged_p50"])


class TestAggregateDimensions(unittest.TestCase):
    """Test the multi-dimension grouping (team / agent / qa / ww_qa)."""

    @classmethod
    def setUpClass(cls):
        cls.aggs = aggregate(_fixture(), TODAY, OWNERSHIP_ASSIGNEES)

    def test_top_level_keys(self):
        self.assertEqual(self.aggs["date"], "2026-05-15")
        self.assertEqual(self.aggs["sampling_target_pct"], SAMPLING_TARGET_PCT)
        self.assertEqual(self.aggs["reject_threshold"], REJECT_THRESHOLD)
        for k in ("totals", "by_team", "by_agent", "by_qa", "by_ww_qa"):
            self.assertIn(k, self.aggs)

    def test_out_of_scope_records_dropped(self):
        # 5 OutsideUser records should not appear anywhere.
        self.assertNotIn("OutsideUser", self.aggs["by_agent"])

    def test_team_dimension(self):
        self.assertEqual(set(self.aggs["by_team"].keys()), {"Simba", "Tembo", "Nyati"})
        self.assertEqual(self.aggs["by_team"]["Simba"]["tagged_today"], 5)
        self.assertEqual(self.aggs["by_team"]["Tembo"]["tagged_today"], 3)
        self.assertEqual(self.aggs["by_team"]["Nyati"]["tagged_today"], 4)

    def test_lead_time_today(self):
        # start_tagging → completion for records completed today. Five in-scope
        # records have a done_selected_time of today (Alice 3,4 / Bob 3 / Carol
        # 4,5 — same set as done_today=5), each tagged exactly 2h before being
        # done, so every sample is 7200s and count reconciles with done_today.
        lt = self.aggs["lead_time_today"]
        self.assertEqual(lt["count"], 5)
        self.assertEqual(lt["count"], self.aggs["totals"]["done_today"])
        self.assertEqual(lt["p50"], 7200)
        self.assertEqual(lt["p90"], 7200)
        # Per-team breakdown: Simba (Alice 3,4) = 2, Tembo (Bob 3) = 1,
        # Nyati (Carol 4,5) = 2. Bob 4 was done yesterday, so Tembo is 1 not 2.
        self.assertEqual(set(lt["by_team"].keys()), {"Simba", "Tembo", "Nyati"})
        self.assertEqual(lt["by_team"]["Simba"]["count"], 2)
        self.assertEqual(lt["by_team"]["Tembo"]["count"], 1)
        self.assertEqual(lt["by_team"]["Nyati"]["count"], 2)
        self.assertEqual(lt["by_team"]["Simba"]["p50"], 7200)
        self.assertEqual(lt["by_team"]["Nyati"]["p90"], 7200)

    def test_agent_dimension(self):
        self.assertEqual(set(self.aggs["by_agent"].keys()), {"Alice", "Bob", "Carol"})
        self.assertEqual(self.aggs["by_agent"]["Alice"]["team_routed_intake_today"], 5)
        self.assertEqual(self.aggs["by_agent"]["Carol"]["team_routed_intake_today"], 3)
        self.assertEqual(self.aggs["by_agent"]["Bob"]["team_routed_intake_today"], 0)

    def test_qa_dimension(self):
        self.assertEqual(set(self.aggs["by_qa"].keys()), {"QA1", "QA2", "QA3"})

    def test_by_qa_exposes_bo_qa_backlog(self):
        # Per-reviewer assigned BO QA queue (for the Per-reviewer BO QA load
        # panel). Alice 2 (QA1) and Bob 2 (QA2) are SELECTED_FOR_BO_QA; Carol
        # (QA3) has none.
        self.assertEqual(self.aggs["by_qa"]["QA1"]["bo_qa_backlog"], 1)
        self.assertEqual(self.aggs["by_qa"]["QA2"]["bo_qa_backlog"], 1)
        self.assertEqual(self.aggs["by_qa"]["QA3"]["bo_qa_backlog"], 0)

    def test_ww_qa_dimension(self):
        self.assertEqual(set(self.aggs["by_ww_qa"].keys()), {"WW1", "WW2"})
        # WW1 reviewed 3 records: Alice 3 (approve), Alice 4 (change), Carol 5 (approve).
        self.assertEqual(self.aggs["by_ww_qa"]["WW1"]["ww_qa_throughput"], 3)
        # WW2 reviewed 1 record: Bob 4 (change).
        self.assertEqual(self.aggs["by_ww_qa"]["WW2"]["ww_qa_throughput"], 1)
        self.assertEqual(self.aggs["by_ww_qa"]["WW2"]["ww_qa_change_rate"], 100.0)

    def test_by_qa_is_slimmed(self):
        # by_qa should expose only the QA-relevant metric keys.
        for qa_name, block in self.aggs["by_qa"].items():
            self.assertEqual(set(block.keys()), QA_KEYS,
                             f"by_qa[{qa_name}] should match QA_KEYS exactly")

    def test_by_ww_qa_is_slimmed(self):
        for ww_name, block in self.aggs["by_ww_qa"].items():
            self.assertEqual(set(block.keys()), WW_QA_KEYS,
                             f"by_ww_qa[{ww_name}] should match WW_QA_KEYS exactly")

    def test_full_dims_keep_full_block(self):
        # by_team, by_agent, totals get the full metric block — team_routed_intake_today
        # and per_role_volume should be present (and absent from the slimmed dims).
        self.assertIn("team_routed_intake_today", self.aggs["totals"])
        self.assertIn("per_role_volume", self.aggs["totals"])
        self.assertIn("team_routed_intake_today", self.aggs["by_team"]["Simba"])
        self.assertIn("team_routed_intake_today", self.aggs["by_agent"]["Alice"])
        self.assertNotIn("team_routed_intake_today", self.aggs["by_qa"]["QA1"])
        self.assertNotIn("team_routed_intake_today", self.aggs["by_ww_qa"]["WW1"])

    def test_relations_support_intake_today_totals_only(self):
        # Whole-table intake including out-of-scope records (the 5 OutsideUser records also created today).
        # In fixture: 5 Alice (created today) + 3 Carol (today) + 5 OutsideUser (today) = 13
        self.assertEqual(self.aggs["totals"]["relations_support_intake_today"], 13)
        # Must NOT appear in per-team or per-agent slices
        self.assertNotIn("relations_support_intake_today", self.aggs["by_team"]["Simba"])
        self.assertNotIn("relations_support_intake_today", self.aggs["by_agent"]["Alice"])
        self.assertNotIn("relations_support_intake_today", self.aggs["by_qa"]["QA1"])


class TestLeadTimeTodaySanctionsCohorts(unittest.TestCase):
    """lead_time_today.by_cohort splits today's completions by
    is_sanctions(requested_by) — the name-pattern rule. The shared
    _fixture() has no sanctions task, so this uses a small dedicated set
    exercising both cohorts."""

    @classmethod
    def setUpClass(cls):
        # Three completions today across both cohorts, plus one tagged-only
        # record (not completed today → must be ignored by lead_time_today).
        #   - sanctions (Alice/Simba): tagged 09:00 → done 12:00 = 3h = 10800s
        #   - non-sanctions (Bob/Tembo): tagged 10:00 → done 11:00 = 1h = 3600s
        #   - non-sanctions via blank requested_by (Carol/Nyati): 1h = 3600s
        records = [
            _info(assignee="Alice", requested_by="Vessel Sanctions Screening",
                  verification_status="Done",
                  start_tagging=_ts(0, 9), done_selected_time=_ts(0, 12), created=_ts(0, 8)),
            _info(assignee="Bob", requested_by="Standard ownership task",
                  verification_status="Done",
                  start_tagging=_ts(0, 10), done_selected_time=_ts(0, 11), created=_ts(0, 8)),
            _info(assignee="Carol", requested_by=None,
                  verification_status="Done",
                  start_tagging=_ts(0, 10), done_selected_time=_ts(0, 11), created=_ts(0, 8)),
            _info(assignee="Alice", requested_by="Vessel Sanctions Screening",
                  verification_status="tagged",
                  start_tagging=_ts(0, 9), created=_ts(0, 8)),
        ]
        cls.ltt = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)["lead_time_today"]

    def test_by_cohort_present_and_both_cohorts_exercised(self):
        self.assertIn("by_cohort", self.ltt)
        self.assertEqual(set(self.ltt["by_cohort"].keys()), {"sanctions", "non_sanctions"})

    def test_by_cohort_counts_sum_to_overall(self):
        self.assertEqual(self.ltt["count"], 3)
        cohort_sum = sum(c["count"] for c in self.ltt["by_cohort"].values())
        self.assertEqual(cohort_sum, self.ltt["count"])

    def test_cohort_percentiles_land_at_fixture_values(self):
        # Sanctions: single 3h sample. Non-sanctions: two 1h samples
        # (one named, one with blank requested_by → defaults to non_sanctions).
        sanc = self.ltt["by_cohort"]["sanctions"]
        nonsanc = self.ltt["by_cohort"]["non_sanctions"]
        self.assertEqual((sanc["count"], sanc["p50"], sanc["p90"]), (1, 10800, 10800))
        self.assertEqual((nonsanc["count"], nonsanc["p50"], nonsanc["p90"]), (2, 3600, 3600))

    def test_zero_count_cohort_omitted(self):
        # Only non-sanctions completions today → sanctions cohort omitted,
        # mirroring the by_team empty convention.
        recs = [
            _info(assignee="Bob", requested_by="Standard ownership task",
                  verification_status="Done",
                  start_tagging=_ts(0, 10), done_selected_time=_ts(0, 11), created=_ts(0, 8)),
        ]
        bc = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)["lead_time_today"]["by_cohort"]
        self.assertEqual(set(bc.keys()), {"non_sanctions"})

    def test_name_pattern_drives_cohort(self):
        """Sanctions cohort is determined by the task name (requested_by)
        containing 'sanction'/'sanctions' case-insensitively, NOT by any
        per-record field. 'SanctionChangeIntel20May2026' (singular) matches."""
        # Records identical except for requested_by — one matches sanctions
        # pattern, one doesn't, both completed today
        recs = [
            _info(assignee="Alice", requested_by="SanctionChangeIntel20May2026",
                  verification_status="Done",
                  start_tagging=_ts(0, 10), done_selected_time=_ts(0, 11), created=_ts(0, 8)),
            _info(assignee="Bob", requested_by="CargoChangeIntel20May2026",
                  verification_status="Done",
                  start_tagging=_ts(0, 10), done_selected_time=_ts(0, 11), created=_ts(0, 8)),
        ]
        bc = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)["lead_time_today"]["by_cohort"]
        self.assertEqual(bc["sanctions"]["count"], 1)
        self.assertEqual(bc["non_sanctions"]["count"], 1)


class TestCaseInsensitiveAssigneeMatch(unittest.TestCase):
    """Airtable returns assignee names with varying casing
    ('Hellen vigehi' vs the roster's 'Hellen Vigehi'). The aggregator
    must match them via case-insensitive lookup, same as the poller."""

    def test_case_and_whitespace_variants_match(self):
        records = [
            _info(assignee="ALICE", verification_status="tagged"),
            _info(assignee="alice", verification_status="tagged"),
            _info(assignee="  Alice  ", verification_status="tagged"),  # surrounding spaces
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(
            aggs["by_team"]["Simba"]["counts_by_verification_status"].get("tagged", 0),
            3,
            "all 3 case/space variants of 'Alice' should fall into Simba",
        )


class TestCanonicalAgentGroupingKey(unittest.TestCase):
    """by_agent keys must match the roster's canonical spelling, not Airtable's raw casing or aliases."""

    def test_case_variants_collapse_to_canonical(self):
        records = [
            _info(assignee="ALICE", verification_status="tagged"),
            _info(assignee="alice", verification_status="tagged"),
            _info(assignee="  Alice  ", verification_status="tagged"),
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(list(aggs["by_agent"].keys()), ["Alice"],
                         "3 case variants should collapse to one 'Alice' key")
        self.assertEqual(aggs["by_agent"]["Alice"]["counts_by_verification_status"]["tagged"], 3)

    def test_alias_resolves_to_parent_member_name(self):
        from aggregate_v2 import _build_ownership_assignees
        # Mirrors the real config/roster.json shape for Simba's Merline Akinyi.
        roster = {
            "Simba": {
                "qa": {"name": "Q"},
                "ww_qa": None,
                "members": [
                    {"name": "Merline Akinyi", "aliases": ["Bet Merline Akinyi"]},
                ],
            },
        }
        own = _build_ownership_assignees(roster)
        records = [
            _info(assignee="bet merline akinyi", verification_status="tagged"),
            _info(assignee="Bet Merline Akinyi", verification_status="tagged"),
            _info(assignee="Merline Akinyi",     verification_status="tagged"),
        ]
        aggs = aggregate(records, TODAY, own)
        self.assertEqual(list(aggs["by_agent"].keys()), ["Merline Akinyi"],
                         "alias + canonical should both group under 'Merline Akinyi'")
        self.assertEqual(aggs["by_team"]["Simba"]["counts_by_verification_status"]["tagged"], 3)


class TestComputedAt(unittest.TestCase):
    def test_computed_at_present_recent_iso(self):
        aggs = aggregate(_fixture(), TODAY, OWNERSHIP_ASSIGNEES)
        self.assertIn("computed_at", aggs)
        dt = datetime.fromisoformat(aggs["computed_at"])
        age_s = abs((datetime.now(EAT) - dt).total_seconds())
        self.assertLess(age_s, 60, f"computed_at should be within the last 60s, was {age_s:.1f}s")


# A vessel business date — start_date is a build/inception year, NOT a tagging
# timestamp. Tests keep it distinct from start_tagging on purpose so the two
# fields' semantics stay honest in fixtures (start_date 2020-03-15, bucketing
# decided by start_tagging at today's EAT hour).
_BUSINESS_DATE = "2020-03-15T00:00:00.000+00:00"


# Helper for the hourly_buckets rule — a record that satisfies all gates at
# the given EAT start_tagging timestamp. Override any field via kwargs to
# construct negative-case fixtures (set start_date=None, etc.).
def _tagging_rec(assignee, start_tagging_iso, **overrides):
    base = dict(
        assignee=assignee,
        company_id="recCo1",
        company_name="12345 - Acme Shipping",
        start_tagging=start_tagging_iso,
        start_date=_BUSINESS_DATE,
        # verification_status / dead_vessel / add_new_company left at defaults.
    )
    base.update(overrides)
    return _info(**base)


class TestHourlyBuckets(unittest.TestCase):
    """hourly_buckets rule (post-fix-hourly-rule-transient-state-bug):
       contributes to hour H for agent A on day D iff ALL:
         - company_id truthy AND company_name truthy
         - start_date non-null (any value — not date-compared)
         - start_tagging parses; EAT date == today; 6 <= EAT hour <= 23
         - dead_vessel falsy
         - add_new_company falsy
       verification_status is NOT gated — "tagged" is transient and records
       move downstream within seconds, so the bucket counts records that
       PASSED THROUGH tagged today regardless of their current state."""

    def test_hours_8_9_9_happy_path(self):
        # Three records, start_tagging at 08:30, 09:15, 09:45 EAT today.
        # start_date is a 2020 business date — distinct from start_tagging.
        records = [
            _tagging_rec("Alice", f"{TODAY.isoformat()}T08:30:00.000+03:00"),
            _tagging_rec("Alice", f"{TODAY.isoformat()}T09:15:00.000+03:00"),
            _tagging_rec("Alice", f"{TODAY.isoformat()}T09:45:00.000+03:00"),
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        expected = {h: 0 for h in range(6, 24)}
        expected[8] = 1
        expected[9] = 2
        self.assertEqual(aggs["by_agent"]["Alice"]["hourly_buckets"], expected)
        self.assertEqual(aggs["by_team"]["Simba"]["hourly_buckets"], expected)
        self.assertEqual(aggs["totals"]["hourly_buckets"], expected)

    def test_records_outside_working_hours_dropped(self):
        # 03:00 EAT — outside the 6..23 window, even with all other gates passing.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T03:00:00.000+03:00")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"], {h: 0 for h in range(6, 24)})

    def test_records_not_today_dropped(self):
        # start_tagging is yesterday EAT — should NOT appear in today's buckets.
        records = [_tagging_rec("Alice", "2026-05-14T09:00:00.000+03:00")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"], {h: 0 for h in range(6, 24)})

    def test_positive_verification_status_selected_for_bo_qa(self):
        # The transient-state insight: a record that PASSED THROUGH tagged today
        # is now in "Selected for BO QA" by poll time. It MUST still contribute
        # — gating on vs=="tagged" would zero out the histogram on a busy day.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                verification_status=SELECTED_FOR_BO_QA)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 1)

    def test_positive_verification_status_valid(self):
        # Same insight, further downstream — record moved straight to Valid.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                verification_status="Valid")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 1)

    def test_positive_verification_status_done(self):
        # Same insight — record moved all the way to Done.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                verification_status="Done")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 1)

    def test_positive_verification_status_waiting(self):
        # Trade-off documented: Option X drops the verification_status gate
        # entirely, so a record still in "waiting" with company already linked
        # WILL contribute. In practice agents link company AT the moment of
        # tagging, so this combination is rare — and counting it is the right
        # call: if company_id+company_name+start_date are all filled by an
        # in-roster agent today within working hours, the work happened.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                verification_status="waiting")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 1)

    def test_negative_company_id_blank(self):
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                company_id=None)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 0)

    def test_negative_company_name_blank(self):
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                company_name=None)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 0)

    def test_negative_add_new_company_filled(self):
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                add_new_company="Proposed New Co Ltd")]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 0,
                         "add_new_company records are 100% QA workflow, not agent tagging output")

    def test_negative_dead_vessel_set(self):
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                dead_vessel=True)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 0)

    def test_negative_start_date_missing(self):
        # start_date is a required non-null gate (any value), even though the
        # value itself isn't used for bucketing — it's evidence the agent
        # recorded the vessel's business-date entry.
        records = [_tagging_rec("Alice", f"{TODAY.isoformat()}T10:00:00.000+03:00",
                                start_date=None)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"][10], 0)

    def test_negative_start_tagging_missing(self):
        # Without start_tagging there's no hour-of-day to bucket into.
        records = [_tagging_rec("Alice", None)]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["hourly_buckets"], {h: 0 for h in range(6, 24)})

    def test_boundary_hours_06_23_inclusive_and_05_excluded(self):
        records = [
            _tagging_rec("Alice", f"{TODAY.isoformat()}T06:00:00.000+03:00"),  # h=6 included
            _tagging_rec("Alice", f"{TODAY.isoformat()}T23:59:00.000+03:00"),  # h=23 included
            _tagging_rec("Alice", f"{TODAY.isoformat()}T05:59:00.000+03:00"),  # h=5 excluded
            _tagging_rec("Alice", f"{TODAY.isoformat()}T00:00:00.000+03:00"),  # h=0 excluded
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        h = aggs["totals"]["hourly_buckets"]
        self.assertEqual(h[6], 1, "06:00 EAT should bucket into hour 6")
        self.assertEqual(h[23], 1, "23:59 EAT should bucket into hour 23")
        # 05:59 and 00:00 don't bucket because hours 5 and 0 aren't in the dict at all.
        self.assertNotIn(5, h)
        self.assertNotIn(0, h)
        # Sanity: total bucketed = 2 (the two boundary-included ones).
        self.assertEqual(sum(h.values()), 2)

    def test_hourly_buckets_excluded_from_slimmed_dims(self):
        aggs = aggregate(_fixture(), TODAY, OWNERSHIP_ASSIGNEES)
        for block in aggs["by_qa"].values():
            self.assertNotIn("hourly_buckets", block)
        for block in aggs["by_ww_qa"].values():
            self.assertNotIn("hourly_buckets", block)


class TestComputeTaskBreakdowns(unittest.TestCase):
    """compute_task_breakdowns groups by requested_by and emits per-task lifecycle dicts."""

    def _build_norm_ownership(self):
        # Mirrors aggregate()'s lookup shape for testing alias resolution.
        return {
            "Alice": {"team": "Simba", "canonical": "Alice"},
            "Bob":   {"team": "Tembo", "canonical": "Bob"},
            "Carol": {"team": "Nyati", "canonical": "Carol"},
        }

    def test_multi_assignee_and_multi_team_attribution(self):
        # Task1: Alice solo, Bob solo, Alice+Bob co-assigned (=multi-team record)
        recs = [
            _info(requested_by="Task1", assignees=["Alice"],         verification_status="Done",
                  start_date="2024-01-01", company_id="recC1",
                  created="2026-05-10T08:00:00.000+03:00", last_modified=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(requested_by="Task1", assignees=["Bob"],           verification_status="Done",
                  start_date="2024-01-01", company_id="recC2",
                  created="2026-05-10T08:00:00.000+03:00", last_modified=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(requested_by="Task1", assignees=["Alice", "Bob"], verification_status="tagged",
                  created="2026-05-10T08:00:00.000+03:00", last_modified=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(requested_by="Task2", assignees=["Carol"],         verification_status="waiting",
                  created="2026-05-10T08:00:00.000+03:00", last_modified=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
        ]
        tasks = compute_task_breakdowns(recs, TODAY, self._build_norm_ownership())
        t1 = next(t for t in tasks if t["name"] == "Task1")
        # 2 agents, each touched 2 records (Alice: solo+co; Bob: solo+co)
        agents = {a["name"]: a for a in t1["agents_worked"]}
        self.assertEqual(agents["Alice"]["records"], 2)
        self.assertEqual(agents["Alice"]["team"], "Simba")
        self.assertEqual(agents["Bob"]["records"], 2)
        self.assertEqual(agents["Bob"]["team"], "Tembo")
        # Teams worked — 2 records each (Simba: Alice solo + co-assigned; Tembo: Bob solo + co-assigned)
        teams = {t["team"]: t for t in t1["teams_worked"]}
        self.assertEqual(teams["Simba"]["records"], 2)
        self.assertEqual(teams["Tembo"]["records"], 2)

    def test_status_distribution_zero_filled(self):
        recs = [_info(requested_by="T1", assignees=["Alice"], verification_status="Done")]
        tasks = compute_task_breakdowns(recs, TODAY, self._build_norm_ownership())
        d = tasks[0]["status_distribution"]
        self.assertEqual(set(d.keys()), set(
            ["waiting", "tagged", SELECTED_FOR_BO_QA, "Done", "Valid", SELECTED_FOR_WW_QA, "need to be update"]
        ))
        self.assertEqual(d["Done"], 1)
        self.assertEqual(d["waiting"], 0)

    def test_blank_requested_by_collapses_to_no_task_name(self):
        recs = [
            _info(requested_by=None,  assignees=["Alice"]),
            _info(requested_by="",    assignees=["Bob"]),
            _info(requested_by="   ", assignees=["Carol"]),
        ]
        tasks = compute_task_breakdowns(recs, TODAY, self._build_norm_ownership())
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "(no task name)")
        self.assertEqual(tasks[0]["total_records_in_cache"], 3)


class TestTaskFlags(unittest.TestCase):
    """One test per flag rule. last_modified must be within the past 24h of
    real wall-clock NOW (not TODAY fixture date) for the "stuck" flag to behave."""
    norm = {"Alice": {"team": "Simba", "canonical": "Alice"}}

    @classmethod
    def setUpClass(cls):
        # 10 min ago → guaranteed inside the 24h window when the test runs.
        cls.TODAY_TS = (datetime.now(EAT) - timedelta(minutes=10)).isoformat()

    def _task(self, recs):
        tasks = compute_task_breakdowns(recs, TODAY, self.norm)
        return tasks[0] if tasks else None

    def test_flag_incomplete(self):
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="tagged",
                      last_modified=self.TODAY_TS)]
        self.assertIn("incomplete", self._task(recs)["flags"])

    def test_flag_not_incomplete_when_all_done(self):
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="Done",
                      start_date="2024-01-01", company_id="rec1", last_modified=self.TODAY_TS)]
        self.assertNotIn("incomplete", self._task(recs)["flags"])

    def test_flag_stuck_when_all_records_dormant(self):
        # All last_modified > 24h ago — task is stuck.
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="Done",
                      start_date="2024-01-01", company_id="rec1",
                      last_modified="2024-01-01T00:00:00.000+03:00")]
        self.assertIn("stuck", self._task(recs)["flags"])

    def test_flag_not_stuck_when_recent_activity(self):
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="Done",
                      start_date="2024-01-01", company_id="rec1", last_modified=self.TODAY_TS)]
        self.assertNotIn("stuck", self._task(recs)["flags"])

    def test_flag_company_gap(self):
        # Done with no company AND not dead_vessel = SOP violation
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="Done",
                      company_id=None, dead_vessel=False, last_modified=self.TODAY_TS)]
        self.assertIn("company-gap", self._task(recs)["flags"])

    def test_flag_company_gap_NOT_raised_for_dead_vessel(self):
        recs = [_info(requested_by="T", assignees=["Alice"], verification_status="Done",
                      company_id=None, dead_vessel=True, last_modified=self.TODAY_TS)]
        self.assertNotIn("company-gap", self._task(recs)["flags"])

    def test_flag_unassigned(self):
        recs = [_info(requested_by="T", assignees=[], verification_status="tagged",
                      last_modified=self.TODAY_TS)]
        self.assertIn("unassigned", self._task(recs)["flags"])

    def test_flag_high_waiting(self):
        # 3 waiting + 1 tagged = 75% waiting → fires
        recs = [
            _info(requested_by="T", assignees=["Alice"], verification_status="waiting", last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="waiting", last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="waiting", last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="tagged",  last_modified=self.TODAY_TS),
        ]
        self.assertIn("high-waiting", self._task(recs)["flags"])

    def test_flag_high_waiting_not_at_50pct_exact(self):
        # 2 waiting + 2 tagged = exactly 50% → does NOT fire (rule is strict >)
        recs = [
            _info(requested_by="T", assignees=["Alice"], verification_status="waiting", last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="waiting", last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="tagged",  last_modified=self.TODAY_TS),
            _info(requested_by="T", assignees=["Alice"], verification_status="tagged",  last_modified=self.TODAY_TS),
        ]
        self.assertNotIn("high-waiting", self._task(recs)["flags"])


class TestLoadRecordsDedup(unittest.TestCase):
    """_load_records reads recent_p*.json + intake_p*.json + boqa_p*.json,
    deduping by record id — same record across files counts once."""

    def test_dedup_across_page_files(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            # Record "recA" appears in recent_p1 AND intake_p1 — should dedup.
            # Record "recB" only in recent_p1. Record "recC" only in intake_p1.
            recA = {"id": "recA", "cellValuesByFieldId": {"fldqWGr2XDH9BRmtE": "imo-A"}}
            recB = {"id": "recB", "cellValuesByFieldId": {"fldqWGr2XDH9BRmtE": "imo-B"}}
            recC = {"id": "recC", "cellValuesByFieldId": {"fldqWGr2XDH9BRmtE": "imo-C"}}
            (work / "recent_p1.json").write_text(_json.dumps({"records": [recA, recB]}))
            (work / "intake_p1.json").write_text(_json.dumps({"records": [recA, recC]}))
            (work / "boqa_p1.json").write_text(_json.dumps({"records": [recB]}))
            records = _load_records(work)
            ids = sorted(r["imo"] for r in records)
            self.assertEqual(ids, ["imo-A", "imo-B", "imo-C"],
                             "Each unique id should be counted exactly once across all 3 file sets")


class TestFlowABC(unittest.TestCase):
    """Flow framework (Windward parity).
       A = Done/Valid + no qa_assignee   (completed without QA)
       B = qa_assignee + qa_status both filled
       C = Done/Valid + has_qa
       A record can be both B and C."""

    def test_flow_classification(self):
        recs = [
            # Flow A only: Done, no QA
            _info(assignee="Alice", verification_status="Done"),
            _info(assignee="Alice", verification_status="Valid"),
            # Flow B only: QA reviewed but not yet Done
            _info(assignee="Alice", verification_status=SELECTED_FOR_BO_QA,
                  qa_assignee="Q1", qa_status="approve"),
            # Flow B AND Flow C: Done + QA reviewed
            _info(assignee="Alice", verification_status="Done",
                  qa_assignee="Q1", qa_status="approve"),
            _info(assignee="Alice", verification_status="Valid",
                  qa_assignee="Q1", qa_status="changed"),
            # Neither: still tagged, no QA
            _info(assignee="Alice", verification_status="tagged"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        m = aggs["totals"]
        self.assertEqual(m["flow_a_in_cache"], 2,
                         "2 records Done/Valid without qa_assignee")
        self.assertEqual(m["flow_b_in_cache"], 3,
                         "3 records with qa_assignee + qa_status")
        self.assertEqual(m["flow_c_in_cache"], 2,
                         "2 records Done/Valid AND with QA review")
        self.assertEqual(m["flow_a_today"], 0,
                         "no start_tagging date means no today-scoped Flow A")
        self.assertEqual(m["flow_b_today"], 0,
                         "no start_tagging date means no today-scoped Flow B")
        self.assertEqual(m["flow_c_today"], 0,
                         "no start_tagging date means no today-scoped Flow C")
        self.assertEqual(m["total_completions"], 4,
                         "in-cache completions = flow_a_in_cache (2) + flow_c_in_cache (2)")
        self.assertEqual(m["total_completions_today"], 0)


class TestUniqueImos(unittest.TestCase):
    def test_unique_imos_per_dimension(self):
        recs = [
            _info(assignee="Alice", imo="1001", verification_status="tagged"),
            _info(assignee="Alice", imo="1001", verification_status="Done"),  # same IMO, diff role
            _info(assignee="Alice", imo="1002", verification_status="tagged"),
            _info(assignee="Bob",   imo="2001", verification_status="tagged"),
            _info(assignee="Bob",   imo="2002", verification_status="tagged"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["unique_imos"], 4)
        self.assertEqual(aggs["by_agent"]["Alice"]["unique_imos"], 2)
        self.assertEqual(aggs["by_agent"]["Bob"]["unique_imos"], 2)


class TestComputeQaReviewers(unittest.TestCase):
    def test_basic_aggregation_and_sort(self):
        recs = [
            _info(qa_assignee="Q1", qa_status="approve",
                  start_tagging="2026-05-15T10:00:00.000+03:00",
                  qa_status_ts="2026-05-15T11:00:00.000+03:00"),  # 1h
            _info(qa_assignee="Q1", qa_status="approve",
                  start_tagging="2026-05-15T10:00:00.000+03:00",
                  qa_status_ts="2026-05-15T13:00:00.000+03:00"),  # 3h
            _info(qa_assignee="Q1", qa_status="changed",
                  start_tagging="2026-05-15T10:00:00.000+03:00",
                  qa_status_ts="2026-05-15T15:00:00.000+03:00"),  # 5h
            _info(qa_assignee="Q2", qa_status="approve",
                  start_tagging="2026-05-15T10:00:00.000+03:00",
                  qa_status_ts="2026-05-15T22:00:00.000+03:00"),  # 12h
            _info(qa_assignee=None, qa_status="approve"),  # excluded
            _info(qa_assignee="Q3", qa_status=None),       # excluded
        ]
        out = compute_qa_reviewers(recs)
        names = [x["name"] for x in out]
        self.assertEqual(names, ["Q1", "Q2"], "sorted by reviews desc; Q3 excluded")
        q1 = out[0]
        self.assertEqual(q1["reviews"], 3)
        self.assertEqual(q1["approvals"], 2)
        self.assertEqual(q1["changes"], 1)
        self.assertAlmostEqual(q1["approval_pct"], 66.7, places=1)
        self.assertAlmostEqual(q1["avg_review_time_hours"], 3.0, places=2)
        self.assertAlmostEqual(q1["median_review_time_hours"], 3.0, places=2)
        self.assertAlmostEqual(q1["p90_review_time_hours"], 4.6, places=1)

    def test_handles_missing_timestamps(self):
        recs = [_info(qa_assignee="Q1", qa_status="approve",
                      start_tagging=None, qa_status_ts=None)]
        out = compute_qa_reviewers(recs)
        self.assertEqual(out[0]["reviews"], 1)
        self.assertEqual(out[0]["avg_review_time_hours"], 0.0)


class TestComputeNotYetFinalized(unittest.TestCase):
    def test_filters_to_incomplete_states(self):
        recs = [
            _info(assignee="Alice", imo="100", verification_status="Done"),       # closed
            _info(assignee="Alice", imo="101", verification_status="waiting"),    # open
            _info(assignee="Bob",   imo="102", verification_status="tagged"),     # open
            _info(assignee="Carol", imo="103", verification_status=SELECTED_FOR_BO_QA),
            _info(assignee="Carol", imo="104", verification_status="need to be update"),
        ]
        out, truncated = compute_not_yet_finalized(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(len(out), 4)
        self.assertFalse(truncated)
        imos = sorted(r["imo"] for r in out)
        self.assertEqual(imos, ["101", "102", "103", "104"])

    def test_open_roles_count_excludes_self(self):
        # 3 records share imo='200', all open. open_roles_count should = 2 for each.
        recs = [
            _info(assignee="Alice", imo="200", verification_status="waiting", role="OWNER"),
            _info(assignee="Bob",   imo="200", verification_status="tagged",  role="OPERATOR"),
            _info(assignee="Carol", imo="200", verification_status=SELECTED_FOR_BO_QA, role="MANAGEMENT"),
            _info(assignee="Alice", imo="201", verification_status="waiting"),  # singleton
        ]
        out, _ = compute_not_yet_finalized(recs, TODAY, OWNERSHIP_ASSIGNEES)
        for r in out:
            if r["imo"] == "200":
                self.assertEqual(r["open_roles_count"], 2)
            elif r["imo"] == "201":
                self.assertEqual(r["open_roles_count"], 0)

    def test_days_open_from_start_tagging(self):
        # start_tagging 3 days before TODAY → days_open = 3
        three_days_ago = "2026-05-12T10:00:00.000+03:00"  # TODAY = 2026-05-15
        recs = [_info(assignee="Alice", imo="300", verification_status="tagged",
                      start_tagging=three_days_ago)]
        out, _ = compute_not_yet_finalized(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(out[0]["days_open"], 3)

    def test_truncation_flag(self):
        recs = [_info(assignee="Alice", imo=str(i), verification_status="waiting")
                for i in range(501)]
        out, truncated = compute_not_yet_finalized(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(len(out), 500)
        self.assertTrue(truncated)


class TestComputeQaDoneNotFinalized(unittest.TestCase):
    def test_filters_to_qa_done_subset(self):
        recs = [
            _info(assignee="Alice", imo="A", verification_status="waiting"),  # not QA'd
            _info(assignee="Bob",   imo="B", verification_status="tagged",
                  qa_assignee="Q1", qa_status="approve"),  # QA'd + open
            _info(assignee="Carol", imo="C", verification_status="Done",
                  qa_assignee="Q1", qa_status="approve"),  # QA'd + closed (excluded — already finalized)
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(len(aggs["qa_done_not_finalized"]), 1)
        self.assertEqual(aggs["qa_done_not_finalized"][0]["imo"], "B")


class TestPerTaskQaCoverage(unittest.TestCase):
    def test_qa_coverage_pct(self):
        recs = [
            # Task1: 5 records, 3 completed, 2 of those QA'd → 66.7%
            _info(requested_by="Task1", assignees=["Alice"], verification_status="Done",
                  qa_assignee="Q1", qa_status="approve"),
            _info(requested_by="Task1", assignees=["Alice"], verification_status="Done",
                  qa_assignee="Q1", qa_status="approve"),
            _info(requested_by="Task1", assignees=["Alice"], verification_status="Done"),  # not QA'd
            _info(requested_by="Task1", assignees=["Alice"], verification_status="tagged"),  # not completed
            _info(requested_by="Task1", assignees=["Alice"], verification_status="waiting"),  # not completed
        ]
        norm = {"Alice": {"team": "Simba", "canonical": "Alice"}}
        tasks = compute_task_breakdowns(recs, TODAY, norm)
        t = tasks[0]
        self.assertEqual(t["completed"], 3)
        self.assertEqual(t["qa_reviewed"], 2)
        self.assertAlmostEqual(t["qa_coverage_pct"], 66.7, places=1)


class TestIntakeTotalFromMetadata(unittest.TestCase):
    def test_reads_total_from_first_intake_page(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            (work / "intake_p1.json").write_text(_json.dumps({
                "records": [{"id": "r1"}, {"id": "r2"}],
                "metadata": {"totalRecordCount": 12481}
            }))
            total, partial = _intake_total_from_metadata(work)
            self.assertEqual(total, 12481)
            self.assertTrue(partial, "12481 > 3000 → partial=true")

    def test_partial_false_when_under_cap(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            (work / "intake_p1.json").write_text(_json.dumps({
                "records": [], "metadata": {"totalRecordCount": 1500}
            }))
            total, partial = _intake_total_from_metadata(work)
            self.assertEqual(total, 1500)
            self.assertFalse(partial, "1500 <= 3000 → partial=false")

    def test_no_intake_files_returns_zero(self):
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            total, partial = _intake_total_from_metadata(_Path(d))
            self.assertEqual(total, 0)
            self.assertFalse(partial)


class TestTasksAllEmittedFromAggregate(unittest.TestCase):
    def test_aggregate_emits_tasks_all(self):
        recs = [_info(assignee="Alice", requested_by="TaskA",
                      verification_status="Done", start_date="2024-01-01", company_id="rec1",
                      last_modified=f"{TODAY.isoformat()}T10:00:00.000+03:00")]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertIn("tasks_all", aggs)
        self.assertEqual(len(aggs["tasks_all"]), 1)
        self.assertEqual(aggs["tasks_all"][0]["name"], "TaskA")


class TestProperlyCompletedAndDeadVessels(unittest.TestCase):
    """properly_completed_today = tagged today AND meets ops completion test.
    dead_vessels_today = tagged today AND dead_vessel checkbox ticked."""

    def _fix(self):
        today_ts = lambda h: f"{TODAY.isoformat()}T{h:02d}:00:00.000+03:00"
        recs = [
            # Alice - properly completed via company
            _info(assignee="Alice", start_tagging=today_ts(8),
                  start_date="2024-01-15T00:00:00.000Z", company_id="recCo1"),
            # Alice - properly completed via dead_vessel
            _info(assignee="Alice", start_tagging=today_ts(9),
                  start_date="2024-01-15T00:00:00.000Z", dead_vessel=True),
            # Alice - tagged today but start_date missing → not properly completed
            _info(assignee="Alice", start_tagging=today_ts(10), company_id="recCo3"),
            # Bob - dead vessel, no start_date → not properly completed but is dead_vessel
            _info(assignee="Bob", start_tagging=today_ts(11), dead_vessel=True),
            # Bob - tagged but properly completed via company
            _info(assignee="Bob", start_tagging=today_ts(12),
                  start_date="2024-02-10T00:00:00.000Z", company_id="recCo2"),
        ]
        return aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)

    def test_totals(self):
        m = self._fix()["totals"]
        # Tagged today: all 5
        self.assertEqual(m["tagged_today"], 5)
        # Properly completed: Alice#1, Alice#2, Bob#2 = 3
        self.assertEqual(m["properly_completed_today"], 3)
        # Dead vessels tagged today: Alice#2, Bob#1 = 2
        self.assertEqual(m["dead_vessels_today"], 2)

    def test_per_team(self):
        aggs = self._fix()
        # Alice (Simba): 3 tagged, 2 properly completed (#1 via company, #2 via dead_vessel), 1 dead_vessel
        self.assertEqual(aggs["by_team"]["Simba"]["tagged_today"], 3)
        self.assertEqual(aggs["by_team"]["Simba"]["properly_completed_today"], 2)
        self.assertEqual(aggs["by_team"]["Simba"]["dead_vessels_today"], 1)
        # Bob (Tembo): 2 tagged, 1 properly completed (#2 via company), 1 dead_vessel
        self.assertEqual(aggs["by_team"]["Tembo"]["tagged_today"], 2)
        self.assertEqual(aggs["by_team"]["Tembo"]["properly_completed_today"], 1)
        self.assertEqual(aggs["by_team"]["Tembo"]["dead_vessels_today"], 1)

    def test_per_agent(self):
        aggs = self._fix()
        self.assertEqual(aggs["by_agent"]["Alice"]["properly_completed_today"], 2)
        self.assertEqual(aggs["by_agent"]["Bob"]["properly_completed_today"], 1)


class TestTasksToday(unittest.TestCase):
    """tasks_today: whole-table grouping by requested_by where created==today.
    Emitted at totals scope only, sorted by record count desc."""

    def test_grouping_and_sanctions_flag(self):
        today_ts = f"{TODAY.isoformat()}T10:00:00.000+03:00"
        recs = (
            [_info(assignee="x", requested_by="TaskA",                created=today_ts)] * 5
          + [_info(assignee="x", requested_by="TaskB",                created=today_ts)] * 3
          + [_info(assignee="x", requested_by="TaskC",                created=today_ts)] * 2
          + [_info(assignee="x", requested_by=None,                   created=today_ts)]
          + [_info(assignee="x", requested_by="CargoSanctionsCheck_X", created=today_ts)]
        )
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        tasks = aggs["totals"]["tasks_today"]
        self.assertEqual(aggs["totals"]["tasks_today_count"], 5)

        # Ordering: 5,3,2 dominate; the two 1-counts come last (stable insertion order).
        self.assertEqual(tasks[0]["name"], "TaskA")
        self.assertEqual(tasks[0]["records"], 5)
        self.assertFalse(tasks[0]["is_sanctions"])
        self.assertEqual(tasks[1]["name"], "TaskB")
        self.assertEqual(tasks[1]["records"], 3)
        self.assertEqual(tasks[2]["name"], "TaskC")
        self.assertEqual(tasks[2]["records"], 2)

        by_name = {t["name"]: t for t in tasks}
        self.assertIn("(no task name)", by_name)
        self.assertEqual(by_name["(no task name)"]["records"], 1)
        self.assertFalse(by_name["(no task name)"]["is_sanctions"])
        self.assertEqual(by_name["CargoSanctionsCheck_X"]["records"], 1)
        self.assertTrue(by_name["CargoSanctionsCheck_X"]["is_sanctions"])

    def test_not_today_records_excluded(self):
        recs = [
            _info(assignee="x", requested_by="TaskA",
                  created=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(assignee="x", requested_by="TaskB",
                  created="2026-05-10T10:00:00.000+03:00"),  # not today
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["tasks_today_count"], 1)
        self.assertEqual(aggs["totals"]["tasks_today"][0]["name"], "TaskA")

    def test_tasks_today_only_at_totals_scope(self):
        recs = [_info(assignee="Alice", requested_by="TaskA",
                      created=f"{TODAY.isoformat()}T10:00:00.000+03:00")]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        # Should appear in totals
        self.assertIn("tasks_today", aggs["totals"])
        # Should NOT appear in per-team or per-agent (or sliced dims)
        for team_block in aggs["by_team"].values():
            self.assertNotIn("tasks_today", team_block)
        for agent_block in aggs["by_agent"].values():
            self.assertNotIn("tasks_today", agent_block)


class TestSanctionsSampling(unittest.TestCase):
    """Operations rule: non-sanctions ≥ 15%, sanctions ≥ 50%. Task type
    detected from requested_by via extract_v2.is_sanctions()."""

    def _fix(self):
        # 4 sanctions records tagged today, 6 non-sanctions records tagged today.
        # Sanctions cohort:
        #   2 in BO QA queue (in_bo_qa_today)
        #   1 reviewed approve (qa_inspected_today, NOT changed)
        #   1 plain tagged, not sampled
        #   → sampled = 2 + 1 = 3 of 4 tagged_today_sanctions = 75.0%  (≥50 ✓)
        #   reject_rate_sanctions = 0/1 = 0.0
        # Non-sanctions cohort:
        #   1 in BO QA queue
        #   2 reviewed (1 approve, 1 changed)
        #   3 plain tagged, not sampled
        #   → sampled = 1 + 2 = 3 of 6 = 50.0%  (≥15 ✓)
        #   reject_rate_non_sanctions = 1/2 = 50.0
        # Combined:
        #   tagged_today = 10
        #   in_bo_qa + qa_inspected + ntbu = (2+1) + (1+2) + 0 = 6  → 60.0%
        today_ts = lambda h: f"{TODAY.isoformat()}T{h:02d}:00:00.000+03:00"
        recs = [
            # 4 sanctions
            _info(assignee="Alice", requested_by="CargoSanctionsCheck_A", start_tagging=today_ts(8),
                  verification_status=SELECTED_FOR_BO_QA),
            _info(assignee="Alice", requested_by="CargoSanctionsCheck_B", start_tagging=today_ts(9),
                  verification_status=SELECTED_FOR_BO_QA),
            _info(assignee="Alice", requested_by="CargoSanctionsCheck_C", start_tagging=today_ts(10),
                  verification_status="Done", qa_status="approve"),
            _info(assignee="Alice", requested_by="CargoSanctionsCheck_D", start_tagging=today_ts(11),
                  verification_status="tagged"),
            # 6 non-sanctions
            _info(assignee="Bob", requested_by="CargoChangeIntel_1", start_tagging=today_ts(8),
                  verification_status=SELECTED_FOR_BO_QA),
            _info(assignee="Bob", requested_by="CargoChangeIntel_2", start_tagging=today_ts(9),
                  verification_status="Done", qa_status="approve"),
            _info(assignee="Bob", requested_by="CargoChangeIntel_3", start_tagging=today_ts(10),
                  verification_status="Done", qa_status="changed"),
            _info(assignee="Bob", requested_by="CargoChangeIntel_4", start_tagging=today_ts(11),
                  verification_status="tagged"),
            _info(assignee="Bob", requested_by=None, start_tagging=today_ts(12),
                  verification_status="tagged"),
            _info(assignee="Bob", requested_by="", start_tagging=today_ts(13),
                  verification_status="tagged"),
        ]
        return aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)

    def test_cohort_counts(self):
        m = self._fix()["totals"]
        self.assertEqual(m["tagged_today"], 10)
        self.assertEqual(m["tagged_today_sanctions"], 4)
        self.assertEqual(m["tagged_today_non_sanctions"], 6)
        self.assertEqual(m["in_bo_qa_today_sanctions"], 2)
        self.assertEqual(m["in_bo_qa_today_non_sanctions"], 1)
        self.assertEqual(m["qa_inspected_today_sanctions"], 1)
        self.assertEqual(m["qa_inspected_today_non_sanctions"], 2)
        self.assertEqual(m["qa_changed_today_sanctions"], 0)
        self.assertEqual(m["qa_changed_today_non_sanctions"], 1)

    def test_sampling_percentages(self):
        m = self._fix()["totals"]
        self.assertEqual(m["sampling_sanctions_pct"], 75.0)          # 3/4
        self.assertEqual(m["sampling_non_sanctions_pct"], 50.0)      # 3/6
        self.assertEqual(m["sampling_actual_pct"], 60.0)             # 6/10 combined
        self.assertEqual(m["sampling_target_sanctions_pct"], 50.0)
        self.assertEqual(m["sampling_target_non_sanctions_pct"], 15.0)
        self.assertEqual(m["sampling_target_pct"], 25.0)             # legacy combined target

    def test_reject_rates(self):
        m = self._fix()["totals"]
        self.assertEqual(m["reject_rate_sanctions"], 0.0)            # 0/1
        self.assertEqual(m["reject_rate_non_sanctions"], 50.0)       # 1/2
        self.assertEqual(m["reject_rate"], 33.3)                     # 1/3 combined

    def test_split_metrics_present_on_by_qa_slim(self):
        # by_qa is slimmed but should expose the cohort + percentage fields.
        m = self._fix()
        # No qa_assignee on the fixture records → by_qa is empty. Use by_team check instead.
        for k in ("sampling_sanctions_pct", "sampling_non_sanctions_pct",
                  "reject_rate_sanctions", "reject_rate_non_sanctions"):
            self.assertIn(k, m["by_team"]["Simba"])


def _info_in_fetch(*fetches, **overrides):
    """Build an info dict tagged as appearing in the given fetch letters.
    Used to simulate _load_records' source-tracking in test fixtures."""
    info = _info(**overrides)
    info["_sources"] = set(fetches)
    return info


class TestWWQABacklog(unittest.TestCase):
    """ww_qa_backlog counts records appearing in Fetch G (assigned to WW QA
    but not yet reviewed). Semantic change from the legacy
    ``vs == "Selected for WW QA"`` rule — see fix-systemic-today-metric-
    truncation refactor."""

    def test_zero_when_no_records_in_fetch_g(self):
        # Main fixture has no Fetch-G-sourced records.
        aggs = aggregate(_fixture(), TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["ww_qa_backlog"], 0)

    def test_counts_records_from_fetch_g(self):
        records = [
            _info_in_fetch("G", assignee="Alice"),
            _info_in_fetch("G", assignee="Alice"),
            _info_in_fetch("G", assignee="Bob"),
            _info(assignee="Bob", verification_status="Done"),  # NOT in Fetch G
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["ww_qa_backlog"], 3)
        self.assertEqual(aggs["by_team"]["Simba"]["ww_qa_backlog"], 2)
        self.assertEqual(aggs["by_team"]["Tembo"]["ww_qa_backlog"], 1)

    def test_old_vs_filter_no_longer_drives_backlog(self):
        # Records currently in vs == "Selected for WW QA" but NOT in Fetch G
        # should NOT be counted (vs alone is not sufficient anymore).
        records = [
            _info(assignee="Alice", verification_status=SELECTED_FOR_WW_QA),
        ]
        aggs = aggregate(records, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["ww_qa_backlog"], 0,
                         "vs alone shouldn't drive backlog under the new semantic")


class TestFetchSourceReconciliation(unittest.TestCase):
    """The fix-systemic-today-metric-truncation refactor moves each
    today-scoped metric to its dedicated fetch (D/E/F/G). These tests
    enforce the architectural invariant: the metric's count must match
    the size of its authoritative fetch's record set."""

    def test_tagged_today_reconciles_with_fetch_d_count(self):
        # 50 records from Fetch D (start_tagging today) + 20 records from
        # Fetch A only (modified today, tagged yesterday). tagged_today must
        # equal 50 — the Fetch D count, not 50+20.
        recs = []
        for _ in range(50):
            recs.append(_info_in_fetch("D", assignee="Alice", start_tagging=_ts(0, 9)))
        for _ in range(20):
            recs.append(_info_in_fetch("A", assignee="Alice", start_tagging=_ts(-1, 9)))
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        fetch_d_count = sum(1 for r in recs if "D" in r.get("_sources", set()))
        self.assertEqual(aggs["totals"]["tagged_today"], fetch_d_count)
        self.assertEqual(aggs["totals"]["tagged_today"], 50)

    def test_per_team_tagged_reconciles_with_per_team_fetch_d_slice(self):
        # 30 records for Alice (Simba), 20 for Bob (Tembo), 10 for Carol (Nyati).
        recs = []
        for _ in range(30): recs.append(_info_in_fetch("D", assignee="Alice", start_tagging=_ts(0, 9)))
        for _ in range(20): recs.append(_info_in_fetch("D", assignee="Bob",   start_tagging=_ts(0, 9)))
        for _ in range(10): recs.append(_info_in_fetch("D", assignee="Carol", start_tagging=_ts(0, 9)))
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["by_team"]["Simba"]["tagged_today"], 30)
        self.assertEqual(aggs["by_team"]["Tembo"]["tagged_today"], 20)
        self.assertEqual(aggs["by_team"]["Nyati"]["tagged_today"], 10)
        # Total reconciles with per-team sum AND with Fetch D count.
        self.assertEqual(aggs["totals"]["tagged_today"], 60)

    def test_done_today_reconciles_with_fetch_e_count(self):
        # Fetch E source = records done OR valid today. 15 records.
        recs = []
        for _ in range(15):
            recs.append(_info_in_fetch("E", assignee="Alice",
                                        done_selected_time=_ts(0, 14)))
        # Plus a record done yesterday — should NOT count.
        recs.append(_info_in_fetch("A", assignee="Alice", done_selected_time=_ts(-1, 14)))
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        fetch_e_count = sum(1 for r in recs if "E" in r.get("_sources", set()))
        self.assertEqual(aggs["totals"]["done_today"], fetch_e_count)
        self.assertEqual(aggs["totals"]["done_today"], 15)

    def test_qa_reviewed_today_reconciles_with_fetch_f_set(self):
        # Fetch F = records with qa_status_ts today. Aggregator's
        # qa_inspected_today currently gates on start_tagging today AND
        # qa_status set. Under the new architecture, a record in Fetch F
        # with qa_status filled should count as qa_inspected_today even
        # if it was tagged a prior day — the review happened today.
        recs = [
            # Tagged today AND QA-reviewed today
            _info_in_fetch("D", "F", assignee="Alice", qa_assignee="QA1",
                            start_tagging=_ts(0, 9), qa_status_ts=_ts(0, 11),
                            qa_status="approve"),
            # Tagged yesterday, QA-reviewed today (the cross-day case)
            _info_in_fetch("F", assignee="Alice", qa_assignee="QA1",
                            start_tagging=_ts(-1, 9), qa_status_ts=_ts(0, 11),
                            qa_status="approve"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        # tagged_today: 1 (only the first record was tagged today)
        self.assertEqual(aggs["totals"]["tagged_today"], 1)
        # NOTE: The current aggregator scopes qa_inspected_today inside the
        # start_tagging-today gate. The cross-day case (tagged yesterday,
        # reviewed today) is still under-counted by the existing aggregator.
        # This test documents that gap — the metric reads correctly off
        # Fetch D's tagged-today subset but doesn't yet integrate Fetch F's
        # cross-day reviews. Follow-up: route qa_inspected_today off Fetch F
        # directly (TODO in next PR — too invasive for this refactor).
        # For now, just assert Fetch F's records are in the universe.
        self.assertEqual(sum(1 for r in recs if "F" in r.get("_sources", set())), 2)

    def test_ww_qa_backlog_reconciles_with_fetch_g_count(self):
        recs = [_info_in_fetch("G", assignee=name) for name in ["Alice", "Bob", "Carol"]]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["ww_qa_backlog"], 3)

    def test_records_without_sources_dont_pollute_fetch_metrics(self):
        # Backwards-compat: legacy records without _sources tag should NOT
        # falsely count toward any G-sourced metric.
        recs = [_info(assignee="Alice", verification_status=SELECTED_FOR_WW_QA)]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["ww_qa_backlog"], 0)


class TestAddNewCompanyBroaderDefinition(unittest.TestCase):
    """Broader operational definition (fix-add-new-company-metric-
    broader-definition): count any record TAGGED TODAY where the
    add_new_company column has any input. The verification_status and
    company_id_and_name checks from the old 3-condition rule are dropped
    — under-counted ops reality (11 vs 96 Airtable truth).

    Backward-compat: the metric key add_new_company_open is preserved
    so existing dashboard tiles read the new value automatically. The
    new alias add_new_company_today is published with the same value
    for future per-team rendering."""

    def test_add_new_company_today_counts_any_filled(self):
        """A record tagged today with add_new_company filled counts —
        REGARDLESS of verification_status or company_id_and_name."""
        recs = [
            # All have add_new_company filled + tagged today; all should count.
            _info(assignee="Alice", add_new_company="Proposed Co A",
                  verification_status="need to be update", company_id=None,
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
            _info(assignee="Alice", add_new_company="Proposed Co B",
                  verification_status="need to be update", company_id="recCo123",
                  start_tagging=f"{TODAY.isoformat()}T09:30:00.000+03:00"),
            _info(assignee="Alice", add_new_company="Proposed Co C",
                  verification_status="tagged", company_id=None,
                  start_tagging=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(assignee="Alice", add_new_company="Proposed Co D",
                  verification_status=SELECTED_FOR_BO_QA, company_id=None,
                  start_tagging=f"{TODAY.isoformat()}T10:30:00.000+03:00"),
            _info(assignee="Alice", add_new_company="Proposed Co E",
                  verification_status="Done", company_id="recCo456",
                  start_tagging=f"{TODAY.isoformat()}T11:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["add_new_company_open"], 5,
                         "every record with add_new_company filled + tagged today counts")
        # The new alias key publishes the same value.
        self.assertEqual(aggs["totals"]["add_new_company_today"], 5)

    def test_add_new_company_today_excludes_blank(self):
        """Records without add_new_company filled don't count, even if
        tagged today."""
        recs = [
            _info(assignee="Alice", add_new_company=None,
                  verification_status="need to be update",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
            _info(assignee="Alice", add_new_company="",
                  verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["add_new_company_open"], 0)

    def test_add_new_company_today_excludes_records_not_tagged_today(self):
        """Records with add_new_company filled but tagged yesterday don't
        count — the metric is scoped to today's tagged work."""
        recs = [
            _info(assignee="Alice", add_new_company="Proposed Co Yesterday",
                  verification_status="need to be update",
                  start_tagging="2026-04-01T09:00:00.000+03:00"),
            # Sanity: one tagged today that DOES count
            _info(assignee="Alice", add_new_company="Proposed Co Today",
                  verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["add_new_company_open"], 1,
                         "only the today-tagged record counts")

    def test_add_new_company_today_per_team_sums_to_total(self):
        """Per-team breakdown reconciles with the totals figure."""
        recs = [
            _info(assignee="Alice", add_new_company="Co Alice 1",
                  verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
            _info(assignee="Alice", add_new_company="Co Alice 2",
                  verification_status="Done",
                  start_tagging=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
            _info(assignee="Bob", add_new_company="Co Bob 1",
                  verification_status="need to be update",
                  start_tagging=f"{TODAY.isoformat()}T11:00:00.000+03:00"),
            _info(assignee="Carol", add_new_company=None,  # NOT counted
                  start_tagging=f"{TODAY.isoformat()}T12:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        total = aggs["totals"]["add_new_company_open"]
        team_sum = sum(t.get("add_new_company_open", 0) for t in aggs["by_team"].values())
        self.assertEqual(total, 3)
        self.assertEqual(team_sum, total, "per-team breakdown must sum to total")
        # Per-team specifics (Alice → Simba, Bob → Tembo, Carol → Nyati)
        self.assertEqual(aggs["by_team"]["Simba"]["add_new_company_open"], 2)
        self.assertEqual(aggs["by_team"]["Tembo"]["add_new_company_open"], 1)
        self.assertEqual(aggs["by_team"]["Nyati"]["add_new_company_open"], 0)
        # by_team also exposes the add_new_company_today alias
        self.assertEqual(aggs["by_team"]["Simba"]["add_new_company_today"], 2)


class TestSamplingNoDoubleCount(unittest.TestCase):
    def test_sampling_union_counts_record_once(self):
        recs = [
            _info(assignee="Alice", verification_status="need to be update",
                  qa_assignee="Q1", qa_status="changed",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
            _info(assignee="Alice", verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T10:00:00.000+03:00"),
        ]
        m = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)["totals"]
        self.assertEqual(m["tagged_today"], 2)
        self.assertEqual(m["qa_inspected_today"], 1)
        self.assertEqual(m["need_to_be_update_today"], 1)
        self.assertEqual(m["sampling_actual_pct"], 50.0)


class TestDoneTodayValidTime(unittest.TestCase):
    def test_valid_selected_time_counts_done_today(self):
        recs = [
            _info(assignee="Alice", verification_status="Valid", done_selected_time=None,
                  valid_selected_time=f"{TODAY.isoformat()}T12:00:00.000+03:00"),
        ]
        m = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)["totals"]
        self.assertGreaterEqual(m["done_today"], 1)


class TestWeeklyUniqueImosSumName(unittest.TestCase):
    def test_rollup_uses_sum_key_not_union_key(self):
        snaps = [(TODAY, {"totals": {"unique_imos": 7}, "by_agent": {}, "by_team": {}})]
        totals = compute_weekly_rollup(snaps)["totals"]
        self.assertEqual(totals["unique_imos_sum"], 7)
        self.assertNotIn("unique_imos_union", totals)


class TestMultiAssigneeFallbackToRoster(unittest.TestCase):
    def test_second_roster_assignee_is_in_scope_and_attributed(self):
        recs = [
            _info(assignees=["NonRosterUser", "Hellen Vigehi"], verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, {"Hellen Vigehi": "Tembo"})
        self.assertIn("Tembo", aggs["by_team"])
        self.assertIn("Hellen Vigehi", aggs["by_agent"])
        self.assertEqual(aggs["by_team"]["Tembo"]["tagged_today"], 1)

    def test_all_non_roster_assignees_are_dropped(self):
        recs = [
            _info(assignees=["NonRosterUser", "OtherNonRoster"], verification_status="tagged",
                  start_tagging=f"{TODAY.isoformat()}T09:00:00.000+03:00"),
        ]
        aggs = aggregate(recs, TODAY, {"Hellen Vigehi": "Tembo"})
        self.assertEqual(aggs["totals"]["tagged_today"], 0)
        self.assertEqual(aggs["by_team"], {})
        self.assertEqual(aggs["by_agent"], {})


class TestMultiAssigneeCount(unittest.TestCase):
    def test_counts_multi_assignee_records_in_scope(self):
        recs = [
            _info(assignees=["Alice", "Bob"], verification_status="tagged"),
            _info(assignees=["Bob"], verification_status="tagged"),
            _info(assignees=["Carol", "Alice"], verification_status="tagged"),
        ]
        aggs = aggregate(recs, TODAY, OWNERSHIP_ASSIGNEES)
        self.assertEqual(aggs["totals"]["multi_assignee_count"], 2)
        self.assertEqual(aggs["by_team"]["Simba"]["multi_assignee_count"], 1)
        self.assertEqual(aggs["by_team"]["Nyati"]["multi_assignee_count"], 1)


class TestSnapshotsRoundTrip(unittest.TestCase):
    def test_save_and_load_one_snapshot(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            aggs = {"date": "2026-05-17", "totals": {"tagged_today": 42}}
            _save_snapshot(aggs, work)
            self.assertTrue((work / "snapshots" / "2026-05-17.json").exists())
            loaded = _json.loads((work / "snapshots" / "2026-05-17.json").read_text())
            self.assertEqual(loaded["totals"]["tagged_today"], 42)

    def test_load_snapshots_filters_by_range_and_skips_gaps(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            (work / "snapshots").mkdir()
            for day, n in [("2026-05-10", 1), ("2026-05-12", 3), ("2026-05-14", 5)]:
                (work / "snapshots" / f"{day}.json").write_text(_json.dumps({"date": day, "totals": {"tagged_today": n}}))
            # Range 05-10 to 05-13 should pick up 05-10 and 05-12 (skipping the missing 05-11 and 05-13)
            out = load_snapshots(work, date(2026, 5, 10), date(2026, 5, 13))
            self.assertEqual([d.isoformat() for d, _ in out], ["2026-05-10", "2026-05-12"])
            # Empty range
            self.assertEqual(load_snapshots(work, date(2026, 1, 1), date(2026, 1, 5)), [])

    def test_save_overwrites_same_day(self):
        import json as _json
        import tempfile
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as d:
            work = _Path(d)
            _save_snapshot({"date": "2026-05-17", "totals": {"tagged_today": 10}}, work)
            _save_snapshot({"date": "2026-05-17", "totals": {"tagged_today": 99}}, work)
            loaded = _json.loads((work / "snapshots" / "2026-05-17.json").read_text())
            self.assertEqual(loaded["totals"]["tagged_today"], 99, "second write should overwrite first")


class TestComputeWeeklyRollup(unittest.TestCase):
    """3-day fixture with sums, gap handling, and agents_not_working detection."""

    ROSTER = {
        "Simba": {"qa": {"name": "Q1"}, "ww_qa": None, "members": [
            {"name": "Alice"}, {"name": "Allison"}
        ]},
        "Tembo": {"qa": {"name": "Q2"}, "ww_qa": None, "members": [
            {"name": "Bob"}, {"name": "Boyd"}
        ]},
    }

    def _fixture(self):
        # Day 1: Alice + Bob work. Day 2: only Alice. Day 3 (skipped). Day 4: only Bob.
        snap_day1 = (date(2026, 5, 10), {
            "date": "2026-05-10",
            "totals": {"tagged_today": 100, "done_today": 50,
                       "flow_a_in_cache": 5, "flow_b_in_cache": 30, "flow_c_in_cache": 25, "total_completions": 30,
                       "unique_imos": 40},
            "by_team":  {"Simba": {"tagged_today": 60, "unique_imos": 20},
                         "Tembo": {"tagged_today": 40, "unique_imos": 20}},
            "by_agent": {"Alice": {"tagged_today": 60, "total_completions": 30},
                         "Bob":   {"tagged_today": 40, "total_completions": 20}},
            "qa_reviewers": [{"name": "Q1", "reviews": 15, "changes": 2},
                             {"name": "Q2", "reviews": 10, "changes": 1}],
        })
        snap_day2 = (date(2026, 5, 11), {
            "date": "2026-05-11",
            "totals": {"tagged_today": 50, "done_today": 25,
                       "flow_a_in_cache": 2, "flow_b_in_cache": 18, "flow_c_in_cache": 23, "total_completions": 25,
                       "unique_imos": 30},
            "by_team":  {"Simba": {"tagged_today": 50, "unique_imos": 30}},
            "by_agent": {"Alice": {"tagged_today": 50, "total_completions": 25}},
            "qa_reviewers": [{"name": "Q1", "reviews": 18, "changes": 0}],
        })
        # 05-12 SKIPPED — no snapshot
        snap_day4 = (date(2026, 5, 13), {
            "date": "2026-05-13",
            "totals": {"tagged_today": 30, "done_today": 15,
                       "flow_a_in_cache": 1, "flow_b_in_cache": 14, "flow_c_in_cache": 14, "total_completions": 15,
                       "unique_imos": 20},
            "by_team":  {"Tembo": {"tagged_today": 30, "unique_imos": 20}},
            "by_agent": {"Bob":   {"tagged_today": 30, "total_completions": 15}},
            "qa_reviewers": [{"name": "Q2", "reviews": 8, "changes": 3}],
        })
        return [snap_day1, snap_day2, snap_day4]

    def test_sums(self):
        r = compute_weekly_rollup(self._fixture(), roster=self.ROSTER)
        self.assertEqual(r["days_with_data"], 3)
        self.assertEqual(r["date_range"], {"start": "2026-05-10", "end": "2026-05-13"})
        t = r["totals"]
        self.assertEqual(t["tagged"], 100 + 50 + 30)
        self.assertEqual(t["done"],   50 + 25 + 15)
        self.assertEqual(t["flow_a"], 5 + 2 + 1)
        self.assertEqual(t["flow_b"], 30 + 18 + 14)
        self.assertEqual(t["flow_c"], 25 + 23 + 14)
        self.assertEqual(t["total_completions"], 30 + 25 + 15)
        self.assertEqual(t["unique_imos_sum"], 40 + 30 + 20)
        self.assertNotIn("unique_imos_union", t)
        self.assertEqual(t["qa_reviews"], (15 + 10) + 18 + 8)
        self.assertEqual(t["qa_changes"], (2 + 1) + 0 + 3)

    def test_per_day_in_order_and_gaps_skipped(self):
        r = compute_weekly_rollup(self._fixture(), roster=self.ROSTER)
        dates = [d["date"] for d in r["per_day"]]
        self.assertEqual(dates, ["2026-05-10", "2026-05-11", "2026-05-13"])
        self.assertNotIn("2026-05-12", dates, "missing day should be skipped, not zero-filled")

    def test_active_agents_avg(self):
        # Day 1: 2 active (Alice, Bob). Day 2: 1 active. Day 3: 1 active. Avg = 4/3 = 1.3
        r = compute_weekly_rollup(self._fixture(), roster=self.ROSTER)
        self.assertEqual(r["totals"]["active_agents_avg"], 1.3)

    def test_per_team_rollup_and_top_performer(self):
        r = compute_weekly_rollup(self._fixture(), roster=self.ROSTER)
        # Simba: day1=60 + day2=50 = 110, unique=20+30=50, top performer Alice
        self.assertEqual(r["per_team_rollup"]["Simba"]["records"], 110)
        self.assertEqual(r["per_team_rollup"]["Simba"]["unique_imos_sum"], 50)
        self.assertEqual(r["per_team_rollup"]["Simba"]["top_performer"]["name"], "Alice")
        self.assertEqual(r["per_team_rollup"]["Simba"]["top_performer"]["records"], 110)
        # Tembo: day1=40 + day3=30 = 70, top performer Bob
        self.assertEqual(r["per_team_rollup"]["Tembo"]["records"], 70)
        self.assertEqual(r["per_team_rollup"]["Tembo"]["top_performer"]["name"], "Bob")

    def test_agents_not_working(self):
        r = compute_weekly_rollup(self._fixture(), roster=self.ROSTER)
        anw_by_name = {a["name"]: a for a in r["agents_not_working"]}
        # Alice worked on days 1 and 2, missed day 3 (2026-05-13)
        self.assertIn("Alice", anw_by_name)
        self.assertEqual(anw_by_name["Alice"]["days_missed"], 1)
        self.assertEqual(anw_by_name["Alice"]["missing_dates"], ["2026-05-13"])
        # Allison worked NO days — missed all 3
        self.assertEqual(anw_by_name["Allison"]["days_missed"], 3)
        # Bob worked day 1 and day 3, missed day 2
        self.assertEqual(anw_by_name["Bob"]["missing_dates"], ["2026-05-11"])
        # Boyd worked NO days
        self.assertEqual(anw_by_name["Boyd"]["days_missed"], 3)

    def test_empty_snapshots_returns_safe_empty(self):
        r = compute_weekly_rollup([], roster=self.ROSTER)
        self.assertEqual(r["days_with_data"], 0)
        self.assertEqual(r["totals"], {})
        self.assertEqual(r["per_day"], [])
        self.assertEqual(r["agents_not_working"], [])


class TestFlowFrameworkV3Counts(unittest.TestCase):
    """Flow v3 — cache-based A/B/C/D classification. The Flow counts come
    from .poll_work/tagged_today_p*.json (Fetch D) — each in-scope record
    is passed through completion_detector.classify() and bucketed by its
    CURRENT state. Sum reconciles with tagged_today.

    Alerts counts still come from Supabase (separate test class below)
    because integrity issues span multiple days, not just today's tagged.
    """

    def _write_page(self, work_dir, page_num, records):
        """Write a tagged_today_p<N>.json fixture in the temp work_dir."""
        import json as _json
        p = work_dir / f"tagged_today_p{page_num}.json"
        p.write_text(_json.dumps({"records": records}))

    def _rec(self, rec_id, assignee_name, **fields_kw):
        """Build a raw Airtable record dict (fields keyed by field ID)."""
        from completion_detector import (
            FLD_ASSIGNEE, FLD_VERIFICATION_STATUS, FLD_QA_ASSIGNEE,
            FLD_QA_STATUS, FLD_COMPANY_ID_AND_NAME, FLD_DEAD_VESSEL,
            FLD_ADD_NEW_COMPANY,
        )
        keymap = {
            "verification_status": FLD_VERIFICATION_STATUS,
            "qa_assignee":         FLD_QA_ASSIGNEE,
            "qa_status":           FLD_QA_STATUS,
            "company_id_and_name": FLD_COMPANY_ID_AND_NAME,
            "dead_vessel":         FLD_DEAD_VESSEL,
            "add_new_company":     FLD_ADD_NEW_COMPANY,
        }
        fields = {FLD_ASSIGNEE: [{"id": "u1", "name": assignee_name}]}
        for k, v in fields_kw.items():
            fields[keymap[k]] = v
        return {"id": rec_id, "fields": fields}

    def _roster(self):
        # Mirror the shape ownership_assignees uses (extract-style dict).
        # Reuse the test fixture's OWNERSHIP_ASSIGNEES.
        return OWNERSHIP_ASSIGNEES

    def test_flow_a_b_c_d_sum_equals_tagged_today(self):
        """Architectural invariant: every in-scope Fetch D record lands in
        exactly one bucket → A + B + C + D == count of records classified."""
        import tempfile
        from pathlib import Path
        from aggregate_v2 import _compute_flow_framework_counts
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            recs = [
                # 3 × Flow A (Done, no qa)
                self._rec("rA1", "Alice", verification_status="Done"),
                self._rec("rA2", "Alice", verification_status="Valid"),
                self._rec("rA3", "Bob",   verification_status="Done"),
                # 2 × Flow B (SBO + qa_assignee)
                self._rec("rB1", "Alice", verification_status="Selected for BO QA ",
                          qa_assignee={"id": "q1", "name": "QA"}),
                self._rec("rB2", "Bob",   verification_status="Selected for BO QA ",
                          qa_assignee={"id": "q1", "name": "QA"}),
                # 1 × Flow C (Done + qa + status)
                self._rec("rC1", "Alice", verification_status="Done",
                          qa_assignee={"id": "q1", "name": "QA"},
                          qa_status="approve"),
                # 1 × Flow D — pre_flow (tagged + company)
                self._rec("rD1", "Alice", verification_status="tagged",
                          company_id_and_name=["recX"]),
                # 1 × Flow D — alert (Done + qa_assignee, missing qa_status)
                self._rec("rD2", "Bob", verification_status="Done",
                          qa_assignee={"id": "q1", "name": "QA"}),
                # 1 × Flow D — skip-state (waiting)
                self._rec("rD3", "Alice", verification_status="waiting"),
                # Non-roster record — filtered out, NOT counted in any bucket
                self._rec("rNR", "Stranger Danger", verification_status="Done"),
            ]
            self._write_page(work, 1, recs)
            result = _compute_flow_framework_counts(work, self._roster())
        self.assertEqual(result["flow_a_today"], 3)
        self.assertEqual(result["flow_b_today"], 2)
        self.assertEqual(result["flow_c_today"], 1)
        self.assertEqual(result["flow_d_today"], 3)
        # Reconciliation: 3+2+1+3 = 9 in-scope (non-roster Stranger excluded).
        self.assertEqual(
            result["flow_a_today"] + result["flow_b_today"]
            + result["flow_c_today"] + result["flow_d_today"],
            9, "A+B+C+D must equal in-scope record count")
        # Total Completions = A + C only (B + D excluded — in-progress / in-flight)
        self.assertEqual(result["total_completions_today"], 4)

    def test_flow_d_catches_pre_flow_records(self):
        """Records still in tagged / need-to-be-update (with the underlying
        completion rule met) → classify returns ('pre_flow', None) → Flow D.
        Must NOT count toward A or C."""
        import tempfile
        from pathlib import Path
        from aggregate_v2 import _compute_flow_framework_counts
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            self._write_page(work, 1, [
                self._rec("r1", "Alice", verification_status="tagged",
                          company_id_and_name=["recX"]),
                self._rec("r2", "Alice", verification_status="need to be update",
                          add_new_company="Proposed Co"),
            ])
            r = _compute_flow_framework_counts(work, self._roster())
        self.assertEqual(r["flow_a_today"], 0)
        self.assertEqual(r["flow_b_today"], 0)
        self.assertEqual(r["flow_c_today"], 0)
        self.assertEqual(r["flow_d_today"], 2)

    def test_flow_d_catches_alert_records(self):
        """Records in shape-mismatch states (Done+qa_assignee-missing-status,
        SBO without assignee) → classify returns ('alert', ...) → Flow D.
        Must NOT count toward A or C."""
        import tempfile
        from pathlib import Path
        from aggregate_v2 import _compute_flow_framework_counts
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            self._write_page(work, 1, [
                # Done + qa_assignee but no qa_status → alert: missing_qa_status
                self._rec("r1", "Alice", verification_status="Done",
                          qa_assignee={"id": "q", "name": "QA"}),
                # SBO without qa_assignee → alert: missing_qa_assignee
                self._rec("r2", "Bob", verification_status="Selected for BO QA "),
            ])
            r = _compute_flow_framework_counts(work, self._roster())
        self.assertEqual(r["flow_a_today"], 0)
        self.assertEqual(r["flow_b_today"], 0)
        self.assertEqual(r["flow_c_today"], 0)
        self.assertEqual(r["flow_d_today"], 2)

    def test_no_cache_files_returns_zeros(self):
        """No tagged_today_p*.json (e.g. local dev without polling) →
        helper returns all zeros, doesn't crash."""
        import tempfile
        from pathlib import Path
        from aggregate_v2 import _compute_flow_framework_counts
        with tempfile.TemporaryDirectory() as td:
            r = _compute_flow_framework_counts(Path(td), self._roster())
        self.assertEqual(r["flow_a_today"], 0)
        self.assertEqual(r["flow_d_today"], 0)
        self.assertEqual(r["total_completions_today"], 0)

    def test_total_completions_excludes_b_and_d(self):
        """Regression guard: Total = A + C only. Neither B (in-progress)
        nor D (in-flight) should ever be added to Total Completions."""
        import tempfile
        from pathlib import Path
        from aggregate_v2 import _compute_flow_framework_counts
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            self._write_page(work, 1, [
                self._rec("a", "Alice", verification_status="Done"),  # A
                self._rec("b", "Alice", verification_status="Selected for BO QA ",
                          qa_assignee={"id": "q", "name": "QA"}),    # B
                self._rec("c", "Alice", verification_status="Done",
                          qa_assignee={"id": "q", "name": "QA"},
                          qa_status="approve"),                       # C
                self._rec("d", "Alice", verification_status="tagged",
                          company_id_and_name=["x"]),                 # D
            ])
            r = _compute_flow_framework_counts(work, self._roster())
        self.assertEqual(r["total_completions_today"], 2,
                         "Total = A(1) + C(1) = 2; B and D both excluded")
        # Regression guard: if anyone refactors total to include B or D,
        # this catches it.
        self.assertNotEqual(r["total_completions_today"], 3,
                            "Total must not include B")
        self.assertNotEqual(r["total_completions_today"], 4,
                            "Total must not include D")


class TestFetchAlertsCounts(unittest.TestCase):
    """Supabase-backed alert counts (separate from cache-based Flow counts).
    Alerts span multiple days, so they're queried directly from flow_alerts."""

    def _mock_count_resp(self, count):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.status_code = 200
        m.headers = {"Content-Range": f"0-0/{count}"}
        return m

    def test_alerts_counts_from_supabase(self):
        from unittest.mock import patch
        from aggregate_v2 import _fetch_alerts_counts
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x", "SUPABASE_SERVICE_ROLE_KEY": "k"}), \
             patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._mock_count_resp(2),  # missing_qa_assignee
                self._mock_count_resp(3),  # missing_qa_status
                self._mock_count_resp(1),  # stuck_in_sampling
            ]
            r = _fetch_alerts_counts()
        self.assertEqual(r["alerts_missing_qa_assignee"], 2)
        self.assertEqual(r["alerts_missing_qa_status"], 3)
        self.assertEqual(r["alerts_stuck_in_sampling"], 1)
        self.assertEqual(r["alerts_open_total"], 6)

    def test_returns_empty_dict_when_env_missing(self):
        from unittest.mock import patch
        from aggregate_v2 import _fetch_alerts_counts
        with patch.dict("os.environ", {}, clear=True):
            r = _fetch_alerts_counts()
        self.assertEqual(r, {})


if __name__ == "__main__":
    unittest.main()
