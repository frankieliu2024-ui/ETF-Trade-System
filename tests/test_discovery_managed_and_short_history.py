import unittest

from scripts.formal_etf_opportunity_discovery import _bounded_prefilter, classify_states


class DiscoveryManagedAndShortHistoryTests(unittest.TestCase):
    def test_held_etf_remains_in_history_prefilter(self):
        row = {
            "code": "159217",
            "name": "港股通创新药ETF工银",
            "price": 1.1,
            "amount": 50_000_000,
            "change_pct": 2.0,
            "return_60d_pct": 1.0,
            "volume_ratio": 1.2,
        }
        queued = _bounded_prefilter([row], held_codes={"159217"}, market_date="2026-11-06")
        self.assertEqual([item["code"] for item in queued], ["159217"])

    def test_classification_includes_five_day_window(self):
        closes = [100.0] * 65
        closes[-6] = 90.0
        closes[-1] = 100.0
        states = classify_states([{"close": value} for value in closes])
        short_term = next(item for item in states if item["state"] == "SHORT_TERM_CHANGE")
        self.assertIn("return_5", short_term["evidence"])
        self.assertIn("return_20", short_term["evidence"])


if __name__ == "__main__":
    unittest.main()
