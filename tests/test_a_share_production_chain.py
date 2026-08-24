from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_account_stock_market as stock_market  # noqa: E402
import build_stock_context as stock_context  # noqa: E402


class AShareProductionChainTests(unittest.TestCase):
    def test_stock_classifier_reads_canonical_etf_universe(self):
        codes = stock_context.load_etf_codes()
        configured = {
            str(item["code"])
            for item in json.loads((ROOT / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))["objects"]
        }
        self.assertEqual(codes, configured)
        self.assertEqual(len(codes), 11)

    def test_dynamic_account_stocks_use_supported_market_snapshot(self):
        stocks = [
            {"code": "300750", "name": "宁德时代", "quantity": 100, "market_value": 1},
            {"code": "601138", "name": "工业富联", "quantity": 500, "market_value": 2},
        ]

        def fake_run(command, **kwargs):
            self.assertEqual(command[1:3], ["market", "snapshot"])
            self.assertIn("300750.SZ,601138.SH", command)
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({
                "ok": True,
                "data": {
                    "item": [
                        {"thscode": "300750.SZ", "open_price": 10, "high_price": 12, "low_price": 9, "last_price": 11, "prev_price": 10, "price_change_ratio_pct": 10, "volume": 1, "turnover": 2},
                        {"thscode": "601138.SH", "open_price": 20, "high_price": 21, "low_price": 18, "last_price": 19, "prev_price": 20, "price_change_ratio_pct": -5, "volume": 3, "turnover": 4},
                    ],
                    "timestamp": 1787542230000,
                },
                "meta": {"source": "remote", "request_id": "test"},
            }), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(stock_market.subprocess, "run", side_effect=fake_run):
            result = stock_market.fetch_many("hithink-finance", stocks)

        self.assertEqual(set(result), {"300750", "601138"})
        self.assertTrue(all(item["quality_status"] == "PASS" for item in result.values()))
        self.assertTrue(all(item["as_of_beijing"].endswith("+08:00") for item in result.values()))

    def test_only_market_snapshot_dispatch_can_write_full_state(self):
        production = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        on_demand = (ROOT / ".github/workflows/on-demand-market-data.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", production)
        self.assertIn("git add -A -- data/market/snapshots data/state", production)
        self.assertNotIn("run_full_snapshot_recovery.py", on_demand)
        self.assertNotIn("data/state/CURRENT.json", on_demand)
        self.assertFalse((ROOT / ".github/workflows/full-snapshot-recovery.yml").exists())


if __name__ == "__main__":
    unittest.main()
