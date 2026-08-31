import unittest

from scripts.enrich_overseas_previous_close import derive_previous_close, enrich_context


class KOSPIPreviousCloseTests(unittest.TestCase):
    def test_derive_falling_previous_close(self):
        row = {
            "closePriceRaw": "6665.37",
            "compareToPreviousClosePriceRaw": "123.51",
            "compareToPreviousPrice": {"name": "FALLING"},
        }
        self.assertAlmostEqual(derive_previous_close(row), 6788.88, places=6)

    def test_derive_rising_previous_close(self):
        row = {
            "closePriceRaw": "6927.84",
            "compareToPreviousClosePriceRaw": "119.63",
            "compareToPreviousPrice": {"name": "RISING"},
        }
        self.assertAlmostEqual(derive_previous_close(row), 6808.21, places=6)

    def test_enriches_only_aligned_naver_context(self):
        context = {
            "objects": {
                "KOSPI": {
                    "quality_status": "PASS",
                    "provider": "naver_finance",
                    "latest": {
                        "close": 6665.37,
                        "market_date_local": "2026-08-31",
                    },
                }
            }
        }
        row = {
            "closePriceRaw": "6665.37",
            "compareToPreviousClosePriceRaw": "123.51",
            "compareToPreviousPrice": {"name": "FALLING"},
            "fluctuationsRatioRaw": "1.82",
            "localTradedAt": "2026-08-31T09:51:13+09:00",
        }
        enriched, status = enrich_context(context, row)
        self.assertEqual(status, "ENRICHED")
        kospi = enriched["objects"]["KOSPI"]
        self.assertAlmostEqual(kospi["latest"]["previous_close"], 6788.88, places=6)
        self.assertEqual(kospi["previous_close_reference_source"], "naver_finance:compareToPreviousClosePrice")


if __name__ == "__main__":
    unittest.main()
