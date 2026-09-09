from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / ".github" / "workflows" / "decision-notification.yml"
CONSISTENCY = ROOT / ".github" / "workflows" / "system-consistency.yml"


class WorkflowCostConservationTests(unittest.TestCase):
    def test_notification_does_not_wake_on_watchdog_completion(self):
        text = DECISION.read_text(encoding="utf-8")
        trigger = text.split("  workflow_run:\\n", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertNotIn('"ETF runtime self-healing watchdog"', trigger)
        for required in (
            '"ETF market snapshot"',
            '"Overseas pre-open pulse"',
            '"US extended-hours pulse"',
            '"ETF workflow failure guard"',
        ):
            self.assertIn(required, trigger)

    def test_production_acceptance_requires_successful_consistency_gate(self):
        text = CONSISTENCY.read_text(encoding="utf-8")
        self.assertIn("needs.consistency.result == 'success'", text)
        self.assertNotIn("if: ${{ always() && github.event_name != 'pull_request' }}", text)

    def test_cost_changes_do_not_touch_market_or_decision_contracts(self):
        self.assertIn('name: ETF market snapshot', (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8"))
        self.assertIn('name: ETF runtime self-healing watchdog', (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8"))
        self.assertIn('name: ETF decision notification', DECISION.read_text(encoding="utf-8"))
        self.assertIn('name: ETF system consistency', CONSISTENCY.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
