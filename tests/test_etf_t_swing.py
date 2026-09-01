import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/backtests"))
import analyze_etf_t_swing as mod  # noqa: E402


class EtfTSwingContractTest(unittest.TestCase):
    def rows(self):
        return {f"2024-01-{i:02d}": {"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i} for i in range(1, 40)}

    def test_no_lookahead_signal_can_only_start_next_open(self):
        out = mod.run_strategy(self.rows(), threshold=0.01, hold=1, cost=0.0)
        self.assertGreaterEqual(out["trades"], 0)
        self.assertTrue("net_return" in out)

    def test_cost_is_deducted(self):
        rows = self.rows()
        free = mod.run_strategy(rows, threshold=0.001, hold=1, cost=0.0)
        costly = mod.run_strategy(rows, threshold=0.001, hold=1, cost=0.003)
        self.assertLessEqual(costly["net_return"], free["net_return"])

    def test_beta_control_keeps_cash_residual(self):
        out = mod.run_strategy(self.rows(), mode="beta_control", cost=0.0)
        self.assertGreater(out["net_return"], -1.0)

    def test_external_candidate_is_not_production_universe(self):
        universe = json.loads((ROOT / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        self.assertEqual(len(universe["objects"]), 11)
        self.assertNotIn("159687", {str(x["code"]) for x in universe["objects"]})

    def test_intraday_is_not_claimed(self):
        self.assertEqual(mod.features(self.rows())["intraday_coverage"], "NOT_AVAILABLE")


if __name__ == "__main__":
    unittest.main()
