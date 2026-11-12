import unittest

from scripts.process_state_sync_request import merge_account_fact


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


if __name__ == "__main__":
    unittest.main()
