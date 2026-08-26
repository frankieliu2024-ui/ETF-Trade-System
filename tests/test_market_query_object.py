import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from query_market_object import format_quote, normalize_input, resolve_object


class MarketObjectQueryTests(unittest.TestCase):
    def test_user_alias_is_not_system_monitored(self):
        code, provider_symbol, market, name = normalize_input("美光科技")
        self.assertEqual((code, provider_symbol, market), ("MU", "MU", "US"))
        obj, source_type = resolve_object(ROOT, "美光科技")
        self.assertEqual(source_type, "USER_REQUESTED")
        self.assertEqual(obj["code"], "MU")

    def test_formal_etf_is_system_monitored(self):
        obj, source_type = resolve_object(ROOT, "561980")
        self.assertEqual(source_type, "SYSTEM_MONITORED")
        self.assertEqual(obj["code"], "561980")

    def test_output_contract_keeps_timestamp_and_source_type(self):
        obj = {"name": "美光科技", "code": "MU", "asset_type": "STOCK", "market": "US"}
        row = {"close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "volume": 1000, "amount": None, "provider": "yahoo_chart_api"}
        result = format_quote(row, obj, "2026-08-26T10:00:00+08:00", "DATA_UNAVAILABLE", 0, "USER_REQUESTED")
        self.assertEqual(result["source_type"], "USER_REQUESTED")
        self.assertEqual(result["data_time_beijing"], "2026-08-26T10:00:00+08:00")
        self.assertEqual(result["turnover"], None)


if __name__ == "__main__":
    unittest.main()
