import csv
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts import formal_etf_opportunity_discovery as discovery


def spot(code: str) -> dict:
    return {
        "code": code,
        "name": code,
        "market_id": 1,
        "price": 1.0,
        "change_pct": 1.0,
        "amount": 100_000_000.0,
        "volume_ratio": 1.2,
        "return_60d_pct": 5.0,
    }


def history() -> list[dict]:
    return [
        {"date": f"2026-06-{(i % 28) + 1:02d}", "close": 1.0 + i / 1000, "amount": 100_000_000.0}
        for i in range(70)
    ]


class DiscoveryRuntimeCoverageTests(unittest.TestCase):
    def test_szse_official_etf_list_parses_html_and_paginates(self) -> None:
        page1 = [{"data": [{"sys_key": '<a>158022</a>', "zxjghj": '<a>创业板综增强ETF国联</a>',
                            "nhzs": "399102 创业板综", "glrmc": "国联基金管理有限公司"}],
                  "error": None, "metadata": {"catalogid": "1945", "pagecount": 2, "recordcount": 2}}]
        page2 = [{"data": [{"sys_key": '<a>159001</a>', "zxjghj": '<a>货币ETF</a>',
                            "nhzs": "", "glrmc": "示例基金"}],
                  "error": None, "metadata": {"catalogid": "1945", "pagecount": 2, "recordcount": 2}}]
        with patch.object(discovery, "_request_json_headers", side_effect=[page1, page2]) as request:
            rows = discovery.fetch_szse_official_etf_master()
        self.assertEqual(request.call_count, 2)
        self.assertEqual([x["code"] for x in rows], ["158022", "159001"])
        self.assertEqual(rows[0]["name"], "创业板综增强ETF国联")
        self.assertEqual(rows[0]["identity_source"], "SZSE_OFFICIAL_ETF_LIST_1945")

    def test_szse_official_etf_list_fails_closed_on_coverage_mismatch(self) -> None:
        payload = [{"data": [{"sys_key": '<a>158022</a>', "zxjghj": '<a>ETF</a>'}],
                    "error": None, "metadata": {"catalogid": "1945", "pagecount": 1, "recordcount": 2}}]
        with patch.object(discovery, "_request_json_headers", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "coverage mismatch"):
                discovery.fetch_szse_official_etf_master()

    def test_reconciled_broad_path_keeps_eastmoney_primary_without_fallback_call(self) -> None:
        east = [
            {"code": "510001", "name": "主源ETF", "market_id": 1, "price": 1.0, "change_pct": 1.0, "amount": 20_000_000},
            {"code": "159999", "name": "主源深市ETF", "market_id": 0, "price": 1.0, "change_pct": 0.5, "amount": 20_000_000},
        ]
        with patch.object(discovery, "fetch_broad_etf_spot", return_value=east), \
             patch.object(discovery, "fetch_official_tencent_broad_spot") as fallback:
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, east)
        fallback.assert_not_called()
        self.assertFalse(meta["fallback_used"])
        self.assertEqual(meta["broad_primary_source"], "EASTMONEY_PUSH2DELAY")


    def test_hithink_master_normalizes_six_digit_etf_identities(self) -> None:
        payload = {
            "data": {"data": {
                "0": {"code": "588000", "name": "科创50ETF"},
                "1": {"code": "159781", "name": "科创创业ETF"},
                "2": {"code": "bad", "name": "bad"},
            }}
        }
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return ("g(" + json.dumps(payload, ensure_ascii=False) + ")").encode("utf-8")
        with patch.object(discovery, "urlopen", return_value=Response()):
            rows = discovery.fetch_hithink_etf_master()
        by_code = {row["code"]: row for row in rows}
        self.assertEqual(set(by_code), {"588000", "159781"})
        self.assertEqual(by_code["588000"]["market_id"], 1)
        self.assertEqual(by_code["159781"]["market_id"], 0)
        self.assertEqual(by_code["588000"]["identity_source"], "HITHINK_ETF_CATEGORY_ENUMERATION")

    def test_official_master_survives_single_exchange_failure(self) -> None:
        szse = [{"code": "159001", "name": "深市ETF", "market_id": 0, "exchange": "SZSE",
                 "identity_source": "SZSE_OFFICIAL_ETF_LIST_1945"}]
        with patch.object(discovery, "fetch_sse_official_etf_master", side_effect=ConnectionResetError("sse reset")), \
             patch.object(discovery, "fetch_szse_official_etf_master", return_value=szse):
            rows, meta = discovery.fetch_official_etf_master("2026-09-18")
        self.assertEqual([x["code"] for x in rows], ["159001"])
        self.assertEqual(meta["sse_official_master_count"], 0)
        self.assertEqual(meta["szse_official_master_count"], 1)
        self.assertIn("sse reset", meta["sse_official_master_error"])

    def test_official_master_fails_only_when_both_exchanges_fail(self) -> None:
        with patch.object(discovery, "fetch_sse_official_etf_master", side_effect=ConnectionResetError("sse reset")), \
             patch.object(discovery, "fetch_szse_official_etf_master", side_effect=TimeoutError("szse timeout")):
            with self.assertRaisesRegex(RuntimeError, "official ETF masters unavailable"):
                discovery.fetch_official_etf_master("2026-09-18")

    def test_reconciled_broad_path_survives_eastmoney_failure(self) -> None:
        official = [{"code": "510001", "name": "官方ETF", "market_id": 1, "price": 1.0,
                     "change_pct": 1.0, "amount": 20_000_000}]
        with patch.object(discovery, "fetch_official_tencent_broad_spot",
                          return_value=(official, {"official_master_count": 1, "tencent_quote_count": 1})), \
             patch.object(discovery, "fetch_broad_etf_spot", side_effect=ConnectionError("eastmoney down")):
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-18")
        self.assertEqual(len(rows), 1)
        self.assertIn("eastmoney down", meta["eastmoney_error"])

    def test_tencent_runtime_import_has_direct_script_fallback(self) -> None:
        import builtins
        import sys
        import types

        fake = types.ModuleType("tencent_quote")
        fake.fetch_tencent_quotes = lambda symbols, timeout=10: {}
        real_import = builtins.__import__

        def import_without_scripts(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "scripts.tencent_quote":
                raise ModuleNotFoundError("No module named 'scripts'")
            return real_import(name, globals, locals, fromlist, level)

        previous = sys.modules.get("tencent_quote")
        sys.modules["tencent_quote"] = fake
        try:
            with patch.object(builtins, "__import__", side_effect=import_without_scripts), \
                 patch.object(discovery, "fetch_official_etf_master", return_value=([], {"sse_official_master_count": 0, "szse_official_master_count": 0})):
                rows, meta = discovery.fetch_official_tencent_broad_spot("2026-09-18")
        finally:
            if previous is None:
                sys.modules.pop("tencent_quote", None)
            else:
                sys.modules["tencent_quote"] = previous
        self.assertEqual(rows, [])
        self.assertEqual(meta["official_master_count"], 0)
        self.assertEqual(meta["tencent_quote_count"], 0)

    def test_history_required_pit_uses_latest_completed_session_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calendar = root / "config/market"
            calendar.mkdir(parents=True)
            (calendar / "a_share_trading_calendar_2026.json").write_text(
                json.dumps({"closed_dates": []}), encoding="utf-8"
            )
            self.assertEqual(discovery.required_completed_history_date(root, "2026-09-20"), "2026-09-18")
            self.assertEqual(discovery.required_completed_history_date(root, "2026-09-21"), "2026-09-18")
            self.assertEqual(discovery.required_completed_history_date(root, "2026-09-23"), "2026-09-22")

    def test_history_required_pit_can_include_execution_date_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calendar = root / "config/market"
            calendar.mkdir(parents=True)
            (calendar / "a_share_trading_calendar_2026.json").write_text(
                json.dumps({"closed_dates": []}), encoding="utf-8"
            )
            self.assertEqual(
                discovery.required_completed_history_date(root, "2026-09-18", include_execution_date=True),
                "2026-09-18",
            )

    def test_history_freshness_requires_exact_completed_session(self) -> None:
        stale = [{"date": "2026-09-17", "close": 1.0}] * 65
        current = stale[:-1] + [{"date": "2026-09-18", "close": 1.0}]
        self.assertFalse(discovery._history_is_current_for_required_date(stale, "2026-09-18"))
        self.assertTrue(discovery._history_is_current_for_required_date(current, "2026-09-18"))

    def test_history_network_repair_prefers_hithink_then_tencent_then_eastmoney(self) -> None:
        hithink_rows = [{**row, "_provider": "hithink_finance_history"} for row in history()]
        with patch.object(discovery, "fetch_hithink_daily_history_bounded", return_value=hithink_rows) as hithink, \
             patch.object(discovery, "fetch_tencent_daily_history_bounded") as tencent, \
             patch.object(discovery, "fetch_eastmoney_daily_history") as eastmoney:
            rows = discovery.fetch_daily_history("510001", 1, "2026-09-18", 90)
        hithink.assert_called_once()
        tencent.assert_not_called()
        eastmoney.assert_not_called()
        self.assertEqual(rows[0]["_provider"], "hithink_finance_history")

    def test_history_network_repair_falls_back_hithink_to_tencent(self) -> None:
        tencent_rows = [{**row, "_provider": "tencent_qq_history"} for row in history()]
        with patch.object(discovery, "fetch_hithink_daily_history_bounded", side_effect=TimeoutError("hithink timeout")), \
             patch.object(discovery, "fetch_tencent_daily_history_bounded", return_value=tencent_rows) as tencent, \
             patch.object(discovery, "fetch_eastmoney_daily_history") as eastmoney:
            rows = discovery.fetch_daily_history("510001", 1, "2026-09-18", 90)
        tencent.assert_called_once()
        eastmoney.assert_not_called()
        self.assertEqual(rows[0]["_provider"], "tencent_qq_history")

    def test_history_network_repair_falls_back_to_eastmoney(self) -> None:
        eastmoney_rows = [{**row, "_provider": "eastmoney_push2his"} for row in history()]
        with patch.object(discovery, "fetch_hithink_daily_history_bounded", side_effect=TimeoutError("hithink timeout")), \
             patch.object(discovery, "fetch_tencent_daily_history_bounded", side_effect=TimeoutError("tencent timeout")), \
             patch.object(discovery, "fetch_eastmoney_daily_history", return_value=eastmoney_rows) as eastmoney:
            rows = discovery.fetch_daily_history("510001", 1, "2026-09-18", 90)
        eastmoney.assert_called_once()
        self.assertEqual(rows[0]["_provider"], "eastmoney_push2his")

    def test_history_network_repair_fails_closed_after_both_providers(self) -> None:
        with patch.object(discovery, "fetch_hithink_daily_history_bounded", side_effect=TimeoutError("hithink timeout")), \
             patch.object(discovery, "fetch_tencent_daily_history_bounded", side_effect=TimeoutError("tencent timeout")), \
             patch.object(discovery, "fetch_eastmoney_daily_history", side_effect=ConnectionError("eastmoney closed")):
            with self.assertRaisesRegex(RuntimeError, "historical provider chain exhausted"):
                discovery.fetch_daily_history("510001", 1, "2026-09-18", 90)

    def test_any_history_failure_degrades_discovery_instead_of_false_ready(self) -> None:
        rows = [spot("510001"), spot("510002")]
        with patch.object(discovery, "fetch_daily_history", side_effect=[history(), RuntimeError("provider closed")]):
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", managed_codes=set(), spot_rows=rows
            )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["coverage_status"], "PARTIAL")
        self.assertEqual(result["history_attempted_count"], 2)
        self.assertEqual(result["history_succeeded_count"], 1)
        self.assertEqual(result["history_failure_count"], 1)

    def test_complete_history_coverage_can_be_ready(self) -> None:
        rows = [spot("510001")]
        with patch.object(discovery, "fetch_daily_history", return_value=history()):
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", managed_codes=set(), spot_rows=rows
            )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["coverage_status"], "COMPLETE")
        self.assertEqual(result["history_attempted_count"], 1)
        self.assertEqual(result["history_succeeded_count"], 1)
        self.assertEqual(result["history_failure_count"], 0)

    def test_managed_etf_remains_in_all_market_prefilter(self) -> None:
        rows = [spot("510001"), spot("510002")]
        selected = discovery._bounded_prefilter(rows)
        self.assertEqual({x["code"] for x in selected}, {"510001", "510002"})

    def test_managed_candidate_keeps_opportunity_signal_with_identity_overlay(self) -> None:
        rows = [spot("510001")]
        with patch.object(discovery, "fetch_daily_history", return_value=history()):
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", managed_codes={"510001"}, spot_rows=rows
            )
        self.assertEqual(result["managed_excluded_count"], 0)
        self.assertEqual(result["managed_identity_count"], 1)
        if result["candidates"]:
            self.assertEqual(result["candidates"][0]["management_identity"], "MANAGED")
            self.assertEqual(
                result["candidates"][0]["discovery_semantic"],
                "NODE_LOCAL_ALL_MARKET_OPPORTUNITY_SIGNAL_FOR_EXISTING_MANAGED_ETF",
            )

    def test_validated_existing_history_precedes_network_repair(self) -> None:
        rows = [spot("510001")]
        with patch.object(discovery, "load_validated_history", return_value=history()) as local_history, patch.object(discovery, "fetch_daily_history") as repair:
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", managed_codes=set(), spot_rows=rows
            )
        local_history.assert_called_once()
        repair.assert_not_called()
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["history_failure_count"], 0)
        self.assertEqual(result["history_reused_count"], 1)
        self.assertEqual(result["history_repair_attempted_count"], 0)
        if result["candidates"]:
            self.assertEqual(result["candidates"][0]["history_source"], "VALIDATED_EXISTING_HISTORY")

    def test_market_date_dataset_is_reused_with_market_date_row_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = discovery.Path(td)
            result_dir = root / "data/market/on_demand/results"
            dataset_dir = root / "data/market/on_demand/datasets"
            result_dir.mkdir(parents=True)
            dataset_dir.mkdir(parents=True)
            dataset = dataset_dir / "sample_518880.csv"
            rows = []
            start = discovery.datetime(2026, 6, 1)
            for i in range(110):
                day = start + discovery.timedelta(days=i)
                if day.date().isoformat() > "2026-09-18":
                    break
                rows.append({"date": day.date().isoformat(), "open": 1, "high": 1, "low": 1, "close": 1 + i / 1000, "volume": 1, "amount": 1})
            with dataset.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = {
                "ok": True, "asset_type": "etf", "mode": "history",
                "last_date": "2026-09-18",
                "dataset": "data/market/on_demand/datasets/sample_518880.csv",
            }
            (result_dir / "sample_518880_result.json").write_text(json.dumps(result), encoding="utf-8")
            loaded = discovery.load_validated_history(root, "518880", "2026-09-18", 90)
        self.assertIsNotNone(loaded)
        self.assertGreaterEqual(len(loaded), discovery.MIN_HISTORY)
        self.assertEqual(loaded[-1]["date"], "2026-09-18")

    def test_successful_repair_is_reused_on_same_market_date_node(self) -> None:
        rows = [spot("510001")]
        with tempfile.TemporaryDirectory() as td:
            root = discovery.Path(td)
            with patch.object(discovery, "fetch_daily_history", return_value=history()) as repair:
                first = discovery.discover_formal_candidates(root, market_date="2026-09-18", managed_codes=set(), spot_rows=rows)
                second = discovery.discover_formal_candidates(root, market_date="2026-09-18", managed_codes=set(), spot_rows=rows)
        self.assertEqual(repair.call_count, 1)
        self.assertEqual(first["history_repair_attempted_count"], 1)
        self.assertEqual(second["history_repair_attempted_count"], 0)
        self.assertEqual(second["history_reused_count"], 1)
        self.assertEqual(second["coverage_status"], "COMPLETE")


    def test_reconciled_broad_path_uses_hithink_tencent_when_eastmoney_fails(self) -> None:
        fallback = [
            {"market_id": 1, "code": "510001", "name": "沪市ETF", "price": 1.0},
            {"market_id": 0, "code": "159999", "name": "深市ETF", "price": 3.0},
        ]
        with patch.object(discovery, "fetch_broad_etf_spot", side_effect=ConnectionError("eastmoney down")), \
             patch.object(discovery, "fetch_official_tencent_broad_spot",
                          return_value=(fallback, {"identity_master_source": "HITHINK_ETF_CATEGORY"})):
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, fallback)
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(meta["fallback_source"], "HITHINK_ETF_MASTER_PLUS_TENCENT")
        self.assertIn("eastmoney down", meta["eastmoney_error"])


    def test_official_master_requests_use_repo_proven_browser_contract(self) -> None:
        with patch.object(discovery, "_request_json_headers", return_value={"result": []}) as req:
            discovery.fetch_sse_official_etf_master("2026-09-18")
        headers = req.call_args.args[2]
        self.assertIn("Chrome/", headers["User-Agent"])
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertIn("zh-CN", headers["Accept-Language"])


if __name__ == "__main__":
    unittest.main()
