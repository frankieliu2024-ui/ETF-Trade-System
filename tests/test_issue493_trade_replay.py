import unittest
from pathlib import Path

from scripts.process_state_sync_request import _apply_trade_to_account


class Issue493TradeReplayTests(unittest.TestCase):
    def test_full_sell_converges_quantity_and_available_quantity(self):
        account = {"positions": [{"code": "515220", "quantity": 3700, "available_quantity": 3700, "last_price": 1.2}], "cash": 0, "total_asset": 0}
        result = _apply_trade_to_account(account, {"code": "515220", "side": "SELL", "quantity": 3700, "price": 1.2, "amount": 4440, "confirmed_at_beijing": "2026-09-10T13:37:20+08:00"})
        self.assertEqual(result["positions"][0]["quantity"], 0)
        self.assertEqual(result["positions"][0]["available_quantity"], 0)

    def test_partial_sell_preserves_partial_position(self):
        account = {"positions": [{"code": "301689", "quantity": 500, "available_quantity": 500, "last_price": 16}], "cash": 0, "total_asset": 0}
        result = _apply_trade_to_account(account, {"code": "301689", "side": "SELL", "quantity": 200, "price": 16, "amount": 3200, "confirmed_at_beijing": "2026-09-10T13:37:46+08:00"})
        self.assertEqual(result["positions"][0]["quantity"], 300)
        self.assertEqual(result["positions"][0]["available_quantity"], 300)

    def test_workflow_refreshes_execution_baseline_before_state_sync(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        marker = "Process optional broker/dashboard state sync"
        section = workflow[workflow.index(marker):workflow.index("Build objective intraday market delta")]
        self.assertIn("git fetch origin main", section)
        self.assertIn("git reset --hard origin/main", section)
        self.assertIn("preserving only the triggering request payload", section)


if __name__ == "__main__":
    unittest.main()

