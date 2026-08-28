from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_minute_path_features as minute_path
import build_state_context as state_context


BEIJING = ZoneInfo("Asia/Shanghai")


class TencentMinutePointInTimeTest(unittest.TestCase):
    def test_future_minute_bucket_is_excluded(self):
        points = [
            {"dt": datetime(2026, 8, 28, 14, 35, tzinfo=BEIJING), "price": 1.0},
            {"dt": datetime(2026, 8, 28, 14, 36, tzinfo=BEIJING), "price": 1.1},
            {"dt": datetime(2026, 8, 28, 14, 37, tzinfo=BEIJING), "price": 1.2},
        ]
        quote_dt = datetime(2026, 8, 28, 14, 36, 27, tzinfo=BEIJING)
        quote = {"provider_timestamp_ms": int(quote_dt.timestamp() * 1000), "last_price": 1.1}
        usable, diagnostic = minute_path.point_in_time_filter(points, quote, now_bj=quote_dt)
        self.assertEqual([x["dt"].minute for x in usable], [35, 36])
        self.assertEqual(diagnostic["excluded_future_bucket_count"], 1)
        self.assertEqual(diagnostic["cutoff_source"], "SIMULTANEOUS_TENCENT_QUOTE")

    def test_quality_thresholds_are_loaded_from_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config/minute_path_validation_policy.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"quality_requirements": {
                "etf_coverage_ratio": 1.0,
                "freshness_max_seconds": 123,
                "quote_alignment_max_abs_diff_pct": 0.12,
                "quote_comparable_max_seconds": 45,
            }}), encoding="utf-8")
            q = minute_path.quality_requirements(root)
            self.assertEqual(q["freshness_max_seconds"], 123.0)
            self.assertEqual(q["quote_alignment_max_abs_diff_pct"], 0.12)
            self.assertEqual(q["quote_comparable_max_seconds"], 45.0)


class TencentMinuteObjectFallbackTest(unittest.TestCase):
    def test_single_failed_etf_only_falls_back_for_that_object(self):
        discrete = {
            "schema_version": "1.0",
            "mode": "DISCRETE",
            "status": "READY",
            "features": [
                {"symbol": "IDX", "asset_class": "A_SHARE_INDEX", "path_change_pct": 0.5},
                {"symbol": "AAA", "asset_class": "ETF", "path_change_pct": 1.0, "sampling": {"coverage": "MEDIUM"}},
                {"symbol": "BBB", "asset_class": "ETF", "path_change_pct": 2.0, "sampling": {"coverage": "MEDIUM"}},
            ],
        }
        minute = {
            "status": "DEGRADED",
            "coverage_ratio": 0.5,
            "quality_summary": {"formal_gate_pass": False, "production_usable_ratio": 0.5},
            "features": [
                {
                    "symbol": "AAA",
                    "status": "READY",
                    "item_quality_pass": True,
                    "freshness_pass": True,
                    "sampling": {"continuity_pass": True, "coverage": "HIGH"},
                    "quote_alignment": {"pass": True},
                    "point_in_time": {"pass": True},
                    "path_change_pct": 9.0,
                },
                {"symbol": "BBB", "status": "FAILED", "item_quality_pass": False, "error": "injected failure"},
            ],
        }
        with patch.object(state_context, "build_intraday_path_features", return_value=discrete), patch.object(state_context, "build_minute_path_features", return_value=minute):
            selected, diag = state_context.select_intraday_path_features(ROOT)
        by_symbol = {x["symbol"]: x for x in selected["features"]}
        self.assertEqual(selected["production_selection"]["selected_source"], "HYBRID_OBJECT_LEVEL")
        self.assertEqual(by_symbol["AAA"]["production_source"], "TENCENT_1M")
        self.assertEqual(by_symbol["BBB"]["production_source"], "DISCRETE_SNAPSHOT_PATH")
        self.assertEqual(by_symbol["IDX"]["path_role"], "PRODUCTION_NATIVE_DISCRETE_EVIDENCE")
        self.assertNotIn("fallback_reason", by_symbol["IDX"])
        self.assertEqual(diag["minute_selected_count"], 1)
        self.assertEqual(diag["fallback_count"], 1)
        self.assertEqual(diag["fallback_symbols"], ["BBB"])
        self.assertEqual(diag["native_discrete_symbols"], ["IDX"])

    def test_all_target_etfs_on_minute_with_native_index_reports_tencent(self):
        discrete = {
            "status": "READY",
            "features": [
                {"symbol": "IDX", "asset_class": "A_SHARE_INDEX"},
                {"symbol": "AAA", "asset_class": "ETF"},
            ],
        }
        minute = {
            "status": "READY",
            "coverage_ratio": 1.0,
            "quality_summary": {"formal_gate_pass": True, "production_usable_ratio": 1.0},
            "features": [{
                "symbol": "AAA", "status": "READY", "item_quality_pass": True, "freshness_pass": True,
                "sampling": {"continuity_pass": True}, "quote_alignment": {"pass": True}, "point_in_time": {"pass": True},
            }],
        }
        with patch.object(state_context, "build_intraday_path_features", return_value=discrete), patch.object(state_context, "build_minute_path_features", return_value=minute):
            selected, diag = state_context.select_intraday_path_features(ROOT)
        self.assertEqual(selected["production_selection"]["selected_source"], "TENCENT_1M")
        self.assertEqual(diag["fallback_count"], 0)
        self.assertEqual(diag["native_discrete_count"], 1)

    def test_systemic_minute_failure_preserves_discrete_path(self):
        discrete = {"schema_version": "1.0", "mode": "DISCRETE", "status": "READY", "features": [{"symbol": "AAA", "asset_class": "ETF", "path_change_pct": 1.0}]}
        with patch.object(state_context, "build_intraday_path_features", return_value=discrete), patch.object(state_context, "build_minute_path_features", side_effect=RuntimeError("provider down")):
            selected, diag = state_context.select_intraday_path_features(ROOT)
        self.assertEqual(selected["production_selection"]["selected_source"], "DISCRETE_SNAPSHOT_PATH")
        self.assertEqual(selected["features"][0]["production_source"], "DISCRETE_SNAPSHOT_PATH")
        self.assertEqual(diag["fallback_count"], 1)


if __name__ == "__main__":
    unittest.main()
