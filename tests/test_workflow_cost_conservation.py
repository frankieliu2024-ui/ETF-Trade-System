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

    def test_issue656_prewrite_migration_is_fail_closed_and_incident_bounded(self):
        text = CONSISTENCY.read_text(encoding="utf-8")
        self.assertIn("global_prewrite_consistency", text)
        self.assertIn("FAIL_PRESERVED", text)
        self.assertIn('allowed = "formal_files:confirmed_fee_projection"', text)
        self.assertIn('hard_error_count") or 0) != 1', text)
        self.assertIn('trade_fact_count") or 0) != 33', text)
        self.assertIn("known_fees != 159.56", text)
        for code, qty in (("588000", "14100.0"), ("159781", "19500.0"), ("159941", "12200.0"), ("561980", "28900.0"), ("513180", "0.0")):
            self.assertIn(f'"{code}": {qty}', text)
        self.assertNotIn('steps.consistency.outputs.research_rc }}" = "0"', text)
        self.assertIn("research checker consumes this same fresh report", text)
        self.assertIn("exact failed-check/error-set validation", text)
        self.assertIn('steps.consistency.outputs.market_rc }}" = "0"', text)

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


if __name__ == "__main__":
    unittest.main()
