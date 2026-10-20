import unittest
from pathlib import Path


class FormalDecisionWorkflowSeparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_market_snapshot_does_not_define_a_second_formal_completion_ingress(self):
        workflow = (self.root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("FORMAL_SCHEDULED_REVIEW", workflow)
        self.assertNotIn("formal_completion_path", workflow)
        self.assertIn("Legacy replay inputs", workflow)

    def test_market_snapshot_keeps_unified_scheduled_review_on_review_pipeline(self):
        workflow = (self.root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("FORMAL_SCHEDULED_REVIEW", workflow)
        self.assertIn("FORMAL_SCHEDULED_SYSTEM_REVIEW", workflow)
        self.assertIn("FORMAL_SCHEDULED_TRADE_REVIEW", workflow)
        self.assertIn("STATE_SYNC_ONLY", workflow)

    def test_notification_workflow_has_no_bds_push_wake(self):
        workflow = (self.root / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertNotIn('requests/live_snapshot/*__bds*.json', workflow)
        self.assertNotIn('requests/live_snapshot/*__business_decision_source*.json', workflow)
        self.assertNotIn("Reject direct BDS notification path", workflow)
        self.assertNotIn("DECISION_EVENT_PATH", workflow)
        self.assertNotIn("events/decisions/*.json", workflow)

    def test_notification_workflow_matches_the_unified_completion_filename(self):
        workflow = (self.root / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('requests/live_snapshot/*__scheduled_review_completion.json', workflow)
        self.assertIn(r"^requests/live_snapshot/[^/]+__scheduled_review_completion\\.json$", workflow)
        self.assertNotIn("__formal_decision_completion.json", workflow)

    def test_market_snapshot_does_not_reconsume_scheduled_review_completion(self):
        workflow = (self.root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("__scheduled_review_completion", workflow)
        self.assertIn("grep -vE", workflow)


if __name__ == "__main__":
    unittest.main()
