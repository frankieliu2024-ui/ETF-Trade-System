import json
import unittest
from unittest import mock

from scripts.tencent_quote import fetch_tencent_daily_history


class _Response:
    def __init__(self, payload: str, status: int = 200):
        self.payload = payload.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class TencentHistoryTest(unittest.TestCase):
    def test_parses_unadjusted_day_rows(self):
        payload = {
            "code": 0,
            "data": {
                "sh510300": {
                    "day": [
                        ["2026-09-10", "4.100", "4.120", "4.150", "4.080", "12345"],
                        ["2026-09-11", "4.120", "4.090", "4.130", "4.060", "15000"],
                    ]
                }
            },
        }
        with mock.patch(
            "scripts.tencent_quote.urllib.request.urlopen",
            return_value=_Response(json.dumps(payload)),
        ) as mocked:
            rows, meta = fetch_tencent_daily_history(
                "510300.SH", "2026-09-10", "2026-09-11", page_size=500
            )
        self.assertEqual([row["date"] for row in rows], ["2026-09-10", "2026-09-11"])
        self.assertEqual(rows[0]["open"], 4.1)
        self.assertEqual(rows[0]["amount"], None)
        self.assertEqual(meta["provider"], "tencent_qq_history")
        self.assertTrue(meta["production_provider_priority_unchanged"])
        requested_url = mocked.call_args.args[0].full_url
        self.assertIn("fqkline/get", requested_url)
        self.assertNotIn("%2Cqfq", requested_url)

    def test_parses_qfq_jsonp_and_prefers_qfqday(self):
        payload = {
            "code": 0,
            "data": {
                "sz159915": {
                    "qfqday": [
                        ["2026-09-11", "3.000", "3.050", "3.080", "2.990", "8000"]
                    ]
                }
            },
        }
        raw = "kline_dayqfq=" + json.dumps(payload, ensure_ascii=False) + ";"
        with mock.patch(
            "scripts.tencent_quote.urllib.request.urlopen",
            return_value=_Response(raw),
        ):
            rows, meta = fetch_tencent_daily_history(
                "159915.SZ", "2026-09-11", "2026-09-11", adjustment="qfq"
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 3.05)
        self.assertEqual(meta["adjustment"], "前复权 qfq")

    def test_splits_long_range_into_bounded_calendar_windows(self):
        first_window = {
            "code": 0,
            "data": {
                "sh510300": {
                    "day": [
                        ["2025-01-02", "3.9", "4.0", "4.1", "3.8", "90"],
                        ["2025-12-31", "4.0", "4.1", "4.2", "3.9", "95"],
                    ]
                }
            },
        }
        second_window = {
            "code": 0,
            "data": {
                "sh510300": {
                    "day": [
                        ["2026-01-02", "4.1", "4.2", "4.3", "4.0", "100"],
                        ["2026-01-10", "4.2", "4.3", "4.4", "4.1", "110"],
                    ]
                }
            },
        }
        with mock.patch(
            "scripts.tencent_quote.urllib.request.urlopen",
            side_effect=[_Response(json.dumps(first_window)), _Response(json.dumps(second_window))],
        ) as mocked:
            rows, meta = fetch_tencent_daily_history(
                "510300.SH", "2025-01-01", "2026-01-10", window_days=366
            )
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["date"], "2025-01-02")
        self.assertEqual(rows[-1]["date"], "2026-01-10")
        self.assertEqual(meta["pages"], 2)
        self.assertEqual(mocked.call_count, 2)
        first_url = mocked.call_args_list[0].args[0].full_url
        second_url = mocked.call_args_list[1].args[0].full_url
        self.assertIn("2025-01-01", first_url)
        self.assertIn("2026-01-01", second_url)

    def test_rejects_unsupported_symbol(self):
        with self.assertRaises(RuntimeError):
            fetch_tencent_daily_history("NDX", "2026-09-01", "2026-09-11")


if __name__ == "__main__":
    unittest.main()
