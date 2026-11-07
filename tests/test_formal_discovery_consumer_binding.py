"""PIT regression tests for selecting the existing current-main Discovery."""
import unittest

from scripts.scheduled_review_completion import select_latest_qualified_formal_discovery


class FormalDiscoverySelectionTests(unittest.TestCase):
    def setUp(self):
        self.projection = {
            "market_date": "2026-11-04",
            "latest_snapshot": "data/market/snapshots/2026-11-04_133004.json",
            "status": "READY",
            "coverage_status": "COMPLETE",
            "candidate_count": 8,
            "generated_at_beijing": "2026-11-04T13:30:33+08:00",
            "candidates": [
                {"code": code} for code in (
                    "159189", "159310", "159355", "159529",
                    "159567", "560570", "588710", "589990",
                )
            ],
        }
        self.kwargs = {
            "requested_at_beijing": "2026-11-04T13:31:55+08:00",
            "market_date": "2026-11-04",
        }

    def test_1331_consumes_current_node_eight_not_weekly_twelve(self):
        selected = select_latest_qualified_formal_discovery(self.projection, **self.kwargs)
        self.assertIs(selected, self.projection)
        self.assertEqual(len(selected["candidates"]), 8)
        weekly = dict(self.projection, market_date="2026-10-30", candidate_count=12)
        self.assertIsNone(select_latest_qualified_formal_discovery(weekly, **self.kwargs))

    def test_1426_consumes_latest_nine(self):
        nine = dict(
            self.projection,
            latest_snapshot="data/market/snapshots/2026-11-04_142303.json",
            generated_at_beijing="2026-11-04T14:23:36+08:00",
            candidates=[{"code": code} for code in (
                "159310", "159355", "159529", "159567", "159892",
                "516520", "560570", "562310", "589000",
            )],
            candidate_count=9,
        )
        selected = select_latest_qualified_formal_discovery(
            nine, requested_at_beijing="2026-11-04T14:26:00+08:00",
            market_date="2026-11-04",
        )
        self.assertIs(selected, nine)
        self.assertIsNone(select_latest_qualified_formal_discovery(nine, **self.kwargs))

    def test_invalid_projections_do_not_silently_fallback(self):
        for bad in (
            {},
            dict(self.projection, status="DEGRADED"),
            dict(self.projection, coverage_status="PARTIAL"),
            dict(self.projection, candidate_count=12),
            dict(self.projection, generated_at_beijing="2026-11-04T14:23:36+08:00"),
            dict(self.projection, candidates=[{"code": "159189"}] * 8),
            dict(self.projection, latest_snapshot=""),
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(select_latest_qualified_formal_discovery(bad, **self.kwargs))

    def test_query_consumer_never_rewrites_producer_projection(self):
        import json
        import tempfile
        from pathlib import Path
        from scripts.build_query_context import _write_formal_layer_projections

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = {
                "market_date": "2026-11-04",
                "latest_valid_node": "live",
                "latest_snapshot": self.projection["latest_snapshot"],
            }
            producer_projection = {
                "schema_version": "1.0",
                "layer": "DISCOVERY",
                "producer": "build_weekly_discovery.py",
                "market_date": current["market_date"],
                "latest_valid_node": current["latest_valid_node"],
                "latest_snapshot": current["latest_snapshot"],
                "status": "READY",
                "coverage_status": "COMPLETE",
                "generated_at_beijing": "2026-11-04T13:30:33+08:00",
                "candidates": self.projection["candidates"],
            }
            target = root / "data/state/formal_discovery_current.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(producer_projection), encoding="utf-8")
            before = target.read_text(encoding="utf-8")
            _write_formal_layer_projections(root, current, {
                "status": "NOT_REQUESTED", "candidates": []
            })
            actual = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(target.read_text(encoding="utf-8"), before)
            self.assertEqual(actual["producer"], "build_weekly_discovery.py")
            self.assertEqual(len(actual["candidates"]), 8)

    def test_fault_injection_rejects_time_and_identity_corruption(self):
        """Offline fault matrix: no fallback, no mutation, no duplicate output."""
        for name, patch in (
            ("wrong_trading_day", {"market_date": "2026-11-03"}),
            ("future_publication", {"generated_at_beijing": "2026-11-04T13:32:00+08:00"}),
            ("naive_timestamp", {"generated_at_beijing": "2026-11-04T13:30:33"}),
            ("broken_timestamp", {"generated_at_beijing": "invalid"}),
            ("bad_status", {"status": "FAILED"}),
            ("partial_coverage", {"coverage_status": "PARTIAL"}),
            ("missing_snapshot", {"latest_snapshot": None}),
            ("wrong_count", {"candidate_count": 9}),
            ("null_candidates", {"candidates": None}),
            ("duplicate_codes", {"candidates": [{"code": "159189"}] * 8}),
            ("missing_code", {"candidates": [{"code": ""}] * 8}),
        ):
            with self.subTest(fault=name):
                injected = dict(self.projection, **patch)
                before = repr(injected)
                self.assertIsNone(
                    select_latest_qualified_formal_discovery(injected, **self.kwargs)
                )
                self.assertEqual(repr(injected), before)

    def test_simulation_timezone_equivalence_and_boundary(self):
        self.assertIs(
            select_latest_qualified_formal_discovery(
                self.projection,
                requested_at_beijing="2026-11-04T05:30:33+00:00",
                market_date="2026-11-04",
            ),
            self.projection,
        )
        self.assertIsNone(
            select_latest_qualified_formal_discovery(
                self.projection,
                requested_at_beijing="2026-11-04T05:30:32+00:00",
                market_date="2026-11-04",
            )
        )

    def test_simulated_entry_selection_to_frozen_completion_identity(self):
        """Replay two historical PIT boundaries through the existing completion builder."""
        import hashlib
        from unittest.mock import patch
        from scripts.scheduled_review_completion import build_formal_runtime_completion_request

        nine_codes = (
            "159310", "159355", "159529", "159567", "159892",
            "516520", "560570", "562310", "589000",
        )
        cases = (
            ("2026-11-04T13:31:55+08:00", self.projection, 8),
            ("2026-11-04T14:26:00+08:00", dict(
                self.projection,
                generated_at_beijing="2026-11-04T14:23:36+08:00",
                latest_snapshot="data/market/snapshots/2026-11-04_142303.json",
                candidate_count=9,
                candidates=[{"code": code} for code in nine_codes],
            ), 9),
        )
        for index, (at, projection, expected_count) in enumerate(cases):
            with self.subTest(at=at):
                selected = select_latest_qualified_formal_discovery(
                    projection, requested_at_beijing=at, market_date="2026-11-04"
                )
                self.assertIs(selected, projection)
                self.assertEqual(len(selected["candidates"]), expected_count)
                codes = tuple(c["code"] for c in selected["candidates"])
                body = ("【ETF正式决策】2026-11-04 " +
                        at[11:16] + " 正式判断\n\n" +
                        "Discovery逐只对象：" + "、".join(codes))
                identity = {
                    "task_run_id": "sim-formal-" + str(index),
                    "occurrence_id": "sim-formal-" + str(index),
                    "requested_at_beijing": at,
                    "entry_mode": "MANUAL_SCHEDULED_TASK",
                }
                with patch("scripts.scheduled_review_completion.validate_user_visible_content"), patch(
                    "scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"
                ):
                    payload = build_formal_runtime_completion_request(
                        run_identity=identity,
                        market_date="2026-11-04",
                        final_content=body,
                        decision_path="formal",
                        fact_summary={"simulation_discovery_codes": list(codes)},
                    )
                self.assertEqual(payload["request_type"], "SCHEDULED_REVIEW_COMPLETION")
                self.assertEqual(payload["occurrence_id"], identity["occurrence_id"])
                self.assertEqual(payload["presentation_binding"]["full_content"], body)
                self.assertEqual(payload["content_hash"], hashlib.sha256(body.encode()).hexdigest())
                self.assertEqual(payload["fact_summary"]["simulation_discovery_codes"], list(codes))
                self.assertEqual(payload["completion_contract_version"], "2")

    def test_simulated_completion_failure_does_not_create_second_identity(self):
        from unittest.mock import patch
        from scripts.scheduled_review_completion import build_formal_runtime_completion_request

        identity = {
            "task_run_id": "sim-failure-single-run",
            "occurrence_id": "sim-failure-single-run",
            "requested_at_beijing": "2026-11-04T14:26:00+08:00",
            "entry_mode": "MANUAL_SCHEDULED_TASK",
        }
        with patch(
            "scripts.scheduled_review_completion.validate_user_visible_content",
            side_effect=ValueError("fault: presentation rejected"),
        ):
            with self.assertRaisesRegex(ValueError, "fault: presentation rejected"):
                build_formal_runtime_completion_request(
                    run_identity=identity, market_date="2026-11-04",
                    final_content="injected invalid body", decision_path="formal",
                )
        self.assertEqual(identity["task_run_id"], "sim-failure-single-run")
        self.assertEqual(identity["occurrence_id"], "sim-failure-single-run")

    def test_valid_zero_candidates_is_not_missing(self):
        empty = dict(self.projection, candidate_count=0, candidates=[])
        self.assertIs(select_latest_qualified_formal_discovery(empty, **self.kwargs), empty)


if __name__ == "__main__":
    unittest.main()
