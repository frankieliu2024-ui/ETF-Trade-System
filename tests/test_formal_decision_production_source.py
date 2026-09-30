from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import manual_formal_decision_completion as completion


SOURCE = {
    "request_id": "20260925_164900_chatgpt_formal_decision",
    "request_type": "MARKET_QUOTE_REFRESH",
    "interaction_scenario": "POST_CLOSE_REVIEW",
    "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
    "requested_at_beijing": "2026-09-25T16:49:00+08:00",
    "market_date": "2026-09-25",
    "query_intent": "EXPLICIT_LATEST",
}

BASE_DECISION = {
    "decision_id": "20260925_164900_chatgpt_formal_decision_decision",
    "risk_permission": "PERMITTED",
    "main_candidate": "能源化工ETF（159981）",
}

WORK_PACKAGE = {
    "problem_graph": [
        {"problem_id": "RISK_PERMISSION", "decision_object": "risk"},
        {"problem_id": "MAIN_CANDIDATE", "decision_object": "candidate"},
        {"problem_id": "NEXT_UNIT_CAPITAL_USE", "decision_object": "capital"},
        {
            "problem_id": "HOLDING:561980",
            "decision_object": "561980",
            "security": "半导体设备ETF",
            "alternatives": {"HOLD": "继续占用当前资本", "REDUCE": "部分释放资本", "EXIT": "全部释放资本"},
        }
    ],
    "evidence_requirements": [],
}

RESPONSE = {
    "answers": {
        "RISK_PERMISSION": {
            "final_action": "允许Confirm",
            "capital_comparison": "比较完成",
            "next_change_condition": "风险许可变化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
        },
        "MAIN_CANDIDATE": {
            "final_action": "NO_ADD",
            "capital_comparison": "现金优先",
            "next_change_condition": "候选效率变化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "candidate_code": "159981",
            "candidate_name": "能源化工ETF",
            "opportunity_status": "Trial机会",
        },
        "NEXT_UNIT_CAPITAL_USE": {
            "final_action": "CASH",
            "capital_comparison": "现金优先",
            "next_change_condition": "机会效率变化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "new_amount_yuan": 0,
            "post_action_deployable_cash": 10000,
            "future_opportunity_capacity": "保留",
            "cash_opportunity_cost": "放弃当前机会",
            "alternative_capital_use_review": "已比较",
            "concentration_account_structure_effect": "不增加",
            "selected_state_reason": "现金更优",
            "zero_amount_decisive_reason": "无更优机会",
            "compared_capital_states": [{"state_name": "现金", "capital_action": "保留"}],
        },
        "HOLDING:561980": {
            "final_action": "HOLD",
            "capital_comparison": "继续占用资本相对现金仍合理",
            "next_change_condition": "相对效率恶化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "capital_occupancy_reason": "当前继续占资效率更高",
            "higher_efficiency_alternative": "现金",
        }
    }
}


class ProductionBusinessSourceIngressTests(unittest.TestCase):
    def test_structured_completion_is_business_decision_source(self):
        payload = completion.build_structured_completion_request(
            SOURCE, BASE_DECISION, RESPONSE, WORK_PACKAGE, "data/market/snapshots/x.json"
        )
        self.assertEqual(payload["request_type"], "BUSINESS_DECISION_SOURCE")
        self.assertEqual(payload["source"], "CHATGPT_BUSINESS_DECISION_RESPONSE")
        self.assertEqual(payload["decision_response"], RESPONSE)
        self.assertEqual(payload["decision_work_package"], WORK_PACKAGE)
        self.assertEqual(payload["parent_request_id"], SOURCE["request_id"])
        self.assertNotIn("position_capital_states", RESPONSE["answers"]["HOLDING:561980"])

    def test_structured_completion_rejects_observation_or_discovery_aggregates_as_capital_states(self):
        response = json.loads(json.dumps(RESPONSE))
        response["answers"]["NEXT_UNIT_CAPITAL_USE"]["compared_capital_states_as_business_state_names"] = [
            "持续观察ETF",
            "Discovery候选",
        ]
        with self.assertRaisesRegex(
            ValueError,
            "cannot use observation/discovery aggregates as capital states",
        ):
            completion.build_structured_completion_request(
                SOURCE, BASE_DECISION, response, WORK_PACKAGE, "data/market/snapshots/x.json"
            )

    def test_durable_business_source_must_come_from_authoritative_builder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requests/live_snapshot").mkdir(parents=True)
            source_path = root / "requests/live_snapshot/source.json"
            response_path = root / "response.json"
            source_path.write_text(json.dumps(SOURCE), encoding="utf-8")
            response_path.write_text(json.dumps(RESPONSE), encoding="utf-8")
            query = {
                "decision_fact_pack": {
                    "trigger": {"request_id": SOURCE["request_id"]},
                    "decision_work_package": WORK_PACKAGE,
                    "formal_action_readiness": {
                        "ready": True,
                        "status": "READY",
                        "request_scoped_pit_resolved": True,
                    },
                },
                "freshness_assurance": {},
            }
            (root / "data/state").mkdir(parents=True)
            (root / "data/state/query_context.json").write_text(json.dumps(query), encoding="utf-8")
            with mock.patch.object(completion, "ROOT", root), mock.patch.object(
                completion, "REQUEST_DIR", root / "requests/live_snapshot"
            ):
                target = completion.write_completion_request(
                    "requests/live_snapshot/source.json",
                    "",
                    "data/market/snapshots/x.json",
                    decision_response_path="response.json",
                )
            durable = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(durable["request_type"], "BUSINESS_DECISION_SOURCE")
            self.assertEqual(durable["decision_response"], RESPONSE)
            self.assertEqual(durable["decision_work_package"], WORK_PACKAGE)

    def test_structured_completion_rejects_missing_business_answers(self):
        with self.assertRaisesRegex(ValueError, "structured decision_response"):
            completion.build_structured_completion_request(
                SOURCE, BASE_DECISION, {}, WORK_PACKAGE, "data/market/snapshots/x.json"
            )

    def test_production_writer_rejects_legacy_no_response_surface(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requests/live_snapshot").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            source_path = root / "requests/live_snapshot/source.json"
            decision_path = root / "requests/live_snapshot/decision.json"
            source_path.write_text(json.dumps(SOURCE), encoding="utf-8")
            decision_path.write_text(json.dumps(BASE_DECISION), encoding="utf-8")
            with mock.patch.object(completion, "ROOT", root), mock.patch.object(
                completion, "REQUEST_DIR", root / "requests/live_snapshot"
            ):
                with self.assertRaisesRegex(ValueError, "--decision-response"):
                    completion.write_completion_request(
                        "requests/live_snapshot/source.json",
                        "requests/live_snapshot/decision.json",
                        "data/market/snapshots/x.json",
                    )


if __name__ == "__main__":
    unittest.main()
