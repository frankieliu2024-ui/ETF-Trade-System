from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_post_market_review import build_close_data_contract


class PostCloseTimeSemanticsTest(unittest.TestCase):
    def test_explicit_close_uses_1500_effective_time_and_keeps_provider_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "data" / "market" / "snapshots" / "close.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({
                "market_date": "2026-08-26",
                "node": "close",
                "planned_time": "15:00",
                "market_phase": "OUTSIDE_SESSION",
                "quality_status": "PASS",
                "rows": [
                    {
                        "quality_status": "PASS",
                        "open": 1.0,
                        "high": 1.1,
                        "low": 0.9,
                        "close": 1.05,
                        "volume": 100,
                        "amount": 1000,
                        "as_of_beijing": "2026-08-26T15:15:45+08:00",
                    }
                ],
            }), encoding="utf-8")
            current = {
                "market_date": "2026-08-26",
                "latest_valid_node": "close",
                "latest_snapshot": "data/market/snapshots/close.json",
            }
            result = build_close_data_contract(root, current)
            self.assertEqual(result["status"], "VERIFIED_SESSION_CLOSE")
            self.assertTrue(result["verified_session_close"])
            self.assertEqual(result["effective_market_time_beijing"], "2026-08-26T15:00:00+08:00")
            self.assertEqual(result["provider_observation_time_max_beijing"], "2026-08-26T15:15:45+08:00")

    def test_non_close_node_cannot_claim_close_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "data" / "market" / "snapshots" / "live.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({
                "market_date": "2026-08-26",
                "node": "live",
                "planned_time": "",
                "market_phase": "POST_CLOSE_GRACE",
                "quality_status": "PASS",
                "rows": [{
                    "quality_status": "PASS",
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.05,
                    "volume": 100,
                    "amount": 1000,
                    "as_of_beijing": "2026-08-26T15:10:00+08:00",
                }],
            }), encoding="utf-8")
            current = {
                "market_date": "2026-08-26",
                "latest_valid_node": "live",
                "latest_snapshot": "data/market/snapshots/live.json",
            }
            result = build_close_data_contract(root, current)
            self.assertEqual(result["status"], "UNVERIFIED")
            self.assertFalse(result["verified_session_close"])

    def test_explicit_close_with_incomplete_or_degraded_rows_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "data" / "market" / "snapshots" / "bad_close.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps({
                "market_date": "2026-08-26",
                "node": "close",
                "planned_time": "15:00",
                "market_phase": "POST_CLOSE_GRACE",
                "quality_status": "DEGRADED",
                "rows": [{
                    "quality_status": "DEGRADED",
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.05,
                    "volume": None,
                    "amount": 1000,
                    "as_of_beijing": "2026-08-26T15:15:45+08:00",
                }],
            }), encoding="utf-8")
            current = {
                "market_date": "2026-08-26",
                "latest_valid_node": "close",
                "latest_snapshot": "data/market/snapshots/bad_close.json",
            }
            result = build_close_data_contract(root, current)
            self.assertEqual(result["status"], "UNVERIFIED")
            self.assertFalse(result["verified_session_close"])
            self.assertEqual(result["effective_market_time_beijing"], "")


if __name__ == "__main__":
    unittest.main()
