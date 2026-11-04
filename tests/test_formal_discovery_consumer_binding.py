"""Regression tests for Formal Decision Discovery provenance and object coverage."""
import unittest

from scripts.scheduled_review_completion import (
    validate_formal_discovery_consumer_binding,
    build_formal_runtime_completion_request,
)


class FormalDiscoveryConsumerBindingTests(unittest.TestCase):
    def setUp(self):
        self.discovery = {
            "market_date": "2026-11-04",
            "latest_snapshot": "data/market/snapshots/2026-11-04_133004.json",
            "status": "READY",
            "candidates": [{"code": "159567"}, {"code": "588710"}],
        }
        self.kwargs = {
            "expected_market_date": "2026-11-04",
            "expected_snapshot": "data/market/snapshots/2026-11-04_133004.json",
            "candidate_codes": ["588710", "159567"],
        }

    def test_same_node_same_candidates(self):
        validate_formal_discovery_consumer_binding(self.discovery, **self.kwargs)

    def test_weekly_fallback_rejected(self):
        old = dict(self.discovery, market_date="2026-10-30")
        with self.assertRaisesRegex(ValueError, "market_date"):
            validate_formal_discovery_consumer_binding(old, **self.kwargs)

    def test_stale_snapshot_rejected(self):
        old = dict(self.discovery, latest_snapshot="data/market/snapshots/2026-10-30_150000.json")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            validate_formal_discovery_consumer_binding(old, **self.kwargs)

    def test_candidate_omission_rejected(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_formal_discovery_consumer_binding(
                self.discovery, **dict(self.kwargs, candidate_codes=["159567"])
            )

    def test_extra_candidate_rejected(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_formal_discovery_consumer_binding(
                self.discovery, **dict(self.kwargs, candidate_codes=["159567", "588710", "515080"])
            )

    def test_actual_1331_candidate_set_vs_weekly_fallback(self):
        """Replays the 13:30:33 main projection vs 13:31 decision's weekly set."""
        actual = dict(
            self.discovery,
            candidates=[{"code": c} for c in
                        ("159189", "159310", "159355", "159529",
                         "159567", "560570", "588710", "589990")],
        )
        correct = dict(self.kwargs, candidate_codes=[
            "159189", "159310", "159355", "159529",
            "159567", "560570", "588710", "589990",
        ])
        validate_formal_discovery_consumer_binding(actual, **correct)
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_formal_discovery_consumer_binding(
                actual, **dict(correct, candidate_codes=[
                    "159202", "159307", "159377", "159569", "159691",
                    "159699", "513290", "513630", "513750", "515080",
                    "562060", "589580",
                ])
            )

    def test_actual_1426_candidate_set_vs_weekly_fallback(self):
        """Replays the 14:23:36 main projection vs 14:26 stale weekly set."""
        actual = dict(
            self.discovery,
            latest_snapshot="data/market/snapshots/2026-11-04_142303.json",
            candidates=[{"code": c} for c in
                        ("159310", "159355", "159529", "159567", "159892",
                         "516520", "560570", "562310", "589000")],
        )
        correct = dict(
            self.kwargs,
            expected_snapshot=actual["latest_snapshot"],
            candidate_codes=[x["code"] for x in actual["candidates"]],
        )
        validate_formal_discovery_consumer_binding(actual, **correct)
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_formal_discovery_consumer_binding(
                actual, **dict(correct, candidate_codes=["159202", "159307", "159377", "159569", "159691",
                    "159699", "513290", "513630", "513750", "515080",
                    "562060", "589580"])
            )

    def test_verified_zero_candidates_is_not_a_missing_layer(self):
        empty = dict(self.discovery, candidates=[], candidate_count=0)
        validate_formal_discovery_consumer_binding(
            empty, **dict(self.kwargs, candidate_codes=[])
        )

    def test_declared_count_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "count"):
            validate_formal_discovery_consumer_binding(
                dict(self.discovery, candidate_count=12), **self.kwargs
            )

    def test_malformed_or_duplicate_candidates_rejected(self):
        for bad in ([{"code": "159567"}, {"code": "159567"}],
                    [{"code": ""}], ["159567"]):
            with self.subTest(candidates=bad), self.assertRaisesRegex(ValueError, "invalid"):
                validate_formal_discovery_consumer_binding(
                    dict(self.discovery, candidates=bad), **self.kwargs
                )

    def test_builder_preserves_frozen_discovery_binding(self):
        binding = {
            "status": "READY",
            "selected_discovery": self.discovery,
            "candidate_codes": ["159567", "588710"],
        }
        # Builder performs user-visible content checks; this test isolates
        # transport of the already-validated frozen binding.
        from unittest.mock import patch
        with patch("scripts.scheduled_review_completion.validate_user_visible_content"), patch(
            "scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"
        ):
            payload = build_formal_runtime_completion_request(
                run_identity={
                    "task_run_id": "fd-20261104-test",
                    "occurrence_id": "fd-20261104-test",
                    "requested_at_beijing": "2026-11-04T13:31:55+08:00",
                    "entry_mode": "MANUAL_CONVERSATION",
                },
                market_date="2026-11-04",
                final_content="【ETF正式决策】2026-11-04 13:31 正式判断",
                decision_path="formal",
                fact_summary={"discovery_binding": binding},
            )
        self.assertEqual(payload["discovery_binding"], binding)
        self.assertEqual(payload["fact_summary"]["discovery_binding"], binding)
        self.assertEqual(payload["completion_contract_version"], "3")

    def test_builder_does_not_invent_discovery_binding(self):
        from unittest.mock import patch
        with patch("scripts.scheduled_review_completion.validate_user_visible_content"), patch(
            "scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"
        ):
            payload = build_formal_runtime_completion_request(
                run_identity={
                    "task_run_id": "fd-20261104-test",
                    "occurrence_id": "fd-20261104-test",
                    "requested_at_beijing": "2026-11-04T13:31:55+08:00",
                    "entry_mode": "MANUAL_CONVERSATION",
                },
                market_date="2026-11-04",
                final_content="【ETF正式决策】2026-11-04 13:31 正式判断",
                decision_path="formal",
            )
        self.assertNotIn("discovery_binding", payload)
        self.assertEqual(payload["completion_contract_version"], "2")

    def test_v3_requires_binding_and_checks_run_start_pit(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / "data/market/snapshots/2026-11-04_133004.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({
                "market_date": "2026-11-04", "quality_status": "PASS"
            }), encoding="utf-8")
            with patch("scripts.scheduled_review_completion.validate_user_visible_content"), patch(
                "scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"
            ):
                payload = build_formal_runtime_completion_request(
                    run_identity={
                        "task_run_id": "fd-20261104-v3",
                        "occurrence_id": "fd-20261104-v3",
                        "requested_at_beijing": "2026-11-04T13:31:55+08:00",
                        "entry_mode": "MANUAL_CONVERSATION",
                    },
                    market_date="2026-11-04",
                    final_content="【ETF正式决策】2026-11-04 13:31 正式判断",
                    decision_path="formal",
                    fact_summary={"fact_binding": {
                        "market_date": "2026-11-04",
                        "latest_valid_node": "live",
                        "latest_snapshot": "data/market/snapshots/2026-11-04_133004.json",
                        "captured_at_beijing": "2026-11-04T13:30:04+08:00",
                        "fact_status": "PASS",
                    }},
                )
                payload["completion_contract_version"] = "3"
                with self.assertRaisesRegex(ValueError, "requires run-frozen"):
                    validate_formal_runtime_completion_contract(payload, root=root)

                payload["discovery_binding"] = {
                    "status": "READY",
                    "selected_discovery": dict(
                        self.discovery,
                        generated_at_beijing="2026-11-04T13:30:33+08:00",
                    ),
                    "candidate_codes": ["159567", "588710"],
                    "evaluated_candidate_codes": ["588710", "159567"],
                }
                validate_formal_runtime_completion_contract(payload, root=root)
                payload["discovery_binding"]["evaluated_candidate_codes"] = ["159567"]
                with self.assertRaisesRegex(ValueError, "evaluated Discovery"):
                    validate_formal_runtime_completion_contract(payload, root=root)
                payload["discovery_binding"]["evaluated_candidate_codes"] = ["588710", "159567"]
                payload["discovery_binding"]["selected_discovery"]["generated_at_beijing"] = (
                    "2026-11-04T14:23:36+08:00"
                )
                with self.assertRaisesRegex(ValueError, "PIT generation"):
                    validate_formal_runtime_completion_contract(payload, root=root)

    def test_v3_limited_discovery_preserves_completion(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / "data/market/snapshots/2026-11-04_133004.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({
                "market_date": "2026-11-04", "quality_status": "PASS"
            }), encoding="utf-8")
            with patch("scripts.scheduled_review_completion.validate_user_visible_content"), patch(
                "scripts.scheduled_review_completion.validate_formal_decision_presentation_timestamp"
            ):
                payload = build_formal_runtime_completion_request(
                    run_identity={
                        "task_run_id": "fd-20261104-limited",
                        "occurrence_id": "fd-20261104-limited",
                        "requested_at_beijing": "2026-11-04T13:31:55+08:00",
                        "entry_mode": "MANUAL_CONVERSATION",
                    },
                    market_date="2026-11-04",
                    final_content="【ETF正式决策】2026-11-04 13:31 正式判断",
                    decision_path="formal",
                    fact_summary={"fact_binding": {
                        "market_date": "2026-11-04",
                        "latest_valid_node": "live",
                        "latest_snapshot": "data/market/snapshots/2026-11-04_133004.json",
                        "captured_at_beijing": "2026-11-04T13:30:04+08:00",
                        "fact_status": "PASS",
                    }},
                )
                payload["completion_contract_version"] = "3"
                payload["discovery_binding"] = {
                    "status": "MISSING",
                    "reason": "Current-node Discovery not available at run start",
                    "candidate_codes": [],
                }
                validate_formal_runtime_completion_contract(payload, root=root)

    def test_degraded_not_upgraded(self):
        with self.assertRaisesRegex(ValueError, "not complete"):
            validate_formal_discovery_consumer_binding(
                dict(self.discovery, status="DEGRADED"), **self.kwargs
            )


if __name__ == "__main__":
    unittest.main()
