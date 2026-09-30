import unittest
from unittest.mock import patch

from scripts import process_state_sync_request as state_sync


class HistoricalRecoveryTemporalContractTests(unittest.TestCase):
    def test_historical_validation_uses_request_bound_account_snapshot(self):
        historical = {"status": "VALID", "positions": [{"code": "513520", "quantity": 2100}]}
        current = {"status": "VALID", "positions": [{"code": "159981", "quantity": 2800}]}
        request = {
            "_historical_replay": True,
            "_historical_account_fact": historical,
        }
        with patch.object(state_sync, "_load_account_for_lifecycle_validation", return_value=current):
            resolved = state_sync._account_for_decision_validation(request)
        self.assertEqual(resolved, historical)
        self.assertNotEqual(resolved, current)
        self.assertIsNot(resolved, historical)

    def test_ordinary_completion_keeps_current_account_contract(self):
        current = {"status": "VALID", "positions": [{"code": "159981", "quantity": 2800}]}
        with patch.object(state_sync, "_load_account_for_lifecycle_validation", return_value=current):
            resolved = state_sync._account_for_decision_validation({})
        self.assertEqual(resolved, current)

    def test_historical_recovery_fails_closed_without_request_bound_account(self):
        with self.assertRaisesRegex(ValueError, "request-bound account fact"):
            state_sync._account_for_decision_validation({"_historical_replay": True})


if __name__ == "__main__":
    unittest.main()
