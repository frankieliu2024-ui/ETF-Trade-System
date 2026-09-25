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
