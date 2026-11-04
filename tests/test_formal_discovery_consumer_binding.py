"""Regression tests for Formal Decision Discovery provenance and object coverage."""
import unittest

from scripts.scheduled_review_completion import validate_formal_discovery_consumer_binding


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

    def test_degraded_not_upgraded(self):
        with self.assertRaisesRegex(ValueError, "not complete"):
            validate_formal_discovery_consumer_binding(
                dict(self.discovery, status="DEGRADED"), **self.kwargs
            )


if __name__ == "__main__":
    unittest.main()
