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

    def test_formal_completion_identity_is_bound_before_optional_state_sync(self):
        detection_start = self.text.index("- name: Detect triggering request envelope before any runtime work")
        state_sync_start = self.text.index("- name: Process optional broker/dashboard state sync")
        detection = self.text[detection_start:state_sync_start]
        self.assertIn("FORMAL_COMPLETION=false", detection)
        self.assertIn('request.get("request_type") or "").upper() == "BUSINESS_DECISION_SOURCE"', detection)
        self.assertIn('echo "formal_completion=$FORMAL_COMPLETION"', detection)

    def test_formal_completion_does_not_reenter_query_context_after_state_sync_failure(self):
        start = self.text.index("- name: Build on-demand query context")
        block = self.text[start:start + 650]
        self.assertIn("steps.state_sync.outputs.formal_completion != 'true'", block)
        self.assertIn("steps.triggering_request.outputs.formal_completion != 'true'", block)

    def test_all_derived_decision_steps_use_the_early_formal_completion_guard(self):
        for name in (
            "Build E2E usability state",
            "Build state context and candidates",
            "Build post-market review context",
        ):
            start = self.text.index(f"- name: {name}")
            block = self.text[start:start + 700]
            self.assertIn("steps.triggering_request.outputs.formal_completion != 'true'", block, name)

if __name__ == "__main__":
    unittest.main()
