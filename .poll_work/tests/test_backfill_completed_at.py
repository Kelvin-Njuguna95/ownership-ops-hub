"""Tests for backfill_completed_at idempotency — instant-equality comparison.

The backfill must not churn on rows that are already at the correct instant but
whose stored string differs from the resolved raw Airtable string (Postgres
normalizes '...000Z' to '...+00:00'). These tests pin the parsed-instant
comparison that fixes that.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backfill_completed_at import (  # noqa: E402
    _same_instant,
    _needs_update,
    find_affected,
)

# valid_selected_time, relations_support scheme — what a 'Valid' row resolves to.
FLD_VALID_SEL = "fldu7c6IVtQ7MucWP"


def _row(stored, real, vs="Valid"):
    """An ownership_completions row with a resolvable real time in raw_payload."""
    return {
        "id": "u-" + (stored or "null"),
        "airtable_record_id": "rec" + (stored or "x"),
        "verification_status": vs,
        "completed_at": stored,
        "raw_payload": {FLD_VALID_SEL: real} if real else {},
    }


class TestSameInstant(unittest.TestCase):
    def test_same_instant_different_format(self):
        # Postgres-normalized vs raw Airtable string — identical instant.
        self.assertTrue(_same_instant("2026-05-28T17:52:55+00:00",
                                      "2026-05-28T17:52:55.000Z"))

    def test_sub_second_within_tolerance(self):
        self.assertTrue(_same_instant("2026-05-28T17:52:55+00:00",
                                      "2026-05-28T17:52:55.400Z"))

    def test_one_point_five_seconds_not_same(self):
        self.assertFalse(_same_instant("2026-05-28T17:52:55+00:00",
                                       "2026-05-28T17:52:56.500Z"))

    def test_unparseable_is_not_same(self):
        self.assertFalse(_same_instant(None, "2026-05-28T17:52:55.000Z"))


class TestNeedsUpdate(unittest.TestCase):
    def test_same_instant_format_only_not_flagged(self):
        self.assertFalse(_needs_update("2026-05-28T17:52:55+00:00",
                                       "2026-05-28T17:52:55.000Z"))

    def test_1_5s_drift_flagged(self):
        self.assertTrue(_needs_update("2026-05-28T17:52:55+00:00",
                                      "2026-05-28T17:52:56.500Z"))

    def test_minute_drift_flagged(self):
        # The real bulk-stamp signature: stored clock vs real time minutes apart.
        self.assertTrue(_needs_update("2026-05-28T12:55:17+00:00",
                                      "2026-05-28T12:20:18.000Z"))

    def test_hour_drift_flagged(self):
        self.assertTrue(_needs_update("2026-05-28T12:55:17+00:00",
                                      "2026-05-28T11:05:00.000Z"))

    def test_missing_stored_flagged(self):
        self.assertTrue(_needs_update(None, "2026-05-28T11:05:00.000Z"))
        self.assertTrue(_needs_update("", "2026-05-28T11:05:00.000Z"))


class TestFindAffected(unittest.TestCase):
    def test_same_instant_rows_not_flagged(self):
        rows = [
            _row("2026-05-28T17:52:55+00:00", "2026-05-28T17:52:55.000Z"),
            _row("2026-05-28T12:20:18+00:00", "2026-05-28T12:20:18.000Z"),
        ]
        self.assertEqual(find_affected(rows, 10, 30), [])

    def test_drifted_and_missing_flagged(self):
        rows = [
            _row("2026-05-28T17:52:55+00:00", "2026-05-28T17:52:55.000Z"),  # ok
            _row("2026-05-28T12:55:17+00:00", "2026-05-28T12:20:18.000Z"),  # 35m drift
            _row(None, "2026-05-28T11:05:00.000Z"),                          # missing
        ]
        affected = find_affected(rows, 10, 30)
        new_times = sorted(new for _, new in affected)
        self.assertEqual(new_times,
                         ["2026-05-28T11:05:00.000Z", "2026-05-28T12:20:18.000Z"])

    def test_no_resolvable_date_left_alone(self):
        # raw_payload has no date field -> resolve returns sentinel -> skip.
        rows = [{"id": "u1", "airtable_record_id": "r1",
                 "verification_status": "Valid",
                 "completed_at": "2026-05-28T12:55:17+00:00", "raw_payload": {}}]
        self.assertEqual(find_affected(rows, 10, 30), [])


if __name__ == "__main__":
    unittest.main()
