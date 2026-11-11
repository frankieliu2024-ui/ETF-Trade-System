import unittest

from scripts.security_display import security_display_name


class SecurityDisplayTests(unittest.TestCase):
    def test_canonical_name_and_code(self):
        self.assertEqual(security_display_name("科创50ETF", "588000"), "科创50ETF（588000）")

    def test_does_not_duplicate_embedded_code(self):
        self.assertEqual(security_display_name("科创50ETF（588000）", "588000"), "科创50ETF（588000）")

    def test_code_only_is_preserved(self):
        self.assertEqual(security_display_name("", "601899"), "601899")


if __name__ == "__main__":
    unittest.main()
