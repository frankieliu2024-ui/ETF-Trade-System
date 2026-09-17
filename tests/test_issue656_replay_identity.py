import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import canonical_etf_trade_facts, trade_signature


class Issue656ReplayIdentityTests(unittest.TestCase):
    def _root(self, tmp: str) -> Path:
        root = Path(tmp)
        p = root / "config" / "market" / "etf_monitor_universe.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"objects": [{"code": "513180", "name": "恒生科技ETF"}]}), encoding="utf-8")
        (root / "events" / "trades").mkdir(parents=True)
        return root

    def test_adoption_time_is_not_economic_trade_identity(self):
        experience = {"datetime": "2026-08-17 10:47:19", "code": "513180", "side": "BUY", "quantity": 8200, "price": 0.606}
        event = {"code": "513180", "side": "BUY", "quantity": 8200, "price": 0.606, "executed_at_beijing": "2026-08-17T10:47:19+08:00", "confirmed_at_beijing": "2026-09-16T06:00:00Z"}
        self.assertEqual(trade_signature(experience), trade_signature(event))

    def test_fact_enrichment_only_persisted_row_cannot_seed_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            economic = {"datetime": "2026-08-17 10:47:19", "code": "513180", "side": "BUY", "quantity": 8200, "price": 0.606}
            enrichment = {"event_id": "historical_20260817104719_513180_BUY_8200", "code": "513180", "side": "BUY", "quantity": 8200, "price": 0.606, "executed_at_beijing": "2026-08-17T10:47:19+08:00", "confirmed_at_beijing": "2026-09-16T06:00:00Z", "execution_status": "EXECUTED", "replay_semantics": "FACT_ENRICHMENT_ONLY"}
            (root / "events" / "trades" / "historical.json").write_text(json.dumps(enrichment), encoding="utf-8")
            facts = canonical_etf_trade_facts(root, [economic, enrichment])
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["datetime"], "2026-08-17 10:47:19")

    def test_real_sell_overlay_remains_effective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            buy = {"datetime": "2026-08-17 10:47:19", "code": "513180", "side": "BUY", "quantity": 8200, "price": 0.606}
            sell = {"event_id": "20260825_100518", "code": "513180", "side": "SELL", "quantity": 8200, "price": 0.574, "executed_at_beijing": "2026-08-25T10:02:57+08:00", "confirmed_at_beijing": "2026-08-25T10:02:57+08:00", "execution_status": "EXECUTED"}
            (root / "events" / "trades" / "sell.json").write_text(json.dumps(sell), encoding="utf-8")
            facts = canonical_etf_trade_facts(root, [buy])
            self.assertEqual([(x["side"], x["quantity"]) for x in facts], [("BUY", 8200), ("SELL", 8200)])


if __name__ == "__main__":
    unittest.main()
