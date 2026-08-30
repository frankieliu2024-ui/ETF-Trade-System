from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_intraday_path_features as discrete_path
import build_market_structure_context as market_structure
import build_minute_path_features as minute_path

BEIJING = ZoneInfo("Asia/Shanghai")


class LateSessionContextTest(unittest.TestCase):
    def test_discrete_path_waits_until_1430(self):
        points = [
            {"as_of_beijing": "2026-08-28T14:20:00+08:00", "price": 1.00},
            {"as_of_beijing": "2026-08-28T14:25:00+08:00", "price": 1.01},
        ]
        result = discrete_path.late_session_context(points)
        self.assertEqual(result["status"], "NOT_YET_AVAILABLE")
        self.assertIsNone(result["move_from_reference_pct"])

    def test_minute_path_uses_first_pit_point_at_or_after_1430(self):
        points = [
            {"dt": datetime(2026, 8, 28, 14, 29, tzinfo=BEIJING), "price": 1.00},
            {"dt": datetime(2026, 8, 28, 14, 30, tzinfo=BEIJING), "price": 1.01},
            {"dt": datetime(2026, 8, 28, 14, 55, tzinfo=BEIJING), "price": 1.02},
        ]
        result = minute_path.late_session_context(points)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["reference_as_of_beijing"], "2026-08-28T14:30:00+08:00")
        self.assertAlmostEqual(result["move_from_reference_pct"], (1.02 / 1.01 - 1.0) * 100.0, places=4)


class FormalIntradayPathRiskEvidenceTest(unittest.TestCase):
    def test_tail_rally_only_raises_review(self):
        item = {"code": "588000", "price": 1.02, "current_return_pct": 2.0, "as_of_beijing": "2026-08-28T14:55:00+08:00", "intraday_context": {"day_range_position": 0.8}}
        path = {
            "production_source": "TENCENT_1M",
            "sampling": {"coverage": "HIGH"},
            "path_high": 1.025,
            "path_high_as_of_beijing": "2026-08-28T14:50:00+08:00",
            "latest_as_of_beijing": "2026-08-28T14:55:00+08:00",
            "retreat_from_path_high_pct": -0.4878,
            "late_session_context": {"status": "READY", "move_from_reference_pct": 0.9},
        }
        evidence = market_structure._path_risk_evidence(item, path, "2026-08-28")
        self.assertTrue(evidence["risk_review_active"])
        self.assertIn("LATE_SESSION_RALLY_REVIEW", evidence["active_components"])
        self.assertFalse(evidence["can_generate_decision_independently"])
        self.assertIsNone(evidence["trade_signal"])

    def test_spike_reversal_no_reclaim_raises_review_without_order(self):
        item = {"code": "159781", "price": 1.00, "current_return_pct": 0.0, "as_of_beijing": "2026-08-28T14:55:00+08:00", "intraday_context": {"day_range_position": 0.2}}
        path = {
            "production_source": "DISCRETE_SNAPSHOT_PATH",
            "sampling": {"coverage": "MEDIUM"},
            "path_high": 1.03,
            "path_high_as_of_beijing": "2026-08-28T10:00:00+08:00",
            "latest_as_of_beijing": "2026-08-28T14:55:00+08:00",
            "retreat_from_path_high_pct": -2.9126,
            "late_session_context": {"status": "READY", "move_from_reference_pct": -0.2},
        }
        evidence = market_structure._path_risk_evidence(item, path, "2026-08-28")
        self.assertTrue(evidence["risk_review_active"])
        self.assertIn("SPIKE_REVERSAL_NO_RECLAIM_REVIEW", evidence["active_components"])
        self.assertEqual(evidence["direction"], "INCREASE_RISK_REWARD_REVIEW")
        self.assertFalse(evidence["automatic_trade"])

    def test_low_quality_path_degrades_and_cannot_carry_stale_direction(self):
        item = {"code": "515880", "price": 1.00, "current_return_pct": 0.0, "intraday_context": {"day_range_position": 0.2}}
        path = {"sampling": {"coverage": "LOW"}, "late_session_context": {"status": "READY", "move_from_reference_pct": 2.0}}
        evidence = market_structure._path_risk_evidence(item, path, "2026-08-28")
        self.assertEqual(evidence["status"], "DEGRADED")
        self.assertIsNone(evidence["risk_review_active"])
        self.assertFalse(evidence["use_as_decision_evidence"])


if __name__ == "__main__":
    unittest.main()
