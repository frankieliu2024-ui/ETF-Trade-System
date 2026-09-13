import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import canonical_etf_fee_projection
from scripts.rebuild_etf_strategy_equity import replay


class ReplayFeeCoherenceTests(unittest.TestCase):
    def test_declared_canonical_replay_count_recovers_fee_projection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "events/trades").mkdir(parents=True)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "561980"}]}), encoding="utf-8"
            )
            (root / "data/state/etf_strategy_equity.json").write_text(
                json.dumps({
                    "schema_version": "1.0-canonical-replay-candidate",
                    "summary": {"trade_fact_count": 2},
                }),
                encoding="utf-8",
            )
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-07-13 09:30:00|测试ETF（561980）|561980|买入|100|1.000|100.00|5.00|−105.00|CASE-1|\n"
                "|2026-07-14 09:30:00|测试ETF（561980）|561980|卖出|40|1.200|48.00|待确认|48.00|CASE-1|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )

            projection = canonical_etf_fee_projection(root, [])
            self.assertEqual(projection["canonical_trade_count"], 2)
            self.assertEqual(projection["effective_confirmed_fee_sum"], 5.0)
            self.assertEqual(projection["pending_fee_count"], 1)
            self.assertTrue(projection["recovered_from_formal_index"])

    def test_replay_emits_fee_boundary_metadata_without_blocking_gross(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data/state").mkdir(parents=True)
            (root / "events/trades").mkdir(parents=True)
            (root / "events/research/daily_features").mkdir(parents=True)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "561980"}]}), encoding="utf-8"
            )
            (root / "data/state/etf_strategy_equity.json").write_text(
                json.dumps({
                    "trades": [
                        {"datetime": "2026-07-13 09:30:00", "code": "561980", "side": "BUY", "quantity": 100, "price": 1.0, "fee_status": "CONFIRMED", "fee_amount": 5.0},
                        {"datetime": "2026-07-14 09:30:00", "code": "561980", "side": "SELL", "quantity": 40, "price": 1.2, "fee_status": "PENDING"},
                    ]
                }),
                encoding="utf-8",
            )
            for day, close in (("2026-07-13", 1.0), ("2026-07-14", 1.1)):
                (root / "events/research/daily_features" / f"{day}.json").write_text(
                    json.dumps({"market_date": day, "features": [{"code": "561980", "close": close, "quality_status": "PASS"}]}),
                    encoding="utf-8",
                )

            result = replay(root)
            summary = result["summary"]
            self.assertEqual(summary["confirmed_fees_separate"], 5.0)
            self.assertEqual(summary["known_fees"], 5.0)
            self.assertEqual(summary["pending_fee_count"], 1)
            self.assertIn("1 RECORDED TRADE FEE", summary["fee_status"])
            self.assertTrue(summary["pending_fees_do_not_block_gross"])


if __name__ == "__main__":
    unittest.main()
