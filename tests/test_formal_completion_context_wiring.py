import unittest
from pathlib import Path


class FormalCompletionContextWiringTests(unittest.TestCase):
    def test_formal_completion_push_rebuilds_derived_decision_context(self):
        workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("run: python scripts/build_state_context.py --decision-ready-only", workflow)
        self.assertNotIn("steps.state_sync.outputs.formal_completion != 'true'", workflow)
        self.assertNotIn("steps.triggering_request.outputs.formal_completion != 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
