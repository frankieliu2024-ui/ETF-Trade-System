import unittest
from unittest import mock
from pathlib import Path

from scripts import runtime_session_gate as gate


class BusinessDecisionSourceRoutingTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_formal_protocol_separates_chat_reply_from_durable_handoff(self):
        protocol = (self.ROOT / "docs" / "Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("READY回复与正式交接的不可混淆边界", protocol)
        self.assertIn("BUSINESS_DECISION_SOURCE", protocol)
        self.assertIn("presentation_content", protocol)
        self.assertIn("逐字等于冻结的`FINAL_CONTENT`", protocol)
        self.assertIn("不能据此声称正式决策已进入REPORT通知链", protocol)

    def test_market_snapshot_keeps_business_source_on_canonical_owner_path(self):
        workflow = (self.ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn('str(request.get("request_type") or "").upper() == "BUSINESS_DECISION_SOURCE"', workflow)
        self.assertIn('python scripts/process_state_sync_request.py "$request_files"', workflow)
    def test_classifier_keeps_business_source_distinct(self):
        request = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
            "parent_request_id": "formal-r1",
            "decision_response": {"answers": {"RISK_PERMISSION": {"final_action": "允许Confirm"}}},
        }
        self.assertEqual(gate.classify_live_snapshot_request(request), "BUSINESS_DECISION_SOURCE")

    def test_actor_business_source_envelope_stays_minimal(self):
        request = {
            "request_id": "formal-r1__business_decision_source_1",
            "request_type": "BUSINESS_DECISION_SOURCE",
            "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
            "parent_request_id": "formal-r1",
            "decision_id": "formal-r1_decision",
            "consumed_snapshot": "data/market/snapshots/2026-09-30_151018.json",
            "decision_response": {"answers": {"RISK_PERMISSION": {
                "final_action": "允许Confirm",
                "capital_comparison": "comparison",
                "next_change_condition": "condition",
                "evidence_decision_impact": ["ALL_REQUIRED"],
            }}},
        }
        self.assertEqual(gate.classify_live_snapshot_request(request), "BUSINESS_DECISION_SOURCE")
        self.assertNotIn("formal_decision", request)
        self.assertNotIn("decision_work_package", request)

    def test_broker_screenshot_source_routes_to_existing_state_sync(self):
        request = {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "fact_type": "BROKER_ACCOUNT_SNAPSHOT",
            "interaction_scenario": "POST_CLOSE_REVIEW",
            "account_fact": {"status": "VALID", "updated_at": "2026-09-29T18:44:00+08:00"},
        }
        self.assertEqual(gate.classify_live_snapshot_request(request), "STATE_SYNC_ONLY")

    def test_broker_screenshot_sync_scenario_routes_to_existing_state_sync(self):
        request = {
            "source": "CHATGPT_MANUAL",
            "interaction_scenario": "BROKER_SCREENSHOT_SYNC",
            "account_fact": {"status": "VALID"},
        }
        self.assertEqual(gate.classify_live_snapshot_request(request), "STATE_SYNC_ONLY")

    def test_push_class_preserves_business_source(self):
        request = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
            "parent_request_id": "formal-r1",
        }
        with mock.patch.object(gate, "_changed_request_files") as changed, \
             mock.patch.object(gate, "load_json", return_value={"interaction_routing": {"routes": [{"start": "00:00", "end": "23:59", "scenario": "NON_TRADING_DAY"}]}}):
            path = mock.MagicMock()
            path.read_text.return_value = __import__("json").dumps(request)
            changed.return_value = [path]
            self.assertEqual(gate._push_request_class(), "BUSINESS_DECISION_SOURCE")


if __name__ == "__main__":
    unittest.main()
