import unittest
from pathlib import Path


class FormalDecisionWorkflowSeparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_market_snapshot_accepts_completion_not_bds_as_formal_completion(self):
        workflow = (self.root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("CHATGPT_FORMAL_DECISION_COMPLETION", workflow)
        self.assertIn("FORMAL_DECISION_COMPLETION", workflow)
        self.assertIn("BDS-only push: no Formal Decision completion ingress", workflow)
        self.assertNotIn("The BDS itself is the completion envelope", workflow)
        self.assertNotIn('request_files="$bds_file"', workflow)

    def test_market_snapshot_classifies_formal_scheduled_review_as_completion(self):
        workflow = (self.root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn('"FORMAL_SCHEDULED_REVIEW"', workflow)
        self.assertIn('"FORMAL_SCHEDULED_SYSTEM_REVIEW"', workflow)
        self.assertIn("formal_completion=$(python", workflow)

    def test_notification_workflow_has_no_bds_push_wake(self):
        workflow = (self.root / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertNotIn('requests/live_snapshot/*__bds*.json', workflow)
        self.assertNotIn('requests/live_snapshot/*__business_decision_source*.json', workflow)
        self.assertNotIn("Reject direct BDS notification path", workflow)
        self.assertNotIn("DECISION_EVENT_PATH", workflow)
        self.assertNotIn("events/decisions/*.json", workflow)


if __name__ == "__main__":
    unittest.main()
