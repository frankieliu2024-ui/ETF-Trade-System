from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "decision-notification.yml"


class NotificationWorkflowStructureTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_has_single_canonical_step_for_each_route(self):
        names = [
            "Detect push notification kind",
            "Build delayed execution reconciliation",
            "Send A-share opening value signal or close summary when due",
            "Send A-share monitored-object value event when due",
            "Send safe notification-channel test",
            "Send material user-action notification",
            "Send conditional A-share trading-day post-close account reminder",
            "Persist unified notification and execution-reconciliation state",
            "Surface notification delivery failure without blocking trading system",
        ]
        for name in names:
            self.assertEqual(self.text.count(f"- name: {name}"), 1, name)

    def test_push_routes_include_report_without_dangling_shell(self):
        self.assertIn('requests/report_delivery/*.json', self.text)
        self.assertIn(r"grep -Eq '^requests/report_delivery/[^/]+\.json$'", self.text)
        self.assertIn('echo "report=true" >> "$GITHUB_OUTPUT"', self.text)
        self.assertNotIn("/tmp/changed_files.txt; then", self.text)
        self.assertNotIn("echo report=true", self.text)

    def test_existing_route_commands_remain_present_once(self):
        commands = [
            "python scripts/build_execution_reconciliation.py",
            "python scripts/run_guarded_notification.py regional --market a-share",
            "python scripts/run_guarded_notification.py shock --market a-share",
            "python scripts/run_guarded_notification.py center --mode channel-test",
            "python scripts/run_guarded_notification.py center --mode event",
            "python scripts/run_guarded_notification.py center --mode close",
            "python scripts/merge_notification_state.py",
        ]
        for command in commands:
            self.assertEqual(self.text.count(command), 1, command)


if __name__ == "__main__":
    unittest.main()
