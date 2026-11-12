import tempfile
import unittest
from pathlib import Path


from scripts.process_state_sync_request import account_snapshot_transaction, merge_account_fact


class AccountPositionCanonicalizationTests(unittest.TestCase):
    def test_screenshot_row_replaces_duplicate_trade_row(self):
        prior = {
            "positions": [
                {"code": "588710", "name": "科创半导体设备ETF", "quantity": 6400, "available_quantity": 0, "cost": 0.778},
            ]
        }
        supplied = {
            "positions": [
                {"code": "588710", "name": "科创半导体设备ETF", "quantity": 6400, "available_quantity": 6400, "cost": 0.778},
            ]
        }
        merged = merge_account_fact(prior, supplied)
        rows = [row for row in merged["positions"] if row["code"] == "588710"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["available_quantity"], 6400)

    def test_event_merge_deduplicates_legacy_rows_without_screenshot(self):
        prior = {
            "positions": [
                {"code": "588710", "quantity": 6400, "available_quantity": 0},
                {"code": "588710", "quantity": 6400, "available_quantity": 6400},
            ]
        }
        merged = merge_account_fact(prior, {})
        self.assertEqual([row["code"] for row in merged["positions"]], ["588710"])

    def test_duplicate_snapshot_rows_fail_fast(self):
        supplied = {
            "source": "BROKER_HOLDINGS_SCREENSHOT",
            "positions": [
                {"code": "588710", "quantity": 6400},
                {"code": "588710", "quantity": 6400},
            ],
        }
        with self.assertRaisesRegex(ValueError, "duplicate security code"):
            merge_account_fact({}, supplied)


    def test_broker_snapshot_replaces_positions_absent_from_batch(self):
        prior = {
            "positions": [
                {"code": "588710", "quantity": 6400},
                {"code": "159941", "quantity": 12200},
            ]
        }
        supplied = {
            "source": "BROKER_HOLDINGS_SCREENSHOT",
            "total_asset": 1010.0,
            "cash": 10.0,
            "stock_market_value": 1000.0,
            "positions": [
                {"code": "588710", "quantity": 6400, "market_value": 1000.0},
            ],
        }
        merged = merge_account_fact(prior, supplied)
        self.assertEqual([row["code"] for row in merged["positions"]], ["588710"])

    def test_broker_snapshot_amount_mismatch_fails_before_persistence(self):
        supplied = {
            "source": "BROKER_HOLDINGS_SCREENSHOT",
            "total_asset": 1011.0,
            "cash": 10.0,
            "stock_market_value": 1000.0,
            "positions": [
                {"code": "588710", "quantity": 6400, "market_value": 1000.0},
            ],
        }
        with self.assertRaisesRegex(ValueError, "amount reconciliation failed"):
            merge_account_fact({}, supplied)

    def test_missing_screenshot_summary_does_not_carry_stale_value(self):
        prior = {"daily_pnl": -12.0, "daily_pnl_pct": 0.23}
        supplied = {
            "daily_pnl": -154.2,
            "_screenshot_clear_missing_fields": ["daily_pnl_pct"],
        }
        merged = merge_account_fact(prior, supplied)
        self.assertEqual(merged["daily_pnl"], -154.2)
        self.assertIsNone(merged["daily_pnl_pct"])


    def test_snapshot_transaction_restores_all_state_on_projection_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "data" / "state"
            state.mkdir(parents=True)
            account = state / "account_fact.json"
            account.write_text("old", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "projection failure"):
                with account_snapshot_transaction(root):
                    account.write_text("new", encoding="utf-8")
                    (state / "Dashboard_projection.json").write_text("partial", encoding="utf-8")
                    raise RuntimeError("projection failure")
            self.assertEqual(account.read_text(encoding="utf-8"), "old")
            self.assertFalse((state / "Dashboard_projection.json").exists())

    def test_complete_snapshot_cannot_modify_formal_or_lifecycle_facts(self):
        prior = {
            "formal_action": {"action": "HOLD", "decision_id": "d1"},
            "lifecycle": {"159056": "Trial"},
        }
        supplied = {
            "source": "BROKER_HOLDINGS_SCREENSHOT",
            "total_asset": 1010.0,
            "cash": 10.0,
            "stock_market_value": 1000.0,
            "positions": [{"code": "588710", "quantity": 6400, "market_value": 1000.0}],
            "formal_action": {"action": "BUY", "decision_id": "d2"},
            "lifecycle": {"159056": "退出"},
        }
        with self.assertRaisesRegex(ValueError, "non-account field"):
            merge_account_fact(prior, supplied)
        self.assertEqual(prior["formal_action"]["action"], "HOLD")
        self.assertEqual(prior["lifecycle"]["159056"], "Trial")


if __name__ == "__main__":
    unittest.main()
