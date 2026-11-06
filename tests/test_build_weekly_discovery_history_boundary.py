import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_weekly_discovery


class DiscoveryHistoryBoundaryTests(unittest.TestCase):
    def test_auction_and_close_use_latest_completed_session(self):
        with patch.object(
            build_weekly_discovery,
            "required_completed_history_date",
            return_value="2026-11-05",
        ) as resolver:
            self.assertEqual(
                build_weekly_discovery.resolve_discovery_history_end_date(
                    Path("."), "2026-11-06", "auction"
                ),
                "2026-11-05",
            )
            self.assertEqual(
                build_weekly_discovery.resolve_discovery_history_end_date(
                    Path("."), "2026-11-06", "close"
                ),
                "2026-11-05",
            )
        self.assertEqual(resolver.call_count, 2)
        self.assertTrue(all(call.kwargs["include_execution_date"] is False for call in resolver.call_args_list))

    def test_auction_is_a_publishable_discovery_node(self):
        source = Path("scripts/build_weekly_discovery.py").read_text(encoding="utf-8")
        self.assertIn('node not in {"auction", "open", "midday", "close"}', source)


if __name__ == "__main__":
    unittest.main()
