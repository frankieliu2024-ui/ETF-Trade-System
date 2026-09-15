from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import manual_formal_decision_completion as completion  # noqa: E402
import process_state_sync_request as state_sync  # noqa: E402


class ManualFormalDecisionCompletionTests(unittest.TestCase):
    def test_build_completion_reuses_parent_identity_and_stays_state_sync_only(self):
        source = {
            "request_id": "manual-20260915-1430",
            "interaction_scenario": "INTRADAY",
            "requested_at_beijing": "2026-09-15T14:30:10+08:00",
            "market_date": "2026-09-15",
            "consumed_snapshot": "data/market/snapshots/2026-09-15_143000.json",
            "wait_for_refresh": True,
            "require_post_request_snapshot": True,
        }
        decision = {
            "decision_id": "manual-20260915-1430-decision",
            "opportunity_status": "无机会",
            "risk_permission": "禁止新增",
            "lifecycle": {},
            "managed_position_reviews": [],
            "data_as_of_beijing": "2026-09-15T14:30:00+08:00",
        }
        payload = completion.build_completion_request(source, decision)
        self.assertEqual(payload["parent_request_id"], source["request_id"])
        self.assertEqual(payload["request_id"], source["request_id"] + "__formal_completion")
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertNotIn("wait_for_refresh", payload)
        self.assertNotIn("require_post_request_snapshot", payload)
        self.assertEqual(payload["formal_decision"], decision)

    def test_no_completed_decision_fails_closed(self):
        source = {"request_id": "manual-x", "interaction_scenario": "INTRADAY"}
        with self.assertRaisesRegex(ValueError, "completed formal_decision"):
            completion.build_completion_request(source, {})

    def test_completion_request_can_be_consumed_by_existing_owner_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requests/live_snapshot").mkdir(parents=True)
            (root / "events/decisions").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            (root / "data/market/snapshots").mkdir(parents=True)
            (root / "config/market").mkdir(parents=True)
            source = {
                "request_id": "manual-idem",
                "interaction_scenario": "INTRADAY",
                "requested_at_beijing": "2026-09-15T14:30:10+08:00",
                "market_date": "2026-09-15",
                "consumed_snapshot": "data/market/snapshots/2026-09-15_143000.json",
            }
            decision = {
                "decision_id": "manual-idem-decision",
                "opportunity_status": "无机会",
                "risk_permission": "禁止新增",
                "lifecycle": {},
                "managed_position_reviews": [],
                "data_as_of_beijing": "2026-09-15T14:30:00+08:00",
            }
            snapshot = {
                "market_date": "2026-09-15",
                "quality_status": "PASS",
                "captured_at_beijing": "2026-09-15T14:30:05+08:00",
                "rows": [],
            }
            universe = {"objects": []}
            (root / source["consumed_snapshot"]).write_text(json.dumps(snapshot), encoding="utf-8")
            (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-09-15"}), encoding="utf-8")
            (root / "data/state/account_fact.json").write_text(json.dumps({"status": "VALID", "positions": []}), encoding="utf-8")
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps(universe), encoding="utf-8")

            payload = completion.build_completion_request(source, decision)
            with mock.patch.object(state_sync, "ROOT", root), \
                 mock.patch.object(state_sync, "ACCOUNT", root / "data/state/account_fact.json"), \
                 mock.patch.object(state_sync, "validate_managed_position_lifecycle", return_value=""), \
                 mock.patch.object(state_sync, "validate_managed_position_review_contract", return_value=""), \
                 mock.patch.object(state_sync, "build_managed_position_projection", return_value={"positions": []}), \
                 mock.patch.object(state_sync, "build_comparison_snapshot", return_value={"items": []}):
                first = state_sync.record_formal_decision(payload)
                second = state_sync.record_formal_decision(payload)
            self.assertEqual(first, (True, decision["decision_id"]))
            self.assertEqual(second, first)
            event = json.loads((root / "events/decisions/manual-idem-decision.json").read_text(encoding="utf-8"))
            self.assertEqual(event["event_type"], "FORMAL_DECISION")
            self.assertEqual(event["formal_decision"], decision)
            self.assertEqual(event["price_source_snapshot"], source["consumed_snapshot"])


if __name__ == "__main__":
    unittest.main()
