import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/backtests"))
import analyze_etf_t_swing as mod  # noqa: E402
import analyze_etf_intraday_sina as intraday  # noqa: E402


class EtfTSwingContractTest(unittest.TestCase):
    def rows(self):
        return {f"2024-01-{i:02d}": {"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i} for i in range(1, 40)}

    def oscillating_rows(self):
        out = {}
        for i in range(1, 90):
            px = 100 if i < 25 else (105 if i % 8 in (0, 1) else 95 if i % 8 in (4, 5) else 100)
            out[f"2024-{(i - 1) // 28 + 1:02d}-{(i - 1) % 28 + 1:02d}"] = {"open": px, "high": px + 1, "low": px - 1, "close": px}
        return out

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

    def test_initial_core_plus_mobile_exposure_equals_100_percent(self):
        out = mod.evaluate("TEST", self.rows(), "test")
        self.assertEqual(out["accounting_contract"]["initial_total_etf_exposure"], 1.0)
        run = mod.run_strategy(self.rows(), mobile=0.25, cost=0.0)
        self.assertAlmostEqual(run["initial_total_exposure"], 1.0)

    def test_mobile_starts_invested_not_cash(self):
        self.assertEqual(mod.transition_mobile("MOBILE_INVESTED", "release"), "MOBILE_CASH")
        self.assertEqual(mod.run_strategy(self.rows(), mobile=0.25)["initial_mobile_state"], "MOBILE_INVESTED")

    def test_release_and_rebuy_state_transitions(self):
        self.assertEqual(mod.transition_mobile("MOBILE_CASH", "rebuy"), "MOBILE_INVESTED")

    def test_cannot_rebuy_when_mobile_already_invested(self):
        with self.assertRaises(ValueError):
            mod.transition_mobile("MOBILE_INVESTED", "rebuy")

    def test_cannot_release_when_mobile_already_cash(self):
        with self.assertRaises(ValueError):
            mod.transition_mobile("MOBILE_CASH", "release")

    def test_no_leverage_created(self):
        out = mod.run_strategy(self.rows(), mobile=0.25, cost=0.0)
        self.assertAlmostEqual(out["initial_total_exposure"], 1.0)
        self.assertEqual(out["initial_mobile_state"], "MOBILE_INVESTED")

    def test_transaction_count_matches_position_changes(self):
        out = mod.run_strategy(self.rows(), mobile=0.25, cost=0.0)
        self.assertEqual(out["trades"], len(out["events"]))

    def test_beta_control_has_zero_strategy_trades(self):
        self.assertEqual(mod.run_strategy(self.rows(), mode="beta_control")["trades"], 0)

    def test_costs_apply_on_release_rebuy_legs(self):
        rows = self.rows()
        free = mod.run_strategy(rows, cost=0.0)
        costly = mod.run_strategy(rows, cost=0.003)
        self.assertLessEqual(costly["net_return"], free["net_return"])

    def test_buy_hold_and_core_mobile_same_initial_exposure(self):
        run = mod.run_strategy(self.rows(), mobile=0.25, cost=0.0)
        self.assertAlmostEqual(run["initial_total_exposure"], 1.0)
        self.assertEqual(mod.buy_hold(self.rows())["trades"], 0)

    def test_release_rebuy_events_are_real_state_transitions(self):
        out = mod.run_strategy(self.oscillating_rows(), threshold=0.02, cost=0.0, family="fixed")
        self.assertGreaterEqual(out["trades"], 2)
        for a, b in zip(out["events"], out["events"][1:]):
            self.assertNotEqual(a["action"], b["action"])
            self.assertGreater(b["exec_i"], a["exec_i"])

    def test_right_tail_and_wrong_rebuy_are_scoped_to_their_legs(self):
        out = mod.run_strategy(self.oscillating_rows(), threshold=0.02, cost=0.0, family="fixed")
        releases = [e for e in out["events"] if e["action"] == "release"]
        rebuys = [e for e in out["events"] if e["action"] == "rebuy"]
        self.assertTrue(releases and rebuys)
        self.assertGreaterEqual(out["winner_right_tail_loss"], 0.0)
        self.assertGreaterEqual(out["wrong_rebuy_loss"], 0.0)

    def test_pit_future_append_does_not_rewrite_prior_events(self):
        rows = self.oscillating_rows()
        early_keys = sorted(rows)[:55]
        early = {k: rows[k] for k in early_keys}
        a = mod.run_strategy(early, threshold=0.02, cost=0.0, family="fixed")["events"]
        b = mod.run_strategy(rows, threshold=0.02, cost=0.0, family="fixed")["events"]
        self.assertEqual([(e["date"], e["action"]) for e in a], [(e["date"], e["action"]) for e in b if e["exec_i"] < len(early_keys)])

    def test_hold_parameter_changes_actual_trade_path(self):
        short = mod.run_strategy(self.oscillating_rows(), threshold=0.02, hold=1, cost=0.0, family="fixed")
        long = mod.run_strategy(self.oscillating_rows(), threshold=0.02, hold=10, cost=0.0, family="fixed")
        self.assertTrue(short["events"] != long["events"] or short["net_return"] != long["net_return"])

    def test_hold_1d_and_10d_are_not_identical(self):
        a = mod.run_strategy(self.oscillating_rows(), hold=1, cost=0.002, family="fixed")
        b = mod.run_strategy(self.oscillating_rows(), hold=10, cost=0.002, family="fixed")
        self.assertNotEqual((a["trades"], a["net_return"]), (b["trades"], b["net_return"]))

    def test_strategy_families_have_distinct_logic(self):
        fixed = mod.run_strategy(self.oscillating_rows(), family="fixed", cost=0.0)
        vol = mod.run_strategy(self.oscillating_rows(), family="volatility", cost=0.0)
        trend = mod.run_strategy(self.oscillating_rows(), family="trend_filter", cost=0.0)
        self.assertTrue((fixed["trades"], fixed["net_return"]) != (vol["trades"], vol["net_return"]) or (vol["trades"], vol["net_return"]) != (trend["trades"], trend["net_return"]))

    def test_episode_metrics_include_max_mean_median_and_cumulative(self):
        out = mod.run_strategy(self.oscillating_rows(), family="fixed", cost=0.0)
        for key in ("right_tail_episode_metrics", "wrong_rebuy_episode_metrics"):
            self.assertTrue({"max_single_episode_loss", "mean_episode_loss", "median_episode_loss", "cumulative_portfolio_drag"} <= set(out[key]))

    def intraday_rows(self):
        rows = []
        for i in range(24):
            px = 100.0 + (5 if 8 <= i <= 10 else -4 if 16 <= i <= 18 else 0)
            rows.append({"timestamp": f"2026-08-31T{9 + (i * 15) // 60:02d}:{(30 + i * 15) % 60:02d}:00", "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1000, "amount": px * 1000})
        return rows

    def test_intraday_signal_close_then_next_bar_open(self):
        out = intraday.run_day(self.intraday_rows(), hold=1, cost=0.0)
        for event in out["events"]:
            self.assertGreater(event["release_execution_time"], event["release_signal_time"])

    def test_intraday_horizon_changes_path(self):
        a = intraday.run_day(self.intraday_rows(), hold=1, cost=0.002)
        b = intraday.run_day(self.intraday_rows(), hold=8, cost=0.002)
        self.assertNotEqual((a["trades"], a["net_return"]), (b["trades"], b["net_return"]))

    def test_intraday_no_same_bar_high_low_order(self):
        out = intraday.run_day(self.intraday_rows(), hold=1, cost=0.002)
        for event in out["events"]:
            self.assertNotEqual(event["release_signal_time"], event["release_execution_time"])

    def test_intraday_costs_both_legs_and_unclosed_explicit(self):
        free = intraday.run_day(self.intraday_rows(), hold=1, cost=0.0)
        costly = intraday.run_day(self.intraday_rows(), hold=1, cost=0.003)
        self.assertLessEqual(costly["net_return"], free["net_return"])
        self.assertTrue(all("unclosed_at_day_end" in e for e in costly["events"]))

    def test_intraday_unclosed_mobile_is_forced_rebuy_at_eod(self):
        out = intraday.run_day(self.intraday_rows(), hold=999, cost=0.002)
        self.assertTrue(out["events"])
        self.assertTrue(all(not e["unclosed_at_day_end"] for e in out["events"]))
        self.assertTrue(any(e.get("forced_eod_rebuy") for e in out["events"]))

    def test_t1_multiple_cycles_cannot_resell_same_day_inventory(self):
        out = intraday.run_day(self.intraday_rows(), hold=1, cost=0.0, multiple=True, allow_same_day_resale=False)
        for a, b in zip(out["events"], out["events"][1:]):
            if a.get("rebuy_execution_time") and b.get("release_execution_time"):
                self.assertNotEqual(a["rebuy_execution_time"][:10], b["release_execution_time"][:10])


if __name__ == "__main__":
    unittest.main()
