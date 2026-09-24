import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.trading_time_semantics import (
    is_a_share_trading_day,
    next_a_share_trading_day,
    user_visible_trading_time_anchor,
)


class TradingTimeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-12-31",
            "closed_dates": ["2026-09-25", "2026-09-26", "2026-09-27"],
        }

    def test_mid_autumn_holiday_does_not_call_tomorrow_a_trading_day(self):
        anchor = user_visible_trading_time_anchor(date(2026, 9, 24), self.calendar)
        self.assertFalse(anchor["tomorrow_is_a_share_trading_day"])
        self.assertEqual(anchor["next_a_share_trading_date"], "2026-09-28")
        self.assertEqual(anchor["preferred_future_action_wording"], "下一交易日")
        self.assertIn("2026-09-28", anchor["explicit_date_wording"])

    def test_adjacent_normal_trading_day(self):
        anchor = user_visible_trading_time_anchor(date(2026, 9, 28), self.calendar)
        self.assertTrue(anchor["tomorrow_is_a_share_trading_day"])
        self.assertEqual(anchor["next_a_share_trading_date"], "2026-09-29")

    def test_weekend_chain(self):
        self.assertEqual(
            next_a_share_trading_day(date(2026, 9, 25), self.calendar),
            date(2026, 9, 28),
        )

    def test_outside_calendar_coverage_fails_closed(self):
        with self.assertRaises(ValueError):
            next_a_share_trading_day(date(2026, 12, 31), self.calendar)


if __name__ == "__main__":
    unittest.main()
