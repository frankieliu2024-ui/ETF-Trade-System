import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.market_quote_router import build_market_quote_context


class QueryTimeRefreshTests(unittest.TestCase):
    def _root(self, timestamp: str):
        root = Path(tempfile.mkdtemp())
        (root / "data/state").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / "data/state/CURRENT.json").write_text(json.dumps({"latest_snapshot": ""}), encoding="utf-8")
        (root / "data/state/overseas_context.json").write_text(json.dumps({
            "objects": {"NDX": {"name": "NDX", "market_timezone": "America/New_York", "latest": {
                "close": 100, "as_of_beijing": timestamp, "as_of_local": timestamp, "session": "REGULAR"
            }}}
        }), encoding="utf-8")
        (root / "data/state/us_extended_hours_context.json").write_text(json.dumps({"objects": {}}), encoding="utf-8")
        (root / "config/runtime_policy.json").write_text(json.dumps({
            "fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500,
            "query_time_refresh_preference": {"active_market_max_age_seconds": 300}
        }), encoding="utf-8")
        return root

    def test_fresh_cache_does_not_call_provider(self):
        root = self._root("2026-08-25T00:29:00+08:00")
        now = datetime.fromisoformat("2026-08-25T00:30:00+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes") as refresh:
            result = build_market_quote_context(root, now=now, force_refresh=True, requested_symbols=["NDX"])
        refresh.assert_not_called()
        self.assertEqual(result["refresh_mode"], "CACHED_STATE")

    def test_stale_cache_calls_provider_and_prefers_new_quote(self):
        root = self._root("2026-08-24T23:00:00+08:00")
        now = datetime.fromisoformat("2026-08-25T00:30:00+08:00")
        fresh = {"symbol": "NDX", "market": "US", "latest_price": 101, "data_time_beijing": "2026-08-25T00:29:30+08:00", "market_phase": "REGULAR", "market_status_cn": "美股交易中", "data_nature_cn": "实时交易行情", "freshness": "FRESH", "source": "QUERY_TIME_PROVIDER"}
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [fresh], "failures": []}) as refresh:
            result = build_market_quote_context(root, now=now, force_refresh=True, requested_symbols=["NDX"])
        refresh.assert_called_once()
        self.assertEqual(result["refresh_mode"], "QUERY_TIME_IMMEDIATE_REFRESH")
        self.assertEqual([q["latest_price"] for q in result["quotes"] if q["symbol"] == "NDX"], [101])

    def test_failed_refresh_keeps_cached_quote_and_exposes_failure(self):
        root = self._root("2026-08-24T23:00:00+08:00")
        now = datetime.fromisoformat("2026-08-25T00:30:00+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [], "failures": [{"symbol": "NDX", "error": "provider timeout"}]}):
            result = build_market_quote_context(root, now=now, force_refresh=True, requested_symbols=["NDX"])
        self.assertEqual(result["refresh_mode"], "QUERY_TIME_IMMEDIATE_REFRESH")
        self.assertTrue(result["refresh_failures"])
        self.assertEqual(result["quotes"][0]["symbol"], "NDX")
        self.assertEqual(result["quotes"][0]["latest_price"], 100)
        self.assertEqual(result["quotes"][0]["freshness"], "STALE")


if __name__ == "__main__":
    unittest.main()
