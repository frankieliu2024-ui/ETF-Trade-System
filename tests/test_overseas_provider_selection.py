from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_overseas_context as overseas  # noqa: E402


class OverseasProviderSelectionTests(unittest.TestCase):
    def test_selects_lowest_latency_stable_direct_source(self):
        candidates = [
            (
                {"quality_status": "PASS", "provider": "yahoo_chart_api"},
                {"provider": "yahoo_chart_api:HSTECH.HK", "as_of_beijing": "2026-08-24T12:05:00+08:00", "delay_minutes": 46.62, "stable_for_10m_pulse": True},
            ),
            (
                {"quality_status": "PASS", "provider": "eastmoney_push2"},
                {"provider": "eastmoney_push2:124.HSTECH", "as_of_beijing": "2026-08-24T12:51:40+08:00", "delay_minutes": 0.0, "stable_for_10m_pulse": True},
            ),
        ]
        selected = overseas.select_best_candidate(candidates)
        self.assertIsNotNone(selected)
        self.assertEqual(selected[1]["provider"], "eastmoney_push2:124.HSTECH")

    def test_prefers_stable_source_before_less_stale_degraded_source(self):
        candidates = [
            (
                {"quality_status": "PASS"},
                {"provider": "fresh_but_degraded", "as_of_beijing": "2026-08-24T12:54:00+08:00", "delay_minutes": 0.0, "stable_for_10m_pulse": False},
            ),
            (
                {"quality_status": "PASS"},
                {"provider": "stable_source", "as_of_beijing": "2026-08-24T12:50:00+08:00", "delay_minutes": 4.0, "stable_for_10m_pulse": True},
            ),
        ]
        selected = overseas.select_best_candidate(candidates)
        self.assertEqual(selected[1]["provider"], "stable_source")


if __name__ == "__main__":
    unittest.main()
