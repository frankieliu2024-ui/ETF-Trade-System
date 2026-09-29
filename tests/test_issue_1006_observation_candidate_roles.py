from __future__ import annotations

import unittest

from scripts.build_query_context import _decision_problem_graph
from scripts.business_decision_source import project_decision_response


class ObservationCandidateRoleContractTest(unittest.TestCase):
    def test_observation_and_candidate_may_overlap_same_node(self) -> None:
        graph = _decision_problem_graph(
            [],
            [{"code": "561980", "name": "半导体设备ETF", "formal_quote_status": "READY", "management_identity": "MANAGED"}],
            {},
            [{"code": "561980", "name": "半导体设备ETF", "thscode": "561980.SH", "existing_thesis_state": {}}],
        )
        by_id = {x["problem_id"]: x for x in graph}
        self.assertEqual(by_id["OBSERVATION:561980"]["role_contract"], "CONTINUOUS_INFORMATION_V1")
        self.assertEqual(by_id["DISCOVERY:561980"]["role_contract"], "NODE_LOCAL_CANDIDATE_V1")
        self.assertEqual(by_id["DISCOVERY:561980"]["management_identity"], "MANAGED")
        self.assertIn("持续信息功能", by_id["OBSERVATION:561980"]["required_business_judgment"])
        self.assertIn("候选身份本身不持久", by_id["DISCOVERY:561980"]["required_business_judgment"])

    def test_new_observation_exit_requires_information_loss_and_continuity_cost(self) -> None:
        work_package = {
            "problem_graph": [{
                "problem_id": "OBSERVATION:561980",
                "role_contract": "CONTINUOUS_INFORMATION_V1",
            }]
        }
        response = {"answers": {
            "OBSERVATION:561980": {
                "final_action": "退出观察",
                "capital_comparison": "当前不是最强机会",
                "next_change_condition": "未来再发现",
                "evidence_decision_impact": ["ALL_REQUIRED"],
                "opportunity_status": "无机会",
                "disposition": "EXIT",
                "reason": "当前机会较弱",
            }
        }}
        with self.assertRaisesRegex(ValueError, "information_value_reason"):
            project_decision_response({}, response, work_package)

    def test_historical_observation_exit_contract_remains_replay_compatible(self) -> None:
        # Legacy work packages do not carry the V1 continuous-information role
        # contract. They must not be rejected merely because the new fields did
        # not exist when the historical decision was formed.
        work_package = {"problem_graph": [{"problem_id": "OBSERVATION:561980"}]}
        response = {"answers": {
            "OBSERVATION:561980": {
                "final_action": "退出观察",
                "capital_comparison": "历史判断",
                "next_change_condition": "历史条件",
                "evidence_decision_impact": ["ALL_REQUIRED"],
                "opportunity_status": "无机会",
                "disposition": "EXIT",
                "reason": "历史退出理由",
            }
        }}
        try:
            project_decision_response({}, response, work_package)
        except ValueError as exc:
            self.assertNotIn("continuous-information rationale", str(exc))


    def test_v1_role_reconciliation_keeps_observation_only_out_of_capital_states(self) -> None:
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r1010","parent_request_id":"p1010","decision_id":"d1010","consumed_snapshot":"snap"}
        thesis = {"name":"恒生科技ETF","thscode":"513180.SH","thesis":"持续观察港股科技结构","falsifier":"结构失效","next_decision_information":"下一节点结构","information_value_reason":"提供港股科技独立信息"}
        graph = [
            {"problem_id":"RISK_PERMISSION"},
            {"problem_id":"MAIN_CANDIDATE"},
            {"problem_id":"DEPLOYABLE_CASH"},
            {"problem_id":"RELEASABLE_CAPITAL"},
            {"problem_id":"CONCENTRATION_COMMON_RISK"},
            {"problem_id":"NEXT_UNIT_CAPITAL_USE"},
            {"problem_id":"OBSERVATION:513180","security":"恒生科技ETF","existing_thesis_state":thesis},
            {"problem_id":"DISCOVERY:588080","security":"科创板ETF"},
        ]
        base={"final_action":"保持","capital_comparison":"已比较","next_change_condition":"下一节点重评","evidence_decision_impact":["ALL_REQUIRED"]}
        answers={x["problem_id"]:dict(base) for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Trial"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"","candidate_name":"现金","opportunity_status":"无机会"})
        answers["OBSERVATION:513180"].update({"disposition":"RETAIN","reason":"保留独立信息价值","opportunity_status":"无机会"})
        answers["DISCOVERY:588080"].update({"disposition":"REJECT","reason":"发现命中但当前证据不足","opportunity_status":"无机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"现金；新增0元","new_amount_yuan":0,"post_action_deployable_cash":10000,
            "future_opportunity_capacity":"保留","cash_opportunity_cost":"可能错过右尾",
            "alternative_capital_use_review":"已比较全部真实资本状态",
            "concentration_account_structure_effect":"不增加共同风险","selected_state_reason":"现金边际效率最高",
            "compared_capital_states_as_business_state_names":["现金","全部实际持仓继续占资","全部持仓ETF追加"],
            "zero_amount_decisive_reason_if_zero":"当前没有机会ETF形成更优新增资本用途",
        })
        projected=project_decision_response(source,{"answers":answers},{
            "business_role_reconciliation_contract":"V1","problem_graph":graph,"evidence_requirements":[]
        })
        roles=projected["business_role_reconciliation"]
        self.assertEqual(roles["观察ETF"][0]["code"],"513180")
        self.assertEqual(roles["观察ETF"][0]["current_opportunity_status"],"无机会")
        self.assertEqual(roles["机会ETF"],[])
        self.assertEqual(roles["全市场机会发现"]["evaluated"],1)
        self.assertEqual(roles["全市场机会发现"]["rejected_from_opportunity_role"],1)
        names=[x["state_name"] for x in projected["capital_competition"]["compared_capital_states"]]
        self.assertNotIn("全部正式观察ETF",names)
        self.assertFalse(any("Discovery" in x for x in names))

    def test_v1_role_reconciliation_allows_observation_and_opportunity_overlap(self) -> None:
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r1010b","parent_request_id":"p1010b","decision_id":"d1010b","consumed_snapshot":"snap"}
        thesis = {"name":"恒生科技ETF","thscode":"513180.SH","thesis":"持续观察港股科技结构","falsifier":"结构失效","next_decision_information":"下一节点结构","information_value_reason":"提供港股科技独立信息"}
        graph=[
            {"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"DEPLOYABLE_CASH"},
            {"problem_id":"RELEASABLE_CAPITAL"},{"problem_id":"TRIAL_CONFIRM_CAPACITY"},{"problem_id":"CONCENTRATION_COMMON_RISK"},
            {"problem_id":"NEXT_UNIT_CAPITAL_USE"},{"problem_id":"OBSERVATION:513180","security":"恒生科技ETF","existing_thesis_state":thesis},
        ]
        base={"final_action":"保持","capital_comparison":"已比较","next_change_condition":"下一节点重评","evidence_decision_impact":["ALL_REQUIRED"]}
        answers={x["problem_id"]:dict(base) for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Trial"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"513180","candidate_name":"恒生科技ETF","opportunity_status":"Trial机会"})
        answers["OBSERVATION:513180"].update({"disposition":"RETAIN","reason":"信息价值继续成立且本节点机会增强","opportunity_status":"Trial机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"恒生科技ETF（513180）Trial 5,000元","new_amount_yuan":5000,"post_action_deployable_cash":5000,
            "future_opportunity_capacity":"保留后续容量","cash_opportunity_cost":"减少现金缓冲",
            "alternative_capital_use_review":"已比较全部真实资本状态","concentration_account_structure_effect":"受控",
            "selected_state_reason":"本节点Trial证据成立",
            "compared_capital_states_as_business_state_names":["现金","513180 Trial"],
        })
        projected=project_decision_response(source,{"answers":answers},{
            "business_role_reconciliation_contract":"V1","problem_graph":graph,"evidence_requirements":[]
        })
        roles=projected["business_role_reconciliation"]
        self.assertEqual(roles["观察ETF"][0]["code"],"513180")
        self.assertEqual(roles["机会ETF"][0]["code"],"513180")
        self.assertEqual(roles["机会ETF"][0]["source"],"观察ETF")

    def test_v1_rejects_observation_or_discovery_aggregate_as_capital_state(self) -> None:
        source = {"request_type":"BUSINESS_DECISION_SOURCE","request_id":"r1010c","parent_request_id":"p1010c","decision_id":"d1010c","consumed_snapshot":"snap"}
        graph=[{"problem_id":"RISK_PERMISSION"},{"problem_id":"MAIN_CANDIDATE"},{"problem_id":"DEPLOYABLE_CASH"},{"problem_id":"CONCENTRATION_COMMON_RISK"},{"problem_id":"NEXT_UNIT_CAPITAL_USE"}]
        base={"final_action":"保持","capital_comparison":"已比较","next_change_condition":"下一节点重评","evidence_decision_impact":["ALL_REQUIRED"]}
        answers={x["problem_id"]:dict(base) for x in graph}
        answers["RISK_PERMISSION"]["final_action"]="允许Trial"
        answers["MAIN_CANDIDATE"].update({"candidate_code":"","candidate_name":"现金","opportunity_status":"无机会"})
        answers["NEXT_UNIT_CAPITAL_USE"].update({
            "final_action":"现金；新增0元","new_amount_yuan":0,"post_action_deployable_cash":10000,
            "future_opportunity_capacity":"保留","cash_opportunity_cost":"右尾","alternative_capital_use_review":"已比较",
            "concentration_account_structure_effect":"不增加","selected_state_reason":"现金",
            "compared_capital_states_as_business_state_names":["现金","全部正式观察ETF","7只Discovery临时评估对象"],
            "zero_amount_decisive_reason_if_zero":"无更优机会",
        })
        with self.assertRaisesRegex(ValueError,"cannot use observation/discovery aggregates"):
            project_decision_response(source,{"answers":answers},{
                "business_role_reconciliation_contract":"V1","problem_graph":graph,"evidence_requirements":[]
            })


if __name__ == "__main__":
    unittest.main()
