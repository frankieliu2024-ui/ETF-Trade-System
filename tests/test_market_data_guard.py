import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scripts.market_data_guard import classify_provider_failure, validate_market_row


BEIJING = ZoneInfo("Asia/Shanghai")


class MarketDataGuardTests(unittest.TestCase):
    def test_failure_classes(self):
        self.assertEqual(classify_provider_failure("FUYAO_1002 Unknown thscode"), "STRUCTURAL")
        self.assertEqual(classify_provider_failure("timeout after 25s"), "TRANSIENT")
        self.assertEqual(classify_provider_failure("missing provider timestamp"), "QUALITY")

    def test_valid_direct_row(self):
        now = datetime.now(timezone.utc)
        row = {
            "symbol": "399006",
            "provider_timestamp_ms": int((now - timedelta(seconds=10)).timestamp() * 1000),
            "open": 100,
            "high": 105,
            "low": 99,
            "close": 103,
            "prev_close": 101,
            "volume": 10,
            "amount": 1000,
            "provider": "hithink-finance",
        }
        ok, reason = validate_market_row(
            row, "399006", market_date=now.astimezone(BEIJING).date().isoformat(), now=now,
            runtime_policy={"fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500},
        )
        self.assertTrue(ok, reason)
        self.assertEqual(row["freshness_status"], "FRESH")

    def test_proxy_rejected_for_direct_object(self):
        now = datetime.now(timezone.utc)
        row = {
            "symbol": "399006",
            "provider_timestamp_ms": int(now.timestamp() * 1000),
            "open": 100, "high": 105, "low": 99, "close": 103,
            "prev_close": 101, "volume": 10, "amount": 1000,
            "provider": "ETF_PROXY_399006",
        }
        ok, reason = validate_market_row(
            row,
            "399006",
            market_date=now.astimezone(BEIJING).date().isoformat(),
            now=now,
        )
        self.assertFalse(ok)
        self.assertIn("proxy", reason)


if __name__ == "__main__":
    unittest.main()
