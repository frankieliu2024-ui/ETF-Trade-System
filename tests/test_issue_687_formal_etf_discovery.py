from __future__ import annotations

import unittest

from scripts import formal_etf_opportunity_discovery as discovery
from scripts import state_manager


def history(code_shift: float = 0.0) -> list[dict]:
    rows = []
    for i in range(70):
        close = 1.0 + code_shift + i * 0.004
        rows.append({
            "date": f"2026-{6 + (i // 28):02d}-{1 + (i % 28):02d}",
            "open": close * 0.998,
            "close": close,
            "high": close * 1.003,
            "low": close * 0.997,
            "volume": 1_000_000,
            "amount": 30_000_000 + i * 1000,
        })
    return rows


class FormalEtfOpportunityDiscoveryTest(unittest.TestCase):
    def test_managed_etf_keeps_identity_overlay_when_discovered(self) -> None:
        spot = [{
            "code": "510300", "name": "300ETF", "market_id": 1, "price": 4.2,
            "change_pct": 1.0, "amount": 100_000_000, "volume_ratio": 1.2,
            "return_60d_pct": 10.0, "return_ytd_pct": 15.0,
        }]
        result = discovery.discover_formal_candidates(
            None, market_date="2026-09-19", managed_codes={"510300"},
            spot_rows=spot, history_by_code={"510300": history()},
        )
        self.assertEqual(result["managed_excluded_count"], 0)
        self.assertEqual(result["candidates"][0]["management_identity"], "MANAGED")

    def test_out_of_pool_candidate_gets_evaluation_not_trade_permission(self) -> None:
        spot = [{
            "code": "588080", "name": "科创50ETF易方达", "market_id": 1, "price": 1.4,
            "change_pct": 1.2, "amount": 120_000_000, "volume_ratio": 1.1,
            "return_60d_pct": 12.0, "return_ytd_pct": 20.0,
        }]
        result = discovery.discover_formal_candidates(
            None, market_date="2026-09-19", managed_codes=set(),
            spot_rows=spot, history_by_code={"588080": history()},
        )
        self.assertEqual(result["candidate_count"], 1)
        item = result["candidates"][0]
        self.assertEqual(item["category"], "OBSERVATION_EVALUATION_INPUT")
        self.assertEqual(item["eligibility"], "OBSERVATION_FULL_EVALUATION")
        self.assertIsNone(item["management_identity"])
        self.assertFalse(item["auto_promote_to_observation"])
        self.assertFalse(item["trial_confirm_permission"])
        self.assertIsNone(item["trade_signal"])

    def test_discovery_uses_interpretable_states_not_hidden_score(self) -> None:
        states = discovery.classify_states(history())
        self.assertTrue(any(x["state"] == "PERSISTENT_TREND" for x in states))
        result = discovery.discover_formal_candidates(
            None,
            market_date="2026-09-19",
            managed_codes=set(),
            spot_rows=[{
                "code": "588080", "name": "科创50ETF易方达", "market_id": 1, "price": 1.4,
                "change_pct": 1.0, "amount": 100_000_000, "volume_ratio": 1.0,
                "return_60d_pct": 10.0, "return_ytd_pct": 12.0,
            }],
            history_by_code={"588080": history()},
        )
        self.assertTrue(result["selection_contract"]["no_hidden_score"])
        self.assertTrue(result["selection_contract"]["no_gain_ranking"])
        self.assertNotIn("score", result["candidates"][0])


    def test_discovery_quote_is_not_treated_as_formal_quote(self) -> None:
        formal = {"status": "READY", "coverage_status": "COMPLETE", "candidates": [{
            "code": "588080", "name": "科创50ETF易方达",
            "discovery_spot": {"price": 1.4},
            "historical_context": {"status": "READY"},
        }]}
        routed = discovery.attach_formal_quotes(formal, {"quotes": [{
            "symbol": "588080", "latest_price": 1.401, "quality_status": "PASS", "source": "tencent_qq",
        }]})
        item = routed["candidates"][0]
        self.assertEqual(item["formal_quote_status"], "READY")
        self.assertEqual(item["formal_quote"]["source"], "tencent_qq")
        self.assertNotEqual(item["formal_quote"]["latest_price"], item["discovery_spot"]["price"])

    def test_capital_comparison_keeps_discovery_eligibility_only(self) -> None:
        base = {
            "comparison_universe": [{"code": None, "category": "CASH"}],
            "ordered_candidates": [{"code": None, "category": "CASH"}],
            "next_unit_capital_use": "old",
        }
        formal = {"status": "READY", "coverage_status": "COMPLETE", "candidates": [{
            "code": "588080", "name": "科创50ETF易方达", "display_name": "科创50ETF易方达（588080）",
            "historical_context": {"status": "READY"}, "formal_quote": {"latest_price": 1.4}, "formal_quote_status": "READY",
            "comparison_basis": ["历史结构", "当前结构"],
        }]}
        out = state_manager._extend_capital_comparison_with_discovery(base, formal)
        self.assertEqual(out["comparison_universe"], base["comparison_universe"])
        self.assertEqual(out["observation_eligibility_inputs"][0]["code"], "588080")
        self.assertEqual(out["formal_discovery_ingress_status"], "ELIGIBILITY_ONLY")

    def test_existing_managed_object_is_not_duplicated_in_capital_comparison(self) -> None:
        base = {
            "comparison_universe": [{"code": "588080", "category": "OBSERVED_ETF"}],
            "ordered_candidates": [{"code": "588080", "category": "OBSERVED_ETF"}],
        }
        formal = {"coverage_status": "COMPLETE", "candidates": [{"code": "588080", "historical_context": {"status": "READY"}}]}
        out = state_manager._extend_capital_comparison_with_discovery(base, formal)
        self.assertEqual(len(out["comparison_universe"]), 1)
        self.assertEqual(out["comparison_universe"][0]["category"], "OBSERVED_ETF")

    def test_partial_discovery_does_not_enter_formal_capital_competition(self) -> None:
        base = {
            "comparison_universe": [{"code": None, "category": "CASH"}],
            "ordered_candidates": [{"code": None, "category": "CASH"}],
        }
        formal = {
            "status": "DEGRADED", "coverage_status": "PARTIAL",
            "candidates": [{"code": "588080", "historical_context": {"status": "READY"}, "formal_quote_status": "READY"}],
        }
        out = state_manager._extend_capital_comparison_with_discovery(base, formal)
        self.assertEqual(out["comparison_universe"], base["comparison_universe"])
        self.assertFalse(out["formal_discovery_included"])
        self.assertEqual(out["formal_discovery_ingress_status"], "BLOCKED_INCOMPLETE_COVERAGE")


if __name__ == "__main__":
    unittest.main()
