import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.refresh_gate as refresh_gate


class RefreshGateRequestIdentityTests(unittest.TestCase):
    def test_force_refresh_is_wait_bearing(self):
        self.assertTrue(refresh_gate._requires_wait({"force_refresh": True}))

    def test_preferred_triggering_request_wins_over_historical_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            request_dir.mkdir(parents=True)
            old = request_dir / "old.json"
            current = request_dir / "current.json"
            old.write_text(json.dumps({
                "request_id": "old",
                "requested_at_beijing": "2026-09-22T15:14:24+08:00",
                "query_intent": "FORMAL_INTRADAY_ANALYSIS",
                "force_refresh": True,
            }), encoding="utf-8")
            current.write_text(json.dumps({
                "request_id": "current",
                "requested_at_beijing": "2026-09-22T16:19:32+08:00",
                "query_intent": "FORMAL_INTRADAY_ANALYSIS",
                "force_refresh": True,
            }), encoding="utf-8")
            with patch.object(refresh_gate, "REQUEST_DIR", request_dir):
                path, req = refresh_gate.latest_wait_request(current)
            self.assertEqual(path, current)
            self.assertEqual(req["request_id"], "current")

    def test_annotate_contexts_uses_triggering_request_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            request_dir.mkdir(parents=True)
            current = request_dir / "current.json"
            current.write_text(json.dumps({
                "request_id": "current",
                "requested_at_beijing": "2026-09-22T16:19:32+08:00",
                "query_intent": "FORMAL_INTRADAY_ANALYSIS",
                "force_refresh": True,
            }), encoding="utf-8")
            current_state = root / "CURRENT.json"
            current_state.write_text(json.dumps({}), encoding="utf-8")
            query = root / "query_context.json"
            query.write_text(json.dumps({}), encoding="utf-8")
            decision = root / "decision_context.json"
            decision.write_text(json.dumps({}), encoding="utf-8")
            with patch.object(refresh_gate, "ROOT", root), \
                 patch.object(refresh_gate, "REQUEST_DIR", request_dir), \
                 patch.object(refresh_gate, "CURRENT", current_state), \
                 patch.object(refresh_gate, "QUERY_CONTEXT", query), \
                 patch.object(refresh_gate, "DECISION_CONTEXT", decision), \
                 patch.dict(os.environ, {"TRIGGERING_REQUEST_FILE": "requests/live_snapshot/current.json"}, clear=False):
                refresh_gate.annotate_contexts()
            result = json.loads(query.read_text(encoding="utf-8"))
            self.assertEqual(result["refresh_gate"]["request_id"], "current")


    def test_post_close_request_reuses_same_day_canonical_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            request_dir.mkdir(parents=True)
            request = request_dir / "current.json"
            request.write_text(json.dumps({
                "request_id": "post-close-current",
                "requested_at_beijing": "2026-09-23T18:01:40+08:00",
                "require_post_request_snapshot": True,
                "query_intent": "EXPLICIT_LATEST",
            }), encoding="utf-8")
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            (state_dir / "CURRENT.json").write_text(json.dumps({
                "market_date": "2026-09-23", "latest_valid_node": "close",
                "captured_at": "2026-09-23T15:27:38+08:00", "quality_status": "PASS",
                "data_freshness": {"status": "PASS", "captured_at_beijing": "2026-09-23T15:27:38+08:00"},
                "latest_snapshot": "data/market/snapshots/2026-09-23_152738.json",
            }), encoding="utf-8")
            (state_dir / "runtime_health.json").write_text(json.dumps({
                "status": "SKIPPED", "finished_at": "2026-09-23T18:01:47+08:00", "reason": "outside_a_share_capture_window",
            }), encoding="utf-8")
            calendar_dir = root / "config" / "market"
            calendar_dir.mkdir(parents=True)
            (calendar_dir / "a_share_trading_calendar_2026.json").write_text(json.dumps({
                "coverage_start": "2026-01-01", "coverage_end": "2026-12-31", "closed_dates": []
            }), encoding="utf-8")
            with patch.object(refresh_gate, "ROOT", root), \\
                 patch.object(refresh_gate, "REQUEST_DIR", request_dir), \\
                 patch.object(refresh_gate, "CURRENT", state_dir / "CURRENT.json"), \\
                 patch.object(refresh_gate, "RUNTIME_HEALTH", state_dir / "runtime_health.json"):
                gate = refresh_gate.build_gate(now=refresh_gate.parse_time("2026-09-23T18:02:00+08:00"), request_file=request)
            self.assertEqual(gate["request_id"], "post-close-current")
            self.assertEqual(gate["request_result"], "READY")
            self.assertTrue(gate["request_result_terminal"])
            self.assertTrue(gate["formal_decision_persist_allowed"])
            self.assertFalse(gate["post_request_snapshot"])
            self.assertEqual(gate["request_scoped_resolution_mode"], "CLOSED_SESSION_CANONICAL_REUSE")

    def test_active_session_skipped_capture_after_request_is_terminal_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_dir = root / "requests" / "live_snapshot"
            request_dir.mkdir(parents=True)
            request = request_dir / "current.json"
            request.write_text(json.dumps({
                "request_id": "current",
                "requested_at_beijing": "2026-09-22T17:04:14+08:00",
                "force_refresh": True,
                "require_post_request_snapshot": True,
                "query_intent": "FORMAL_INTRADAY_ANALYSIS",
            }), encoding="utf-8")
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            (state_dir / "CURRENT.json").write_text(json.dumps({
                "captured_at_beijing": "2026-09-22T14:26:01+08:00",
                "latest_snapshot": "data/market/snapshots/2026-09-22_142601.json",
            }), encoding="utf-8")
            (state_dir / "runtime_health.json").write_text(json.dumps({
                "status": "SKIPPED",
                "finished_at": "2026-09-22T17:04:24+08:00",
                "reason": "outside_a_share_capture_window",
            }), encoding="utf-8")
            with patch.object(refresh_gate, "ROOT", root), \\
                 patch.object(refresh_gate, "REQUEST_DIR", request_dir), \\
                 patch.object(refresh_gate, "CURRENT", state_dir / "CURRENT.json"), \\
                 patch.object(refresh_gate, "RUNTIME_HEALTH", state_dir / "runtime_health.json"):
                gate = refresh_gate.build_gate()
            self.assertEqual(gate["request_result"], "EXPIRED")
            self.assertTrue(gate["request_result_terminal"])
            self.assertTrue(gate["terminal_unavailable"])
            self.assertFalse(gate["formal_decision_persist_allowed"])


if __name__ == "__main__":
    unittest.main()

