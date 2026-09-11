from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / ".github" / "workflows" / "decision-notification.yml"
CONSISTENCY = ROOT / ".github" / "workflows" / "system-consistency.yml"


class WorkflowCostConservationTests(unittest.TestCase):
    def test_notification_does_not_wake_on_watchdog_completion(self):
        text = DECISION.read_text(encoding="utf-8")
        trigger = text.split("  workflow_run:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
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


    def test_notification_wakeup_accepts_market_producer_non_success(self):
        text = DECISION.read_text(encoding="utf-8")
        self.assertIn("github.event.workflow_run.name != 'ETF workflow failure guard'", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertNotIn("github.event.workflow_run.conclusion == 'success' }}", text)

        def allowed(name, conclusion):
            return (
                name != "ETF workflow failure guard"
                or conclusion == "success"
            )

        self.assertTrue(allowed("Overseas pre-open pulse", "failure"))
        self.assertTrue(allowed("US extended-hours pulse", "cancelled"))
        self.assertTrue(allowed("ETF market snapshot", "failure"))
        self.assertFalse(allowed("ETF workflow failure guard", "skipped"))
        self.assertFalse(allowed("ETF workflow failure guard", "failure"))
        self.assertTrue(allowed("ETF workflow failure guard", "success"))

    def test_notification_domain_keeps_fact_and_dedupe_guards(self):
        guarded = (ROOT / "scripts" / "run_guarded_notification.py").read_text(encoding="utf-8")
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        self.assertIn("notification_evidence_error(event)", guarded)
        self.assertIn("find_existing_notification(notifications, event)", center)
        self.assertIn("ALREADY_MANAGED", center)
        self.assertIn("NO_NOTIFICATION_NEEDED", center)
        self.assertIn("notification_center.json", center)
        self.assertNotIn("PUSHPLUS_TOKEN", guarded)

    def test_real_market_orchestration_batches_before_external_send(self):
        text = DECISION.read_text(encoding="utf-8")
        for market in ("a-share", "apac", "us"):
            self.assertIn(f"run_guarded_notification.py batch --market {market}", text)
        runner = (ROOT / "scripts" / "run_guarded_notification.py").read_text(encoding="utf-8")
        self.assertIn("def _batch_event", runner)
        self.assertIn("constituent_events", runner)
        self.assertIn("persist_and_send(event", runner)
        self.assertIn("if: ${{ false }}", text)

    def test_non_success_wake_gate_is_preserved(self):
        text = DECISION.read_text(encoding="utf-8")
        self.assertIn("github.event.workflow_run.name != 'ETF workflow failure guard'", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)

if __name__ == "__main__":
    unittest.main()
