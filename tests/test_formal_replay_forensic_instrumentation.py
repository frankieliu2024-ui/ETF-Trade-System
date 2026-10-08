from __future__ import annotations

import contextlib
import copy
import io
import os
import unittest
from unittest import mock

from scripts import business_decision_source as bds


WORK_PACKAGE = {
    "problem_graph": [
        {"problem_id": "RISK_PERMISSION"},
        {"problem_id": "MAIN_CANDIDATE"},
        {"problem_id": "NEXT_UNIT_CAPITAL_USE"},
        {"problem_id": "HOLDING:561980", "security": "半导体设备ETF", "alternatives": {"HOLD": "继续占用", "REDUCE": "部分释放", "EXIT": "全部释放"}},
    ],
    "evidence_requirements": [],
}
RESPONSE = {"answers": {
    "RISK_PERMISSION": {"final_action": "允许Confirm", "capital_comparison": "完成", "next_change_condition": "风险变化重评", "evidence_decision_impact": ["ALL_REQUIRED"]},
    "MAIN_CANDIDATE": {"final_action": "NO_ADD", "capital_comparison": "现金优先", "next_change_condition": "效率变化重评", "evidence_decision_impact": ["ALL_REQUIRED"], "candidate_code": "159981", "candidate_name": "能源化工ETF", "opportunity_status": "Trial机会"},
    "NEXT_UNIT_CAPITAL_USE": {"final_action": "现金；新增0元", "capital_comparison": "现金优先", "next_change_condition": "机会效率变化重评", "evidence_decision_impact": ["ALL_REQUIRED"], "new_amount_yuan": 0, "post_action_deployable_cash": 10000, "future_opportunity_capacity": "保留", "cash_opportunity_cost": "放弃当前机会", "alternative_capital_use_review": "已比较", "concentration_account_structure_effect": "不增加", "selected_state_reason": "现金更优", "zero_amount_decisive_reason": "无更优机会", "compared_capital_states": [{"state_name": "现金", "capital_action": "保留"}]},
    "HOLDING:561980": {"final_action": "HOLD", "capital_comparison": "继续占资合理", "next_change_condition": "相对效率下降重评", "evidence_decision_impact": ["ALL_REQUIRED"], "capital_occupancy_reason": "现有占资有效", "higher_efficiency_alternative": "现金"},
}}
SOURCE = {"request_type": bds.BUSINESS_DECISION_SOURCE, "request_id": "source-id", "parent_request_id": "parent-id", "decision_id": "decision-id", "consumed_snapshot": "snapshot-id"}


class FormalReplayForensicInstrumentationTests(unittest.TestCase):
    def _run(self, enabled: bool):
        source, response, package = copy.deepcopy((SOURCE, RESPONSE, WORK_PACKAGE))
        output = io.StringIO()
        env = {"ETF_FORMAL_REPLAY_FORENSIC": "1"} if enabled else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not enabled:
                os.environ.pop("ETF_FORMAL_REPLAY_FORENSIC", None)
            bds.reset_formal_replay_forensic_trace(enabled)
            with contextlib.redirect_stdout(output):
                if enabled:
                    historical = {"problem_graph": [{"problem_id": "HISTORICAL:BASELINE"}]}
                    bds.emit_formal_replay_forensic_fingerprint("A_HISTORICAL_PACKET", obj=historical, dwp=historical, object_type="test.packet", provenance="historical_query_context:a73a5a1f", historical=True)
                projected = bds.project_decision_response(source, response, package, forensic_historical_replay=enabled)
                checked = bds.validate_source(projected, expected_snapshot="snapshot-id")
                canonical = bds.build_formal_completion_from_source(checked)
        return projected, checked, canonical, output.getvalue(), source, response, package

    def test_cash_limited_zero_requires_capital_migration_review(self):
        source, response, package = copy.deepcopy((SOURCE, RESPONSE, WORK_PACKAGE))
        package["problem_graph"].append({"problem_id": "DISCOVERY:515220", "security": "煤炭ETF"})
        response["answers"]["DISCOVERY:515220"] = {
            "final_action": "NO_ADD",
            "capital_comparison": "当前机会尚未达到Trial",
            "next_change_condition": "承接改善后重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "disposition": "REJECT",
            "reason": "缺少持续承接",
            "opportunity_status": "观察机会",
        }
        response["answers"]["NEXT_UNIT_CAPITAL_USE"]["zero_amount_decisive_reason_if_zero"] = "现金不足且机会需要重新比较"
        response["answers"]["NEXT_UNIT_CAPITAL_USE"]["zero_amount_decisive_reason"] = "现金不足且机会需要重新比较"
        with self.assertRaisesRegex(ValueError, "capital_migration_review"):
            bds.project_decision_response(source, response, package)

        response["answers"]["NEXT_UNIT_CAPITAL_USE"]["capital_migration_review"] = {
            "current_best_opportunity": "煤炭ETF（515220）",
            "cash_gap": "2820元",
            "lowest_efficiency_holding": "半导体设备ETF（561980）",
            "release_vs_continue_comparison": "释放后仍需比较执行资格与继续持有右尾",
            "decision": "当前不释放，下一节点重新验证",
        }
        projected = bds.project_decision_response(source, response, package)
        self.assertEqual(projected["decision_response"]["answers"]["NEXT_UNIT_CAPITAL_USE"]["capital_migration_review"]["current_best_opportunity"], "煤炭ETF（515220）")

    def test_instrumentation_is_business_semantics_inert(self):
        off = self._run(False)
        on = self._run(True)
        self.assertEqual(off[:3], on[:3])
        self.assertEqual(bds.source_fingerprint(off[1]), bds.source_fingerprint(on[1]))
        self.assertEqual(off[4:], on[4:])
        self.assertEqual(off[3], "")
        self.assertIn('"event": "FIRST_DIVERGENCE_STAGE"', on[3])
        self.assertIn('"stage": "E_PROJECT_ENTRY"', on[3])
        self.assertIn('"stage": "G_COMPLETENESS_VALIDATOR_GRAPH"', on[3])


if __name__ == "__main__":
    unittest.main()
