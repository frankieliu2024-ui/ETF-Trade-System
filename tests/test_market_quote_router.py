import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.market_quote_router import display_market_status, market_phase, route


BEIJING = ZoneInfo("Asia/Shanghai")


def at_beijing(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 26, hour, minute, tzinfo=BEIJING)


class TestMarketQuoteRouter(unittest.TestCase):
    def test_us_regular_0030_beijing(self):
        self.assertEqual(market_phase("US", at_beijing(0, 30)), "REGULAR")
        self.assertEqual(display_market_status("US", "REGULAR"), "美股交易中")

    def test_us_premarket_2000_beijing(self):
        self.assertEqual(market_phase("US", at_beijing(20, 0)), "PRE_MARKET")
        self.assertEqual(display_market_status("US", "PRE_MARKET"), "美股盘前")

    def test_us_postmarket_0500_beijing(self):
        # 05:00 Beijing is 17:00 New York during August daylight time.
        # It is therefore post-market, not off-session.
        self.assertEqual(market_phase("US", at_beijing(5, 0)), "POST_MARKET")
        self.assertEqual(display_market_status("US", "POST_MARKET"), "美股盘后")

    def test_a_share_regular_session(self):
        self.assertEqual(market_phase("CN", at_beijing(10, 0)), "REGULAR")
        self.assertEqual(display_market_status("CN", "REGULAR"), "A股交易中")

    def test_a_share_opening_auction(self):
        self.assertEqual(market_phase("CN", at_beijing(9, 20)), "OPENING_AUCTION")
        self.assertEqual(route("CN", now=at_beijing(9, 20)).market_status_cn, "A股开盘前（集合竞价）")

    def test_us_route_selection_is_phase_specific(self):
        self.assertEqual(route("US", now=at_beijing(0, 30)).source_priority[0], "overseas_context")
        self.assertEqual(route("US", now=at_beijing(20, 0)).source_priority[0], "us_extended_hours_context")
        self.assertEqual(route("US", now=at_beijing(5, 0)).source_priority[0], "us_extended_hours_context")
