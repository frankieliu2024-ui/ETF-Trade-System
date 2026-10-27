import unittest
from pathlib import Path


class FormalDecisionWorkflowSeparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_market_snapshot_does_not_create_formal_fact_request(self):
        workflow = (self.root / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertNotIn("Create deterministic Formal Decision schedule ingress", workflow)
        self.assertNotIn("create_scheduled_formal_request.py", workflow)
        self.assertIn("Formal Decision consumes current main facts directly", workflow)

    def test_notification_workflow_uses_shared_completion_path(self):
        workflow = (self.root / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("requests/live_snapshot/*__scheduled_review_completion.json", workflow)
        self.assertIn("scheduled_review_completion", workflow)
        self.assertNotIn("__formal_decision_completion.json", workflow)

    def test_notification_workflow_has_no_bds_push_wake(self):
        workflow = (self.root / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertNotIn("DECISION_EVENT_PATH", workflow)
        self.assertNotIn("events/decisions/*.json", workflow)


if __name__ == "__main__":
    unittest.main()
