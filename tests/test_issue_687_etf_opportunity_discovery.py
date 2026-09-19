from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from etf_opportunity_discovery import compress_homogeneous_exposure, discover_etf


def series(values):
    dates = pd.date_range("2026-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"date": dates, "close": values, "amount": [1_000_000.0] * len(values)})


class Issue687V0DiscoveryTests(unittest.TestCase):
    def test_insufficient_history_is_explicit(self):
        result = discover_etf(series(range(1, 31)), code="000001", name="sample")
        self.assertEqual(result["status"], "INSUFFICIENT_HISTORY")
        self.assertFalse(result["worth_full_evaluation"])

    def test_persistent_trend_surfaces_without_ranking(self):
        values = [100 + i * 0.35 for i in range(90)]
        result = discover_etf(series(values), code="000001", name="sample")
        states = {x["state"] for x in result["surfaced_states"]}
        self.assertIn("PERSISTENT_TREND", states)
        self.assertEqual(result["discovery_semantic"], "WORTH_FULL_EVALUATION")
        self.assertNotIn("score", result)
        self.assertNotIn("rank", result)

    def test_trend_change_strengthening_surfaces(self):
        values = [100 + i * 0.03 for i in range(75)] + [102.25 + i * 1.0 for i in range(15)]
        result = discover_etf(series(values), code="000002", name="sample")
        trend = [x for x in result["surfaced_states"] if x["state"] == "TREND_CHANGE"]
        self.assertTrue(trend)
        self.assertEqual(trend[0]["evidence"]["direction"], "STRENGTHENING")

    def test_recovery_breakout_requires_prior_drawdown_and_recovery(self):
        values = [100.0] * 49 + list(range(100, 79, -1)) + list(range(80, 101))
        result = discover_etf(series(values), code="000003", name="sample")
        states = {x["state"] for x in result["surfaced_states"]}
        self.assertIn("RECOVERY_BREAKOUT", states)

    def test_flat_series_not_surfaced(self):
        result = discover_etf(series([100.0] * 90), code="000004", name="sample")
        self.assertFalse(result["worth_full_evaluation"])

    def test_formal_universe_overlap_is_descriptive_only(self):
        result = discover_etf(series([100 + i * 0.4 for i in range(90)]), code="000005", name="sample", formal_universe={"000005"})
        self.assertTrue(result["formal_universe_overlap"])
        self.assertTrue(result["worth_full_evaluation"])

    def test_homogeneous_compression_is_deterministic_and_scoreless(self):
        candidates = [
            {"code": "A", "worth_full_evaluation": True, "homogeneous_exposure_cluster": "semiconductor"},
            {"code": "B", "worth_full_evaluation": True, "homogeneous_exposure_cluster": "semiconductor"},
            {"code": "C", "worth_full_evaluation": True, "homogeneous_exposure_cluster": "gold"},
            {"code": "D", "worth_full_evaluation": False, "homogeneous_exposure_cluster": "power"},
        ]
        compressed = compress_homogeneous_exposure(candidates)
        self.assertEqual([x["code"] for x in compressed], ["A", "C"])


if __name__ == "__main__":
    unittest.main()
