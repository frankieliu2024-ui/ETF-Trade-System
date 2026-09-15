from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.process_state_sync_request as state_sync


class ManualFormalDecisionWriterIdentityTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> tuple[dict, dict]:
        (root / "events/decisions").mkdir(parents=True)
        (root / "data/state").mkdir(parents=True)
        (root / "data/market/snapshots").mkdir(parents=True)
        (root / "config/market").mkdir(parents=True)
        snapshot_path = "data/market/snapshots/2026-09-15_144842.json"
        snapshot = {
            "market_date": "2026-09-15",
            "quality_status": "PASS",
            "captured_at_beijing": "2026-09-15T14:48:42+08:00",
            "rows": [],
        }
        (root / snapshot_path).write_text(json.dumps(snapshot), encoding="utf-8")
        (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-15"}), encoding="utf-8")
        (root / "data/state/account_fact.json").write_text(json.dumps({"status": "VALID", "positions": []}), encoding="utf-8")
        (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects": []}), encoding="utf-8")
        decision = {
            "decision_id": "20260915_1447_manual_intraday_chat_decision",
            "opportunity_status": "观察机会",
            "risk_permission": "禁止新增",
            "main_candidate": "半导体设备ETF（561980）",
            "candidate_code": "561980",
            "candidate_name": "半导体设备ETF",
            "lifecycle": {},
            "managed_position_reviews": [],
            "data_as_of_beijing": "2026-09-15T14:48:42+08:00",
        }
        request = {
            "request_id": "20260915_1447_manual_intraday_chat__formal_completion",
            "parent_request_id": "20260915_1447_manual_intraday_chat",
            "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
            "interaction_scenario": "INTRADAY",
            "market_date": "2026-09-15",
            "requested_at_beijing": "2026-09-15T14:49:00+08:00",
            "persistence_available_at_beijing": "2026-09-15T14:49:00+08:00",
            "consumed_snapshot": snapshot_path,
            "formal_decision": decision,
        }
        return request, decision

    def test_manual_completion_persists_parent_identity_and_is_idempotent_across_envelopes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            request, decision = self._write_fixture(root)
            retry = dict(request)
            retry["request_id"] = "20260915_1447_manual_intraday_chat__formal_completion_retry"
            with mock.patch.object(state_sync, "ROOT", root), \
                 mock.patch.object(state_sync, "ACCOUNT", root / "data/state/account_fact.json"), \
                 mock.patch.object(state_sync, "validate_managed_position_lifecycle", return_value=""), \
                 mock.patch.object(state_sync, "validate_managed_position_review_contract", return_value=""), \
                 mock.patch.object(state_sync, "build_managed_position_projection", return_value={"positions": []}), \
                 mock.patch.object(state_sync, "build_comparison_snapshot", return_value={"items": []}):
                first = state_sync.record_formal_decision(request)
                second = state_sync.record_formal_decision(retry)
            self.assertEqual(first, (True, decision["decision_id"]))
            self.assertEqual(second, first)
            event = json.loads((root / "events/decisions" / f"{decision['decision_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(event["request_id"], request["request_id"])
            self.assertEqual(event["parent_request_id"], request["parent_request_id"])
            self.assertEqual(event["price_source_snapshot"], request["consumed_snapshot"])


if __name__ == "__main__":
    unittest.main()
