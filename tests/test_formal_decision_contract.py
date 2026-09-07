import unittest

try:
    from scripts.process_state_sync_request import validate_formal_decision_contract
except ModuleNotFoundError:
    from process_state_sync_request import validate_formal_decision_contract


class FormalDecisionContractTests(unittest.TestCase):
    def test_registered_opportunity_and_holding_values_are_accepted(self):
        self.assertEqual(validate_formal_decision_contract({
            "opportunity_status": "观察机会",
            "risk_permission": "允许Trial",
            "lifecycle": "持有管理",
        }), "")

    def test_invalid_opportunity_status_is_rejected_at_ingress(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "当前无新的ETF机会达到Trial或Confirm标准",
        })
        self.assertIn("opportunity_status", error)

    def test_historical_trial_text_is_not_a_current_holding_lifecycle(self):
        error = validate_formal_decision_contract({
            "opportunity_status": "无机会",
            "lifecycle": "持有并继续Trial验证",
        })
        self.assertIn("historical evidence", error)


if __name__ == "__main__":
    unittest.main()
