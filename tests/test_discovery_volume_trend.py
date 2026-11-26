import unittest

from scripts.formal_etf_opportunity_discovery import _volume_amount_trend


class DiscoveryVolumeTrendTests(unittest.TestCase):
    def test_expansion_is_reported_without_provider_unit_assumption(self):
        rows = []
        for i in range(20):
            rows.append({
                "volume": 100.0 if i < 15 else 150.0,
                "amount": 1_000_000.0 if i < 15 else 1_500_000.0,
            })
        result = _volume_amount_trend(rows)
        self.assertEqual(result["volume"]["status"], "CONFIRMED")
        self.assertEqual(result["volume"]["trend"], "EXPANDING")
        self.assertEqual(result["amount"]["trend"], "EXPANDING")
        self.assertEqual(result["combined_signal"], "VOLUME_AND_AMOUNT_EXPANDING")
        self.assertEqual(result["volume"]["unit_comparison"], "WITHIN_SAME_PROVIDER_HISTORY_ONLY")

    def test_missing_fields_are_unconfirmed_not_contraction(self):
        rows = [{"volume": None, "amount": 1_000_000.0} for _ in range(20)]
        result = _volume_amount_trend(rows)
        self.assertEqual(result["volume"]["status"], "UNCONFIRMED")
        self.assertEqual(result["volume"]["reason"], "INSUFFICIENT_VALID_HISTORY")
        self.assertEqual(result["combined_signal"], "PARTIAL_OR_UNCONFIRMED")


if __name__ == "__main__":
    unittest.main()
