from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.state_manager import build_decision_trace, update_current


class DecisionObservabilityTests(unittest.TestCase):
    def test_trace_identity_reuses_canonical_market_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "account_fact.json").write_text(
                '{"status":"MISSING","updated_at":"","source":"","positions":[],"trades":[]}',
                encoding="utf-8",
            )
            update_current(
                root=root,
                market_date="2026-09-11",
                node="10:24",
                captured_at="2026-09-11T10:24:04+08:00",
                latest_snapshot="data/market/snapshots/2026.json",
                snapshot_commit="snapshot-1",
            )
            first = build_decision_trace(root)
            second = build_decision_trace(root)
        self.assertEqual(first["trace_id"], second["trace_id"])
        self.assertEqual(first["boundary"], "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED")
        self.assertEqual(first["snapshot_commit"], "snapshot-1")

    def test_timing_metadata_is_business_semantics_neutral(self) -> None:
        decision = {
            "market_date": "2026-09-11",
            "formal_action": {"risk_permission": "禁止新增", "amount": 0},
            "point_in_time": {"status": "CONSUMED_SNAPSHOT_VALIDATED"},
            "lifecycle_projection": {"status": "READY"},
        }
        with_metadata = copy.deepcopy(decision)
        with_metadata["observability"] = {
            "trace_id": "formal-decision:test",
            "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED",
            "timing": {"state_builder_started_at": "2026-09-11T10:24:04Z"},
        }
        without_metadata = dict(with_metadata)
        without_metadata.pop("observability")
        self.assertEqual(without_metadata, decision)
        self.assertEqual(with_metadata["formal_action"], decision["formal_action"])
        self.assertEqual(with_metadata["point_in_time"], decision["point_in_time"])
        self.assertEqual(with_metadata["lifecycle_projection"], decision["lifecycle_projection"])


if __name__ == "__main__":
    unittest.main()
