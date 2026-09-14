import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.market_quote_router import market_phase


class KRXAfterMarketCorrectionTests(unittest.TestCase):
    def dt(self, value):
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_qualified_stock_after_market_window(self):
        self.assertEqual(market_phase("KR", self.dt("2026-09-14T16:00:00"), object_type="STOCK"), "POST_MARKET")
        self.assertEqual(market_phase("KR", self.dt("2026-09-14T19:59:00"), object_type="STOCK"), "POST_MARKET")

    def test_index_etf_etn_remain_off_session(self):
        for kind in ("INDEX", "ETF", "ETN"):
            self.assertEqual(market_phase("KR", self.dt("2026-09-14T16:00:00"), object_type=kind), "OFF_SESSION")

    def test_stock_window_boundaries(self):
        self.assertEqual(market_phase("KR", self.dt("2026-09-14T15:30:00"), object_type="STOCK"), "OFF_SESSION")
        self.assertEqual(market_phase("KR", self.dt("2026-09-14T20:00:00"), object_type="STOCK"), "OFF_SESSION")


if __name__ == "__main__":
    unittest.main()
