import json
import tempfile
import unittest
from pathlib import Path

from scripts.rebuild_etf_strategy_equity import replay


class ETFReplayContractTests(unittest.TestCase):
    def test_fifo_partial_sell_rebuy_and_idempotent_facts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "data/state").mkdir(parents=True); (root / "events/trades").mkdir(parents=True); (root / "events/research/daily_features").mkdir(parents=True); (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects":[{"code":"561980"}]}))
            state={"trades":[{"datetime":"2026-07-13 09:30:00","code":"561980","side":"BUY","quantity":100,"price":1.0,"fee_status":"PENDING"},{"datetime":"2026-07-14 09:30:00","code":"561980","side":"SELL","quantity":40,"price":1.2,"fee_status":"CONFIRMED","fee_amount":1},{"datetime":"2026-07-15 09:30:00","code":"561980","side":"BUY","quantity":20,"price":0.9,"fee_status":"CONFIRMED","fee_amount":1}]}
            state_path = root / "data/state/etf_strategy_equity.json"
            state_path.write_text(json.dumps(state))
            for day, close in [("2026-07-13",1.0),("2026-07-14",1.1),("2026-07-15",1.0)]:
                (root / "events/research/daily_features" / f"{day}.json").write_text(json.dumps({"market_date":day,"features":[{"code":"561980","close":close,"quality_status":"PASS"}]}))
            a = replay(root)
            self.assertEqual(len(a["trades"]), 3)
            self.assertEqual(a["summary"]["trade_fact_count"], 3)
            self.assertEqual(a["summary"]["starting_etf_strategy_capital"],200000.0)
            self.assertEqual(a["summary"]["gross_realized_pnl"],8.0)
            self.assertTrue(a["summary"]["pending_fees_do_not_block_gross"])
            self.assertEqual(
                a["summary"]["current_cumulative_pnl_gross"],
                round(a["summary"]["current_gross_strategy_equity"] - a["summary"]["starting_etf_strategy_capital"], 2),
            )

            # Production persists the canonical candidate over the formal state.
            # A second replay from that persisted state must retain the same full
            # trade identity rather than degrading to the subset with event files.
            state_path.write_text(json.dumps(a))
            b = replay(root)
            self.assertEqual(b["trades"], a["trades"])
            self.assertEqual(b["summary"]["trade_fact_count"], a["summary"]["trade_fact_count"])
            self.assertEqual(b["summary"]["current_gross_strategy_equity"], a["summary"]["current_gross_strategy_equity"])
            self.assertEqual(b["summary"]["current_strategy_return_pct_gross"], a["summary"]["current_strategy_return_pct_gross"])
            self.assertEqual(b["series"], a["series"])

    def test_declared_trade_count_recovers_from_formal_experience_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "events/trades").mkdir(parents=True)
            (root / "events/research/daily_features").mkdir(parents=True)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects":[{"code":"561980"}]}))
            (root / "data/state/etf_strategy_equity.json").write_text(json.dumps({
                "summary": {"trade_fact_count": 2},
                "trades": [],
            }))
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "共2笔证券交易：ETF 2笔、个股0笔。\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-07-13 09:30:00|测试ETF（561980）|561980|买入|100|1.000|100.00|5.00|−105.00|CASE-1|\n"
                "|2026-07-14 09:30:00|测试ETF（561980）|561980|卖出|40|1.200|48.00|待确认|48.00|CASE-1|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            for day, close in [("2026-07-13", 1.0), ("2026-07-14", 1.1)]:
                (root / "events/research/daily_features" / f"{day}.json").write_text(json.dumps({
                    "market_date": day,
                    "features": [{"code":"561980","close":close,"quality_status":"PASS"}],
                }))
            result = replay(root)
            self.assertEqual(result["summary"]["trade_fact_count"], 2)
            self.assertEqual(len(result["trades"]), 2)
            self.assertEqual(result["trades"][0]["source"], "experience_trade_index")
            self.assertEqual(result["trades"][1]["fee_status"], "PENDING")
            self.assertEqual(result["summary"]["gross_realized_pnl"], 8.0)

    def test_declared_trade_count_recovery_fails_closed_on_count_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "events/research/daily_features").mkdir(parents=True)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects":[{"code":"561980"}]}))
            (root / "data/state/etf_strategy_equity.json").write_text(json.dumps({"summary":{"trade_fact_count":2},"trades":[]}))
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|2026-07-13 09:30:00|测试ETF（561980）|561980|买入|100|1.000|100.00|5.00|−105.00|CASE-1|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            (root / "events/research/daily_features/2026-07-13.json").write_text(json.dumps({"market_date":"2026-07-13","features":[{"code":"561980","close":1,"quality_status":"PASS"}]}))
            with self.assertRaisesRegex(ValueError, "expected 2, recovered 1"):
                replay(root)

    def test_missing_lot_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root / "data/state").mkdir(parents=True); (root / "events/research/daily_features").mkdir(parents=True); (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects":[{"code":"561980"}]}))
            (root / "data/state/etf_strategy_equity.json").write_text(json.dumps({"trades":[{"datetime":"2026-07-13 09:30:00","code":"561980","side":"SELL","quantity":1,"price":1}]}))
            (root / "events/research/daily_features/2026-07-13.json").write_text(json.dumps({"market_date":"2026-07-13","features":[{"code":"561980","close":1,"quality_status":"PASS"}]}))
            with self.assertRaises(ValueError): replay(root)
