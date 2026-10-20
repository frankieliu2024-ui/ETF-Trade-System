from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import run_scheduled_trade_review_actor as actor


class TradeReviewActorEntryTests(unittest.TestCase):
    def test_actor_delegates_to_canonical_completion_writer(self):
        with patch.object(actor, "write_completion_request", return_value="requests/live_snapshot/x.json") as writer:
            with patch("sys.argv", [
                "actor",
                "--formal-review", "review.json",
                "--request-id", "request-1",
                "--requested-at-beijing", "2026-10-20T10:30:00+08:00",
                "--final-content", "FROZEN FINAL CONTENT",
                "--task-run-id", "run-1",
                "--occurrence-id", "occ-1",
                "--entry-mode", "MANUAL_SCHEDULED_TASK",
            ]):
                self.assertEqual(actor.main(), 0)
            writer.assert_called_once_with(
                "review.json", "request-1", "2026-10-20T10:30:00+08:00", "", "", "",
                "FROZEN FINAL CONTENT", "ETF交易复盘", "run-1", "occ-1",
                "MANUAL_SCHEDULED_TASK",
            )


if __name__ == "__main__":
    unittest.main()
