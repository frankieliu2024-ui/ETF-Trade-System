import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import experience_etf_trade_index_facts, trade_signature


class Issue656TradeIdentityTests(unittest.TestCase):
    def test_experience_adoption_display_time_uses_linked_event_execution_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "159941", "name": "纳指ETF"}]}), encoding="utf-8"
            )
            (root / "events/trades").mkdir(parents=True)
            event = {
                "event_id": "historical_20260714132705_159941_BUY_3100",
                "code": "159941",
                "side": "BUY",
                "quantity": 3100,
                "price": 1.607,
                "executed_at_beijing": "2026-07-14T13:27:05+08:00",
                "confirmed_at_beijing": "2026-09-16T06:00:00Z",
                "execution_status": "EXECUTED",
            }
            (root / "events/trades/historical_20260714132705_159941_BUY_3100.json").write_text(
                json.dumps(event), encoding="utf-8"
            )
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|名称|代码|动作|数量|价格|成交额|费用|现金流|备注|\n"
                "|---|---|---|---|---|---|---|---|---|---|\n"
                "|2026-09-16 06:00:00|纳指ETF（159941）|159941|买入|3,100|1.607|4,981.70|5.00|-4,986.70|真实成交已执行 <!-- TRADE_EVENT:historical_20260714132705_159941_BUY_3100 -->|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            facts = experience_etf_trade_index_facts(root)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["datetime"], "2026-07-14 13:27:05")
            self.assertEqual(facts[0]["display_timestamp"], "2026-09-16 06:00:00")
            self.assertEqual(facts[0]["identity_timestamp_source"], "LINKED_EVENT_EXECUTION_TIME")
            self.assertEqual(trade_signature(facts[0]), trade_signature(event))


if __name__ == "__main__":
    unittest.main()
