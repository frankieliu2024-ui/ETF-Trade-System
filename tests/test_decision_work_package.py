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
        self.assertTrue(holding["capital_efficiency_release_required"])
        self.assertIn("资本效率型释放", holding["required_business_judgment"])

    def test_evidence_plan_scopes_object_and_capital_facts_by_problem_family(self):
        graph = _decision_problem_graph(
            [{"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF", "market_value": 10000}],
            [{"code": "159127", "name": "观察候选"}],
            {"deployable_cash": 1000},
        )
        plan = _evidence_requirement_plan(graph)
        by_problem = {}
        for item in plan:
            by_problem.setdefault(item["target_problem_id"], set()).add(item["evidence_class"])

        self.assertIn("FULL_MARKET_DISCOVERY", by_problem["HOLDING:561980"])
        self.assertIn("TEMPORARY_DISCOVERY_CANDIDATE", by_problem["HOLDING:561980"])
        self.assertIn("RELEASABLE_CAPITAL", by_problem["HOLDING:561980"])
        self.assertIn("CASH", by_problem["HOLDING:561980"])
        self.assertNotIn("ACCOUNT_STOCK", by_problem["DISCOVERY:159127"])
        self.assertNotIn("HOLDING_ETF", by_problem["DISCOVERY:159127"])
        self.assertNotIn("RELEASABLE_CAPITAL", by_problem["RISK_PERMISSION"])
        self.assertIn("FULL_MARKET_DISCOVERY", by_problem["MAIN_CANDIDATE"])
        self.assertIn("RELEASABLE_CAPITAL", by_problem["NEXT_UNIT_CAPITAL_USE"])
        self.assertIn("HOLDING_ADDITIONAL_CAPITAL", by_problem["HELD_ETF_ADD:561980"])

    def test_base_stock_replacement_receives_full_capital_competition_evidence(self):
        graph = [{
            "problem_id": "BASE_STOCK_REPLACEMENT:600900",
            "security": "600900",
            "decision_object": "打新底仓替换候选",
        }]
        plan = _evidence_requirement_plan(graph)
        classes = {item["evidence_class"] for item in plan}

        self.assertIn("ACCOUNT_STOCK", classes)
        self.assertIn("HOLDING_ETF", classes)
        self.assertIn("FULL_MARKET_DISCOVERY", classes)
        self.assertIn("TEMPORARY_DISCOVERY_CANDIDATE", classes)
        self.assertIn("CASH", classes)
        self.assertIn("RELEASABLE_CAPITAL", classes)

    def test_scoped_plan_is_smaller_than_legacy_full_cartesian_plan(self):
        graph = _decision_problem_graph(
            [
                {"code": "300750", "name": "宁德时代", "asset_type": "STOCK"},
                {"code": "561980", "name": "半导体设备ETF", "asset_type": "ETF"},
            ],
            [{"code": "159127", "name": "观察候选"}],
            {"deployable_cash": 1000},
        )
        plan = _evidence_requirement_plan(graph)
        legacy_baseline_classes = 22
        self.assertLess(len(plan), len(graph) * legacy_baseline_classes)
        self.assertEqual({x["problem_id"] for x in graph}, {x["target_problem_id"] for x in plan})

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
            {"requirement_id": "RISK_PERMISSION:A_SHARE_STYLE_FEEDBACK", "target_problem_id": "RISK_PERMISSION", "evidence_class": "A_SHARE_STYLE_FEEDBACK", "required": True, "satisfaction": "SATISFIED"},
            {"requirement_id": "HOLDING:159981:GLOBAL_RISK", "target_problem_id": "HOLDING:159981", "evidence_class": "GLOBAL_RISK", "required": True, "satisfaction": "SATISFIED"},
            {"requirement_id": "NEXT_UNIT_CAPITAL_USE:ETF_RELATIVE_STRENGTH", "target_problem_id": "NEXT_UNIT_CAPITAL_USE", "evidence_class": "ETF_RELATIVE_STRENGTH", "required": True, "satisfaction": "SATISFIED"},
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

    def test_new_holding_contract_requires_separate_capital_efficiency_release_assessment(self):
        package = {
            "problem_graph": [{
                "problem_id": "HOLDING:159981",
                "security": "能源化工ETF",
                "capital_efficiency_release_required": True,
            }],
            "evidence_requirements": [],
        }
        answer = {
            "final_action": "HOLD",
            "capital_comparison": "新机会与继续持有已比较",
            "next_change_condition": "资本效率变化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "capital_occupancy_reason": "旧仓仍有正向预期",
            "higher_efficiency_alternative": "515220 Trial机会",
        }
        with self.assertRaisesRegex(ValueError, "capital-efficiency release assessment"):
            project_decision_response({}, {"answers": {"HOLDING:159981": answer}}, package)

    def test_observation_retain_projects_existing_canonical_thesis(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r2","parent_request_id":"p2","decision_id":"d2","consumed_snapshot":"snap"}
        thesis = {"name":"恒生科技ETF","thscode":"513180.SH","thesis":"港股科技结构观察","falsifier":"结构失效则退出","next_decision_information":"下一节点重新比较","information_value_reason":"可能改变资本配置"}
        graph = [{"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"},{"problem_id":"OBSERVATION:513180","security":"恒生科技ETF","existing_thesis_state":thesis}]
        plan = [{"requirement_id":"r:a","target_problem_id":"RISK_PERMISSION","evidence_class":"A_SHARE_STYLE_FEEDBACK","required":True,"satisfaction":"SATISFIED"},{"requirement_id":"o:g","target_problem_id":"OBSERVATION:513180","evidence_class":"GLOBAL_RISK","required":True,"satisfaction":"SATISFIED"},{"requirement_id":"n:e","target_problem_id":"NEXT_UNIT_CAPITAL_USE","evidence_class":"ETF_RELATIVE_STRENGTH","required":True,"satisfaction":"SATISFIED"}]
        answers = {x["problem_id"]:{"final_action":"NO_ADD","capital_comparison":"compare","next_change_condition":"change","evidence_decision_impact":["ALL_REQUIRED"]} for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Confirm"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"159981","candidate_name":"能源化工ETF","opportunity_status":"Confirm机会"})
        answers["OBSERVATION:513180"].update({"disposition":"RETAIN","reason":"继续观察","opportunity_status":"观察机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({"final_action":"保留现金","new_amount_yuan":0,"post_action_deployable_cash":10000,"future_opportunity_capacity":"保留","cash_opportunity_cost":"右尾","alternative_capital_use_review":"已比较","concentration_account_structure_effect":"不增加","selected_state_reason":"休市","zero_amount_decisive_reason":"休市","compared_capital_states":[{"state_name":"现金","capital_action":"保留","remaining_deployable_cash":10000,"why_not_selected":"已选中","opportunity_cost_if_selected":"右尾"},{"state_name":"候选","capital_action":"等待","remaining_deployable_cash":0,"why_not_selected":"休市","opportunity_cost_if_selected":"风险"}]})
        projected=project_decision_response(source,{"answers":answers},{"problem_graph":graph,"evidence_requirements":plan})
        retained=projected["observation_management"][0]
        for key,value in thesis.items(): self.assertEqual(retained[key],value)
        self.assertNotIn("thesis",answers["OBSERVATION:513180"])



    def test_real_1922_actor_capital_state_names_project_to_strict_objects(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r1922","parent_request_id":"p1922","decision_id":"d1922","consumed_snapshot":"snap"}
        graph = [
            {"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},
            {"problem_id":"DEPLOYABLE_CASH"},{"problem_id":"RELEASABLE_CAPITAL"},
            {"problem_id":"TRIAL_CONFIRM_CAPACITY"},{"problem_id":"CONCENTRATION_COMMON_RISK"},
            {"problem_id":"NEXT_UNIT_CAPITAL_USE"},
        ]
        plan = [
            {"requirement_id":"r:a","target_problem_id":"RISK_PERMISSION","evidence_class":"A_SHARE_STYLE_FEEDBACK","required":True,"satisfaction":"SATISFIED"},
            {"requirement_id":"c:g","target_problem_id":"DEPLOYABLE_CASH","evidence_class":"GLOBAL_RISK","required":False,"satisfaction":"INSUFFICIENT"},
            {"requirement_id":"n:e","target_problem_id":"NEXT_UNIT_CAPITAL_USE","evidence_class":"ETF_RELATIVE_STRENGTH","required":True,"satisfaction":"SATISFIED"},
        ]
        answers = {x["problem_id"]:{"final_action":"保持","capital_comparison":"真实业务比较","next_change_condition":"下一合法节点重评","evidence_decision_impact":["ALL_REQUIRED"]} for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Confirm"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"159981","candidate_name":"能源化工ETF","opportunity_status":"Confirm机会"})
        answers["DEPLOYABLE_CASH"]["final_action"]="当前可部署现金13,169.54元，休市期间保持现金"
        answers["RELEASABLE_CAPITAL"]["final_action"]="561980条件释放资本"
        answers["TRIAL_CONFIRM_CAPACITY"]["final_action"]="现有现金足以承载10,000元Confirm；当前休市不执行"
        answers["CONCENTRATION_COMMON_RISK"]["final_action"]="科技/成长共同暴露较高，不追加"
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"当前保持现金13,169.54元；下一A股合法交易节点第一优先复核能源化工ETF（159981）Confirm 10,000元",
            "new_amount_yuan":0,"post_action_deployable_cash":13169.54,
            "future_opportunity_capacity":"保持完整一次10,000元Confirm承载能力",
            "cash_opportunity_cost":"休市期间现金机会成本低于预设未来成交的执行风险",
            "alternative_capital_use_review":"已比较全部合法资本状态",
            "concentration_account_structure_effect":"当前不增加科技/成长共同暴露",
            "selected_state_reason":"A股休市，当前唯一合法可执行状态为保持现金",
            "zero_amount_decisive_reason":"A股休市，当前不能执行A股新增风险动作",
            "compared_capital_states":["现金","全部实际持仓继续占资","全部持仓ETF追加","全部正式观察ETF","12只Discovery临时评估对象","561980条件释放资本","159981下一节点Confirm"],
        })
        projected=project_decision_response(source,{"answers":answers},{"problem_graph":graph,"evidence_requirements":plan})
        states=projected["capital_competition"]["compared_capital_states"]
        self.assertEqual(len(states),7)
        self.assertTrue(all(isinstance(x,dict) for x in states))
        self.assertTrue(all(set(("state_name","capital_action","remaining_deployable_cash","why_not_selected","opportunity_cost_if_selected")) <= set(x) for x in states))
        self.assertEqual([x["state_name"] for x in states], answers["NEXT_UNIT_CAPITAL_USE"]["compared_capital_states"])
        self.assertTrue(all(isinstance(x,str) for x in answers["NEXT_UNIT_CAPITAL_USE"]["compared_capital_states"]))


    def test_insufficient_optional_layer1_is_not_consumed(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r905","parent_request_id":"p905","decision_id":"d905","consumed_snapshot":"snap"}
        graph = [{"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"}]
        plan = [
            {"requirement_id":"r:a","target_problem_id":"RISK_PERMISSION","evidence_class":"A_SHARE_STYLE_FEEDBACK","required":True,"satisfaction":"SATISFIED"},
            {"requirement_id":"r:g","target_problem_id":"RISK_PERMISSION","evidence_class":"GLOBAL_RISK","required":False,"satisfaction":"INSUFFICIENT"},
            {"requirement_id":"n:e","target_problem_id":"NEXT_UNIT_CAPITAL_USE","evidence_class":"ETF_RELATIVE_STRENGTH","required":True,"satisfaction":"SATISFIED"},
        ]
        answers = {x["problem_id"]:{"final_action":"保持","capital_comparison":"compare","next_change_condition":"change","evidence_decision_impact":["ALL_REQUIRED"]} for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Confirm"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"159981","candidate_name":"能源化工ETF","opportunity_status":"Confirm机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"保持现金","new_amount_yuan":0,"post_action_deployable_cash":13169.54,
            "future_opportunity_capacity":"保留","cash_opportunity_cost":"right-tail","alternative_capital_use_review":"compared",
            "concentration_account_structure_effect":"unchanged","selected_state_reason":"closed","zero_amount_decisive_reason":"closed",
            "compared_capital_states":[
                {"state_name":"现金","capital_action":"保留","remaining_deployable_cash":13169.54,"why_not_selected":"selected","opportunity_cost_if_selected":"right-tail"},
                {"state_name":"候选","capital_action":"等待","remaining_deployable_cash":3169.54,"why_not_selected":"closed","opportunity_cost_if_selected":"risk"}
            ]
        })
        projected=project_decision_response(source,{"answers":answers},{"problem_graph":graph,"evidence_requirements":plan})
        consumed=projected["decision_evidence_consumption"]
        self.assertEqual(consumed["layer_1_external_cross_market"],[])
        self.assertEqual(consumed["layer_2_a_share_internal"],["r:a"])
        self.assertEqual(consumed["layer_3_etf_opportunity_capital"],["n:e"])


    def test_validator_allows_empty_unqualified_layer(self):
        value = {
            "request_id": "p905",
            "layer_1_external_cross_market": [],
            "layer_2_a_share_internal": ["r:a"],
            "layer_3_etf_opportunity_capital": ["n:e"],
            "discovery_to_capital_competition_consumed": True,
            "all_managed_positions_sell_chain_consumed": True,
            "held_etf_additional_capital_consumed": True,
            "next_unit_capital_use_consumed": True,
        }
        from scripts.business_decision_source import validate_decision_evidence_consumption
        self.assertEqual(validate_decision_evidence_consumption(value, parent_request_id="p905"), "")


    def test_actor_contract_cash_candidate_projects_without_hidden_schema_fields(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"cash-source","parent_request_id":"cash-parent","decision_id":"cash-decision","consumed_snapshot":"snap"}
        graph = [
            {"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},
            {"problem_id":"DEPLOYABLE_CASH"},{"problem_id":"RELEASABLE_CAPITAL"},
            {"problem_id":"CONCENTRATION_COMMON_RISK"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"},
        ]
        answers = {x["problem_id"]:{"final_action":"保持","capital_comparison":"真实业务比较","next_change_condition":"下一合法节点重评","evidence_decision_impact":["ALL_REQUIRED"]} for x in graph}
        answers["RISK_PERMISSION"]["final_action"] = "允许Trial"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"","candidate_name":"现金","opportunity_status":"无新增交易机会"})
        answers["DEPLOYABLE_CASH"]["final_action"] = "保留现金"
        answers["RELEASABLE_CAPITAL"]["final_action"] = "当前不释放持仓资本"
        answers["CONCENTRATION_COMMON_RISK"]["final_action"] = "不增加共同风险"
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"现金；新增0元","new_amount_yuan":0,"post_action_deployable_cash":17004.64,
            "future_opportunity_capacity":"保留后续机会承载能力","cash_opportunity_cost":"可能错过右尾",
            "alternative_capital_use_review":"已比较全部合法资本状态","concentration_account_structure_effect":"不扩大共同风险",
            "selected_state_reason":"现金边际效率最高",
            "compared_capital_states_as_business_state_names":["现金","全部实际持仓继续占资","全部持仓ETF追加","全部正式观察ETF","7只Discovery临时评估对象","可释放低效率资本"],
            "zero_amount_decisive_reason_if_zero":"当前没有独立机会优于现金",
        })
        projected = project_decision_response(source, {"answers":answers}, {"problem_graph":graph,"evidence_requirements":[]})
        self.assertEqual(projected["candidate_code"], "")
        self.assertEqual(projected["candidate_name"], "现金")
        self.assertEqual(projected["main_candidate"], "现金")
        self.assertEqual(projected["opportunity_status"], "无机会")
        self.assertTrue(all(x["opportunity_status"] in {"无机会","观察机会","Trial机会","Confirm机会"} for x in projected["etf_opportunity_reviews"]))
        self.assertEqual(projected["capital_competition"]["zero_amount_decisive_reason"], "当前没有独立机会优于现金")
        self.assertEqual(len(projected["capital_competition"]["compared_capital_states"]), 6)

    def test_actor_risk_permission_explanation_projects_to_registered_value(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"risk-source","parent_request_id":"risk-parent","decision_id":"risk-decision","consumed_snapshot":"snap"}
        graph = [
            {"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},
            {"problem_id":"DEPLOYABLE_CASH"},{"problem_id":"RELEASABLE_CAPITAL"},
            {"problem_id":"CONCENTRATION_COMMON_RISK"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"},
        ]
        answers = {x["problem_id"]:{"final_action":"保持","capital_comparison":"真实业务比较","next_change_condition":"下一合法节点重评","evidence_decision_impact":["ALL_REQUIRED"]} for x in graph}
        answers["RISK_PERMISSION"]["final_action"] = "允许Trial；当前不支持新增Confirm"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"","candidate_name":"现金","opportunity_status":"无机会"})
        answers["DEPLOYABLE_CASH"]["final_action"] = "保留现金"
        answers["RELEASABLE_CAPITAL"]["final_action"] = "当前不释放持仓资本"
        answers["CONCENTRATION_COMMON_RISK"]["final_action"] = "不增加共同风险"
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"现金；新增0元","new_amount_yuan":0,"post_action_deployable_cash":17004.64,
            "future_opportunity_capacity":"保留","cash_opportunity_cost":"可能错过右尾",
            "alternative_capital_use_review":"已比较","concentration_account_structure_effect":"不扩大共同风险",
            "selected_state_reason":"现金边际效率最高","compared_capital_states_as_business_state_names":["现金"],
            "zero_amount_decisive_reason_if_zero":"当前没有独立机会优于现金",
        })
        projected = project_decision_response(source, {"answers":answers}, {"problem_graph":graph,"evidence_requirements":[]})
        self.assertEqual(projected["risk_permission"], "允许Trial")

    def test_actor_risk_permission_unknown_or_ambiguous_text_fails_closed(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"risk-bad","parent_request_id":"risk-parent","decision_id":"risk-decision","consumed_snapshot":"snap"}
        graph = [{"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"}]
        base = {"final_action":"保持","capital_comparison":"compare","next_change_condition":"change","evidence_decision_impact":["ALL_REQUIRED"]}
        answers = {x["problem_id"]:dict(base) for x in graph}
        answers["RISK_PERMISSION"]["final_action"] = "适度允许新增"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"","candidate_name":"现金","opportunity_status":"无机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({"new_amount_yuan":0,"post_action_deployable_cash":1000,"future_opportunity_capacity":"保留","cash_opportunity_cost":"成本","alternative_capital_use_review":"已比较","concentration_account_structure_effect":"受控","selected_state_reason":"现金","compared_capital_states_as_business_state_names":["现金"],"zero_amount_decisive_reason_if_zero":"无更优机会"})
        with self.assertRaisesRegex(ValueError, "no registered formal permission"):
            project_decision_response(source, {"answers":answers}, {"problem_graph":graph,"evidence_requirements":[]})

    def test_no_new_trade_status_cannot_mask_security_or_positive_capital(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"bad-cash","parent_request_id":"p","decision_id":"d","consumed_snapshot":"snap"}
        graph = [{"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"}]
        base = {"final_action":"保持","capital_comparison":"compare","next_change_condition":"change","evidence_decision_impact":["ALL_REQUIRED"]}
        answers = {x["problem_id"]:dict(base) for x in graph}
        answers["RISK_PERMISSION"]["final_action"] = "允许Trial"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"159981","candidate_name":"能源化工ETF","opportunity_status":"无新增交易机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({"new_amount_yuan":1000,"post_action_deployable_cash":16004.64,"future_opportunity_capacity":"保留","cash_opportunity_cost":"成本","alternative_capital_use_review":"已比较","concentration_account_structure_effect":"受控","selected_state_reason":"理由","compared_capital_states_as_business_state_names":["现金"]})
        with self.assertRaisesRegex(ValueError, "only valid for cash-selected zero-new-capital decisions"):
            project_decision_response(source, {"answers":answers}, {"problem_graph":graph,"evidence_requirements":[]})

    def test_security_candidate_still_requires_code(self):
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"bad","parent_request_id":"p","decision_id":"d","consumed_snapshot":"snap"}
        graph = [{"problem_id":"MAIN_CANDIDATE"}]
        answer = {"final_action":"新增","capital_comparison":"compare","next_change_condition":"change","evidence_decision_impact":["ALL_REQUIRED"],"candidate_code":"","candidate_name":"能源化工ETF","opportunity_status":"Trial机会"}
        with self.assertRaisesRegex(ValueError, "candidate_code for security candidate"):
            project_decision_response(source, {"answers":{"MAIN_CANDIDATE":answer}}, {"problem_graph":graph,"evidence_requirements":[]})


    def test_observation_discovery_overlap_counts_once_in_capital_competition(self):
        source = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "request_id": "r-overlap",
            "parent_request_id": "p-overlap",
            "decision_id": "d-overlap",
            "consumed_snapshot": "snap-overlap",
        }
        thesis = {
            "name": "煤炭ETF",
            "thscode": "515220.SH",
            "thesis": "资源内部结构观察",
            "falsifier": "相对优势持续消失则退出",
            "next_decision_information": "下一节点复核承接",
            "information_value_reason": "持续提供资源内部横向比较",
        }
        graph = [
            {"problem_id": "RISK_PERMISSION"},
            {"problem_id": "MAIN_CANDIDATE"},
            {"problem_id": "NEXT_UNIT_CAPITAL_USE"},
            {
                "problem_id": "OBSERVATION:515220",
                "security": "煤炭ETF",
                "role_contract": "CONTINUOUS_INFORMATION_V1",
                "existing_thesis_state": thesis,
            },
            {"problem_id": "DISCOVERY:515220", "security": "煤炭ETF"},
        ]
        requirements = []
        for pid in ("RISK_PERMISSION", "MAIN_CANDIDATE", "NEXT_UNIT_CAPITAL_USE"):
            requirements.extend([
                {
                    "requirement_id": pid + ":A_SHARE_STYLE_FEEDBACK",
                    "target_problem_id": pid,
                    "evidence_class": "A_SHARE_STYLE_FEEDBACK",
                    "required": True,
                    "satisfaction": "SATISFIED",
                },
                {
                    "requirement_id": pid + ":ETF_RELATIVE_STRENGTH",
                    "target_problem_id": pid,
                    "evidence_class": "ETF_RELATIVE_STRENGTH",
                    "required": True,
                    "satisfaction": "SATISFIED",
                },
            ])
        answers = {
            item["problem_id"]: {
                "final_action": "review",
                "capital_comparison": "compare against cash",
                "next_change_condition": "reassess on next valid node",
                "evidence_decision_impact": ["ALL_REQUIRED"],
            }
            for item in graph
        }
        answers["RISK_PERMISSION"]["final_action"] = "允许Trial"
        answers["MAIN_CANDIDATE"].update({
            "final_action": "515220观察机会",
            "candidate_code": "515220",
            "candidate_name": "煤炭ETF",
            "opportunity_status": "观察机会",
        })
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action": "保留现金",
            "new_amount_yuan": 0,
            "post_action_deployable_cash": 1000,
            "future_opportunity_capacity": "保留",
            "cash_opportunity_cost": "可能错过机会",
            "alternative_capital_use_review": "比较观察角色和发现角色",
            "concentration_account_structure_effect": "不新增",
            "selected_state_reason": "现金胜出",
            "zero_amount_decisive_reason_if_zero": "没有候选达到Trial条件",
            "compared_capital_states": [{
                "state_name": "现金",
                "capital_action": "保留",
                "remaining_deployable_cash": 1000,
                "why_not_selected": "已选中",
                "opportunity_cost_if_selected": "可能错过机会",
            }],
        })
        answers["OBSERVATION:515220"].update({
            "final_action": "RETAIN",
            "disposition": "RETAIN",
            "opportunity_status": "观察机会",
            "reason": "持续观察角色保留；仍是观察机会。",
        })
        answers["DISCOVERY:515220"].update({
            "final_action": "Discovery评估完成",
            "disposition": "REJECT",
            "opportunity_status": "观察机会",
            "reason": "本节点Discovery角色不重复建身份；仍是观察机会。",
        })

        projected = project_decision_response(
            source,
            {"answers": answers},
            {
                "problem_graph": graph,
                "evidence_requirements": requirements,
                "business_role_reconciliation_contract": "V1",
            },
        )

        self.assertEqual(len(projected["etf_opportunity_reviews"]), 2)
        capital_reviews = projected["capital_competition"]["etf_opportunity_reviews"]
        self.assertEqual(len(capital_reviews), 1)
        self.assertEqual(capital_reviews[0]["code"], "515220")
        self.assertEqual(capital_reviews[0]["category"], "OBSERVED_ETF")
        self.assertEqual(capital_reviews[0]["opportunity_status"], "观察机会")
        self.assertIn("持续观察角色保留", capital_reviews[0]["reason"])
        self.assertIn("Discovery角色不重复建身份", capital_reviews[0]["reason"])
        self.assertEqual(len(projected["business_role_reconciliation"]["机会ETF"]), 1)

    def test_overlap_with_conflicting_role_status_fails_closed(self):
        graph = [
            {"problem_id": "OBSERVATION:515220", "security": "煤炭ETF"},
            {"problem_id": "DISCOVERY:515220", "security": "煤炭ETF"},
        ]
        reviews = [
            {"code": "515220", "category": "OBSERVED_ETF", "opportunity_status": "观察机会", "conclusion": "retain", "reason": "observation"},
            {"code": "515220", "category": "OBSERVATION_EVALUATION_INPUT", "opportunity_status": "无机会", "conclusion": "reject", "reason": "discovery"},
        ]
        from scripts.business_decision_source import _capital_competition_opportunity_reviews
        with self.assertRaisesRegex(ValueError, "conflicting opportunity status across roles"):
            _capital_competition_opportunity_reviews(reviews, graph)


if __name__ == "__main__":
    unittest.main()
