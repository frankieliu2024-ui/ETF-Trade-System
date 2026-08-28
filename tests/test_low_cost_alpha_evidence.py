from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_low_cost_alpha_evidence as alpha
import build_overseas_context as overseas


class LowCostAlphaEvidenceTests(unittest.TestCase):
    def test_formal_review_never_grants_independent_trade(self):
        review = json.loads((ROOT / "research/backtests/low_cost_alpha_formal_conversion_review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["overall_status"], "PASS_WITH_SCOPED_CONVERSION")
        self.assertTrue(review["decision_eligible"])
        self.assertTrue(review["production_context_integration"])
        self.assertFalse(review["can_generate_decision_independently"])
        self.assertFalse(review["automatic_trade"])
        self.assertIsNone(review["trade_signal"])
        self.assertEqual({x["evidence_id"] for x in review["rejected_evidence"]}, {"weak_market_resilience", "dispersion_conditioned_migration"})

    def test_margin_contraction_is_inactive_not_bearish(self):
        panel = [
            {"date": "2026-08-27", "code": "588000", "name": "科创50ETF", "ret": 1.0},
            {"date": "2026-08-27", "code": "561980", "name": "半导体设备ETF", "ret": 0.0},
            {"date": "2026-08-27", "code": "515880", "name": "通信ETF", "ret": -1.0},
        ]
        out = alpha.margin_feedback(panel, "2026-08-28", {"status": "READY", "fact_latest_date": "2026-08-27", "primary_evidence": {"state": "CONTRACTING"}})
        self.assertEqual(out["status"], "READY")
        self.assertEqual(out["active_support_codes"], [])
        self.assertIn("不形成反向看空", out["interpretation_rule"])
        self.assertFalse(out["can_generate_decision_independently"])
        self.assertIsNone(out["trade_signal"])

    def test_margin_expansion_activates_only_validated_stronger_etf(self):
        panel = [
            {"date": "2026-08-27", "code": "588000", "name": "科创50ETF", "ret": 2.0},
            {"date": "2026-08-27", "code": "561980", "name": "半导体设备ETF", "ret": 0.0},
            {"date": "2026-08-27", "code": "515880", "name": "通信ETF", "ret": 3.0},
            {"date": "2026-08-27", "code": "159992", "name": "创新药ETF", "ret": -1.0},
        ]
        out = alpha.margin_feedback(panel, "2026-08-28", {"status": "READY", "fact_latest_date": "2026-08-27", "primary_evidence": {"state": "EXPANDING"}})
        self.assertIn("588000", out["active_support_codes"])
        self.assertNotIn("515880", {x["code"] for x in out["items"]})
        strongest = next(x for x in out["items"] if x["code"] == "588000")
        self.assertEqual(strongest["evidence_strength"], "MOST_STABLE")

    def test_selling_exhaustion_requires_decline_low_volume_and_off_low_close(self):
        panel = []
        for i in range(210):
            panel.append({"date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", "code": "588000", "name": "科创50ETF", "ret": 0.1, "volume_ratio": 0.8 + (i % 30) / 100.0, "close_location": 0.5})
        panel.extend([
            {"date": "2026-08-27", "code": "588000", "name": "科创50ETF", "ret": -1.0, "volume_ratio": 0.75, "close_location": 0.70},
            {"date": "2026-08-27", "code": "561980", "name": "半导体设备ETF", "ret": -1.0, "volume_ratio": 0.75, "close_location": 0.30},
        ])
        out = alpha.selling_exhaustion(panel)
        self.assertIn("588000", out["matched_codes"])
        self.assertNotIn("561980", out["matched_codes"])
        self.assertFalse(out["can_generate_decision_independently"])
        self.assertIsNone(out["trade_signal"])

    def test_yahoo_daily_row_exposes_previous_complete_close(self):
        payload = {"chart": {"result": [{
            "meta": {"timezone": "America/New_York", "chartPreviousClose": 101.0},
            "timestamp": [1787601600, 1787688000],
            "indicators": {"quote": [{
                "open": [100.0, 102.0], "high": [103.0, 105.0], "low": [99.0, 101.0],
                "close": [101.0, 104.0], "volume": [1000, 1200]
            }]}
        }]}}
        row = overseas.latest_valid_row(payload, "^NDX", "America/New_York")
        self.assertEqual(row["close"], 104.0)
        self.assertEqual(row["previous_close"], 101.0)

    def test_master_registry_contains_all_converted_evidence(self):
        master = (ROOT / "ETF规则_MASTER.md").read_text(encoding="utf-8")
        for marker in ("半导体设备ETF（561980）海外开盘定价残差证据", "缩量下跌但收离低点证据", "融资扩张×ETF相对反馈证据"):
            self.assertIn(marker, master)
        self.assertIn("V2.2.22", master)


if __name__ == "__main__":
    unittest.main()
