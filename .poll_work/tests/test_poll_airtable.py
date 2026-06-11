"""Tests for poll_airtable._paginate truncation behavior.

The fix-systemic-today-metric-truncation refactor adds an on_truncate
parameter to _paginate. Today-scoped fetches (D/E/F/G) use "error" so a
silent truncation can never under-report the headline KPIs. This test
asserts that exit(2) fires when the page cap is hit with more records
remaining, and that "warn" mode lets the partial cache through.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mock_resp(records, offset=None):
    """Build a fake requests.Response stub for Airtable."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"records": records, "offset": offset}
    return m


class TestPaginateTruncation(unittest.TestCase):
    def setUp(self):
        # _paginate writes to HERE / "<prefix>_p<N>.json". Redirect HERE to
        # a temp dir so test runs don't pollute .poll_work/.
        self.tmpdir = tempfile.mkdtemp()
        self.tmppath = Path(self.tmpdir)
        # Patch HERE in poll_airtable to the temp dir
        import poll_airtable as pa
        self._orig_here = pa.HERE
        pa.HERE = self.tmppath
        self.pa = pa

    def tearDown(self):
        self.pa.HERE = self._orig_here
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_truncation_with_error_mode_exits_2(self):
        # Every page returns an offset → loop runs until cap; on_truncate=error → exit 2.
        with patch.object(self.pa, "requests") as mock_req:
            mock_req.get.return_value = _mock_resp([{"id": "rec1"}], offset="next-offset-token")
            with self.assertRaises(SystemExit) as cm:
                self.pa._paginate(
                    headers={"Authorization": "Bearer fake"},
                    params={"pageSize": "100"},
                    file_prefix="testD",
                    page_cap=3,  # small cap to trigger quickly
                    label="Fetch D (test)",
                    on_truncate="error",
                )
            self.assertEqual(cm.exception.code, 2)

    def test_truncation_with_warn_mode_returns_partial(self):
        # Same scenario but on_truncate=warn → returns the partial result, no exit.
        with patch.object(self.pa, "requests") as mock_req:
            mock_req.get.return_value = _mock_resp([{"id": "rec1"}], offset="next-offset-token")
            n_pages, n_records, truncated = self.pa._paginate(
                headers={"Authorization": "Bearer fake"},
                params={"pageSize": "100"},
                file_prefix="testB",
                page_cap=3,
                label="Fetch B (test)",
                on_truncate="warn",
            )
            self.assertEqual(n_pages, 3)
            self.assertEqual(n_records, 3)
            self.assertTrue(truncated)

    def test_no_truncation_returns_clean(self):
        # Returning an empty offset on page 1 → loop exits naturally, no truncation.
        with patch.object(self.pa, "requests") as mock_req:
            mock_req.get.return_value = _mock_resp([{"id": "rec1"}, {"id": "rec2"}], offset=None)
            n_pages, n_records, truncated = self.pa._paginate(
                headers={"Authorization": "Bearer fake"},
                params={"pageSize": "100"},
                file_prefix="testNoTrunc",
                page_cap=100,
                label="Fetch D (test)",
                on_truncate="error",
            )
            self.assertEqual(n_pages, 1)
            self.assertEqual(n_records, 2)
            self.assertFalse(truncated)

    def test_writes_one_page_file_per_page(self):
        with patch.object(self.pa, "requests") as mock_req:
            responses = [
                _mock_resp([{"id": "rec1"}], offset="page2"),
                _mock_resp([{"id": "rec2"}], offset="page3"),
                _mock_resp([{"id": "rec3"}], offset=None),
            ]
            mock_req.get.side_effect = responses
            self.pa._paginate(
                headers={"Authorization": "Bearer fake"},
                params={"pageSize": "100"},
                file_prefix="testPages",
                page_cap=10,
                label="Fetch (test)",
                on_truncate="error",
            )
            for n in (1, 2, 3):
                f = self.tmppath / f"testPages_p{n}.json"
                self.assertTrue(f.exists(), f"page file p{n} should be written")
                doc = json.loads(f.read_text())
                self.assertEqual(len(doc["records"]), 1)


class TestGetWithRetry(unittest.TestCase):
    """_get_with_retry — absorb transient runner-network blips (2026-06-10:
    12/107 CI runs died on [Errno 101] Network is unreachable), retrying ONLY
    connection errors and 5xx/429. 4xx returns immediately to _paginate's
    existing loud exit(2) path."""

    def setUp(self):
        import poll_airtable as pa
        self.pa = pa

    def _resp(self, status):
        m = MagicMock()
        m.status_code = status
        return m

    def test_connection_error_twice_then_success(self):
        from requests.exceptions import ConnectionError as CE
        ok = self._resp(200)
        with patch.object(self.pa, "requests") as mock_req, \
             patch.object(self.pa.time, "sleep") as mock_sleep:
            mock_req.get.side_effect = [CE("[Errno 101] Network is unreachable"),
                                        CE("[Errno 101] Network is unreachable"), ok]
            r = self.pa._get_with_retry("https://api.airtable.com/x", {}, {}, "test")
            self.assertIs(r, ok)
            self.assertEqual(mock_req.get.call_count, 3)
            self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [5, 15])

    def test_404_returns_immediately_one_call(self):
        # Non-retryable status: returned unchanged on the FIRST call, so
        # _paginate's status!=200 branch still fails loudly (exit 2).
        bad = self._resp(404)
        with patch.object(self.pa, "requests") as mock_req, \
             patch.object(self.pa.time, "sleep") as mock_sleep:
            mock_req.get.return_value = bad
            r = self.pa._get_with_retry("https://api.airtable.com/x", {}, {}, "test")
            self.assertIs(r, bad)
            self.assertEqual(mock_req.get.call_count, 1)
            mock_sleep.assert_not_called()

    def test_5xx_retried_final_attempt_returned(self):
        # 5xx retried; the LAST attempt's response is returned unchanged so
        # the caller's loud-failure path still sees the real status.
        with patch.object(self.pa, "requests") as mock_req, \
             patch.object(self.pa.time, "sleep") as mock_sleep:
            mock_req.get.side_effect = [self._resp(500), self._resp(502), self._resp(500)]
            r = self.pa._get_with_retry("https://api.airtable.com/x", {}, {}, "test")
            self.assertEqual(r.status_code, 500)
            self.assertEqual(mock_req.get.call_count, 3)
            self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [5, 15])

    def test_exhausted_connection_errors_raise(self):
        from requests.exceptions import ConnectionError as CE
        with patch.object(self.pa, "requests") as mock_req, \
             patch.object(self.pa.time, "sleep") as mock_sleep:
            mock_req.get.side_effect = CE("[Errno 101] Network is unreachable")
            with self.assertRaises(CE):
                self.pa._get_with_retry("https://api.airtable.com/x", {}, {}, "test")
            self.assertEqual(mock_req.get.call_count, 3)
            self.assertEqual(mock_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
