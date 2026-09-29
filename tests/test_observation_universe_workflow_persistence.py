from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "market-snapshot.yml"
MONITOR_PATH = "config/market/etf_monitor_universe.json"


class ObservationUniverseWorkflowPersistenceTests(unittest.TestCase):
    def test_downstream_publish_persists_observation_universe_mutation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Commit downstream runtime/data artifacts"
        self.assertIn(marker, text)
        downstream = text.split(marker, 1)[1]
        self.assertGreaterEqual(
            downstream.count(MONITOR_PATH),
            2,
            "downstream staging and replay staging must both include the canonical monitor-universe mutation",
        )

    def test_core_publish_does_not_claim_observation_universe_before_state_sync(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        core = text.split("- name: Process optional broker/dashboard state sync", 1)[0]
        self.assertNotIn(MONITOR_PATH, core)


if __name__ == "__main__":
    unittest.main()
