import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import scripts.refresh_gate as refresh_gate
from scripts.runtime_session_gate import classify_live_snapshot_request


class ScheduledFormalDecisionPrewarmPersistenceTests(unittest.TestCase):
    def _paths(self, root: Path):
        request_dir = root / "requests" / "live_snapshot"
        request_dir.mkdir(parents=True)
        current = root / "data" / "state" / "CURRENT.json"
        health = root / "data" / "state" / "runtime_health.json"
        policy = root / "config" / "runtime_policy.json"
        current.parent.mkdir(parents=True)
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({
            "query_time_refresh_preference": {
                "explicit_latest_query_intent": "EXPLICIT_LATEST"
            }
        }), encoding="utf-8")
        health.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        return request_dir, current, health, policy

    def _patch_paths(self, request_dir, current, health, policy):
        return patch.multiple(
            refresh_gate,
            REQUEST_DIR=request_dir,
            CURRENT=current,
            RUNTIME_HEALTH=health,
            RUNTIME_POLICY=policy,
        )

    def test_state_sync_classifier_imports_from_repo_root_package(self):
        request = {
            "query_intent": "SCHEDULED_FORMAL_DECISION",
            "interaction_scenario": "INTRADAY",
            "formal_decision": {"decision_id": "d1"},
        }
        self.assertEqual(classify_live_snapshot_request(request), "STATE_SYNC_ONLY")

    def test_scheduled_prewarm_waits_for_nominal_node_but_accepts_post_request_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_dir, current, health, policy = self._paths(root)
            (request_dir / "scheduled.json").write_text(json.dumps({
                "request_id": "scheduled",
                "requested_at_beijing": "2026-09-09T11:23:30+08:00",
                "requested_market_time": "2026-09-09T11:25:00+08:00",
                "query_intent": "SCHEDULED_FORMAL_DECISION",
                "wait_for_refresh": True,
                "require_post_request_snapshot": True,
                "allow_wait_refresh_fallback": False,
            }), encoding="utf-8")
            current.write_text(json.dumps({
                "captured_at": "2026-09-09T11:24:06+08:00",
                "latest_snapshot": "data/market/snapshots/2026-09-09_112406.json",
                "data_freshness": {"captured_at_beijing": "2026-09-09T11:24:06+08:00"},
            }), encoding="utf-8")

            with self._patch_paths(request_dir, current, health, policy):
                before = refresh_gate.build_gate(datetime.fromisoformat("2026-09-09T11:24:30+08:00"))
                after = refresh_gate.build_gate(datetime.fromisoformat("2026-09-09T11:25:30+08:00"))

            self.assertEqual(before["status"], "PENDING")
            self.assertFalse(before["formal_decision_persist_allowed"])
            self.assertTrue(before["post_request_snapshot"])
            self.assertFalse(before["nominal_node_reached"])
            self.assertEqual(after["status"], "READY")
            self.assertTrue(after["formal_decision_persist_allowed"])
            self.assertEqual(after["resolved_snapshot_time"], "2026-09-09T11:24:06+08:00")
            self.assertTrue(after["nominal_node_reached"])

    def test_explicit_latest_keeps_requested_market_time_as_hard_fact_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_dir, current, health, policy = self._paths(root)
            (request_dir / "explicit.json").write_text(json.dumps({
                "request_id": "explicit",
                "requested_at_beijing": "2026-09-09T11:23:30+08:00",
                "requested_market_time": "2026-09-09T11:25:00+08:00",
                "query_intent": "EXPLICIT_LATEST",
                "wait_for_refresh": True,
                "require_post_request_snapshot": True,
                "allow_wait_refresh_fallback": False,
            }), encoding="utf-8")
            current.write_text(json.dumps({
                "captured_at": "2026-09-09T11:24:06+08:00",
                "latest_snapshot": "data/market/snapshots/2026-09-09_112406.json",
                "data_freshness": {"captured_at_beijing": "2026-09-09T11:24:06+08:00"},
            }), encoding="utf-8")

            with self._patch_paths(request_dir, current, health, policy):
                gate = refresh_gate.build_gate(datetime.fromisoformat("2026-09-09T11:25:30+08:00"))

            self.assertEqual(gate["status"], "PENDING")
            self.assertFalse(gate["formal_decision_persist_allowed"])
            self.assertFalse(gate["formal_current_pit_analysis_allowed"])
            self.assertTrue(gate["degraded_analysis_allowed"])

    def test_pending_refresh_does_not_blanket_block_degraded_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_dir, current, health, policy = self._paths(root)
            (request_dir / "explicit.json").write_text(json.dumps({
                "request_id": "explicit",
                "requested_at_beijing": "2026-09-09T11:23:30+08:00",
                "requested_market_time": "2026-09-09T11:25:00+08:00",
                "query_intent": "EXPLICIT_LATEST",
                "wait_for_refresh": True,
                "require_post_request_snapshot": True,
                "allow_wait_refresh_fallback": False,
            }), encoding="utf-8")
            current.write_text(json.dumps({
                "captured_at": "2026-09-09T11:24:06+08:00",
                "latest_snapshot": "data/market/snapshots/2026-09-09_112406.json",
                "data_freshness": {"captured_at_beijing": "2026-09-09T11:24:06+08:00"},
            }), encoding="utf-8")

            with self._patch_paths(request_dir, current, health, policy):
                gate = refresh_gate.build_gate(datetime.fromisoformat("2026-09-09T11:25:30+08:00"))

            self.assertEqual(gate["status"], "PENDING")
            self.assertFalse(gate["formal_analysis_allowed"])
            self.assertFalse(gate["formal_current_pit_analysis_allowed"])
            self.assertTrue(gate["degraded_analysis_allowed"])
            self.assertFalse(gate["formal_decision_persist_allowed"])

    def test_scheduled_decision_alignment_uses_request_time_not_nominal_fact_floor(self):
        request = {
            "requested_at_beijing": "2026-09-09T11:23:30+08:00",
            "requested_market_time": "2026-09-09T11:25:00+08:00",
            "query_intent": "SCHEDULED_FORMAL_DECISION",
            "wait_for_refresh": True,
            "formal_decision": {"data_as_of_beijing": "2026-09-09T11:24:03+08:00"},
        }
        gate = {"resolved_snapshot_time": "2026-09-09T11:24:06+08:00"}
        aligned, reason = refresh_gate._formal_decision_matches_requested_refresh(request, gate)
        self.assertTrue(aligned)
        self.assertEqual(reason, "FORMAL_DECISION_REFRESH_ALIGNED")


if __name__ == "__main__":
    unittest.main()
