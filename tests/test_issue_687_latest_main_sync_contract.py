import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/maintenance/production_mutation_protocol.json"
WORKFLOW = ROOT / ".github/workflows/on-demand-market-data.yml"


class Issue687LatestMainSyncContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_conflict_aware_rebase_is_registered_latest_main_sync(self):
        markers = self.cfg["writer_requirements"]["latest_main_sync_markers"]
        self.assertIn("git rebase -X ours origin/main", markers)
        self.assertIn("git rebase -X ours origin/main", self.workflow)

    def test_generic_existing_markers_remain_registered(self):
        markers = self.cfg["writer_requirements"]["latest_main_sync_markers"]
        self.assertIn("git pull --rebase origin main", markers)
        self.assertIn("git reset --hard origin/main", markers)
        self.assertIn("git rebase origin/main", markers)

    def test_no_workflow_specific_checker_exception(self):
        self.assertNotIn("on-demand-market-data.yml", json.dumps(self.cfg["writer_requirements"]))


if __name__ == "__main__":
    unittest.main()
