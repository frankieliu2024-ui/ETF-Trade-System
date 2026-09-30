import unittest
from pathlib import Path
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

    def test_lifecycle_validator_receives_decision_time_positions(self):
        historical = {"status": "VALID", "positions": [{"code": "513520", "quantity": 2100}]}
        current = {"status": "VALID", "positions": [{"code": "513520", "quantity": 2100}, {"code": "159981", "quantity": 2800}]}
        lifecycle = {"日经ETF（513520）": "观察"}
        with patch.object(state_sync, "build_managed_position_projection",
                          side_effect=lambda _root, account: {
                              "positions": [
                                  {"code": str(item["code"]), "name": str(item["code"]), "quantity": item["quantity"]}
                                  for item in account.get("positions", [])
                                  if float(item.get("quantity") or 0) > 0
                              ]
                          }):
            self.assertEqual(
                state_sync.validate_managed_position_lifecycle(lifecycle, historical),
                "",
            )
            self.assertIn(
                "159981",
                state_sync.validate_managed_position_lifecycle(lifecycle, current),
            )

    def test_real_historical_query_context_uses_nested_account_fact(self):
        historical = {
            "source": "BROKER_HOLDINGS_SCREENSHOT_20260929_1844_USER_CONFIRMED",
            "status": "VALID",
            "as_of_beijing": "2026-09-29T18:44:00+08:00",
            "positions": [
                {"code": "300750", "quantity": 200},
                {"code": "601138", "quantity": 500},
                {"code": "588000", "quantity": 14100},
                {"code": "159941", "quantity": 12200},
                {"code": "159781", "quantity": 19500},
                {"code": "159981", "quantity": 8500},
                {"code": "159992", "quantity": 5800},
            ],
        }
        query_context = {
            "generated_at_beijing": "2026-09-30T09:36:13+08:00",
            "decision_fact_pack": {
                "trigger": {
                    "request_id": "manual-formal-20260930-093510",
                },
                "account_fact": historical,
            },
        }
        self.assertNotIn("account_fact", query_context)
        self.assertEqual(
            query_context["decision_fact_pack"]["trigger"]["request_id"],
            "manual-formal-20260930-093510",
        )
        request = {
            "_historical_replay": True,
            "_historical_account_fact": query_context["decision_fact_pack"]["account_fact"],
        }
        with patch.object(
            state_sync,
            "_load_account_for_lifecycle_validation",
            return_value={"status": "VALID", "positions": [{"code": "513520", "quantity": 2100}]},
        ):
            resolved = state_sync._account_for_decision_validation(request)
        self.assertEqual(resolved, historical)

    def test_historical_query_context_account_fact_path_is_canonical(self):
        source = Path("scripts/process_state_sync_request.py").read_text()
        self.assertIn(
            'decision_fact_pack = query_context.get("decision_fact_pack") or {}',
            source,
        )
        self.assertIn(
            'historical_account = decision_fact_pack.get("account_fact")',
            source,
        )
        self.assertNotIn(
            'historical_account = query_context.get("account_fact")',
            source,
        )

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
