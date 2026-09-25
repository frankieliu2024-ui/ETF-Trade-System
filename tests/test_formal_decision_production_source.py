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
        "HOLDING:561980": {
            "final_action": "HOLD",
            "capital_comparison": "继续占用资本相对现金仍合理",
            "next_change_condition": "相对效率恶化时重评",
            "evidence_decision_impact": ["ALL_REQUIRED"],
            "position_capital_states": {"HOLD": "继续持有", "REDUCE": "部分释放", "EXIT": "全部退出"},
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
