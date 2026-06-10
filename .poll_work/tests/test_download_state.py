"""Tests for download_state — Supabase Storage listing pagination.

Network calls are stubbed via unittest.mock; no real services hit.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from download_state import _list_prefix  # noqa: E402


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json = lambda: payload
    m.text = ""
    return m


class TestListPrefixPagination(unittest.TestCase):
    def test_paginates_past_1000_objects(self):
        """The list endpoint caps each response at `limit` objects; snapshots/
        grows one file per day. 1000-name page then a 3-name page → 1003 names,
        second request at offset=1000."""
        session = MagicMock()
        page1 = [{"name": f"2026-{i:04d}.json"} for i in range(1000)]
        page2 = [{"name": f"tail-{i}.json"} for i in range(3)]
        session.post.side_effect = [_resp(200, page1), _resp(200, page2)]

        names = _list_prefix(session, "https://x.supabase.co", "key", "snapshots")

        self.assertEqual(len(names), 1003)
        self.assertEqual(names[0], "2026-0000.json")
        self.assertEqual(names[-1], "tail-2.json")
        self.assertEqual(session.post.call_count, 2)
        offsets = [c.kwargs["json"]["offset"] for c in session.post.call_args_list]
        self.assertEqual(offsets, [0, 1000])

    def test_single_short_page_stops_after_one_request(self):
        session = MagicMock()
        session.post.return_value = _resp(200, [{"name": "a.json"}, {"name": "b.json"}])
        names = _list_prefix(session, "https://x", "key", "snapshots")
        self.assertEqual(names, ["a.json", "b.json"])
        self.assertEqual(session.post.call_count, 1)

    def test_error_mid_pagination_returns_collected_so_far(self):
        """A non-200 on a later page keeps the earlier pages (warn + partial
        result, same error posture as the single-page version)."""
        session = MagicMock()
        page1 = [{"name": f"f{i}.json"} for i in range(1000)]
        session.post.side_effect = [_resp(200, page1), _resp(500, [])]
        names = _list_prefix(session, "https://x", "key", "snapshots")
        self.assertEqual(len(names), 1000)


if __name__ == "__main__":
    unittest.main()
