from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tencent_stock_minute as mod


class StockMinuteOverlayTest(unittest.TestCase):
    def _base_feature(self, *, live_pass: bool) -> dict:
        return {
            "status": "READY",
            "item_quality_pass": live_pass,
            "recent_volume_delta": 123.0,
            "recent_amount_delta": 4567.0,
            "point_in_time": {"pass": True},
            "sampling": {"continuity_pass": True},
            "quote_alignment": {"abs_diff_pct": 0.0},
        }

    @patch.object(mod, "quality_requirements", return_value={"quote_alignment_max_abs_diff_pct": 0.25})
    @patch.object(mod, "fetch_one")
    def test_live_stock_minute_normalizes_hand_to_share(self, fetch_one, _requirements):
        fetch_one.return_value = self._base_feature(live_pass=True)
        row = {"code": "300750", "name": "宁德时代", "thscode": "300750.SZ", "close": 368.5, "provider_timestamp_ms": 1787901150000, "as_of_beijing": "2026-08-28T14:58:30+08:00", "market_phase": "CONTINUOUS_AFTERNOON"}
        feature = mod.build_stock_minute_feature(Path("."), row, "2026-08-28", row["market_phase"])
        self.assertTrue(feature["production_usable"])
        self.assertEqual(feature["recent_volume_delta_raw_hand"], 123.0)
        self.assertEqual(feature["recent_volume_delta"], 12300.0)
        self.assertEqual(feature["volume_unit"], "share")
        self.assertEqual(feature["volume_normalization_factor"], 100)
        self.assertTrue(feature["formal_latest_price_source_unchanged"])

    @patch.object(mod, "quality_requirements", return_value={"quote_alignment_max_abs_diff_pct": 0.25})
    @patch.object(mod, "fetch_one")
    def test_post_close_terminal_path_can_be_used_without_live_alignment(self, fetch_one, _requirements):
        fetch_one.return_value = self._base_feature(live_pass=False)
        row = {"code": "601138", "name": "工业富联", "thscode": "601138.SH", "close": 64.04, "provider_timestamp_ms": 1787901165000, "as_of_beijing": "2026-08-28T15:12:45+08:00", "market_phase": "POST_CLOSE_GRACE"}
        feature = mod.build_stock_minute_feature(Path("."), row, "2026-08-28", row["market_phase"])
        self.assertTrue(feature["production_usable"])
        self.assertEqual(feature["production_selection_reason"], "same_day_terminal_path_price_aligned")

    def test_overlay_keeps_trade_boundary_explicit(self):
        text = (Path(__file__).resolve().parents[1] / "scripts" / "tencent_stock_minute.py").read_text(encoding="utf-8")
        self.assertIn("正式最新价仍由quote router/个股quote链决定", text)
        self.assertIn("不独立产生ETF或个股交易动作", text)
        self.assertNotIn("自动下单", text)


if __name__ == "__main__":
    unittest.main()
