from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_if_ic_basis_evidence as basis
import build_300750_oversold_reversal_evidence as oversold


class FormalDynamicResearchEvidenceTest(unittest.TestCase):
    def make_root(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "research/backtests").mkdir(parents=True)
        (root / "config/market").mkdir(parents=True)
        (root / "data/state").mkdir(parents=True)
        (root / "research/backtests/index_futures_if_ic_basis_formal_conclusion.json").write_text(
            json.dumps({
                "decision_eligible": True,
                "production_context_integration": True,
                "validated_signals": [
                    {"signal_id": "IF_basis_change_5d", "decision_eligible": True},
                    {"signal_id": "IC_basis_change_5d", "decision_eligible": True},
                ],
            }), encoding="utf-8"
        )
        (root / "research/backtests/ipo_base_stock_specific_signal_conclusion.json").write_text(
            json.dumps({
                "mode": "IPO_BASE_STOCK_SPECIFIC_SIGNAL_FINAL_CONCLUSION",
                "stocks": [{
                    "code": "300750",
                    "validated_signals": [{
                        "signal_id": "OVERSOLD_REVERSAL",
                        "decision_eligible": True,
                        "current_completed_bar_match": False,
                    }],
                }],
            }), encoding="utf-8"
        )
        (root / "config/market/a_share_trading_calendar_2026.json").write_text(json.dumps({"closed_dates": []}), encoding="utf-8")
        (root / "data/state/CURRENT.json").write_text(json.dumps({"market_date": "2026-08-28", "market_phase": "POST_CLOSE_GRACE"}), encoding="utf-8")
        return root

    def test_if_ic_uses_contract_local_5d_and_exposes_near_main(self):
        root = self.make_root()
        dates = [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28)]
        rows = []
        for product, contract in (("IF", "IF2609"), ("IC", "IC2609")):
            for i, d in enumerate(dates):
                rows.append({
                    "trade_date": d,
                    "product": product,
                    "contract": contract,
                    "close": 1000.0 + i * 2.0,
                    "settlement": 1000.0 + i * 2.0,
                    "volume": 1000 + i,
                    "open_interest": 5000 + i,
                })
        spots = {d: 1000.0 for d in dates}
        with mock.patch.object(basis, "_decision_market_date", return_value=date(2026, 8, 31)), \
             mock.patch.object(basis, "_fetch_futures", return_value=rows), \
             mock.patch.object(basis, "_fetch_spot", side_effect=lambda code, start, end: spots):
            out = basis.build(root)
        self.assertEqual(out["status"], "READY")
        self.assertTrue(out["use_in_current_decision"])
        self.assertEqual(out["fact_latest_date"], "2026-08-28")
        self.assertEqual(out["products"]["IF"]["near"]["contract"], "IF2609")
        self.assertEqual(out["products"]["IC"]["pit_main"]["contract"], "IC2609")
        self.assertGreater(out["products"]["IF"]["near"]["basis_change_5d_pct_points"], 0)
        self.assertIsNone(out["trade_signal"])
        self.assertFalse(out["can_generate_decision_independently"])

    def test_if_ic_failure_degrades_without_old_direction(self):
        root = self.make_root()
        with mock.patch.object(basis, "_decision_market_date", return_value=date(2026, 8, 31)), \
             mock.patch.object(basis, "_fetch_futures", side_effect=RuntimeError("cffex down")):
            out = basis.build(root)
        self.assertEqual(out["status"], "DEGRADED")
        self.assertFalse(out["use_in_current_decision"])
        self.assertNotIn("products", out)
        self.assertIsNone(out["trade_signal"])

    def test_300750_current_match_is_recomputed_not_static(self):
        root = self.make_root()
        closes = [100.0] * 15 + [110.0, 108.0, 105.0, 101.0, 98.0, 95.0]
        bars = [{"date": f"2026-08-{i+1:02d}", "close": c} for i, c in enumerate(closes)]
        with mock.patch.object(oversold, "_fetch_daily_bars", return_value=bars), \
             mock.patch.object(oversold, "_latest_completed_date", return_value=bars[-1]["date"]):
            out = oversold.build(root)
        self.assertEqual(out["status"], "READY")
        self.assertTrue(out["use_in_current_decision"])
        self.assertTrue(out["pattern_match"])
        self.assertTrue(out["static_current_match_ignored"])
        self.assertIsNone(out["trade_signal"])
        self.assertFalse(out["can_generate_decision_independently"])

    def test_bridge_ignores_static_current_match_and_exposes_dynamic_map(self):
        bridge = (ROOT / "scripts/build_research_execution_bridge.py").read_text(encoding="utf-8")
        for token in [
            "formal_dynamic_evidence",
            "if_ic_basis_5d",
            "ipo_base_stock_oversold_reversal_300750",
            "static_current_match_ignored",
            "build_if_ic_basis_evidence",
            "build_300750_oversold_reversal_evidence",
        ]:
            self.assertIn(token, bridge)
        self.assertIn('"current_completed_bar_match": None', bridge)


if __name__ == "__main__":
    unittest.main()
