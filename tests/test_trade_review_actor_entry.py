from __future__ import annotations

import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import scheduled_review_completion_owner as owner


class TradeReviewCompletionOwnerTests(unittest.TestCase):
    def test_owner_exposes_persistence_and_notification_operations(self):
        self.assertTrue(callable(owner.persist_completion))
        self.assertTrue(callable(owner.dispatch_notification))

    def test_post_handoff_reports_exact_completion_failure_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "completion.json"
            target.write_text("{}\n", encoding="utf-8")
            with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""}, clear=False):
                result = owner.execute_post_handoff(target)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["failure_stage"], "completion_write")
        self.assertEqual(result["completion_write"]["status"], "FAILED")
        self.assertFalse(result["notification_dispatch"]["attempted"])

    def test_post_handoff_never_dispatches_before_completion_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "completion.json"
            target.write_text("{}\n", encoding="utf-8")
            with patch.object(owner, "persist_completion", return_value=("requests/live_snapshot/x.json", {"written": True, "read_back": True})), \
                 patch.object(owner, "dispatch_notification", return_value={"submitted": True}) as dispatch:
                result = owner.execute_post_handoff(target)
        dispatch.assert_called_once()
        self.assertEqual(result["completion_readback"]["status"], "SUCCEEDED")
        self.assertEqual(result["projection_readback"]["status"], "PENDING_CANONICAL_READBACK")
        self.assertFalse(result["ok"])

    def test_recovery_reuses_same_completion_once_after_explicit_failure(self):
        with patch.object(owner, "dispatch_notification", side_effect=[
            {"submitted": False, "reason": "primary unavailable"},
            {"submitted": True, "completion_path": "same.json"},
        ]) as dispatch:
            result = owner.dispatch_notification_with_one_recovery("same.json")
        self.assertTrue(result["recovery_attempted"])
        self.assertTrue(result["same_completion_path"])
        self.assertEqual(result["recovery_limit"], 1)
        self.assertEqual(dispatch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
