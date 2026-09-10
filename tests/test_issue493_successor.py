import json
import tempfile
import unittest
from pathlib import Path

from scripts.confirmed_trade_facts import canonical_etf_trade_facts


class Issue493SuccessorTests(unittest.TestCase):
    def test_account_stock_event_is_not_projected_into_etf_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "market").mkdir(parents=True)
            (root / "config" / "market" / "etf_monitor_universe.json").write_text(json.dumps({"symbols": [{"code": "515220"}]}), encoding="utf-8")
            facts = canonical_etf_trade_facts(root,
            [{"code": "515220", "asset_type": "ETF", "side": "SELL", "quantity": 3700, "price": 1.324},
             {"code": "301689", "asset_type": "STOCK", "side": "SELL", "quantity": 500, "price": 51.85}],
            )
            self.assertEqual([f["code"] for f in facts], ["515220"])

    def test_missing_asset_type_is_not_assumed_to_be_etf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "market").mkdir(parents=True)
            (root / "config" / "market" / "etf_monitor_universe.json").write_text(json.dumps({"symbols": [{"code": "515220"}]}), encoding="utf-8")
            facts = canonical_etf_trade_facts(root, [{"code": "301689", "side": "SELL", "quantity": 500, "price": 51.85}])
            self.assertEqual(facts, [])


if __name__ == "__main__":
    unittest.main()
