import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/engine-shadow-pulse.yml"


class IndependentEngineShadowEntryTests(unittest.TestCase):
    def test_shadow_workflow_is_read_only_and_not_a_notification_chain(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("engine-shadow-result.json", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("decision-notification.yml", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("pushplus", text.lower())

    def test_shadow_runner_has_explicit_read_only_boundary(self):
        text = (ROOT / "scripts/run_engine_shadow.py").read_text(encoding="utf-8")
        self.assertIn('"INDEPENDENT_ENGINE_SHADOW"', text)
        self.assertIn('"notification_emitted": False', text)
        self.assertIn('"canonical_state_published": False', text)
        self.assertIn("build_hyperliquid", text)
        self.assertIn("discovery.build()", text)


if __name__ == "__main__":
    unittest.main()
