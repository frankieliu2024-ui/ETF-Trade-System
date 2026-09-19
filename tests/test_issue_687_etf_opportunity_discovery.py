from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from etf_opportunity_discovery import (\n    compress_homogeneous_exposure,\n    discover_etf,\n    discover_state_delta,\n    route_discovery_result,\n)


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


    def test_delta_event_does_not_repeat_persistent_state(self):
        previous = {"surfaced_states": ["PERSISTENT_TREND"]}
        current = {"code": "999999", "as_of": "2026-09-18", "surfaced_states": ["PERSISTENT_TREND"], "worth_full_evaluation": True}
        delta = discover_state_delta(previous, current)
        self.assertFalse(delta["material_delta"])
        self.assertFalse(delta["worth_full_evaluation"])
        self.assertIsNone(delta["trade_signal"])

    def test_delta_event_surfaces_new_state_entry(self):
        previous = {"surfaced_states": ["PERSISTENT_TREND"]}
        current = {"code": "999999", "as_of": "2026-09-18", "surfaced_states": ["PERSISTENT_TREND", "TREND_CHANGE"], "worth_full_evaluation": True}
        delta = discover_state_delta(previous, current)
        self.assertEqual(delta["entered_states"], ["TREND_CHANGE"])
        self.assertTrue(delta["worth_full_evaluation"])

    def test_holding_and_observation_remain_only_formal_identities(self):
        holding = route_discovery_result({"code": "561980", "worth_full_evaluation": True}, holding_codes={"561980"}, observation_codes={"513180"})
        self.assertEqual(holding["route"], "HOLDING_DELTA_EVIDENCE")
        self.assertFalse(holding["management_identity_change"])
        observed = route_discovery_result({"code": "513180", "worth_full_evaluation": True}, holding_codes={"561980"}, observation_codes={"513180"})
        self.assertEqual(observed["route"], "OBSERVATION_DELTA_EVIDENCE")
        self.assertFalse(observed["auto_promote_to_observation"])

    def test_out_of_pool_hit_is_ephemeral_not_third_identity(self):
        routed = route_discovery_result({"code": "999999", "worth_full_evaluation": True}, holding_codes={"561980"}, observation_codes={"513180"})
        self.assertEqual(routed["route"], "EPHEMERAL_FULL_EVALUATION_INPUT")
        self.assertTrue(routed["ephemeral"])
        self.assertTrue(routed["full_evaluation_ingress"])
        self.assertFalse(routed["management_identity_change"])
        self.assertFalse(routed["auto_promote_to_observation"])

if __name__ == "__main__":
    unittest.main()
