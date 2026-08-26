from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MarketLevelAndRefreshContractTest(unittest.TestCase):
    def test_market_regime_builder_exists(self):
        text = (ROOT / "scripts" / "build_market_regime_context.py").read_text(encoding="utf-8")
        self.assertIn("上证指数", text)
        self.assertIn("创业板指", text)
        self.assertIn("etf_breadth", text)
        self.assertIn("style_context", text)
        self.assertIn("只分析持仓和观察ETF", text)

    def test_intraday_contract_requires_market_first(self):
        text = (ROOT / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
        self.assertIn('context["market_regime_context"]', text)
        self.assertIn('"market_level_analysis"', text)
        self.assertIn('"required_market_analysis"', text)
        self.assertIn("市场层分析必须先于持仓和候选分析", text)
        self.assertIn("上证指数（000001）", text)
        self.assertIn("创业板指（399006）", text)

    def test_explicit_latest_rejects_pre_request_decision(self):
        text = (ROOT / "scripts" / "refresh_gate.py").read_text(encoding="utf-8")
        self.assertIn("FORMAL_DECISION_PREDATES_REQUESTED_REFRESH", text)
        self.assertIn("_formal_decision_matches_requested_refresh", text)
        self.assertIn("decision_as_of < target", text)


if __name__ == "__main__":
    unittest.main()
