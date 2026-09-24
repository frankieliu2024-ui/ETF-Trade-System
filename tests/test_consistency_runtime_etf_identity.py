import unittest

from scripts.check_system_consistency_core import (
    active_account_etf_codes,
    expected_runtime_etf_codes,
    expected_a_share_snapshot_count,
)


class RuntimeEtfIdentityConsistencyTests(unittest.TestCase):
    def test_same_day_actual_holding_extends_runtime_identity_set(self):
        objects = [{"code": "561980"}, {"code": "588000"}]
        account = {"positions": [
            {"asset_type": "ETF", "code": "159981", "quantity": 2800},
            {"asset_type": "STOCK", "code": "300750", "quantity": 200},
        ]}
        runtime = expected_runtime_etf_codes(objects, account)
        self.assertEqual(runtime, {"561980", "588000", "159981"})
        self.assertEqual(expected_a_share_snapshot_count(runtime), 6)

    def test_exited_etf_does_not_extend_runtime_identity_set(self):
        objects = [{"code": "561980"}]
        account = {"positions": [
            {"asset_type": "ETF", "code": "159981", "quantity": 0},
            {"asset_type": "ETF", "code": "513180", "quantity": -1},
        ]}
        self.assertEqual(active_account_etf_codes(account), set())
        self.assertEqual(expected_runtime_etf_codes(objects, account), {"561980"})

    def test_existing_held_etf_is_deduplicated(self):
        objects = [{"code": "561980"}]
        account = {"positions": [{"asset_type": "ETF", "code": "561980", "quantity": 28900}]}
        self.assertEqual(expected_runtime_etf_codes(objects, account), {"561980"})


if __name__ == "__main__":
    unittest.main()
