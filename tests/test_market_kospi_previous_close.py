import unittest

from scripts.build_overseas_context import derive_naver_kospi_previous_close


class KOSPIPreviousCloseTests(unittest.TestCase):
    def test_derive_falling_previous_close(self):
        row = {
            "closePriceRaw": "6665.37",
            "compareToPreviousClosePriceRaw": "123.51",
            "compareToPreviousPrice": {"name": "FALLING"},
        }
        self.assertAlmostEqual(derive_naver_kospi_previous_close(row), 6788.88, places=6)

    def test_derive_rising_previous_close(self):
        row = {
            "closePriceRaw": "6927.84",
            "compareToPreviousClosePriceRaw": "119.63",
            "compareToPreviousPrice": {"name": "RISING"},
        }
        self.assertAlmostEqual(derive_naver_kospi_previous_close(row), 6808.21, places=6)

    def test_unknown_direction_does_not_fabricate_previous_close(self):
        row = {
            "closePriceRaw": "6665.37",
            "compareToPreviousClosePriceRaw": "123.51",
            "compareToPreviousPrice": {"name": "UNKNOWN"},
        }
        self.assertIsNone(derive_naver_kospi_previous_close(row))



if __name__ == "__main__":
    unittest.main()
