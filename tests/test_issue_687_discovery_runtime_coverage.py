import csv
import json
import tempfile
import unittest
import threading
import time
from pathlib import Path
from unittest import mock
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
    def test_eastmoney_primary_does_not_union_hithink_identities(self) -> None:
        east = [
            {"code": "510001", "name": "主源ETF", "market_id": 1, "price": 1.0, "change_pct": 1.0, "amount": 20_000_000},
            {"code": "159999", "name": "主源深市ETF", "market_id": 0, "price": 1.0, "change_pct": 0.5, "amount": 20_000_000},
        ]
        with patch.object(discovery, "fetch_broad_etf_spot", return_value=east), \
             patch.object(discovery, "fetch_hithink_etf_master", side_effect=AssertionError("secondary source must not run")):
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, east)
        self.assertEqual(meta["universe_mode"], "EASTMONEY_PRIMARY")
        self.assertEqual(meta["primary_source"], "EASTMONEY_PUSH2_ETF_CROSS_SECTION")
        self.assertFalse(meta["fallback_used"])
        self.assertEqual(meta["reconciled_count"], len(east))

    def test_eastmoney_primary_does_not_call_hithink_when_available(self) -> None:
        east = [{"code": "510001", "name": "主源ETF", "market_id": 1, "price": 1.0,
                 "change_pct": 1.0, "amount": 20_000_000}]
        with patch.object(discovery, "fetch_broad_etf_spot", return_value=east), \
             patch.object(discovery, "fetch_hithink_etf_master") as hithink:
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, east)
        self.assertEqual(meta["universe_mode"], "EASTMONEY_PRIMARY")
        hithink.assert_not_called()

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

    def test_tencent_hydration_runs_batches_concurrently_but_preserves_input_order(self) -> None:
        identities = [
            {"code": f"5100{i:02d}", "name": f"ETF{i}", "market_id": 1}
            for i in range(125)
        ]
        calls = []
        lock = threading.Lock()

        def fake_fetch(symbols, timeout=10):
            first = symbols[0]
            with lock:
                calls.append(first)
            if first.endswith("510000.SH"):
                time.sleep(0.04)
            elif first.endswith("510060.SH"):
                time.sleep(0.02)
            return {
                symbol.upper(): {
                    "name": symbol,
                    "last_price": 1.0,
                    "prev_price": 1.0,
                    "open_price": 1.0,
                    "high_price": 1.0,
                    "low_price": 1.0,
                    "turnover": 20_000_000,
                    "price_change_ratio_pct": 0.0,
                    "provider_timestamp_ms": 1,
                }
                for symbol in symbols
            }

        with patch("scripts.tencent_quote.fetch_tencent_quotes", side_effect=fake_fetch):
            rows, meta = discovery._hydrate_tencent_identities(identities)

        self.assertEqual(len(rows), 125)
        self.assertEqual([row["code"] for row in rows], [row["code"] for row in identities])
        self.assertEqual(meta["tencent_hydration_batch_count"], 3)
        self.assertEqual(meta["tencent_hydration_workers"], 3)
        self.assertEqual(meta["tencent_failed_batch_count"], 0)
        self.assertEqual(len(calls), 3)

    def test_tencent_hydration_keeps_batch_failures_ordered_and_local(self) -> None:
        identities = [
            {"code": f"5100{i:02d}", "name": f"ETF{i}", "market_id": 1}
            for i in range(125)
        ]

        def fake_fetch(symbols, timeout=10):
            if symbols[0].endswith("510060.SH"):
                raise TimeoutError("batch timeout")
            return {
                symbol.upper(): {
                    "name": symbol,
                    "last_price": 1.0,
                    "prev_price": 1.0,
                    "open_price": 1.0,
                    "high_price": 1.0,
                    "low_price": 1.0,
                    "turnover": 20_000_000,
                    "price_change_ratio_pct": 0.0,
                    "provider_timestamp_ms": 1,
                }
                for symbol in symbols
            }

        with patch("scripts.tencent_quote.fetch_tencent_quotes", side_effect=fake_fetch):
            rows, meta = discovery._hydrate_tencent_identities(identities)

        self.assertEqual([row["code"] for row in rows], [row["code"] for row in identities[:60]] + [row["code"] for row in identities[120:]])
        self.assertEqual(meta["tencent_failed_batch_count"], 1)
        self.assertEqual(meta["tencent_failures"][0]["offset"], 60)
        self.assertEqual(meta["tencent_failures"][0]["count"], 60)

    def test_reconciled_broad_path_survives_eastmoney_failure(self) -> None:
        official = [{"code": "510001", "name": "官方ETF", "market_id": 1, "price": 1.0,
                     "change_pct": 1.0, "amount": 20_000_000}]
        identities = [{"code": "510001", "name": "独立ETF", "market_id": 1}]
        with patch.object(discovery, "fetch_broad_etf_spot", side_effect=ConnectionError("eastmoney down")), \
             patch.object(discovery, "fetch_hithink_etf_master", return_value=identities), \
             patch.object(discovery, "_hydrate_tencent_identities",
                          return_value=(official, {"requested_identity_count": 1, "tencent_quote_count": 1,
                                                   "tencent_failed_batch_count": 0, "tencent_failures": []})):
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-18")
        self.assertEqual(len(rows), 1)
        self.assertIn("eastmoney down", meta["eastmoney_error"])
        self.assertEqual(meta["universe_mode"], "HITHINK_TENCENT_SECONDARY_FALLBACK")

    def test_hithink_is_used_only_as_secondary_fallback(self) -> None:
        identities = [{"code": "510001", "name": "Fallback ETF", "market_id": 1}]
        hydrated = [{"code": "510001", "name": "Fallback ETF", "market_id": 1, "price": 1.0}]
        with patch.object(discovery, "fetch_broad_etf_spot", side_effect=ConnectionError("eastmoney down")), \
             patch.object(discovery, "fetch_hithink_etf_master", return_value=identities), \
             patch.object(discovery, "_hydrate_tencent_identities",
                          return_value=(hydrated, {"requested_identity_count": 1, "tencent_quote_count": 1,
                                                   "tencent_failed_batch_count": 0, "tencent_failures": []})) as hydrate:
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, hydrated)
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(meta["fallback_source"], "HITHINK_ETF_MASTER_PLUS_TENCENT")
        self.assertEqual(meta["universe_mode"], "HITHINK_TENCENT_SECONDARY_FALLBACK")
        self.assertEqual(hydrate.call_args.args[0], identities)

    def test_discovery_source_contract_excludes_exchange_directory_enumeration(self) -> None:
        source = Path(discovery.__file__).read_text(encoding="utf-8")
        self.assertIn("EASTMONEY_PRIMARY", source)
        self.assertIn("fetch_broad_etf_spot", source)
        self.assertIn("fetch_hithink_etf_master", source)
        self.assertNotIn("query.sse.com.cn/commonQuery.do", source)
        self.assertNotIn("www.szse.cn/api/report/ShowReport/data", source)
        self.assertNotIn("fetch_sse_official_etf_master", source)
        self.assertNotIn("fetch_szse_official_etf_master", source)

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
        with patch.object(discovery, "load_discovery_history_snapshot", return_value={}), patch.object(discovery, "load_validated_history", return_value=None), patch.object(discovery, "fetch_daily_history", side_effect=[history(), RuntimeError("provider closed")]):
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes=set(), spot_rows=rows
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
                discovery.Path("."), market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes=set(), spot_rows=rows
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
                discovery.Path("."), market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes={"510001"}, spot_rows=rows
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
        existing = history()
        existing[-1]["date"] = "2026-09-18"
        with patch.object(discovery, "load_discovery_history_snapshot", return_value={}), patch.object(discovery, "load_validated_history", return_value=existing) as local_history, patch.object(discovery, "fetch_daily_history") as repair:
            result = discovery.discover_formal_candidates(
                discovery.Path("."), market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes=set(), spot_rows=rows
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
        repaired = history()
        repaired[-1]["date"] = "2026-09-18"
        with tempfile.TemporaryDirectory() as td:
            root = discovery.Path(td)
            with patch.object(discovery, "fetch_daily_history", return_value=repaired) as repair:
                first = discovery.discover_formal_candidates(root, market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes=set(), spot_rows=rows)
                second = discovery.discover_formal_candidates(root, market_date="2026-09-18", required_history_end_date="2026-09-18", managed_codes=set(), spot_rows=rows)
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
        identities = [
            {"market_id": 1, "code": "510001", "name": "沪市ETF"},
            {"market_id": 0, "code": "159999", "name": "深市ETF"},
        ]
        with patch.object(discovery, "fetch_broad_etf_spot", side_effect=ConnectionError("eastmoney down")), \
             patch.object(discovery, "fetch_hithink_etf_master", return_value=identities), \
             patch.object(discovery, "_hydrate_tencent_identities",
                          return_value=(fallback, {"requested_identity_count": 2, "tencent_quote_count": 2,
                                                   "tencent_failed_batch_count": 0, "tencent_failures": []})):
            rows, meta = discovery.fetch_reconciled_broad_etf_spot("2026-09-23")
        self.assertEqual(rows, fallback)
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(meta["fallback_source"], "HITHINK_ETF_MASTER_PLUS_TENCENT")
        self.assertEqual(meta["universe_mode"], "HITHINK_TENCENT_SECONDARY_FALLBACK")
        self.assertIn("eastmoney down", meta["eastmoney_error"])


    def test_source_contract_uses_eastmoney_primary_without_exchange_directories(self) -> None:
        source = Path(discovery.__file__).read_text(encoding="utf-8")
        self.assertIn("EASTMONEY_PRIMARY", source)
        self.assertNotIn("query.sse.com.cn/commonQuery.do", source)
        self.assertNotIn("www.szse.cn/api/report/ShowReport/data", source)
        self.assertNotIn("fetch_sse_official_etf_master", source)
        self.assertNotIn("fetch_szse_official_etf_master", source)

    def test_eastmoney_broad_pages_fetch_concurrently_but_preserve_page_order(self):
        calls = []
        lock = threading.Lock()
        def fake_request(url, params, timeout=12):
            page = int(params["pn"])
            with lock:
                calls.append(page)
            if page == 1:
                return {"data": {"total": 250, "diff": [{"f12": "510001", "f13": 1, "f14": "p1"}]}}
            if page == 2:
                time.sleep(0.03)
                return {"data": {"total": 250, "diff": [{"f12": "510002", "f13": 1, "f14": "p2"}]}}
            return {"data": {"total": 250, "diff": [{"f12": "510003", "f13": 1, "f14": "p3"}]}}
        with mock.patch.object(discovery, "_request_json", side_effect=fake_request):
            rows = discovery.fetch_broad_etf_spot()
        self.assertEqual([row["code"] for row in rows], ["510001", "510002", "510003"])
        self.assertEqual(calls[0], 1)
        self.assertEqual(set(calls[1:]), {2, 3})

    def test_eastmoney_broad_fails_closed_if_pagination_total_changes(self):
        def fake_request(url, params, timeout=12):
            page = int(params["pn"])
            total = 201 if page == 2 else 200
            return {"data": {"total": total, "diff": [{"f12": f"51000{page}", "f13": 1, "f14": "x"}]}}
        with mock.patch.object(discovery, "_request_json", side_effect=fake_request):
            with self.assertRaisesRegex(RuntimeError, "pagination total changed"):
                discovery.fetch_broad_etf_spot()

if __name__ == "__main__":
    unittest.main()
