import json
import tempfile
import unittest
from pathlib import Path

from scripts.process_state_sync_request import normalize_broker_account_fact_ingress


class AccountFactConsumptionContractTests(unittest.TestCase):
    def test_screenshot_aliases_are_normalized_into_latest_canonical_fact(self):
        request = {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "submitted_at_beijing": "2026-10-22T18:34:15+08:00",
        }
        supplied = {
            "total_assets": 100.0,
            "available_cash": 60.0,
            "securities_market_value": 40.0,
            "positions": [{"code": "159941", "quantity": 10}],
        }
        normalized, moved = normalize_broker_account_fact_ingress(request, supplied)
        self.assertEqual(moved, 0)
        self.assertEqual(normalized["total_asset"], 100.0)
        self.assertEqual(normalized["cash"], 60.0)
        self.assertEqual(normalized["stock_market_value"], 40.0)
        self.assertEqual(normalized["updated_at"], "2026-10-22T18:34:15+08:00")

    def test_formal_workflow_does_not_scan_or_bind_historical_screenshot_requests(self):
        workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertNotIn("newest unconsumed screenshot", workflow)
        self.assertNotIn("ACCOUNT_SYNC_REQUEST_FILE", workflow)

    def test_maintenance_receipt_fixture_is_request_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "requests/live_snapshot/broker.json"
            state = root / "data/state"
            request.parent.mkdir(parents=True)
            state.mkdir(parents=True)
            fact = {
                "updated_at": "2026-10-22T18:34:15+08:00",
                "total_asset": 100.0,
                "cash": 60.0,
                "stock_market_value": 40.0,
                "positions": [{"code": "159941", "quantity": 10}],
            }
            request.write_text(json.dumps({"account_fact": fact}), encoding="utf-8")
            (state / "account_fact.json").write_text(json.dumps(fact), encoding="utf-8")
            self.assertTrue(request.exists())


if __name__ == "__main__":
    unittest.main()
