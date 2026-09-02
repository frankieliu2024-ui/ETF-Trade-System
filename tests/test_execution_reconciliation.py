import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_execution_reconciliation as reconciliation


class ExactLinkedTradePrecedenceTests(unittest.TestCase):
    def test_exact_linked_trade_before_decision_time_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events/decisions").mkdir(parents=True)
            (root / "events/trades").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            decision = {
                "event_type": "FORMAL_DECISION",
                "decision_id": "decision-159326",
                "decision_time_beijing": "2026-09-02T14:21:31+08:00",
                "market_date": "2026-09-02",
                "candidate_code": "159326",
                "candidate_name": "电网设备ETF",
                "formal_decision": {
                    "lifecycle": {"电网设备ETF（159326）": "Trial已执行"},
                    "amount_action": "电网设备ETF（159326）已于14:17:53以1.651元买入3000份，成交本金4953元。",
                },
            }
            trade = {
                "event_id": "trade-159326",
                "code": "159326",
                "side": "BUY",
                "quantity": 3000,
                "price": 1.651,
                "amount": 4953.0,
                "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
                "linked_decision_id": "decision-159326",
                "execution_status": "EXECUTED",
            }
            (root / "events/decisions/decision.json").write_text(json.dumps(decision), encoding="utf-8")
            (root / "events/trades/trade.json").write_text(json.dumps(trade), encoding="utf-8")
            (root / "data/state/account_fact.json").write_text(json.dumps({"positions": [], "updated_at": "2026-09-02T21:05:00+08:00"}), encoding="utf-8")
            old = (reconciliation.ROOT, reconciliation.STATE, reconciliation.OUT)
            reconciliation.ROOT = root
            reconciliation.STATE = root / "data/state"
            reconciliation.OUT = reconciliation.STATE / "execution_reconciliation.json"
            try:
                result = reconciliation.build()
            finally:
                reconciliation.ROOT, reconciliation.STATE, reconciliation.OUT = old
            self.assertEqual(result["status"], "RECONCILED")
            self.assertEqual(result["actionable_count"], 0)
            self.assertEqual(result["matches"][0]["status"], "CONFIRMED_BY_TRADE_EVENT")
            self.assertEqual(result["matches"][0]["trade_event_ids"], ["trade-159326"])


if __name__ == "__main__":
    unittest.main()
