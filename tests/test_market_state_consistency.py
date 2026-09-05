from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_gate_helpers():
    source = (ROOT / "scripts/check_market_state_consistency.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"is_capture_window_skip", "is_idempotent_close_skip", "is_phase_mismatch_allowed"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / "scripts/check_market_state_consistency.py"), "exec"), namespace)
    return namespace


class MarketStateCloseSkipTests(unittest.TestCase):
    def setUp(self):
        self.helpers = load_gate_helpers()
        self.current = {
            "market_date": "2026-09-04",
            "node_status": "READY",
            "latest_valid_node": "close",
            "latest_snapshot": "data/market/snapshots/2026-09-04_150635.json",
        }
        self.runtime = {
            "status": "SKIPPED",
            "reason": "close_already_recorded",
            "market_date": "2026-09-04",
            "latest_snapshot": self.current["latest_snapshot"],
            "market_phase": "OUTSIDE_SESSION",
            "failure_stage": "",
        }
        self.snapshot = {"market_date": "2026-09-04", "market_phase": "POST_CLOSE_GRACE"}

    def test_idempotent_close_skip_allows_retained_phase_difference(self):
        self.assertTrue(self.helpers["is_idempotent_close_skip"](self.current, self.runtime, self.snapshot, self.current["latest_snapshot"]))
        self.assertTrue(self.helpers["is_phase_mismatch_allowed"](self.current, self.runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_capture_window_skip_remains_allowed(self):
        runtime = dict(self.runtime, reason="outside_a_share_capture_window", failure_stage="session_gate")
        self.assertTrue(self.helpers["is_phase_mismatch_allowed"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_snapshot_identity_mismatch_is_rejected(self):
        runtime = dict(self.runtime, latest_snapshot="data/market/snapshots/other.json")
        self.assertFalse(self.helpers["is_idempotent_close_skip"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_market_date_mismatch_is_rejected(self):
        runtime = dict(self.runtime, market_date="2026-09-03")
        self.assertFalse(self.helpers["is_idempotent_close_skip"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_current_must_be_ready_close(self):
        current = dict(self.current, node_status="READY", latest_valid_node="live")
        self.assertFalse(self.helpers["is_idempotent_close_skip"](current, self.runtime, self.snapshot, current["latest_snapshot"]))

    def test_normal_active_phase_mismatch_is_rejected(self):
        runtime = dict(self.runtime, status="PASS", reason="", market_phase="OUTSIDE_SESSION")
        self.assertFalse(self.helpers["is_phase_mismatch_allowed"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_unknown_skip_reason_is_rejected(self):
        runtime = dict(self.runtime, reason="unknown_skip")
        self.assertFalse(self.helpers["is_phase_mismatch_allowed"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))

    def test_aligned_runtime_passes_idempotent_exception_gate(self):
        runtime = dict(self.runtime, market_phase="POST_CLOSE_GRACE")
        self.assertTrue(self.helpers["is_phase_mismatch_allowed"](self.current, runtime, self.snapshot, self.current["latest_snapshot"]))


if __name__ == "__main__":
    unittest.main()
