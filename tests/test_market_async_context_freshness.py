from __future__ import annotations

import unittest

from scripts import check_system_consistency


class AsyncContextFreshnessContractTest(unittest.TestCase):
    def _report(self) -> dict:
        return {
            "status": "FAIL",
            "hard_error_count": 1,
            "warning_count": 0,
            "errors": ["dynamic_freshness:query_decision_aligned: query=STALE decision=FRESH"],
            "warnings": [],
            "checks": [
                {
                    "name": "dynamic_freshness:query_recalculated",
                    "status": "PASS",
                    "detail": "declared=STALE expected=STALE age_seconds=3193",
                },
                {
                    "name": "dynamic_freshness:decision_recalculated",
                    "status": "PASS",
                    "detail": "declared=FRESH expected=FRESH age_seconds=20",
                },
                {
                    "name": "dynamic_freshness:query_decision_aligned",
                    "status": "FAIL",
                    "detail": "query=STALE decision=FRESH",
                },
                {
                    "name": "decision:query_decision_current_aligned",
                    "status": "PASS",
                    "detail": "query=data/market/snapshots/2026-09-10_112909.json decision=data/market/snapshots/2026-09-10_112909.json",
                },
            ],
        }

    def test_async_build_threshold_crossing_is_not_a_hard_conflict(self) -> None:
        report = self._report()
        check_system_consistency._normalize_async_context_freshness(report)
        target = next(
            item
            for item in report["checks"]
            if item["name"] == "dynamic_freshness:query_decision_aligned"
        )
        self.assertEqual(target["status"], "PASS")
        self.assertIn("asynchronous_build_clocks", target["detail"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["hard_error_count"], 0)

    def test_internal_freshness_error_remains_hard(self) -> None:
        report = self._report()
        query = next(
            item
            for item in report["checks"]
            if item["name"] == "dynamic_freshness:query_recalculated"
        )
        query["status"] = "FAIL"
        report["errors"].append(
            "dynamic_freshness:query_recalculated: declared=FRESH expected=STALE age_seconds=3193"
        )
        report["hard_error_count"] = 2
        check_system_consistency._normalize_async_context_freshness(report)
        target = next(
            item
            for item in report["checks"]
            if item["name"] == "dynamic_freshness:query_decision_aligned"
        )
        self.assertEqual(target["status"], "FAIL")
        self.assertTrue(
            any(
                str(error).startswith("dynamic_freshness:query_decision_aligned:")
                for error in report["errors"]
            )
        )

    def test_snapshot_identity_mismatch_remains_hard(self) -> None:
        report = self._report()
        aligned = next(
            item
            for item in report["checks"]
            if item["name"] == "decision:query_decision_current_aligned"
        )
        aligned["status"] = "FAIL"
        report["errors"].append(
            "decision:query_decision_current_aligned: query=snapshot-a decision=snapshot-b"
        )
        report["hard_error_count"] = 2
        check_system_consistency._normalize_async_context_freshness(report)
        target = next(
            item
            for item in report["checks"]
            if item["name"] == "dynamic_freshness:query_decision_aligned"
        )
        self.assertEqual(target["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
