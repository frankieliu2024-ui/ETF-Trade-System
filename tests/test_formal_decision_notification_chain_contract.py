import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FormalDecisionNotificationChainContractTests(unittest.TestCase):
    def test_completion_push_reaches_event_notification(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("steps.formal_completion_event.outputs.review_event_path != ''", workflow)
        self.assertIn("formal_completion_path:", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.formal_completion_path != ''", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch' && steps.formal_completion_event.outputs.review_event_path != ''", workflow)
        self.assertNotIn("\\n      - name: Send material user-action notification", workflow)

    def test_formal_review_event_requires_identity_binding(self):
        writer = (ROOT / "scripts" / "process_state_sync_request.py").read_text(encoding="utf-8")
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        self.assertIn("complete request/parent/decision identity", writer)
        self.assertIn('"parent_request_id": identity["parent_request_id"]', writer)
        self.assertIn('"decision_id": identity["decision_id"]', writer)
        self.assertIn("binding_parent_request_id", center)
        self.assertIn("binding_decision_id", center)

    def test_formal_protocol_uses_review_event_path(self):
        protocol = (ROOT / "docs" / "Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertNotIn("events/decisions/<decision_id>.json", protocol)
        self.assertIn("events/reviews/formal_<occurrence_id>.json", protocol)

    def test_formal_completion_persists_event_and_enables_strict_mode(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        self.assertIn("Persist Formal Decision canonical review event", workflow)
        self.assertIn('git add -- "$EVENT_PATH"', workflow)
        self.assertIn("FORMAL_COMPLETION_EVENT:", workflow)
        self.assertIn("strict_formal_completion_enabled", center)
        self.assertIn("strict_formal_completion_enabled()", center)

    def test_strict_formal_mode_executes_without_scope_error(self):
        import os
        from unittest import mock
        from scripts import notification_center
        with mock.patch.dict(
            os.environ,
            {
                "FORMAL_COMPLETION_EVENT": "true",
                "REVIEW_EVENT_PATH": "events/reviews/formal_missing.json",
                "FORMAL_PRESENTATION_PATH": "",
                "REPORT_HANDOFF_PATH": "",
                "REPORT_DELIVERY_PATH": "",
            },
            clear=False,
        ):
            self.assertIsNone(notification_center.report_delivery_event())
            self.assertIsNone(notification_center.report_delivery_validation_error())

    def test_strict_formal_mode_requires_exact_event_binding_before_compatibility(self):
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        self.assertIn("strict_formal_completion_enabled() and not review_event_path", center)
        self.assertIn("strict_formal_completion_enabled() and not explicit_report_binding", center)
        self.assertIn("REPORT handoff/delivery compatibility", center)

    def test_legacy_decision_event_route_is_disabled(self):
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        self.assertIn("The legacy DECISION_EVENT_PATH/events/decisions route is deliberately disabled.", center)
        self.assertNotIn("formal_decision_report_event, execution_confirmation_event", center)

    def test_persistence_has_latest_main_retry(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('for attempt in 1 2 3; do', workflow)
        self.assertIn('git fetch --no-tags origin main', workflow)
        self.assertIn('git reset --hard origin/main', workflow)
        self.assertIn('event_backup="/tmp/formal-review-event.json"', workflow)


if __name__ == "__main__":
    unittest.main()
