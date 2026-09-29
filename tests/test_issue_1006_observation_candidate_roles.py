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


if __name__ == "__main__":
    unittest.main()
