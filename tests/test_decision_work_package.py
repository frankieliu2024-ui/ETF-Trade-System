import unittest

from scripts.business_decision_source import project_decision_response, validate_source
from scripts.build_query_context import (
    _decision_problem_graph,
    _evidence_requirement_plan,
    _decision_work_package,
)


class DecisionWorkPackageTests(unittest.TestCase):
    def test_energy_candidate_gets_candidate_relevant_external_requirements(self):
        graph = _decision_problem_graph(
            [{"code": "159981", "name": "能源化工ETF", "asset_type": "ETF", "market_value": 10000}],
            [],
            {"deployable_cash": 13169.54},
        )
        plan = _evidence_requirement_plan(graph)
        energy = {x["evidence_class"] for x in plan if x["target_problem_id"] == "HOLDING:159981"}
        self.assertTrue({"COMMODITY", "FX", "RATES", "OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"} <= energy)

    def test_semiconductor_candidate_does_not_reuse_energy_commodity_list(self):
        graph = _decision_problem_graph(
            [{"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF", "market_value": 10000}],
            [],
            {"deployable_cash": 13169.54},
        )
        plan = _evidence_requirement_plan(graph)
        tech = {x["evidence_class"] for x in plan if x["target_problem_id"] == "HOLDING:561980"}
        self.assertTrue({"FX", "RATES", "OVERSEAS_INDUSTRY_CHAIN", "HK_INDUSTRY_CHAIN"} <= tech)
        self.assertNotIn("COMMODITY", tech)

    def test_every_position_has_hold_reduce_exit_and_add_capital_problems(self):
        graph = _decision_problem_graph(
            [
                {"code": "300750", "name": "宁德时代", "asset_type": "STOCK"},
                {"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF"},
            ],
            [{"code": "159127", "name": "观察候选"}],
            {"deployable_cash": 1000},
        )
        ids = {x["problem_id"] for x in graph}
        self.assertIn("HOLDING:300750", ids)
        self.assertIn("HELD_ETF_ADD:561980", ids)
        self.assertIn("DISCOVERY:159127", ids)
        holding = next(x for x in graph if x["problem_id"] == "HOLDING:561980")
        self.assertEqual(set(holding["alternatives"]), {"HOLD", "REDUCE", "EXIT"})

    def test_work_package_exposes_missing_required_evidence_and_no_schema_knowledge(self):
        graph = _decision_problem_graph(
            [{"code": "159981", "name": "能源化工ETF", "asset_type": "ETF"}],
            [],
            {},
        )
        plan = _evidence_requirement_plan(graph)
        package = _decision_work_package(graph, plan, [])
        self.assertFalse(package["decision_marginal_stop"]["expansion_complete"])
        self.assertTrue(package["decision_marginal_stop"]["unresolved_required_requirements"])
        self.assertFalse(package["response_contract"]["schema_knowledge_required"])


    def test_structured_response_projects_complete_canonical_source_from_actor_answers(self):
        source = {"request_type": "BUSINESS_DECISION_SOURCE", "request_id": "r1", "parent_request_id": "parent-r1", "decision_id": "d1", "consumed_snapshot": "snap-1"}
        graph = [
            {"problem_id": "RISK_PERMISSION", "security": "risk"},
            {"problem_id": "MAIN_CANDIDATE", "security": "candidate"},
            {"problem_id": "NEXT_UNIT_CAPITAL_USE", "security": "capital"},
            {"problem_id": "HOLDING:159981", "security": "能源化工ETF", "current_quantity": 2800, "alternatives": {"HOLD": "retain", "REDUCE": "release", "EXIT": "exit"}},
            {"problem_id": "HELD_ETF_ADD:159981", "security": "能源化工ETF"},
        ]
        plan = [
            {"requirement_id": "RISK_PERMISSION:A_SHARE_STYLE_FEEDBACK", "target_problem_id": "RISK_PERMISSION", "evidence_class": "A_SHARE_STYLE_FEEDBACK", "required": True},
            {"requirement_id": "HOLDING:159981:GLOBAL_RISK", "target_problem_id": "HOLDING:159981", "evidence_class": "GLOBAL_RISK", "required": True},
            {"requirement_id": "NEXT_UNIT_CAPITAL_USE:ETF_RELATIVE_STRENGTH", "target_problem_id": "NEXT_UNIT_CAPITAL_USE", "evidence_class": "ETF_RELATIVE_STRENGTH", "required": True},
        ]
        answers = {
            pid: {"final_action": "NO_ADD", "capital_comparison": "business comparison", "next_change_condition": "facts change", "evidence_decision_impact": ["ALL_REQUIRED"]}
            for pid in [x["problem_id"] for x in graph]
        }
        answers["RISK_PERMISSION"]["final_action"] = "允许Confirm"
        answers["MAIN_CANDIDATE"].update({"candidate_code": "159981", "candidate_name": "能源化工ETF", "opportunity_status": "Confirm机会"})
        answers["HOLDING:159981"].update({"final_action": "HOLD", "capital_occupancy_reason": "retain right tail", "higher_efficiency_alternative": "cash"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action": "当前现金；下一节点复核159981 Confirm 10000元",
            "new_amount_yuan": 0,
            "post_action_deployable_cash": 13169.54,
            "future_opportunity_capacity": "保留Confirm承载能力",
            "cash_opportunity_cost": "可能错过右尾",
            "alternative_capital_use_review": "已比较全部合法资本状态",
            "concentration_account_structure_effect": "不增加共同风险",
            "selected_state_reason": "休市不可执行",
            "zero_amount_decisive_reason": "休市且需下一节点重评",
            "compared_capital_states": [
                {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 13169.54, "why_not_selected": "已选中", "opportunity_cost_if_selected": "可能错过右尾"},
                {"state_name": "159981 Confirm", "capital_action": "条件新增", "remaining_deployable_cash": 3169.54, "why_not_selected": "当前休市", "opportunity_cost_if_selected": "增加风险"},
            ],
        })
        projected = project_decision_response(source, {"answers": answers}, {"problem_graph": graph, "evidence_requirements": plan})
        checked = validate_source(projected, expected_snapshot="snap-1")
        self.assertEqual(checked["risk_permission"], "允许Confirm")
        self.assertEqual(checked["managed_position_reviews"][0]["capital_use"]["position_capital_states"]["HOLD"], "retain")
        self.assertEqual(checked["decision_evidence_consumption"]["layer_2_a_share_internal"], ["RISK_PERMISSION:A_SHARE_STYLE_FEEDBACK"])
        self.assertEqual(checked["decision_evidence_consumption"]["layer_3_etf_opportunity_capital"], ["NEXT_UNIT_CAPITAL_USE:ETF_RELATIVE_STRENGTH"])
        self.assertNotIn("position_capital_states", answers["HOLDING:159981"])

    def test_structured_response_fails_before_strict_validator_for_missing_business_judgment(self):
        package = {"problem_graph": [{"problem_id": "HOLDING:159981", "security": "能源化工ETF"}], "evidence_requirements": []}
        with self.assertRaisesRegex(ValueError, "holding capital rationale"):
            project_decision_response({}, {"answers": {"HOLDING:159981": {"final_action": "HOLD", "capital_comparison": "cash", "next_change_condition": "risk changes", "evidence_decision_impact": ["x"]}}}, package)


if __name__ == "__main__":
    unittest.main()
