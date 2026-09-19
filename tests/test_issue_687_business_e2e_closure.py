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
            required_etf_opportunities={"513180": "OBSERVED_ETF", "588080": "OBSERVATION_EVALUATION_INPUT"},
        )
        self.assertIn("588080", error)

        value["etf_opportunity_reviews"].append(
            {"security_code": "588080", "category": "OBSERVATION_EVALUATION_INPUT", "opportunity_status": "Trial机会", "conclusion": "进入Trial竞争", "reason": "完整MASTER评估后具备资格"}
        )
        self.assertEqual(
            state_sync.validate_capital_competition_contract(
                value,
                {"positions": []},
                required_etf_opportunities={"513180": "OBSERVED_ETF", "588080": "OBSERVATION_EVALUATION_INPUT"},
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
                        {"code": "588080", "category": "OBSERVATION_EVALUATION_INPUT"},
                        {"code": "300750", "category": "ACCOUNT_STOCK"},
                    ]
                }
            }
        }), encoding="utf-8")
        self.assertEqual(
            state_sync.required_etf_opportunity_reviews(root, {"positions": []}),
            {"513180": "OBSERVED_ETF", "588080": "OBSERVATION_EVALUATION_INPUT"},
        )
        self.assertEqual(
            state_sync.required_etf_opportunity_reviews(root, {"positions": []}, "2026-09-17"),
            {},
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

    def test_20260918_shadow_discovery_can_enter_formal_capital_competition_without_persistence(self) -> None:
        from scripts import formal_etf_opportunity_discovery as discovery

        def history(start: float, end: float, n: int = 70) -> list[dict]:
            rows = []
            for i in range(n):
                close = start + (end - start) * i / (n - 1)
                rows.append({
                    "date": f"2026-06-{(i % 28) + 1:02d}" if i < 28 else (
                        f"2026-07-{((i-28) % 28) + 1:02d}" if i < 56 else f"2026-09-{(i-56)+1:02d}"
                    ),
                    "close": close,
                    "amount": 200000000,
                })
            return rows

        # Bounded PIT shadow: the synthetic bar set represents evidence available
        # strictly before 2026-09-18.  The discovered object is outside the
        # persistent managed set and receives no management/trading authority.
        spot = [{
            "code": "588080", "name": "池外ETF", "market_id": 1,
            "price": 1.30, "change_pct": 1.2, "amount": 300000000,
            "return_60d_pct": 18.0, "volume_ratio": 1.2,
        }]
        result = discovery.discover_formal_candidates(
            Path("."),
            market_date="2026-09-18",
            managed_codes={"561980", "588000", "159781", "159941", "513180"},
            spot_rows=spot,
            history_by_code={"588080": history(1.0, 1.30)},
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual([x["code"] for x in result["candidates"]], ["588080"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["category"], "OBSERVATION_EVALUATION_INPUT")
        self.assertEqual(candidate["eligibility"], "OBSERVATION_FULL_EVALUATION")
        self.assertIsNone(candidate["management_identity"])
        self.assertFalse(candidate["trial_confirm_permission"])
        self.assertFalse(candidate["decision_output_generated"])

        required = {"513180": "OBSERVED_ETF", "588080": "OBSERVATION_EVALUATION_INPUT"}
        capital = {
            "next_unit_capital_use": "现金",
            "full_competition_completed": True,
            "releasable_capital_reviewed": True,
            "post_action_deployable_cash": 18009.45,
            "future_opportunity_capacity": "保留后续Trial/Confirm承载能力",
            "concentration_account_structure_effect": "不增加集中度",
            "selected_state_reason": "完整竞争后现金仍优于当前可执行候选",
            "new_amount_yuan": 0,
            "zero_amount_decisive_reason": "完整资本竞争后无更优合法用途",
            "compared_capital_states": [
                {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 18009.45, "why_not_selected": "已选中"},
                {"state_name": "池外ETF（588080）", "capital_action": "不部署", "remaining_deployable_cash": 18009.45, "why_not_selected": "发现只授予完整评估资格；MASTER证据不足"},
            ],
            "etf_opportunity_reviews": [
                {"security_code": "513180", "category": "OBSERVED_ETF", "opportunity_status": "观察机会", "conclusion": "继续观察", "reason": "跨节点假设仍有信息价值"},
                {"security_code": "588080", "category": "OBSERVATION_EVALUATION_INPUT", "opportunity_status": "观察机会", "conclusion": "本节点不部署", "reason": "已进入完整评估但不足以形成合法新增资本动作"},
            ],
        }
        self.assertEqual(
            state_sync.validate_capital_competition_contract(
                capital, {"positions": []}, required_etf_opportunities=required
            ),
            "",
        )

        # A current formal node cannot silently keep an Observation, and a
        # discovered object is not forced through Observation before evaluation.
        validate_observation_management(
            {
                "observation_management": [{
                    "action": "RETAIN",
                    "code": "513180",
                    "name": "观察ETF",
                    "thscode": "513180.SH",
                    "thesis": "跨节点假设仍有效",
                    "falsifier": "结构同步失效",
                    "next_decision_information": "下一节点验证",
                    "information_value_reason": "可能改变资本配置",
                }]
            },
            held_codes={"561980", "588000", "159781", "159941"},
            monitored_codes={"561980", "588000", "159781", "159941", "513180"},
            require_existing_coverage=True,
        )


    def test_discovery_is_eligibility_only_not_final_capital_competitor(self) -> None:
        from scripts import state_manager
        base = {"comparison_universe": [{"code": None, "category": "CASH"}, {"code": "561980", "category": "HELD_ETF"}]}
        discovery = {"coverage_status": "PARTIAL", "candidates": [{
            "code": "588080", "name": "池外ETF", "historical_context": {"status": "READY"},
            "formal_quote_status": "READY", "formal_quote": {"latest_price": 1.2},
        }]}
        out = state_manager._extend_capital_comparison_with_discovery(base, discovery)
        self.assertEqual([x.get("code") for x in out["comparison_universe"]], [None, "561980"])
        self.assertEqual(out["observation_eligibility_inputs"][0]["code"], "588080")
        self.assertEqual(out["formal_discovery_ingress_status"], "ELIGIBILITY_ONLY")

    def test_eligibility_admit_requires_ready_quote_and_then_becomes_legal_observation(self) -> None:
        inputs = {
            "588080": {"code": "588080", "formal_quote_status": "READY"},
            "588090": {"code": "588090", "formal_quote_status": "UNAVAILABLE"},
        }
        admitted, error = state_sync.validate_observation_eligibility_reviews({
            "observation_eligibility_reviews": [
                {"code": "588080", "disposition": "ADMIT", "reason": "完整证据支持持续观察"},
                {"code": "588090", "disposition": "REJECT", "reason": "正式即时行情不可用"},
            ]
        }, inputs)
        self.assertEqual(error, "")
        self.assertEqual(admitted, {"588080"})
        required = state_sync.legal_observation_reviews({
            "observation_management": [
                {"code": "513180", "action": "EXIT"},
                {"code": "159992", "action": "RETAIN"},
                {"code": "588080", "action": "ADMIT"},
            ]
        }, {"513180", "159992"}, admitted)
        self.assertEqual(required, {"159992": "OBSERVED_ETF", "588080": "OBSERVED_ETF"})

    def test_unready_discovery_cannot_be_admitted(self) -> None:
        admitted, error = state_sync.validate_observation_eligibility_reviews({
            "observation_eligibility_reviews": [
                {"code": "588090", "disposition": "ADMIT", "reason": "不应通过"},
            ]
        }, {"588090": {"code": "588090", "formal_quote_status": "UNAVAILABLE"}})
        self.assertEqual(admitted, set())
        self.assertIn("without READY formal quote", error)

if __name__ == "__main__":
    unittest.main()
