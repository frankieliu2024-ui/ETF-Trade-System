from __future__ import annotations

import errno
import unittest
from datetime import date
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import build_if_ic_basis_evidence as mod


class IfIcCffexFastFailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.start = date(2026, 8, 3)
        self.end = date(2026, 8, 27)
        self.calendar = {"closed_dates": []}

    def test_network_unreachable_skips_same_host_monthly_fallback(self) -> None:
        exc = URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))
        with patch.object(mod, "_http", side_effect=exc) as http, patch.object(mod, "_fetch_month") as monthly:
            with self.assertRaisesRegex(RuntimeError, "same-host monthly fallback skipped"):
                mod._fetch_futures(self.start, self.end, self.calendar)
        self.assertEqual(http.call_count, 1)
        monthly.assert_not_called()

    def test_http_failure_still_attempts_official_monthly_fallback(self) -> None:
        exc = HTTPError("https://www.cffex.com.cn/test", 502, "Bad Gateway", None, None)
        with patch.object(mod, "_http", side_effect=exc), patch.object(mod, "_fetch_month", return_value=[]) as monthly:
            with self.assertRaises(RuntimeError):
                mod._fetch_futures(self.start, self.end, self.calendar)
        self.assertGreaterEqual(monthly.call_count, 1)

    def test_timeout_like_error_does_not_disable_monthly_fallback(self) -> None:
        exc = URLError(TimeoutError("timed out"))
        with patch.object(mod, "_http", side_effect=exc), patch.object(mod, "_fetch_month", return_value=[]) as monthly:
            with self.assertRaises(RuntimeError):
                mod._fetch_futures(self.start, self.end, self.calendar)
        self.assertGreaterEqual(monthly.call_count, 1)


if __name__ == "__main__":
    unittest.main()
