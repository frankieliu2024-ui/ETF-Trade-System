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
            (root / "data/state/etf_strategy_equity.json").write_text(json.dumps(state))
            for day, close in [("2026-07-13",1.0),("2026-07-14",1.1),("2026-07-15",1.0)]:
                (root / "events/research/daily_features" / f"{day}.json").write_text(json.dumps({"market_date":day,"features":[{"code":"561980","close":close,"quality_status":"PASS"}]}))
            a=replay(root); b=replay(root)
            self.assertEqual(a,b); self.assertEqual(a["summary"]["starting_etf_strategy_capital"],200000.0); self.assertEqual(a["summary"]["gross_realized_pnl"],8.0); self.assertTrue(a["summary"]["pending_fees_do_not_block_gross"])
            self.assertEqual(
                a["summary"]["current_cumulative_pnl_gross"],
                round(a["summary"]["current_gross_strategy_equity"] - a["summary"]["starting_etf_strategy_capital"], 2),
            )

    def test_missing_lot_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root / "data/state").mkdir(parents=True); (root / "events/research/daily_features").mkdir(parents=True); (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({"objects":[{"code":"561980"}]}))
            (root / "data/state/etf_strategy_equity.json").write_text(json.dumps({"trades":[{"datetime":"2026-07-13 09:30:00","code":"561980","side":"SELL","quantity":1,"price":1}]}))
            (root / "events/research/daily_features/2026-07-13.json").write_text(json.dumps({"market_date":"2026-07-13","features":[{"code":"561980","close":1,"quality_status":"PASS"}]}))
            with self.assertRaises(ValueError): replay(root)
