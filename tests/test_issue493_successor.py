import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import canonical_etf_trade_facts, effective_confirmed_fee_fact, trade_signature


class Issue493SuccessorTests(unittest.TestCase):
    def write_universe(self, root: Path):
        path = root / "config" / "market" / "etf_monitor_universe.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"objects": [{"code": "515220", "name": "煤炭ETF"}, {"code": "159941", "name": "纳指ETF"}]}), encoding="utf-8")

    def test_account_stock_event_is_not_projected_into_etf_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            facts = canonical_etf_trade_facts(
                root,
                [
                    {"code": "515220", "asset_type": "ETF", "side": "SELL", "quantity": 3700, "price": 1.324},
                    {"code": "301689", "asset_type": "STOCK", "side": "SELL", "quantity": 500, "price": 51.85},
                ],
            )
            self.assertEqual([f["code"] for f in facts], ["515220"])

    def test_missing_asset_type_uses_canonical_objects_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            facts = canonical_etf_trade_facts(root, [{"code": "515220", "side": "SELL", "quantity": 3700, "price": 1.324}])
            self.assertEqual([f["code"] for f in facts], ["515220"])

    def test_missing_asset_type_stock_is_not_assumed_to_be_etf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            facts = canonical_etf_trade_facts(root, [{"code": "301689", "side": "SELL", "quantity": 500, "price": 51.85}])
            self.assertEqual(facts, [])

    def test_reconstructed_missing_asset_type_stock_is_not_assumed_to_be_etf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            facts = canonical_etf_trade_facts(root, [{"code": "301689", "name": "电科思仪", "source": "events/trades/trade_301689.json", "entered_events_trades": True, "side": "SELL", "quantity": 500, "price": 51.85}])
            self.assertEqual(facts, [])

    def test_stock_event_is_excluded_from_etf_fee_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            events = root / "events" / "trades"
            events.mkdir(parents=True)
            (events / "stock.json").write_text(json.dumps({
                "event_id": "stock-sell",
                "code": "301689",
                "asset_type": "STOCK",
                "side": "SELL",
                "quantity": 500,
                "price": 51.85,
                "fee_status": "CONFIRMED",
                "fee_amount": 5.0,
                "confirmed_at_beijing": "2026-09-10T13:37:46+08:00",
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            fact = effective_confirmed_fee_fact(root, [])
            self.assertEqual(fact["canonical_trade_count"], 0)
            self.assertEqual(fact["effective_confirmed_fee_sum"], 0)

    def test_executed_at_is_part_of_signature_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            events = root / "events" / "trades"
            events.mkdir(parents=True)
            (events / "etf.json").write_text(json.dumps({
                "event_id": "etf-sell",
                "code": "515220",
                "asset_type": "ETF",
                "side": "SELL",
                "quantity": 3700,
                "price": 1.324,
                "executed_at": "2026-09-10T13:37:20+08:00",
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            reconstructed = [{
                "datetime": "2026-09-10 13:37:20",
                "code": "515220",
                "asset_type": "ETF",
                "side": "SELL",
                "quantity": 3700,
                "price": 1.324,
            }]
            facts = canonical_etf_trade_facts(root, reconstructed)
            self.assertEqual(len(facts), 1)

    def test_historical_adoption_time_does_not_change_trade_identity(self):
        reconstructed = {
            "datetime": "2026-07-14 13:27:05",
            "code": "159941",
            "side": "BUY",
            "quantity": 3100,
            "price": 1.607,
        }
        historical_event = {
            "event_id": "historical_20260714132705_159941_BUY_3100",
            "code": "159941",
            "side": "BUY",
            "quantity": 3100,
            "price": 1.607,
            "executed_at_beijing": "2026-07-14T13:27:05+08:00",
            "confirmed_at_beijing": "2026-09-16T06:00:00Z",
            "execution_status": "EXECUTED",
        }
        self.assertEqual(trade_signature(reconstructed), trade_signature(historical_event))

    def test_historical_event_overlay_deduplicates_against_reconstructed_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_universe(root)
            events = root / "events" / "trades"
            events.mkdir(parents=True)
            (events / "historical.json").write_text(json.dumps({
                "event_id": "historical_20260714132705_159941_BUY_3100",
                "code": "159941",
                "side": "BUY",
                "quantity": 3100,
                "price": 1.607,
                "executed_at_beijing": "2026-07-14T13:27:05+08:00",
                "confirmed_at_beijing": "2026-09-16T06:00:00Z",
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            reconstructed = [{
                "datetime": "2026-07-14 13:27:05",
                "code": "159941",
                "side": "BUY",
                "quantity": 3100,
                "price": 1.607,
            }]
            facts = canonical_etf_trade_facts(root, reconstructed)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["datetime"], "2026-07-14 13:27:05")


if __name__ == "__main__":
    unittest.main()
