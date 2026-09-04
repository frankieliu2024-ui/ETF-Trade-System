import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts import check_system_consistency_core as core
from scripts import apply_trade_fact_correction as correction
from scripts import confirmed_trade_facts
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

    def test_lagging_auxiliary_equity_can_project_event_without_new_trade(self):
        event = {
            "event_id": "event-1", "confirmed_at_beijing": "2026-09-02T14:40:00+08:00",
            "name": "半导体设备ETF", "code": "561980", "side": "SELL",
            "quantity": 13000, "price": 0.681, "amount": 8853,
        }
        row = correction.equity_row_from_event(event, 5)
        self.assertEqual(row["source"], "events/trades/event-1.json")
        self.assertEqual(row["fee_status"], "CONFIRMED")
        self.assertEqual(row["cash_flow_amount"], 8848)

    def test_latest_unavailable_review_does_not_fallback_to_older_fee_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "events/reviews"
            review_dir.mkdir(parents=True)
            (review_dir / "2026-08-31.json").write_text(
                '{"updated_at_beijing":"2026-08-31T21:00:00+08:00","review":{"etf_strategy_known_net":{"confirmed_etf_fees":125.01}}}',
                encoding="utf-8",
            )
            (review_dir / "2026-09-01.json").write_text(
                '{"event_type":"FORMAL_POST_CLOSE_REVIEW_UNAVAILABLE","updated_at_beijing":"2026-09-02T12:44:52+08:00"}',
                encoding="utf-8",
            )
            self.assertIsNone(confirmed_trade_facts.latest_formal_review_confirmed_fees(root))


    def test_missing_lifecycle_does_not_render_bare_pending_note_after_fee_confirmation(self):
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
                "event_id": "missing-lifecycle",
                "confirmed_at_beijing": "2026-09-01T14:40:01+08:00",
                "name": "半导体设备ETF", "code": "561980", "side": "SELL",
                "quantity": 13000, "price": 0.681, "amount": 8853,
                "fee_amount": 5, "fee_status": "CONFIRMED",
            }
            with patch.object(sync, "ROOT", root), patch.object(sync, "EXPERIENCE", experience):
                sync.sync_experience_transaction_index(event)
            updated = experience.read_text(encoding="utf-8")
            row = next(line for line in updated.splitlines() if "TRADE_EVENT:missing-lifecycle" in line)
            self.assertNotIn("待确认", row)
            self.assertIn("真实成交已执行", row)

    def test_duplicate_marker_is_collapsed_when_existing_event_row_is_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            marker = "TRADE_EVENT:duplicate-event"
            experience.write_text(
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                f"|2026-09-03 09:47:55|通信ETF（515880）|515880|卖出|7,400|0.648|4,795.20|待确认|4,795.20（未含待确认费用）|待确认| <!-- {marker} --> <!-- {marker} -->\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "duplicate-event", "confirmed_at_beijing": "2026-09-03T09:47:55+08:00",
                "name": "通信ETF", "code": "515880", "side": "SELL",
                "quantity": 7400, "price": 0.648, "amount": 4795.2,
                "fee_amount": 5, "fee_status": "CONFIRMED",
            }
            with patch.object(sync, "ROOT", root), patch.object(sync, "EXPERIENCE", experience):
                sync.sync_experience_transaction_index(event)
            self.assertEqual(experience.read_text(encoding="utf-8").count(marker), 1)

    def test_effective_fee_projection_includes_overlay_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events/trades"
            events.mkdir(parents=True)
            (events / "overlay.json").write_text(
                '{"event_id":"overlay","execution_status":"EXECUTED","code":"515880","side":"BUY","quantity":7400,"price":0.671,"confirmed_at_beijing":"2026-08-27T10:08:43+08:00","fee":5,"fee_status":"CONFIRMED"}',
                encoding="utf-8",
            )
            reconstructed = [{"code": "561980", "side": "BUY", "quantity": 100, "price": 1, "datetime": "2026-08-01 10:00:00", "fee_amount": 5, "fee_status": "CONFIRMED"}]
            projection = confirmed_trade_facts.canonical_etf_fee_projection(root, reconstructed)
            self.assertEqual(projection["effective_confirmed_fee_sum"], 10)
            self.assertEqual(projection["canonical_trade_count"], 2)

    def test_historical_pit_pending_wording_is_not_globally_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "历史PIT：2026-08-20 当时费用待确认；该原始事实必须保留。\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-09-02 14:17:53|电网设备ETF（159326）|159326|买入|3,000|1.651|4,953.00|待确认|-4,953.00（未含待确认费用）|待确认|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "pit-protected", "confirmed_at_beijing": "2026-09-02T14:17:53+08:00",
                "name": "电网设备ETF", "code": "159326", "side": "BUY",
                "quantity": 3000, "price": 1.651, "amount": 4953,
                "fee_amount": 5, "fee_status": "CONFIRMED",
            }
            with patch.object(sync, "ROOT", root), patch.object(sync, "EXPERIENCE", experience):
                sync.sync_experience_transaction_index(event)
            updated = experience.read_text(encoding="utf-8")
            self.assertIn("历史PIT：2026-08-20 当时费用待确认", updated)
            self.assertIn("|5.00|-4,958.00|", updated)


if __name__ == "__main__":
    unittest.main()

