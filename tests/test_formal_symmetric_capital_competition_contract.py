from pathlib import Path
import unittest

from scripts.build_query_context import (
    _decision_problem_graph,
    _decision_work_package,
    _evidence_requirement_plan,
)


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "docs" / "Formal Decision执行与用户回复协议.md"


class SymmetricCapitalCompetitionContractTests(unittest.TestCase):
    def test_work_package_requires_zero_base_holding_comparison(self):
        graph = _decision_problem_graph(
            [
                {
                    "code": "159981",
                    "name": "能源化工ETF",
                    "asset_type": "ETF",
                    "market_value": 10000,
                }
            ],
            [],
            {"deployable_cash": 1000},
        )
        contract = _decision_work_package(
            graph, _evidence_requirement_plan(graph), []
        )["response_contract"]

        self.assertEqual(contract["holding_capital_competition_policy"], "RELEASE_FIRST")
        self.assertEqual(
            contract["holding_counterfactual_policy"],
            "ZERO_BASE_HOLD_REDUCE_EXIT_COMPARE",
        )
        self.assertTrue(
            {
                "zero_base_reentry_test",
                "hold_over_cash_and_eligible_opportunity_reason",
                "reduce_over_hold_reason",
                "exit_over_hold_reason",
            }
            <= set(contract["holding_additional_fields"])
        )

    def test_work_package_requires_trial_confirm_zero_comparison_without_identity_promotion(self):
        graph = _decision_problem_graph(
            [],
            [{"code": "159127", "name": "发现候选"}],
            {"deployable_cash": 1000},
            observation_inputs=[{"code": "513180", "name": "恒生科技ETF"}],
        )
        contract = _decision_work_package(
            graph, _evidence_requirement_plan(graph), []
        )["response_contract"]

        self.assertEqual(
            contract["opportunity_capital_competition_policy"],
            "TRIAL_CONFIRM_ZERO_COMPARE",
        )
        self.assertTrue(
            {
                "trial_eligibility_and_trial_over_zero_reason",
                "direct_confirm_eligibility_and_confirm_over_trial_reason",
                "zero_deployment_decisive_reason_if_zero",
                "capital_alternative_if_trial_or_confirm",
                "observable_requalification_condition",
                "next_review_node",
            }
            <= set(contract["opportunity_review_fields"])
        )
        self.assertTrue(
            {
                "why_not_trial",
                "why_not_confirm",
                "why_zero_is_currently_better",
            }
            <= set(contract["opportunity_nonqualification_fields"])
        )

        discovery = next(
            item for item in graph if item["problem_id"] == "DISCOVERY:159127"
        )
        observation = next(
            item for item in graph if item["problem_id"] == "OBSERVATION:513180"
        )
        for item in (discovery, observation):
            self.assertIn("直接Confirm和0元", item["required_business_judgment"])
            self.assertIn("不得默认0元", item["required_business_judgment"])
            self.assertIn("自动升级身份", item["required_business_judgment"])

    def test_formal_protocol_preserves_separate_sell_and_buy_decisions(self):
        text = FORMAL.read_text(encoding="utf-8")
        self.assertIn("双向对称资本竞争与反事实检验", text)
        self.assertIn(
            "旧仓减仓/退出与新机会Trial/Confirm分别决议", text
        )
        self.assertIn("不新增独立输出章节、机会身份或交易节点", text)
        self.assertIn("所有交易仍由用户确认并人工执行", text)


if __name__ == "__main__":
    unittest.main()
