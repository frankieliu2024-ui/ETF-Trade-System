import unittest
from datetime import datetime, timezone
from scripts.build_overseas_context import OBJECTS, fetch_naver_korean_stock
from scripts.market_quote_router import market_phase


class NaverKrxStockTests(unittest.TestCase):
    def test_persistent_overseas_objects_keep_kospi_not_nonheld_kr_stocks(self):
        self.assertIn("KOSPI", OBJECTS)
        self.assertNotIn("005930", OBJECTS)
        self.assertNotIn("000660", OBJECTS)

    def test_verified_objects_remain_available_as_stock_capability(self):
        spec = {"name": "test", "role": "KOREA_STOCK", "asset_class": "STOCK"}
        for code in ("005930", "000660"):
            record = fetch_naver_korean_stock(code, spec, datetime(2026, 9, 14, 10, 23, tzinfo=timezone.utc))
            self.assertEqual(record["asset_class"], "STOCK")
            self.assertEqual(record["market_timezone"], "Asia/Seoul")
            self.assertEqual(record["latest"]["symbol"], code)
            self.assertEqual(record["latest"]["market_session_type"], "afterMarket")
            self.assertEqual(record["market_phase_at_generation"], "POST_MARKET")

    def test_router_boundaries(self):
        now = datetime.fromisoformat("2026-09-14T19:00:00+09:00")
        self.assertEqual(market_phase("KR", now, object_type="STOCK"), "POST_MARKET")
        self.assertEqual(market_phase("KR", now, object_type="INDEX"), "OFF_SESSION")


if __name__ == "__main__":
    unittest.main()
