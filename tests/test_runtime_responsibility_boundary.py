import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "market-snapshot.yml"

class RuntimeResponsibilityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_a_share_pulse_does_not_rebuild_overseas_fact_owner(self):
        self.assertNotIn("python scripts/build_overseas_context.py", self.text)
        self.assertNotIn("Refresh overseas and Asia index context", self.text)

    def test_review_is_close_or_state_sync_bound(self):
        start = self.text.index("- name: Build post-market review context")
        block = self.text[start:start + 900]
        self.assertIn("steps.state_sync.outputs.processed == 'true'", block)
        self.assertIn("steps.session_gate.outputs.close_intent == 'true'", block)
        self.assertIn("github.event.inputs.node == 'close'", block)
        self.assertNotIn("(steps.snapshot_result.outputs.snapshot_written == 'true' || steps.state_sync.outputs.processed == 'true')", block)

    def test_scheduled_discovery_cache_remains_available(self):
        self.assertIn("python scripts/build_query_context.py --run-discovery", self.text)
        self.assertIn("Scheduled pulses may refresh", self.text)

    def test_account_stock_fact_collection_remains_eager(self):
        self.assertIn("python scripts/build_stock_context.py", self.text)
        self.assertIn("python scripts/build_account_stock_market.py", self.text)

if __name__ == "__main__":
    unittest.main()
