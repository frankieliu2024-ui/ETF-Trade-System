import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import canonical_etf_trade_facts, recover_canonical_etf_trade_facts


class Issue656AdoptionShadowReplayTests(unittest.TestCase):
    def _root(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "events/trades").mkdir(parents=True)
        (root / "config/market").mkdir(parents=True)
        (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects": [{"code": "159781"}]}), encoding="utf-8")
        (root / "ETF交易复盘与经验库_2026.md").write_text(
            "### 2.1 2026-07-13以来完整证券成交索引\n"
            "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
            "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
            "|2026-07-13 09:36:43|测试ETF|159781|买入|7600|1.305|9918|5|-9923|CASE-1|\n"
            "|2026-09-16 06:00:00|测试ETF|159781|买入|7600|1.305|9918|5|-9923|真实成交已执行|\n"
            "### 2.2 银证转账与非交易现金流水\n", encoding="utf-8")
        (root / "events/trades/historical_trade.json").write_text(json.dumps({
            "event_id": "historical_trade", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305,
            "execution_status": "EXECUTED", "executed_at": "2026-07-13T09:36:43+08:00", "executed_at_beijing": "2026-07-13T09:36:43+08:00",
            "confirmed_at_beijing": "2026-09-16T06:00:00Z", "historical_fact_adopted_at": "2026-09-16T06:00:00Z", "replay_semantics": "FACT_ENRICHMENT_ONLY",
        }), encoding="utf-8")
        return td, root

    def test_adoption_time_shadow_is_not_second_execution(self):
        td, root = self._root()
        try:
            reconstructed = [
                {"datetime": "2026-07-13 09:36:43", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
                {"datetime": "2026-09-16 06:00:00", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
            ]
            facts = canonical_etf_trade_facts(root, reconstructed)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["datetime"], "2026-07-13 09:36:43")
        finally:
            td.cleanup()

    def test_polluted_declared_count_contracts_only_by_proven_shadow(self):
        td, root = self._root()
        try:
            reconstructed = [
                {"datetime": "2026-07-13 09:36:43", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
                {"datetime": "2026-09-16 06:00:00", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
            ]
            self.assertEqual(len(recover_canonical_etf_trade_facts(root, reconstructed, 2)), 1)
        finally:
            td.cleanup()

    def test_same_core_at_unrelated_time_is_preserved(self):
        td, root = self._root()
        try:
            reconstructed = [
                {"datetime": "2026-07-13 09:36:43", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
                {"datetime": "2026-07-20 10:00:00", "code": "159781", "side": "BUY", "quantity": 7600, "price": 1.305},
            ]
            self.assertEqual(len(canonical_etf_trade_facts(root, reconstructed)), 2)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
