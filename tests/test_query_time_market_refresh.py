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

    def _closed_reference_root(self, *, market_date: str, node: str, captured_at: str, closed_dates=None):
        root = self._root(captured_at)
        (root / "config/market").mkdir(parents=True, exist_ok=True)
        (root / "config/market/a_share_trading_calendar_2026.json").write_text(json.dumps({
            "coverage_start": "2026-01-01", "coverage_end": "2026-12-31",
            "closed_dates": list(closed_dates or []),
        }), encoding="utf-8")
        (root / "data/state/CURRENT.json").write_text(json.dumps({
            "market_date": market_date,
            "latest_valid_node": node,
            "captured_at": captured_at,
            "provider_as_of": captured_at,
            "quality_status": "PASS",
            "latest_snapshot": "",
            "data_freshness": {"status": "PASS", "captured_at_beijing": captured_at, "provider_as_of": captured_at},
        }), encoding="utf-8")
        return root

    def test_premarket_formal_request_reuses_prior_canonical_close(self):
        now = datetime.fromisoformat("2026-09-23T08:00:00+08:00")
        root = self._closed_reference_root(market_date="2026-09-22", node="close", captured_at="2026-09-22T15:00:05+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes") as refresh:
            result = build_market_quote_context(root, now=now, decision_request_time=now)
        refresh.assert_not_called()
        gate = result["decision_freshness"]
        self.assertTrue(gate["formal_decision_allowed"])
        self.assertTrue(gate["resolved_post_request"])
        self.assertEqual(gate["request_scoped_resolution_mode"], "CLOSED_SESSION_CANONICAL_REUSE")

    def test_midday_formal_request_accepts_reobserved_1130_reference_without_new_trade(self):
        request = datetime.fromisoformat("2026-09-23T11:55:00+08:00")
        now = datetime.fromisoformat("2026-09-23T11:56:00+08:00")
        root = self._closed_reference_root(market_date="2026-09-23", node="1130", captured_at="2026-09-23T11:55:46+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [], "failures": []}):
            result = build_market_quote_context(root, now=now, decision_request_time=request)
        gate = result["decision_freshness"]
        self.assertTrue(gate["formal_decision_allowed"])
        self.assertEqual(gate["request_scoped_resolution_mode"], "MIDDAY_REQUEST_BOUND_REFERENCE")

    def test_post_close_formal_request_reuses_a_share_close_but_refreshes_active_overseas(self):
        request = datetime.fromisoformat("2026-09-23T16:00:00+08:00")
        root = self._closed_reference_root(market_date="2026-09-23", node="close", captured_at="2026-09-23T15:00:05+08:00")
        fresh = {"symbol": "NDX", "market": "US", "latest_price": 25000, "data_time_beijing": "2026-09-23T16:00:10+08:00", "quality_status": "PASS", "freshness": "FRESH"}
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [fresh], "failures": []}) as refresh:
            result = build_market_quote_context(root, now=request, decision_request_time=request)
        refresh.assert_called_once()
        self.assertNotIn("000001", refresh.call_args.args[1])
        self.assertTrue(result["decision_freshness"]["formal_decision_allowed"])

    def test_weekend_formal_request_reuses_last_close_without_synthetic_cn_refresh(self):
        now = datetime.fromisoformat("2026-09-27T10:00:00+08:00")
        root = self._closed_reference_root(market_date="2026-09-25", node="close", captured_at="2026-09-25T15:00:05+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes") as refresh:
            result = build_market_quote_context(root, now=now, decision_request_time=now)
        refresh.assert_not_called()
        self.assertEqual(result["decision_freshness"]["request_scoped_resolution_mode"], "CLOSED_SESSION_CANONICAL_REUSE")

    def test_a_share_holiday_does_not_create_synthetic_cn_active_refresh(self):
        now = datetime.fromisoformat("2026-10-02T10:00:00+08:00")
        root = self._closed_reference_root(market_date="2026-09-30", node="close", captured_at="2026-09-30T15:00:05+08:00", closed_dates=["2026-10-02"])
        fresh = {"symbol": "N225", "market": "JP", "latest_price": 40000, "data_time_beijing": "2026-10-02T10:00:05+08:00", "quality_status": "PASS", "freshness": "FRESH"}
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [fresh], "failures": []}) as refresh:
            result = build_market_quote_context(root, now=now, requested_symbols=["N225"], decision_request_time=now)
        refresh.assert_called_once()
        self.assertTrue(all(not str(x).split(".")[0].isdigit() for x in refresh.call_args.args[1]))
        self.assertTrue(result["decision_freshness"]["formal_decision_allowed"])

    def test_explicit_force_refresh_calls_provider_even_with_fresh_cache(self):
        root = self._root("2026-08-25T00:29:00+08:00")
        now = datetime.fromisoformat("2026-08-25T00:30:00+08:00")
        with patch("scripts.query_time_market_refresh.refresh_market_quotes", return_value={"quotes": [], "failures": []}) as refresh:
            result = build_market_quote_context(root, now=now, force_refresh=True, requested_symbols=["NDX"])
        refresh.assert_called_once_with(root, ["NDX"], now)
        self.assertEqual(result["refresh_mode"], "QUERY_TIME_IMMEDIATE_REFRESH")

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

    def test_etf_provider_fallback_uses_direct_market_snapshot(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-08-26T02:00:00+08:00")
        now = datetime.fromisoformat("2026-08-26T10:15:00+08:00")
        direct = {
            "data": {
                "timestamp": 1787710490000,
                "item": [{
                    "thscode": "159941.SZ",
                    "name": "纳指ETF",
                    "last_price": 1.234,
                    "open_price": 1.22,
                    "high_price": 1.24,
                    "low_price": 1.21,
                    "pre_close": 1.20,
                    "volume": 1000,
                    "amount": 1234,
                }]
            }
        }
        with patch("scripts.query_time_market_refresh.fetch_tencent_quotes", side_effect=RuntimeError("Tencent unavailable")), \
             patch("scripts.query_time_market_refresh.shutil.which", return_value="hithink-finance"), \
             patch("scripts.query_time_market_refresh._cli_json", side_effect=[RuntimeError("fund not found"), direct]) as cli:
            result = refresh_market_quotes(root, ["159941.SZ"], now)

        self.assertEqual(result["failures"], [])
        self.assertEqual(result["quotes"][0]["latest_price"], 1.234)
        self.assertEqual(cli.call_count, 2)
        self.assertEqual(cli.call_args_list[1].args[1], ["market", "snapshot", "--thscodes", "159941.SZ"])

    def test_kospi_regular_refresh_uses_formal_naver_chain_not_generic_yahoo(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-08-26T07:00:00+08:00")
        now = datetime.fromisoformat("2026-08-26T10:15:00+09:00")
        record = {
            "object": "KOSPI",
            "name": "韩国综合指数",
            "provider": "naver_finance",
            "provider_timestamp_field": "localTradedAt",
            "quality_status": "PASS",
            "latest": {
                "open": 6770.0,
                "high": 6790.0,
                "low": 6760.0,
                "close": 6781.33,
                "volume": 1000,
                "amount": 2000,
                "previous_close": 6750.0,
                "as_of_beijing": "2026-08-26T09:14:50+08:00",
                "as_of_local": "2026-08-26T10:14:50+09:00",
            },
        }
        summary = {"provider": "naver_finance:KOSPI", "freshness_status": "FRESH", "stable_for_10m_pulse": True}
        with patch("scripts.query_time_market_refresh.fetch_naver_kospi", return_value=record) as naver, \
             patch("scripts.query_time_market_refresh.provider_attempt", return_value=summary), \
             patch("scripts.query_time_market_refresh._yahoo") as generic_yahoo:
            result = refresh_market_quotes(root, ["KOSPI"], now)

        naver.assert_called_once()
        generic_yahoo.assert_not_called()
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["quotes"][0]["latest_price"], 6781.33)
        self.assertEqual(result["quotes"][0]["source"], "naver_finance:KOSPI")
        self.assertEqual(result["quotes"][0]["freshness"], "FRESH")
        self.assertEqual(result["quotes"][0]["refresh_source"], "QUERY_TIME_FORMAL_PROVIDER_CHAIN")

    def test_active_kospi_refresh_rejects_delayed_result(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-08-26T07:00:00+08:00")
        now = datetime.fromisoformat("2026-08-26T10:15:00+09:00")
        record = {
            "object": "KOSPI",
            "name": "韩国综合指数",
            "provider": "naver_finance",
            "provider_timestamp_field": "localTradedAt",
            "quality_status": "PASS",
            "latest": {"open": 1, "high": 1, "low": 1, "close": 1, "as_of_beijing": "2026-08-26T08:40:00+08:00", "as_of_local": "2026-08-26T09:40:00+09:00"},
        }
        delayed = {"provider": "naver_finance:KOSPI", "freshness_status": "DELAYED", "stable_for_10m_pulse": False}
        with patch("scripts.query_time_market_refresh.fetch_naver_kospi", return_value=record), \
             patch("scripts.query_time_market_refresh.fetch_eastmoney_index", side_effect=RuntimeError("fallback unavailable")), \
             patch("scripts.query_time_market_refresh.fetch_formal_yahoo", side_effect=RuntimeError("fallback unavailable")), \
             patch("scripts.query_time_market_refresh.provider_attempt", return_value=delayed):
            result = refresh_market_quotes(root, ["KOSPI"], now)

        self.assertEqual(result["quotes"], [])
        self.assertEqual(result["failures"][0]["symbol"], "KOSPI")
        self.assertIn("no fresh usable quote", result["failures"][0]["error"])


    def test_us_pre_market_query_time_refresh_rejects_degraded_proxy(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-09-21T17:00:00+08:00")
        now = datetime.fromisoformat("2026-09-21T05:30:00-04:00")
        degraded = {
            "symbol": "QQQ", "market": "US", "latest_price": 600,
            "data_time_beijing": "2026-09-21T17:10:00+08:00",
            "market_phase": "PRE_MARKET", "freshness": "DEGRADED",
            "quality_status": "DEGRADED", "source": "yahoo_chart_api",
        }
        with patch("scripts.query_time_market_refresh._yahoo", return_value=degraded):
            result = refresh_market_quotes(root, ["QQQ"], now)

        self.assertEqual(result["quotes"], [])
        self.assertEqual(result["failures"][0]["symbol"], "QQQ")
        self.assertIn("requires FRESH provider timestamp", result["failures"][0]["error"])

    def test_us_post_market_query_time_refresh_rejects_stale_proxy(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-09-21T17:00:00+08:00")
        now = datetime.fromisoformat("2026-09-21T17:30:00-04:00")
        stale = {
            "symbol": "SOXX", "market": "US", "latest_price": 300,
            "data_time_beijing": "2026-09-22T04:00:00+08:00",
            "market_phase": "POST_MARKET", "freshness": "STALE",
            "quality_status": "STALE", "source": "yahoo_chart_api",
        }
        with patch("scripts.query_time_market_refresh._yahoo", return_value=stale):
            result = refresh_market_quotes(root, ["SOXX"], now)

        self.assertEqual(result["quotes"], [])
        self.assertEqual(result["failures"][0]["symbol"], "SOXX")
        self.assertIn("requires FRESH provider timestamp", result["failures"][0]["error"])

    def test_us_extended_query_time_refresh_accepts_fresh_proxy(self):
        from scripts.query_time_market_refresh import refresh_market_quotes

        root = self._root("2026-09-21T17:00:00+08:00")
        now = datetime.fromisoformat("2026-09-21T05:30:00-04:00")
        fresh = {
            "symbol": "QQQ", "market": "US", "latest_price": 601,
            "data_time_beijing": "2026-09-21T17:29:30+08:00",
            "market_phase": "PRE_MARKET", "freshness": "FRESH",
            "quality_status": "PASS", "source": "yahoo_chart_api",
        }
        with patch("scripts.query_time_market_refresh._yahoo", return_value=fresh):
            result = refresh_market_quotes(root, ["QQQ"], now)

        self.assertEqual(result["failures"], [])
        self.assertEqual(result["quotes"][0]["freshness"], "FRESH")
        self.assertEqual(result["quotes"][0]["latest_price"], 601)


if __name__ == "__main__":
    unittest.main()
