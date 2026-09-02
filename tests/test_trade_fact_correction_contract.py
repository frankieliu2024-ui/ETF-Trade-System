import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_system_consistency_core as core
from scripts import process_state_sync_request as sync


class TradeFactCorrectionContractTests(unittest.TestCase):
    def test_owned_event_mutation_is_allowed_but_unrelated_trade_is_not(self):
        dirty = [
            " M events/trades/known-event.json",
            " M events/trades/unrelated-event.json",
        ]
        self.assertEqual(
            core.unexpected_dirty_paths(dirty, ("events/trades/known-event.json",)),
            [" M events/trades/unrelated-event.json"],
        )

    def test_legacy_transaction_row_is_updated_in_place_after_fee_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-09-01 14:40:01|半导体设备ETF（561980）|561980|卖出|13,000|0.681|8,853.00|待确认|8,853.00（未含待确认费用）|待确认|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "20260901_143932_561980_reduce_risk_execution",
                "confirmed_at_beijing": "2026-09-01T14:40:01+08:00",
                "name": "半导体设备ETF",
                "code": "561980",
                "side": "SELL",
                "quantity": 13000,
                "price": 0.681,
                "amount": 8853,
                "fee_amount": 5,
                "fee_status": "CONFIRMED",
                "lifecycle": "降低风险",
                "linked_decision_id": "decision-1",
            }
            with patch.object(sync, "ROOT", root), patch.object(sync, "EXPERIENCE", experience):
                sync.sync_experience_transaction_index(event)
            text = experience.read_text(encoding="utf-8")
            self.assertEqual(text.count("|2026-09-01 14:40:01|"), 1)
            self.assertIn("|5.00|8,848.00|", text)
            self.assertIn("TRADE_EVENT:20260901_143932_561980_reduce_risk_execution", text)

    def test_legacy_fee_field_is_treated_as_confirmed_trade_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "legacy-fee-event",
                "confirmed_at_beijing": "2026-08-27T10:08:43+08:00",
                "name": "通信ETF",
                "code": "515880",
                "side": "BUY",
                "quantity": 7400,
                "price": 0.671,
                "amount": 4965.4,
                "fee": 5.0,
                "fee_status": "CONFIRMED",
            }
            with patch.object(sync, "ROOT", root), patch.object(sync, "EXPERIENCE", experience):
                sync.sync_experience_transaction_index(event)
            text = experience.read_text(encoding="utf-8")
            self.assertIn("|5.00|-4,970.40|", text)
            self.assertNotIn("待确认费用", text)


if __name__ == "__main__":
    unittest.main()
