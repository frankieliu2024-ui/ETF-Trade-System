import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CanonicalSnapshotReadinessTests(unittest.TestCase):
    def test_publication_checks_identity_quality_and_timestamps(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("expected_symbols", source)
        self.assertIn("object identity mismatch", source)
        self.assertIn("non-consumable row", source)
        self.assertIn("row missing timestamp", source)


    def test_failed_snapshot_does_not_advance_current_or_bypass_other_objects(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertNotIn("PRIMARY_RUN_BYPASS", source)
        guard_pos = source.index("snapshot contains non-consumable rows; previous CURRENT preserved")
        update_pos = source.index("update_current(root=ROOT, market_date=market_date")
        self.assertLess(guard_pos, update_pos)
        self.assertIn("failed_symbols = ", source)

    def test_failure_is_fail_closed_before_health_is_written(self):
        source = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        verify_pos = source.index("verify_published_snapshot(target")
        health_pos = source.index("write_runtime_health(", verify_pos)
        self.assertLess(verify_pos, health_pos)


if __name__ == "__main__":
    unittest.main()
