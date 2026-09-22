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


if __name__ == "__main__":
    unittest.main()

