from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import send_market_shock_notification as shock
import send_us_session_summary as us
from session_notification_semantics import us_session_structure


class UsNotificationPresentationTests(unittest.TestCase):
    def test_open_title_merges_gap_and_extreme_current_level(self):
        tech = {"regular_session_change_vs_previous_close_pct": -0.40, "regular_session_change_from_open_pct": -0.05}
        semi = {"regular_session_change_vs_previous_close_pct": -1.60, "regular_session_change_from_open_pct": -0.32}
        title = us._open_title(
            "纳斯达克100指数（NDX）",
            "费城半导体指数（SOX）",
            -0.33,
            -1.28,
            tech,
            semi,
        )
        self.assertIn("低开-1.28%", title)
        self.assertIn("当前-1.60%", title)

    def test_open_fact_lines_are_layered(self):
        lines = us._open_fact_lines(
            "纳斯达克100指数（NDX）",
            "费城半导体指数（SOX）",
            -0.33,
            -1.28,
            -0.41,
            -1.60,
            "实际现金盘开盘最大跳空1.28%",
        )
        self.assertTrue(any("开盘事实｜费城半导体指数" in line for line in lines))
        self.assertTrue(any("当前进展｜费城半导体指数" in line for line in lines))

    def test_cash_close_time_uses_market_session_close_not_post_market_quote_time(self):
        # 2026-08-28 is during US daylight saving time: 16:00 New York = 04:00 Beijing next day.
        self.assertEqual(us._regular_cash_close_beijing("2026-08-28"), "2026-08-29 04:00:00")

    def test_us_structure_separates_index_path_lines(self):
        tech = {
            "regular_session_change_vs_previous_close_pct": -0.71,
            "regular_session_change_from_open_pct": -0.38,
            "regular_session_path": {"range_position": 0.12, "first_hour_change_pct": 0.65, "late_session_change_pct": -0.10},
        }
        semi = {
            "regular_session_change_vs_previous_close_pct": -3.48,
            "regular_session_change_from_open_pct": -2.23,
            "regular_session_path": {"range_position": 0.09, "first_hour_change_pct": 0.06, "late_session_change_pct": -0.40},
        }
        headline, path_lines, implication, _action, _tone = us_session_structure(
            tech=tech,
            semi=semi,
            tech_label="纳斯达克100指数（NDX）",
            semi_label="费城半导体指数（SOX）",
            direct=True,
            node="CLOSE",
        )
        self.assertGreaterEqual(len(path_lines), 2)
        self.assertTrue(any("宽科技" in line for line in headline))
        self.assertTrue(any("半导体" in line for line in headline))
        self.assertIn("\n- **本地验证**", implication)

    def test_recent_us_open_absorbs_same_pulse_extreme(self):
        event = {
            "security_code": "SOX",
            "confirmation_context": {
                "market": "US",
                "market_date": "2026-08-28",
                "direction": "DOWN",
                "event_category": "EXTREME",
            },
        }
        state = {
            "notifications": [
                {
                    "event_type": "US_OPEN_VALUE_ALERT",
                    "sent_at": "2026-08-28T22:17:20+08:00",
                    "confirmation_context": {
                        "us_market_date": "2026-08-28",
                        "tech_change_pct": -0.41,
                        "semi_change_pct": -1.60,
                    },
                }
            ]
        }
        fake_now = datetime(2026, 8, 28, 14, 17, 21, tzinfo=timezone.utc)
        with patch.object(shock._legacy, "read_json", return_value=state), patch.object(shock._legacy, "now", return_value=fake_now):
            self.assertTrue(shock._covered_by_recent_us_open(event))

    def test_divergence_upgrade_uses_spread_magnitude(self):
        event = {
            "security_code": "US_TECH_DIVERGENCE",
            "confirmation_context": {
                "market": "US",
                "market_date": "2026-08-28",
                "direction": "DIVERGED",
                "event_category": "DIVERGENCE",
                "event_magnitude_pct": 2.64,
            },
        }
        old_state = {
            "notifications": [
                {
                    "event_type": "MARKET_VALUE_ALERT",
                    "security_code": "US_TECH_DIVERGENCE",
                    "confirmation_context": {
                        "market_date": "2026-08-28",
                        "direction": "DIVERGED",
                        "event_category": "DIVERGENCE",
                        "event_magnitude_pct": 1.82,
                    },
                }
            ]
        }
        with patch.object(shock._legacy, "read_json", return_value=old_state):
            self.assertAlmostEqual(shock._recent_same_family_level(event), 1.82)
            self.assertAlmostEqual(shock._comparison_level(event["confirmation_context"], "DIVERGENCE"), 2.64)


if __name__ == "__main__":
    unittest.main()
