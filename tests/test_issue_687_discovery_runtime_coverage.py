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
        self.assertTrue(all(row["date"] < "2026-09-18" for row in loaded))


if __name__ == "__main__":
    unittest.main()
