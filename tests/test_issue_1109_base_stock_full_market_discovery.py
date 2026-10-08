import unittest

from scripts.discover_ipo_base_stock_candidates import (
    bounded_candidates,
    cheap_eligible,
    is_a_share_code,
)


class BaseStockFullMarketDiscoveryTest(unittest.TestCase):
    def test_same_market_a_share_identity_boundary(self):
        self.assertTrue(is_a_share_code("600900", "SH"))
        self.assertTrue(is_a_share_code("688981", "SH"))
        self.assertTrue(is_a_share_code("000333", "SZ"))
        self.assertTrue(is_a_share_code("300750", "SZ"))
        self.assertFalse(is_a_share_code("513180", "SH"))
        self.assertFalse(is_a_share_code("159941", "SZ"))
        self.assertFalse(is_a_share_code("600900", "SZ"))

    def test_held_and_non_executable_are_rejected(self):
        row = {"code":"600900","name":"长江电力","price":29.0,"amount":2e9}
        self.assertFalse(cheap_eligible(row, {"600900"}))
        self.assertFalse(cheap_eligible({**row, "code":"600036", "price":None}, set()))
        self.assertFalse(cheap_eligible({**row, "code":"600036", "amount":0}, set()))
        self.assertFalse(cheap_eligible({**row, "code":"600036", "name":"*ST样本"}, set()))

    def test_bounded_supply_is_research_only_and_diverse(self):
        rows = [
            {"code":"600001","name":"A","price":10,"amount":2e9,"amplitude_pct":4,"return_60d_pct":20},
            {"code":"600002","name":"B","price":10,"amount":5e8,"amplitude_pct":1,"return_60d_pct":20},
            {"code":"600003","name":"C","price":10,"amount":5e8,"amplitude_pct":4,"return_60d_pct":5},
            {"code":"600004","name":"D","price":10,"amount":5e8,"amplitude_pct":4,"return_60d_pct":30},
        ]
        got = bounded_candidates(rows, set(), 4)
        self.assertEqual({x["evidence_bucket"] for x in got}, {
            "HIGH_LIQUIDITY","LOW_INTRADAY_VARIATION","MIDDLE_60D_PATH","OTHER_EXECUTABLE"
        })
        self.assertTrue(all(x["authority"] == "RESEARCH_CANDIDATE_ONLY" for x in got))

    def test_zero_candidate_is_legal(self):
        rows = [{"code":"600900","name":"长江电力","price":29,"amount":2e9}]
        self.assertEqual(bounded_candidates(rows, {"600900"}), [])


if __name__ == "__main__":
    unittest.main()
