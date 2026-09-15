from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sync_formal_files import build_dashboard_block, canonical_risk_identity  # noqa: E402


class DashboardRiskProjectionTests(unittest.TestCase):
    def test_account_and_formal_risk_keep_independent_provenance(self):
        account = {
            "updated_at": "2026-09-14T16:43:00+08:00",
            "source": "BROKER_SCREENSHOT",
            "positions": [],
            "total_asset": 100000,
            "stock_market_value": 0,
            "cash": 100000,
            "deployable_cash": 100000,
            "holding_pnl": 0,
            "daily_pnl": 0,
            "daily_pnl_pct": 0,
            "trades": [],
        }
        equity = {
            "replay_cutoff": "2026-09-15",
            "summary": {
                "current_strategy_return_pct_gross": -10.3852,
                "trade_fact_count": 32,
                "unknown_fee_flag": False,
            },
            "trades": [],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                '{"objects":[{"code":"588000","name":"科创50ETF"}]}',
                encoding="utf-8",
            )
            (root / "events/trades").mkdir(parents=True)
            block = build_dashboard_block(account, equity, "", root)
        self.assertIn("账户事实更新时间：2026-09-14T16:43:00+08:00", block)
        self.assertIn("账户事实来源：BROKER_SCREENSHOT", block)
        self.assertIn("ETF策略风险as-of：2026-09-15", block)
        self.assertIn("ETF策略风险率|约-10.3852%（Gross；as-of 2026-09-15）", block)

    def test_formal_risk_identity_is_canonical_equity_identity(self):
        identity = canonical_risk_identity({
            "replay_cutoff": "2026-09-15",
            "summary": {"trade_fact_count": 32},
        })
        self.assertEqual(identity["replay_cutoff"], "2026-09-15")
        self.assertEqual(identity["trade_fact_count"], 32)
        self.assertEqual(
            identity["source"],
            "data/state/etf_strategy_equity.json::summary.current_strategy_return_pct_gross",
        )


if __name__ == "__main__":
    unittest.main()
