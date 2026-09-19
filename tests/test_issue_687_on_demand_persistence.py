from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/on-demand-market-data.yml"


class Issue687OnDemandPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_existing_owner_coalesces_all_pending_requests(self):
        self.assertIn("for req in requests/market_data/*.json; do", self.text)
        self.assertIn('if [ -f "data/market/on_demand/results/${rid}.json" ]; then', self.text)

    def test_persistence_rebases_against_latest_main(self):
        self.assertIn("git fetch origin main", self.text)
        self.assertIn("git rebase -X ours origin/main", self.text)
        self.assertNotIn("git pull --rebase origin main", self.text)

    def test_concurrent_same_path_prefers_canonical_main(self):
        # In a rebase, "ours" is the upstream (origin/main) side. This avoids
        # replaying a stale duplicate result over an already-persisted result,
        # while non-overlapping result files remain in the replayed commit.
        self.assertIn("canonical main wins only on", self.text)
        self.assertIn("git rebase -X ours origin/main", self.text)

    def test_no_second_owner_or_production_snapshot_write(self):
        self.assertIn("This DEGRADED data service never writes CURRENT or production", self.text)
        self.assertNotIn("data/state/CURRENT.json", self.text)


if __name__ == "__main__":
    unittest.main()
