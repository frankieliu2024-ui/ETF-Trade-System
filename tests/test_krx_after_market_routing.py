import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from scripts.market_quote_router import market_phase, route
class KRXAfterMarketRoutingTests(unittest.TestCase):
    def dt(self, value):
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    def test_qualified_stock_enters_after_market(self):
        now=self.dt("2026-09-14T16:00:00")
        self.assertEqual(market_phase("KR",now,object_type="STOCK"),"POST_MARKET")
        self.assertEqual(route("KR",now=now,object_type="STOCK").phase,"POST_MARKET")
    def test_index_etf_etn_do_not_inherit_after_market(self):
        now=self.dt("2026-09-14T16:00:00")
        for kind in ("INDEX","ETF","ETN"):
            self.assertEqual(market_phase("KR",now,object_type=kind),"OFF_SESSION")
    def test_stock_after_market_closes_at_2000(self):
        self.assertEqual(market_phase("KR",self.dt("2026-09-14T20:00:00"),object_type="STOCK"),"OFF_SESSION")
if __name__=="__main__":
    unittest.main()
