from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "decision-notification.yml"


class NotificationPublicationWakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_canonical_market_publications_wake_existing_notification_consumer(self):
        for path in (
            'data/state/CURRENT.json',
            'data/state/overseas_context.json',
            'data/state/us_extended_hours_context.json',
        ):
            self.assertIn(f'- "{path}"', self.text)

    def test_market_publication_pushes_are_classified_by_domain(self):
        expected = {
            "CURRENT.json": "a_share_market=true",
            "overseas_context.json": "apac_market=true",
            "us_extended_hours_context.json": "us_market=true",
        }
        for filename, output in expected.items():
            self.assertIn(filename, self.text)
            self.assertIn(output, self.text)

    def test_existing_guarded_batches_handle_publication_wakes(self):
        for domain in ("a-share", "apac", "us"):
            self.assertIn(f"python scripts/run_guarded_notification.py batch --market {domain}", self.text)
        self.assertIn("steps.push_kind.outputs.a_share_market == 'true'", self.text)
        self.assertIn("steps.push_kind.outputs.apac_market == 'true'", self.text)
        self.assertIn("steps.push_kind.outputs.us_market == 'true'", self.text)

    def test_original_workflow_run_ingress_is_preserved_for_duplicate_wake_coexistence(self):
        self.assertIn("workflow_run:", self.text)
        self.assertIn('github.event.workflow_run.name == \'ETF market snapshot\'', self.text)
        self.assertIn('github.event.workflow_run.name == \'Overseas pre-open pulse\'', self.text)
        self.assertIn('github.event.workflow_run.name == \'US extended-hours pulse\'', self.text)
        self.assertIn("concurrency:\n  group: etf-decision-notification\n  cancel-in-progress: false", self.text)

    def test_no_new_notification_workflow_or_direct_producer_notification_path(self):
        overseas = (ROOT / ".github" / "workflows" / "overseas-preopen-pulse.yml").read_text(encoding="utf-8")
        market = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertNotIn("run_guarded_notification.py", overseas)
        self.assertNotIn("run_guarded_notification.py", market)


if __name__ == "__main__":
    unittest.main()
