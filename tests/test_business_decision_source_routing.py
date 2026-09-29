import unittest
from unittest import mock

from scripts import runtime_session_gate as gate


class BusinessDecisionSourceRoutingTests(unittest.TestCase):
    def test_classifier_keeps_business_source_distinct(self):
        request = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "source": "CHATGPT_BUSINESS_DECISION_RESPONSE",
            "parent_request_id": "formal-r1",
            "decision_response": {"answers": {"RISK_PERMISSION": {"final_action": "允许Confirm"}}},
        }
        self.assertEqual(gate.classify_live_snapshot_request(request), "BUSINESS_DECISION_SOURCE")

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
