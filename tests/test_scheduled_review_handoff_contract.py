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
        self.assertEqual(payload["source"], "CHATGPT_DIRECT_GITHUB_CONTENTS_WRITER")
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


def test_system_review_builder_derives_missing_request_identity():
    payload = completion.build_system_review_completion_request(
        {"status": "PASS", "market_date": "2026-10-27"},
        "", "2026-10-27T10:30:00+08:00", "ETF系统复核", "",
        "【ETF系统复核】2026-10-27 10:30 早间复核\n\n正文",
        entry_mode="MANUAL_SCHEDULED_TASK",
    )
    assert payload["request_id"].startswith("manual-system-review-")
    assert payload["task_run_id"]
    assert payload["occurrence_id"] == payload["task_run_id"]



def test_system_review_identity_gate_rejects_wrong_entry_and_binding():
    from scripts import process_state_sync_request as state_sync

    try:
        state_sync._validate_scheduled_system_review_identity({
            "request_type": "SCHEDULED_REVIEW_COMPLETION",
            "task_id": "ETF系统复核",
            "entry_mode": "UNKNOWN_ENTRY",
            "interaction_scenario": "SCHEDULED_SYSTEM_REVIEW",
        })
    except ValueError as exc:
        assert "entry_mode" in str(exc)
    else:
        raise AssertionError("invalid entry_mode was accepted")

    try:
        state_sync._validate_scheduled_system_review_identity({
            "request_type": "SCHEDULED_REVIEW_COMPLETION",
            "task_id": "ETF系统复核",
            "entry_mode": "AUTOMATIC_SCHEDULED_TASK",
            "interaction_scenario": "SCHEDULED_SYSTEM_REVIEW",
            "presentation_binding": {
                "task_id": "ETF系统复核",
                "entry_mode": "MANUAL_CONVERSATION",
            },
        })
    except ValueError as exc:
        assert "presentation entry_mode" in str(exc)
    else:
        raise AssertionError("conflicting presentation entry_mode was accepted")



def test_system_review_identity_gate_rejects_invalid_binding_and_origin():
    from scripts import process_state_sync_request as state_sync

    cases = [
        (
            {"presentation_binding": []},
            "presentation_binding",
        ),
        (
            {
                "system_review": {
                    "source": "CHATGPT_SCHEDULED_SYSTEM_REVIEW",
                    "entry_mode": "MANUAL_CONVERSATION",
                }
            },
            "origin source/entry_mode",
        ),
    ]
    for extra, message in cases:
        request = {
            "request_type": "SCHEDULED_REVIEW_COMPLETION",
            "task_id": "ETF系统复核",
            "entry_mode": "AUTOMATIC_SCHEDULED_TASK",
            "interaction_scenario": "SCHEDULED_SYSTEM_REVIEW",
            **extra,
        }
        try:
            state_sync._validate_scheduled_system_review_identity(request)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"invalid identity was accepted: {message}")
