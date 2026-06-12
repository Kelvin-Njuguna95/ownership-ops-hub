#!/usr/bin/env python3
"""Unit tests for the pipeline health sentinel.

All HTTP is mocked — no real Airtable/Supabase/GitHub/Slack calls. Tests assert
message construction and exit codes for: healthy, missing snapshot, stale
computed_at, stale last-success, no successful run, Sunday skip, and that no
Slack POST happens when healthy or in --dry-run.
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_health_sentinel as phs  # noqa: E402

EAT = phs.EAT


def eat(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=EAT)


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text="ok"):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


# --------------------------------------------------------------------------
# Check 1 — snapshot freshness (pure logic)
# --------------------------------------------------------------------------
class TestCheckSnapshot(unittest.TestCase):
    def setUp(self):
        # A Friday 19:30 EAT — the intended snapshot-check time.
        self.now = eat(2026, 6, 12, 19, 30)

    def test_healthy_when_computed_after_cutoff(self):
        snap = {"computed_at": "2026-06-12T23:57:00+03:00"}
        healthy, msg = phs.check_snapshot(self.now, snap)
        self.assertTrue(healthy)
        self.assertIsNone(msg)

    def test_alert_when_snapshot_missing(self):
        healthy, msg = phs.check_snapshot(self.now, None)
        self.assertFalse(healthy)
        self.assertIn("no end-of-day snapshot for 2026-06-12", msg)
        self.assertIn("snapshots/2026-06-12.json", msg)
        self.assertIn("rotating_light", msg)

    def test_alert_when_computed_at_too_early(self):
        # 17:54 EAT — exactly the 2026-05-29 failure mode (cutoff is 18:30).
        snap = {"computed_at": "2026-06-12T17:54:00+03:00"}
        healthy, msg = phs.check_snapshot(self.now, snap)
        self.assertFalse(healthy)
        self.assertIn("froze early", msg)
        self.assertIn("17:54 EAT", msg)              # the computed_at is surfaced
        self.assertIn("36 min", msg)                 # gap to the 18:30 cutoff
        self.assertIn("2026-05-29", msg)             # cites the incident

    def test_alert_when_computed_at_unparseable(self):
        healthy, msg = phs.check_snapshot(self.now, {"computed_at": "not-a-date"})
        self.assertFalse(healthy)
        self.assertIn("no usable", msg)

    def test_cutoff_uses_eat_not_utc(self):
        # 18:40 EAT == 15:40 UTC. A UTC-naive comparison would call this stale; EAT
        # comparison must pass (it's after the 18:30 EAT cutoff).
        snap = {"computed_at": "2026-06-12T15:40:00+00:00"}  # = 18:40 EAT
        healthy, _ = phs.check_snapshot(self.now, snap)
        self.assertTrue(healthy)


class TestFetchSnapshot(unittest.TestCase):
    def test_200_returns_dict(self):
        with mock.patch.object(phs.requests, "get",
                               return_value=FakeResp(200, {"computed_at": "x"})):
            self.assertEqual(phs.fetch_snapshot("2026-06-12"), {"computed_at": "x"})

    def test_404_returns_none(self):
        with mock.patch.object(phs.requests, "get", return_value=FakeResp(404, text="")):
            self.assertIsNone(phs.fetch_snapshot("2026-06-12"))

    def test_500_raises(self):
        with mock.patch.object(phs.requests, "get", return_value=FakeResp(500, text="boom")):
            with self.assertRaises(RuntimeError):
                phs.fetch_snapshot("2026-06-12")


# --------------------------------------------------------------------------
# Check 2 — blind window (pure logic)
# --------------------------------------------------------------------------
class TestCheckBlindWindow(unittest.TestCase):
    def setUp(self):
        self.now_utc = eat(2026, 6, 12, 13, 30).astimezone(timezone.utc)  # 13:30 EAT check

    def test_healthy_when_recent(self):
        last = self.now_utc - timedelta(minutes=12)
        healthy, msg = phs.check_blind_window(self.now_utc, last)
        self.assertTrue(healthy)
        self.assertIsNone(msg)

    def test_alert_when_stale(self):
        last = self.now_utc - timedelta(minutes=73)
        healthy, msg = phs.check_blind_window(self.now_utc, last)
        self.assertFalse(healthy)
        self.assertIn("poll pipeline is blind", msg)
        self.assertIn("73 min", msg)                 # the age
        self.assertIn("EAT", msg)                    # the last-success time, EAT
        self.assertIn("2026-05-30", msg)             # cites the incident
        # last-success time is 12:17 EAT (13:30 EAT − 73 min)
        self.assertIn("12:17 EAT", msg)

    def test_boundary_60min_is_healthy(self):
        last = self.now_utc - timedelta(minutes=60)   # exactly at threshold → not >60
        healthy, _ = phs.check_blind_window(self.now_utc, last)
        self.assertTrue(healthy)

    def test_alert_when_no_successful_run(self):
        healthy, msg = phs.check_blind_window(self.now_utc, None)
        self.assertFalse(healthy)
        self.assertIn("no successful", msg.lower())


class TestFetchLatestPollSuccess(unittest.TestCase):
    ENV = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_TOKEN": "t"}

    def test_returns_latest_run_started_at(self):
        payload = {"workflow_runs": [{"id": 1, "run_started_at": "2026-06-12T10:00:00Z",
                                      "created_at": "2026-06-12T09:59:00Z"}]}
        with mock.patch.dict(os.environ, self.ENV, clear=False), \
             mock.patch.object(phs.requests, "get", return_value=FakeResp(200, payload)):
            ts = phs.fetch_latest_poll_success()
        self.assertEqual(ts, datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc))

    def test_empty_returns_none(self):
        with mock.patch.dict(os.environ, self.ENV, clear=False), \
             mock.patch.object(phs.requests, "get", return_value=FakeResp(200, {"workflow_runs": []})):
            self.assertIsNone(phs.fetch_latest_poll_success())

    def test_missing_repo_raises(self):
        env = {k: v for k, v in self.ENV.items() if k != "GITHUB_REPOSITORY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                phs.fetch_latest_poll_success()

    def test_api_error_raises(self):
        with mock.patch.dict(os.environ, self.ENV, clear=False), \
             mock.patch.object(phs.requests, "get", return_value=FakeResp(403, text="forbidden")):
            with self.assertRaises(RuntimeError):
                phs.fetch_latest_poll_success()


# --------------------------------------------------------------------------
# main() orchestration + exit codes (no real Slack)
# --------------------------------------------------------------------------
class TestMain(unittest.TestCase):
    def test_sunday_skips_and_exits_0(self):
        sunday = eat(2026, 6, 14, 19, 30).astimezone(timezone.utc)  # 2026-06-14 is a Sunday
        with mock.patch.object(phs, "fetch_snapshot") as fs, \
             mock.patch.object(phs, "fetch_latest_poll_success") as fp, \
             mock.patch.object(phs, "post_to_slack") as ps:
            rc = phs.main(["--check", "both"], now_utc=sunday)
        self.assertEqual(rc, phs.EXIT_HEALTHY)
        fs.assert_not_called()
        fp.assert_not_called()
        ps.assert_not_called()

    def test_healthy_snapshot_no_post_exit_0(self):
        fri = eat(2026, 6, 12, 19, 30).astimezone(timezone.utc)
        with mock.patch.object(phs, "fetch_snapshot",
                               return_value={"computed_at": "2026-06-12T23:50:00+03:00"}), \
             mock.patch.object(phs, "post_to_slack") as ps:
            rc = phs.main(["--check", "snapshot"], now_utc=fri)
        self.assertEqual(rc, phs.EXIT_HEALTHY)
        ps.assert_not_called()

    def test_unhealthy_snapshot_posts_and_exits_1(self):
        fri = eat(2026, 6, 12, 19, 30).astimezone(timezone.utc)
        with mock.patch.object(phs, "fetch_snapshot", return_value=None), \
             mock.patch.object(phs, "post_to_slack", return_value="webhook") as ps:
            rc = phs.main(["--check", "snapshot"], now_utc=fri)
        self.assertEqual(rc, phs.EXIT_ALERT)
        ps.assert_called_once()
        posted = ps.call_args.args[0]
        self.assertIn("no end-of-day snapshot", posted)

    def test_dry_run_does_not_post(self):
        fri = eat(2026, 6, 12, 19, 30).astimezone(timezone.utc)
        with mock.patch.object(phs, "fetch_snapshot", return_value=None), \
             mock.patch.object(phs, "post_to_slack") as ps:
            rc = phs.main(["--check", "snapshot", "--dry-run"], now_utc=fri)
        self.assertEqual(rc, phs.EXIT_ALERT)   # alert still detected …
        ps.assert_not_called()                 # … but never posted

    def test_operational_error_exits_2(self):
        fri = eat(2026, 6, 12, 19, 30).astimezone(timezone.utc)
        with mock.patch.object(phs, "fetch_snapshot", side_effect=RuntimeError("bucket down")), \
             mock.patch.object(phs, "post_to_slack") as ps:
            rc = phs.main(["--check", "snapshot"], now_utc=fri)
        self.assertEqual(rc, phs.EXIT_ERROR)
        ps.assert_not_called()

    def test_both_checks_run(self):
        fri = eat(2026, 6, 12, 13, 30).astimezone(timezone.utc)
        # snapshot computed at 13:00 is before the 18:30 cutoff → alerts; blind-window
        # last success 5 min ago → healthy. --dry-run avoids any real post. Assert that
        # an alert in the first check does not stop the second from running.
        with mock.patch.object(phs, "fetch_snapshot",
                               return_value={"computed_at": "2026-06-12T13:00:00+03:00"}) as fs, \
             mock.patch.object(phs, "fetch_latest_poll_success",
                               return_value=fri - timedelta(minutes=5)) as fp, \
             mock.patch.object(phs, "post_to_slack") as ps:
            rc = phs.main(["--check", "both", "--dry-run"], now_utc=fri)
        fs.assert_called_once()
        fp.assert_called_once()
        ps.assert_not_called()
        self.assertEqual(rc, phs.EXIT_ALERT)


if __name__ == "__main__":
    unittest.main()
