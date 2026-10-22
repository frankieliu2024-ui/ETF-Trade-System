import unittest

from scripts.run_scheduled_trade_review_actor import _derive_run_identity


class TradeReviewIdentityFallbackTests(unittest.TestCase):
    def test_manual_identity_is_stable_and_non_scheduled(self):
        first = _derive_run_identity(
            "manual-request-20261022-120000",
            "2026-10-22T12:00:00+08:00",
            "MANUAL_SCHEDULED_TASK",
        )
        second = _derive_run_identity(
            "manual-request-20261022-120000",
            "2026-10-22T12:00:00+08:00",
            "MANUAL_SCHEDULED_TASK",
        )
        self.assertEqual(first, second)
        self.assertTrue(first[0].startswith("trade-review-manual-"))
        self.assertFalse(first[0].startswith("scheduled_"))

    def test_automatic_identity_is_distinct(self):
        manual = _derive_run_identity(
            "same-request",
            "2026-10-22T12:00:00+08:00",
            "MANUAL_SCHEDULED_TASK",
        )
        automatic = _derive_run_identity(
            "same-request",
            "2026-10-22T12:00:00+08:00",
            "AUTOMATIC_SCHEDULED_TASK",
        )
        self.assertNotEqual(manual, automatic)
        self.assertTrue(automatic[0].startswith("trade-review-automatic-"))


if __name__ == "__main__":
    unittest.main()
