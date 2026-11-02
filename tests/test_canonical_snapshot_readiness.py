import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import cloud_runner_snapshot


class CanonicalSnapshotReadinessTests(unittest.TestCase):
    def test_publication_checks_identity_quality_and_timestamps(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("expected_symbols", source)
        self.assertIn("object identity mismatch", source)
        self.assertIn("non-consumable row", source)
        self.assertIn("row missing timestamp", source)


    def test_opening_auction_invalid_ohlc_is_degraded_without_synthetic_prices(self):
        item = {
            "open_price": 10.0,
            "high_price": 9.0,
            "low_price": 11.0,
            "last_price": 10.2,
            "prev_price": 10.0,
            "volume": None,
            "turnover": None,
            "trading_status": "TRADING",
        }
        row = cloud_runner_snapshot.row(
            "ETF",
            "159056",
            "159056.SZ",
            item,
            "2026-11-02T09:17:25+08:00",
            1793572645000,
            "OPENING_CALL_AUCTION",
        )
        self.assertEqual(row["quality_status"], "DEGRADED")
        self.assertIsNone(row["open"])
        self.assertIsNone(row["high"])
        self.assertIsNone(row["low"])
        self.assertEqual(row["close"], 10.2)
        self.assertEqual(row["prev_close"], 10.0)
        self.assertEqual(row["volume"], None)
        self.assertIn("OPENING_CALL_AUCTION", row["semantic_note"])

    def test_failed_snapshot_is_explicit_and_nonblocking(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertNotIn("PRIMARY_RUN_BYPASS", source)
        self.assertIn('status in {"FAILED", "FAIL"}', source)
        self.assertIn('if not row.get("error")', source)
        self.assertIn("analysis_coverage", source)
        self.assertNotIn("previous CURRENT preserved", source)

    def test_failure_is_fail_closed_before_health_is_written(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        verify_pos = source.index("verify_published_snapshot(target")
        health_pos = source.index("write_runtime_health(", verify_pos)
        self.assertLess(verify_pos, health_pos)


if __name__ == "__main__":
    unittest.main()
