import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import confirmed_trade_facts as facts
import maintenance_guard


class ConfirmedTradeFeeOverlayTests(unittest.TestCase):
    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def base_trade(self):
        return {
            "datetime": "2026-08-25 10:00:00",
            "code": "561980",
            "side": "SELL",
            "quantity": 100,
            "price": 1.0,
            "fee_amount": 120.01,
            "fee_status": "CONFIRMED",
        }

    def overlay_event(self):
        return {
            "confirmed_at_beijing": "2026-08-27T10:08:43+08:00",
            "code": "515880",
            "side": "BUY",
            "quantity": 7400,
            "price": 0.671,
            "fee": 5.0,
            "fee_status": "CONFIRMED",
            "execution_status": "EXECUTED",
        }

    def test_unintegrated_executed_event_fee_is_added_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trade = self.base_trade()
            self.write_json(root / "events/trades/new.json", self.overlay_event())
            fact = facts.effective_confirmed_fee_fact(root, [trade])
            self.assertEqual(fact["reconstructed_confirmed_fee_sum"], 120.01)
            self.assertEqual(fact["executed_event_confirmed_fee_sum"], 5.0)
            self.assertEqual(fact["effective_confirmed_fee_sum"], 125.01)
            self.assertEqual(fact["executed_event_overlay_count"], 1)

    def test_integrated_event_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            event = self.overlay_event()
            reconstructed = {
                "datetime": "2026-08-27 10:08:43",
                "code": "515880",
                "side": "BUY",
                "quantity": 7400,
                "price": 0.671,
                "fee_amount": 5.0,
                "fee_status": "CONFIRMED",
            }
            self.write_json(root / "events/trades/new.json", event)
            fact = facts.effective_confirmed_fee_fact(root, [reconstructed])
            self.assertEqual(fact["effective_confirmed_fee_sum"], 5.0)
            self.assertEqual(fact["executed_event_overlay_count"], 0)

    def test_formal_review_fee_mismatch_fails_maintenance_reconciliation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "data/state"
            trade = self.base_trade()
            self.write_json(
                state / "etf_strategy_equity.json",
                {
                    "summary": {
                        "trade_count": 1,
                        "known_fees": 120.01,
                        "strategy_cash_current": 0,
                        "current_etf_market_value": 0,
                        "current_gross_strategy_equity": 0,
                        "equity_reconciliation_status": "RECONCILED_EXACTLY_GROSS",
                    },
                    "trades": [trade],
                },
            )
            self.write_json(state / "account_fact.json", {"positions": []})
            self.write_json(root / "events/trades/new.json", self.overlay_event())
            self.write_json(
                root / "events/reviews/2026-08-28.json",
                {
                    "updated_at_beijing": "2026-08-28T15:58:52+08:00",
                    "review": {"etf_strategy_known_net": {"confirmed_etf_fees": 124.01}},
                },
            )
            old_root, old_equity, old_account = maintenance_guard.ROOT, maintenance_guard.EQUITY, maintenance_guard.ACCOUNT
            try:
                maintenance_guard.ROOT = root
                maintenance_guard.EQUITY = state / "etf_strategy_equity.json"
                maintenance_guard.ACCOUNT = state / "account_fact.json"
                rec = maintenance_guard.reconcile()
            finally:
                maintenance_guard.ROOT, maintenance_guard.EQUITY, maintenance_guard.ACCOUNT = old_root, old_equity, old_account
            self.assertEqual(rec["known_fee_reconciliation"]["effective_confirmed_fee_sum"], 125.01)
            self.assertEqual(rec["known_fee_reconciliation"]["formal_review_confirmed_fees"], 124.01)
            self.assertEqual(rec["known_fee_reconciliation"]["status"], "FAIL")
            self.assertEqual(rec["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
