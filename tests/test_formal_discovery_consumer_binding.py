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

    def test_valid_zero_candidates_is_not_missing(self):
        empty = dict(self.projection, candidate_count=0, candidates=[])
        self.assertIs(select_latest_qualified_formal_discovery(empty, **self.kwargs), empty)


if __name__ == "__main__":
    unittest.main()
