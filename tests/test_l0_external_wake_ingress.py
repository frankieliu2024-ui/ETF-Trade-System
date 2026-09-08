from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/self-healing-watchdog.yml"


class L0ExternalWakeIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_issue_comment_ingress_is_created_only(self):
        trigger = self.workflow.split("  issue_comment:\n", 1)[1].split("  workflow_run:\n", 1)[0]
        self.assertIn("types: [created]", trigger)

    def test_issue_comment_ingress_is_restricted_to_issue_385_and_marker(self):
        job = self.workflow.split("jobs:\n  watchdog:\n", 1)[1].split("    runs-on:", 1)[0]
        self.assertIn("github.event_name != 'issue_comment'", job)
        self.assertIn("github.event.issue.number == 385", job)
        self.assertIn("startsWith(github.event.comment.body, '[ETF_L0_WAKE]')", job)

    def test_external_wake_does_not_add_market_request_transport(self):
        self.assertNotIn("requests/live_snapshot", self.workflow)
        self.assertNotIn("repository_dispatch", self.workflow)
        self.assertEqual(self.workflow.count("name: ETF runtime self-healing watchdog"), 1)

    def test_existing_canonical_dispatches_and_dedupe_remain_single_owner(self):
        self.assertEqual(self.workflow.count("gh workflow run market-snapshot.yml --ref main"), 1)
        self.assertEqual(self.workflow.count("gh workflow run overseas-preopen-pulse.yml --ref main"), 1)
        self.assertEqual(self.workflow.count("gh workflow run us-extended-hours-pulse.yml --ref main"), 1)
        self.assertIn('status == "queued" or .status == "in_progress"', self.workflow)


if __name__ == "__main__":
    unittest.main()
