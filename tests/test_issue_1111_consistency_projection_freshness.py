import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_query_context import consistency_projection_status


class ConsistencyProjectionFreshnessTests(unittest.TestCase):
    def _root(self, status, checked):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        path = root / "data/state"
        path.mkdir(parents=True)
        (path / "system_consistency.json").write_text(json.dumps({
            "status": status,
            "hard_error_count": 0 if status != "FAIL" else 1,
            "warning_count": 0,
            "checks": [], "errors": [], "warnings": [],
            "commit_audit": {"checked_commit": checked},
        }), encoding="utf-8")
        return td, root

    @patch("scripts.build_query_context.subprocess.check_output")
    def test_stale_pass_cannot_prove_current_consistency(self, check_output):
        check_output.return_value = "b" * 40 + "\n"
        td, root = self._root("PASS", "a" * 40)
        self.addCleanup(td.cleanup)
        got = consistency_projection_status(root)
        self.assertEqual(got["evidence_semantic"], "LAST_KNOWN_PROJECTION")
        self.assertFalse(got["can_prove_current_consistency"])
        self.assertEqual(got["effective_current_status"], "UNKNOWN")

    @patch("scripts.build_query_context.subprocess.check_output")
    def test_fresh_pass_is_current_projection(self, check_output):
        sha = "c" * 40
        check_output.return_value = sha + "\n"
        td, root = self._root("PASS", sha)
        self.addCleanup(td.cleanup)
        got = consistency_projection_status(root)
        self.assertTrue(got["can_prove_current_consistency"])
        self.assertEqual(got["effective_current_status"], "PASS")

    @patch("scripts.build_query_context.subprocess.check_output")
    def test_stale_fail_keeps_failure_visible_without_claiming_current(self, check_output):
        check_output.return_value = "d" * 40 + "\n"
        td, root = self._root("FAIL", "e" * 40)
        self.addCleanup(td.cleanup)
        got = consistency_projection_status(root)
        self.assertFalse(got["can_prove_current_consistency"])
        self.assertEqual(got["effective_current_status"], "UNKNOWN")
        self.assertTrue(got["last_known_failure_preserved"])
        self.assertEqual(got["reported_status"], "FAIL")

    @patch("scripts.build_query_context.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, ["git"]))
    def test_unavailable_head_fails_closed(self, _):
        td, root = self._root("PASS", "f" * 40)
        self.addCleanup(td.cleanup)
        got = consistency_projection_status(root)
        self.assertFalse(got["can_prove_current_consistency"])
        self.assertEqual(got["effective_current_status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
