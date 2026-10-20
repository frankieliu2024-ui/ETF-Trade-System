from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_scheduled_trade_review_actor as actor


class TradeReviewActorReadbackTests(unittest.TestCase):
    def test_manual_actor_requires_exact_completion_readback(self):
        final_content = "FROZEN TRADE REVIEW"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "completion.json"
            target.write_text(json.dumps({
                "request_id": "request-1",
                "task_run_id": "run-1",
                "occurrence_id": "occ-1",
                "entry_mode": "MANUAL_SCHEDULED_TASK",
                "presentation_binding": {
                    "report_type": "ETF_TRADE_REVIEW",
                    "full_content": final_content,
                    "content_hash": hashlib.sha256(final_content.encode()).hexdigest(),
                },
            }), encoding="utf-8")
            argv = [
                "run_scheduled_trade_review_actor.py",
                "--formal-review", "review.json",
                "--request-id", "request-1",
                "--requested-at-beijing", "2026-10-20T22:26:00+08:00",
                "--final-content", final_content,
                "--task-run-id", "run-1",
                "--occurrence-id", "occ-1",
                "--entry-mode", "MANUAL_SCHEDULED_TASK",
            ]
            with patch.object(actor, "write_completion_request", return_value=target), patch.object(sys, "argv", argv):
                self.assertEqual(actor.main(), 0)

    def test_manual_actor_fails_when_completion_readback_drifts(self):
        final_content = "FROZEN TRADE REVIEW"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "completion.json"
            target.write_text(json.dumps({
                "request_id": "request-1",
                "task_run_id": "run-1",
                "occurrence_id": "occ-1",
                "entry_mode": "MANUAL_SCHEDULED_TASK",
                "presentation_binding": {
                    "report_type": "ETF_TRADE_REVIEW",
                    "full_content": "DIFFERENT CONTENT",
                    "content_hash": "0" * 64,
                },
            }), encoding="utf-8")
            argv = [
                "run_scheduled_trade_review_actor.py",
                "--formal-review", "review.json",
                "--request-id", "request-1",
                "--requested-at-beijing", "2026-10-20T22:26:00+08:00",
                "--final-content", final_content,
                "--task-run-id", "run-1",
                "--occurrence-id", "occ-1",
                "--entry-mode", "MANUAL_SCHEDULED_TASK",
            ]
            with patch.object(actor, "write_completion_request", return_value=target), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
                    actor.main()


if __name__ == "__main__":
    unittest.main()
