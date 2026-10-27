import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CanonicalSnapshotHealthIsolationTests(unittest.TestCase):
    def test_query_refresh_uses_separate_health_projection(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("data/state/query_refresh_health.json", workflow)
        self.assertIn("ETF_HEALTH_PATH", workflow)
        self.assertIn("data/state/runtime_health.json", workflow)

    def test_query_refresh_does_not_claim_canonical_skip(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("Query-time refresh has its own health projection", workflow)
        self.assertNotIn("health_path = Path('data/state/runtime_health.json')", workflow)

    def test_runtime_health_writer_is_path_selectable(self):
        runner = (ROOT / "scripts" / "cloud_runner_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("write_runtime_health", runner)
        self.assertIn("ETF_HEALTH_PATH", (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
