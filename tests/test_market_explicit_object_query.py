import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import query_market_object as query_mod
from scripts import query_time_market_refresh as refresh_mod


class _JsonResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self._payload


class ExplicitObjectQueryTest(unittest.TestCase):
    def _root(self):
        root = Path(tempfile.mkdtemp())
        (root / "config/market").mkdir(parents=True)
        (root / "data/state").mkdir(parents=True)
        (root / "config/market/etf_monitor_universe.json").write_text('{"objects": []}', encoding="utf-8")
        (root / "config/market/market_monitor_config.json").write_text('{"formal_index_layer": {"required_objects": []}}', encoding="utf-8")
        (root / "data/state/account_fact.json").write_text('{"positions": []}', encoding="utf-8")
        (root / "data/state/query_context.json").write_text('{}', encoding="utf-8")
        return root

    def test_common_names_resolve_without_user_codes(self):
        root = self._root()
        hs300, _ = query_mod.resolve_object(root, "沪深300指数")
        samsung, _ = query_mod.resolve_object(root, "三星电子")
        nvidia, _ = query_mod.resolve_object(root, "英伟达")
        self.assertEqual((hs300["provider_symbol"], hs300["market"], hs300["asset_type"]), ("000300.SH", "CN", "INDEX"))
        self.assertEqual((samsung["provider_symbol"], samsung["market"]), ("005930.KS", "KR"))
        self.assertEqual((nvidia["provider_symbol"], nvidia["market"]), ("NVDA", "US"))

    @patch.object(query_mod.urllib.request, "urlopen")
    def test_unknown_name_uses_security_resolution_not_quote_bypass(self, urlopen):
        urlopen.return_value = _JsonResponse({"quotes": [{"symbol": "512880.SS", "quoteType": "ETF", "longname": "证券ETF", "exchange": "SHH"}]})
        obj = query_mod._online_resolve("证券ETF")
        self.assertEqual(obj["provider_symbol"], "512880.SH")
        self.assertEqual(obj["market"], "CN")
        self.assertEqual(obj["asset_type"], "ETF")

    @patch.object(query_mod, "build_market_quote_context")
    def test_resolved_object_still_uses_unified_router(self, router):
        root = self._root()
        router.return_value = {"quotes": [{
            "symbol": "005930.KS", "name": "Samsung Electronics", "latest_price": 100.0,
            "data_time_beijing": "2026-08-28T14:30:00+08:00", "market_phase": "OFF_SESSION",
            "market_status_cn": "韩股收盘", "data_nature_cn": "最近有效交易时段行情（查询时直取）",
            "source": "yahoo_chart_api", "freshness": "SESSION_REFERENCE", "quality_status": "PASS",
            "direct_quote": True, "refresh_source": "QUERY_TIME_PROVIDER_SESSION_REFERENCE",
        }], "refresh_failures": []}
        result = query_mod.query(root, "三星电子")
        router.assert_called_once()
        self.assertEqual(result["object_code"], "005930.KS")
        self.assertEqual(result["market_code"], "KR")
        self.assertEqual(result["freshness"], "SESSION_REFERENCE")

    @patch.object(refresh_mod._legacy, "refresh_market_quotes", return_value={"quotes": [], "failures": [], "requested_symbols": ["005930.KS"]})
    @patch.object(refresh_mod, "market_phase", return_value="OFF_SESSION")
    @patch.object(refresh_mod, "_yahoo")
    def test_explicit_overseas_off_session_returns_session_reference(self, yahoo, _phase, _legacy):
        root = self._root()
        (root / "config/runtime_policy.json").write_text('{"fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500}', encoding="utf-8")
        yahoo.return_value = {
            "symbol": "005930.KS", "market": "KR", "latest_price": 100.0,
            "data_time_beijing": "2026-08-28T14:30:00+08:00", "source": "yahoo_chart_api",
            "freshness": "STALE", "quality_status": "STALE", "direct_quote": True,
        }
        now = datetime.fromisoformat("2026-08-28T18:30:00+08:00")
        result = refresh_mod.refresh_market_quotes(root, ["005930.KS"], now)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["quotes"][0]["freshness"], "SESSION_REFERENCE")
        self.assertEqual(result["quotes"][0]["refresh_source"], "QUERY_TIME_PROVIDER_SESSION_REFERENCE")


if __name__ == "__main__":
    unittest.main()
