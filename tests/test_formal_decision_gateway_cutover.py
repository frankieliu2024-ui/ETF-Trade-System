from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import process_state_sync_request as state_sync


class FormalDecisionGatewayCutoverTests(unittest.TestCase):
    def _root(self, requested: str):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "requests/live_snapshot").mkdir(parents=True)
        parent_id = "formal-parent"
        (root / f"requests/live_snapshot/{parent_id}.json").write_text(
            json.dumps({
                "request_id": parent_id,
                "requested_at_beijing": requested,
                "request_type": "MARKET_QUOTE_REFRESH",
                "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            }),
            encoding="utf-8",
        )
        legacy = {
            "request_id": f"{parent_id}__formal_completion",
            "parent_request_id": parent_id,
            "request_type": "STATE_SYNC_ONLY",
            "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
            "formal_fact_type": "FORMAL_DECISION",
            "requested_at_beijing": requested,
            "formal_decision": {"decision_id": f"{parent_id}_decision"},
        }
        path = root / "requests/live_snapshot/legacy.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        return td, root, path

    def test_real_post_cutover_semantics_reject_direct_legacy_bypass(self):
        td, root, path = self._root("2026-09-25T17:20:00+08:00")
        try:
            with mock.patch.object(state_sync, "ROOT", root), mock.patch(
                "sys.argv", ["process_state_sync_request.py", str(path.relative_to(root))]
            ):
                with self.assertRaisesRegex(ValueError, "legacy completion bypass is forbidden"):
                    state_sync.main()
        finally:
            td.cleanup()

    def test_historical_legacy_boundary_is_not_rejected_by_cutover_guard(self):
        td, root, path = self._root("2026-09-24T15:04:03+08:00")
        try:
            # The historical envelope is allowed past the new cutover guard.
            # Stop immediately after that point so this test does not need to
            # reconstruct unrelated historical account/market fixtures.
            with mock.patch.object(state_sync, "ROOT", root), mock.patch(
                "sys.argv", ["process_state_sync_request.py", str(path.relative_to(root))]
            ), mock.patch.object(state_sync, "record_formal_decision", side_effect=RuntimeError("past-cutover-guard")):
                with self.assertRaisesRegex((RuntimeError, FileNotFoundError, ValueError), "(past-cutover-guard|query context|account|market|CURRENT)"):
                    state_sync.main()
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
