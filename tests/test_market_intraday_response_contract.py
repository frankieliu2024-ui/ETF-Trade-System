from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FormalIntradayResponseContractTest(unittest.TestCase):
    def test_decision_context_builder_exposes_response_contract(self):
        text = (ROOT / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
        self.assertIn('context["formal_intraday_response_contract"]', text)
        self.assertIn('context["formal_intraday_context_completeness"]', text)
        self.assertIn('"DECISION_CONTEXT_INCOMPLETE"', text)

    def test_previous_candidate_cannot_auto_carry(self):
        text = (ROOT / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
        self.assertIn("previous_main_candidate只作连续性参考，不得自动继承", text)
        self.assertIn("每个正式盘中节点必须重新进入统一比较", text)

    def test_required_structure_evidence_is_explicit(self):
        text = (ROOT / "scripts" / "build_state_context.py").read_text(encoding="utf-8")
        for required in (
            "historical_position_and_trend",
            "intraday_path_and_extreme_sequence",
            "time_normalized_turnover_acceptance",
            "risk_reward_or_capital_efficiency",
        ):
            self.assertIn(required, text)
        self.assertIn("横截面涨幅、名次与相对强弱仅作验证证据", text)


if __name__ == "__main__":
    unittest.main()
