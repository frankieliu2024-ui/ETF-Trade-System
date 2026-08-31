from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.query_intraday_history import extract_intraday_rows, summarize_window


class HistoricalIntradayPathTest(unittest.TestCase):
    def _ts(self, text: str) -> int:
        return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

    def test_extracts_beijing_window_and_summarizes_change(self) -> None:
        timestamps = [
            self._ts("2026-08-31T11:25:00"),
            self._ts("2026-08-31T11:30:00"),
            self._ts("2026-08-31T12:00:00"),
            self._ts("2026-08-31T13:00:00"),
            self._ts("2026-08-31T13:05:00"),
        ]
        payload = {
            "chart": {
                "result": [{
                    "timestamp": timestamps,
                    "indicators": {"quote": [{
                        "open": [99, 100, 101, 103, 104],
                        "high": [100, 101, 103, 105, 105],
                        "low": [98, 99, 100, 102, 103],
                        "close": [99.5, 100, 102, 104, 104.5],
                        "volume": [1, 2, 3, 4, 5],
                    }]},
                }]
            }
        }
        rows = extract_intraday_rows(
            payload,
            timezone_name="Asia/Seoul",
            market_date="2026-08-31",
            start_beijing="11:30",
            end_beijing="13:00",
        )
        self.assertEqual(3, len(rows))
        self.assertTrue(rows[0]["as_of_beijing"].startswith("2026-08-31T11:30:00"))
        self.assertTrue(rows[-1]["as_of_beijing"].startswith("2026-08-31T13:00:00"))
        summary = summarize_window(rows)
        self.assertEqual("PASS", summary["status"])
        self.assertAlmostEqual(4.0, summary["change_pct"], places=6)
        self.assertEqual(105.0, summary["window_high"])
        self.assertEqual(99.0, summary["window_low"])

    def test_empty_window_is_explicitly_missing(self) -> None:
        self.assertEqual({"status": "MISSING", "rows": 0}, summarize_window([]))


if __name__ == "__main__":
    unittest.main()
