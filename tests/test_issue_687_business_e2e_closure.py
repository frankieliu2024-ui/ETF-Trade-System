from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import process_state_sync_request as state_sync
from scripts.observation_etf_management import validate_observation_management


class BusinessE2EClosureContractTests(unittest.TestCase):
    def test_capital_competition_requires_every_observation_and_discovery(self) -> None:
        value = {
            "next_unit_capital_use": "现金",
            "full_competition_completed": True,
            "releasable_capital_reviewed": True,
            "post_action_deployable_cash": 10000,
            "future_opportunity_capacity": "保留后续Trial/Confirm承载能力",
            "concentration_account_structure_effect": "不增加集中度",
            "selected_state_reason": "当前现金优于可执行候选",
            "new_amount_yuan": 0,
            "zero_amount_decisive_reason": "完整竞争后没有更优可执行用途",
            "compared_capital_states": [
                {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 10000, "why_not_selected": "已选中"},
                {"state_name": "候选", "capital_action": "不部署", "remaining_deployable_cash": 10000, "why_not_selected": "风险收益不足"},
            ],
            "etf_opportunity_reviews": [
                {"security_code": "513180", "category": "OBSERVED_ETF", "opportunity_status": "观察机会", "conclusion": "继续观察", "reason": "假设仍待验证"},
            ],
        }
        error = state_sync.validate_capital_competition_contract(
            value,
            {"positions": []},
            required_etf_opportunities={"513180": "OBSERVED_ETF", "588080": "DISCOVERED_ETF"},
        )
        self.assertIn("588080", error)

        value["etf_opportunity_reviews"].append(
            {"security_code": "588080", "category": "DISCOVERED_ETF", "opportunity_status": "Trial机会", "conclusion": "进入Trial竞争", "reason": "完整MASTER评估后具备资格"}
        )
        self.assertEqual(
            state_sync.validate_capital_competition_contract(
                value,
                {"positions": []},
                required_etf_opportunities={"513180": "OBSERVED_ETF", "588080": "DISCOVERED_ETF"},
            ),
            "",
        )

    def test_query_context_is_existing_opportunity_enumeration_owner(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "data/state").mkdir(parents=True)
        (root / "data/state/query_context.json").write_text(json.dumps({
            "decision_context": {
                "capital_efficiency_ranking": {
                    "comparison_universe": [
                        {"code": None, "category": "CASH"},
                        {"code": "561980", "category": "HELD_ETF"},
                        {"code": "513180", "category": "OBSERVED_ETF"},
                        {"code": "588080", "category": "DISCOVERED_ETF"},
                        {"code": "300750", "category": "ACCOUNT_STOCK"},
                    ]
                }
            }
        }), encoding="utf-8")
        self.assertEqual(
            state_sync.required_etf_opportunity_reviews(root, {"positions": []}),
            {"513180": "OBSERVED_ETF", "588080": "DISCOVERED_ETF"},
        )

    def test_formal_decision_must_resolve_every_current_observation(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing current observations"):
            validate_observation_management(
                {
                    "observation_management": [{
                        "action": "RETAIN",
                        "code": "513180",
                        "name": "恒生科技ETF",
                        "thscode": "513180.SH",
                        "thesis": "科技风险偏好仍待验证",
                        "falsifier": "结构同步失效",
                        "next_decision_information": "下一节点承接",
                        "information_value_reason": "可能改变资本配置",
                    }]
                },
                held_codes={"561980"},
                monitored_codes={"561980", "513180", "159992"},
                require_existing_coverage=True,
            )

    def test_discovery_cannot_admit_an_already_managed_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "ADMIT requires a node-local non-managed ETF"):
            validate_observation_management(
                {
                    "observation_management": [{
                        "action": "ADMIT",
                        "code": "513180",
                        "name": "恒生科技ETF",
                        "thscode": "513180.SH",
                        "thesis": "重复身份",
                        "falsifier": "失效",
                        "next_decision_information": "下一节点",
                        "information_value_reason": "无",
                    }]
                },
                held_codes=set(),
                monitored_codes={"513180"},
            )


if __name__ == "__main__":
    unittest.main()
