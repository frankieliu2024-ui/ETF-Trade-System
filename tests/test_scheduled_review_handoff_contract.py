import unittest
from pathlib import Path

from scripts import scheduled_review_completion as completion


ROOT = Path(__file__).resolve().parents[1]


class ScheduledReviewHandoffContractTests(unittest.TestCase):
    def test_system_review_builder_uses_system_identity(self):
        payload = completion.build_system_review_completion_request(
            {"status": "PASS"},
            "system-request",
            "2026-10-23T08:30:00+08:00",
            "ETF系统复核",
            "system-run",
            "FROZEN SYSTEM REPORT",
        )
        self.assertEqual(payload["request_type"], "SCHEDULED_REVIEW_COMPLETION")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_SYSTEM_REVIEW")
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_SYSTEM_REVIEW_ACTOR")
        self.assertEqual(
            payload["presentation_binding"]["report_type"], "ETF_SYSTEM_REVIEW"
        )

    def test_notification_workflow_exposes_scheduled_review_dispatch(self):
        workflow = (
            ROOT / ".github" / "workflows" / "decision-notification.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("scheduled_review_completion_path:", workflow)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path != ''",
            workflow,
        )
        self.assertIn(
            "Project Scheduled Review completion to canonical review event",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
