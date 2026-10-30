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
            "Isolate pushed report delivery request",
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

    def test_push_routes_bind_report_to_triggering_push_identity(self):
        self.assertIn('requests/report_delivery/*.json', self.text)
        self.assertIn('PUSH_BEFORE: ${{ github.event.before }}', self.text)
        self.assertIn('PUSH_AFTER: ${{ github.sha }}', self.text)
        self.assertIn('git diff --name-only "$PUSH_BEFORE" "$PUSH_AFTER" > /tmp/changed_files.txt', self.text)
        self.assertNotIn('git diff --name-only HEAD^ HEAD > /tmp/changed_files.txt', self.text)
        self.assertIn(r"grep -E '^requests/report_delivery/[^/]+\.json$'", self.text)
        self.assertIn('echo "report=true" >> "$GITHUB_OUTPUT"', self.text)
        self.assertIn('echo "report_path=${report_paths[0]}" >> "$GITHUB_OUTPUT"', self.text)
        self.assertIn('REPORT_PATH: ${{ steps.push_kind.outputs.report_path }}', self.text)
        self.assertIn("find requests/report_delivery -maxdepth 1 -type f -name '*.json' ! -path \"$REPORT_PATH\" -delete", self.text)
        self.assertIn('test "$(find requests/report_delivery -maxdepth 1 -type f -name \'*.json\' | wc -l)" -eq 1', self.text)
        self.assertIn("Ambiguous REPORT push: expected exactly one report request", self.text)
        self.assertIn("/tmp/changed_files.txt; then", self.text)
        self.assertNotIn("echo report=true", self.text)

    def test_consistency_workflow_run_validates_current_bds_without_waking_notification(self):
        self.assertIn('"ETF system consistency"', self.text)
        self.assertNotIn('id: workflow_bds', self.text)
        self.assertNotIn("requests/live_snapshot/*__bds*.json", self.text)
        self.assertNotIn("requests/live_snapshot/*__business_decision_source*.json", self.text)
        # The consistency producer may validate the current BDS, but it must not
        # expose a BDS presentation path as a notification input.
        self.assertNotIn("steps.workflow_bds.outputs.formal_content_path", self.text)
        # Formal Decision BDS is not a notification wake; shared completion/REPORT binding is required.
        self.assertNotIn("steps.push_kind.outputs.formal_content == 'true' || steps.push_kind.outputs.trade", self.text)
        self.assertNotIn("DECISION_EVENT_PATH", self.text)
        self.assertIn("REVIEW_EVENT_PATH", self.text)
        self.assertNotIn("--formal-content-file", self.text)

    def test_push_routes_accept_canonical_formal_review_event(self):
        self.assertNotIn("__bds", self.text)
        self.assertIn('events/reviews/*.json', self.text)
        self.assertIn('events/reviews/formal_*.json', self.text)

    def test_account_and_trade_canonical_publications_wake_existing_notification_chain(self):
        self.assertIn('- "data/state/account_fact.json"', self.text)
        self.assertIn('- "events/trades/*.json"', self.text)
        self.assertIn(r"grep -Eq '^events/trades/[^/]+\.json$' /tmp/changed_files.txt", self.text)
        self.assertIn("grep -Fxq 'data/state/account_fact.json' /tmp/changed_files.txt", self.text)
        self.assertIn('echo "trade=true" >> "$GITHUB_OUTPUT"', self.text)
        self.assertIn('echo "account=true" >> "$GITHUB_OUTPUT"', self.text)
        self.assertIn("steps.push_kind.outputs.account == 'true'", self.text)
        self.assertIn("steps.push_kind.outputs.trade == 'true'", self.text)
        self.assertNotIn('- "data/state/execution_reconciliation.json"', self.text)

    def test_account_trade_push_reuses_reconciliation_and_event_center(self):
        reconciliation_condition = (
            "github.event_name == 'workflow_run' || (github.event_name == 'push' && "
            "(steps.push_kind.outputs.account == 'true' || steps.push_kind.outputs.trade == 'true'))"
        )
        event_condition_fragment = (
            "steps.push_kind.outputs.decision == 'true' || "
            "steps.push_kind.outputs.trade == 'true' || steps.push_kind.outputs.account == 'true' || "
            "steps.push_kind.outputs.report == 'true'"
        )
        self.assertIn(reconciliation_condition, self.text)
        self.assertIn(event_condition_fragment, self.text)
        self.assertEqual(self.text.count("python scripts/build_execution_reconciliation.py"), 1)
        self.assertEqual(self.text.count("id: event_notify"), 1)
        self.assertNotIn("DECISION_EVENT_PATH", self.text)
        self.assertIn("REVIEW_EVENT_PATH", self.text)
        self.assertIn("REPORT_HANDOFF_PATH", self.text)
        self.assertNotIn("$FORMAL_PRESENTATION_PATH", self.text)

    def test_existing_route_commands_remain_present_once(self):
        commands = [
            "python scripts/build_execution_reconciliation.py",
            "python scripts/run_guarded_notification.py batch --market a-share",
            "python scripts/run_guarded_notification.py batch --market apac",
            "python scripts/run_guarded_notification.py batch --market us",
            "python scripts/run_guarded_notification.py center --mode channel-test",
            "python scripts/run_guarded_notification.py center --mode close",
            "python scripts/merge_notification_state.py",
        ]
        for command in commands:
            self.assertEqual(self.text.count(command), 1, command)

    def test_overseas_producers_only_publish_context(self):
        root = ROOT / ".github" / "workflows"
        for name in ("overseas-preopen-pulse.yml", "us-extended-hours-pulse.yml"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("PUSHPLUS_TOKEN", text)
            self.assertNotIn("run_guarded_notification.py", text)
            self.assertNotIn("notification_center.json", text)
        self.assertIn('"Overseas pre-open pulse"', self.text)
        self.assertIn('"US extended-hours pulse"', self.text)
        self.assertIn("PREVIOUS_CONTEXT_PATH: data/state/overseas_context_previous.json", self.text)
        self.assertIn("steps.apac_summary_notify.outcome", self.text)

    def test_single_notification_external_effect_owner_is_machine_checked(self):
        from scripts.check_production_mutation_protocol import run
        result = run(ROOT)
        self.assertEqual(result["status"], "PASS", result.get("errors"))
        names = [x for x in result["checks"] if x["name"] == "notification_owner:single_external_effect_committer"]
        self.assertEqual(names[0]["status"], "PASS")

    def test_notification_state_uses_single_owner_contract(self):
        import json
        config = json.loads((ROOT / "config/maintenance/production_mutation_protocol.json").read_text(encoding="utf-8"))
        self.assertEqual(config["single_owner_files"]["data/state/notification_center.json"], ".github/workflows/decision-notification.yml")
        from scripts.check_production_mutation_protocol import _workflow_may_stage
        self.assertTrue(_workflow_may_stage("git add data/state/notification_center.json", "data/state/notification_center.json"))


    def test_guarded_runner_accepts_formal_content_file_and_binds_existing_center(self):
        runner = (ROOT / "scripts" / "run_guarded_notification.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--formal-content-file")', runner)
        self.assertIn('def _run_center(mode: str, formal_content_file: str | None = None)', runner)
        self.assertIn("Formal Decision direct content projection is disabled", runner)
        self.assertIn("return _run_center(args.mode, args.formal_content_file)", runner)


    def test_notification_state_persistence_replays_latest_main_without_resend(self):
        persist = self.text.split("- name: Persist unified notification and execution-reconciliation state", 1)[1]
        persist = persist.split("- name: Surface notification delivery failure", 1)[0]
        self.assertIn("for attempt in 1 2 3; do", persist)
        self.assertIn("git fetch origin main", persist)
        self.assertIn("git reset --hard origin/main", persist)
        self.assertIn("merge_notification_state.py", persist)
        self.assertNotIn("run_guarded_notification.py", persist)
        self.assertNotIn("git push --force", persist)


    def test_notification_persistence_rejects_empty_or_malformed_state(self):
        persist = self.text.split("- name: Persist unified notification and execution-reconciliation state", 1)[1]
        persist = persist.split("- name: Surface notification delivery failure", 1)[0]
        self.assertIn("test -s data/state/notification_center.json", persist)
        self.assertIn("json.loads(path.read_text(encoding=\"utf-8\"))", persist)
        self.assertIn("notification_center.json is not a valid durable state object", persist)

        merge = (ROOT / "scripts" / "merge_notification_state.py").read_text(encoding="utf-8")
        self.assertIn("allow_empty: bool = True", merge)
        self.assertIn("allow_empty=False", merge)
        self.assertIn("incoming notification state must contain a notifications list", merge)


    def test_formal_decision_has_no_legacy_production_route(self):
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        sync = (ROOT / "scripts" / "process_state_sync_request.py").read_text(encoding="utf-8")
        index = (ROOT / "ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        protocol = (ROOT / "docs" / "Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        notice = (ROOT / "docs" / "ETF主动通知体系.md").read_text(encoding="utf-8")
        self.assertIn("events/reviews/formal_<occurrence_id>.json", protocol)
        self.assertIn("events/reviews/formal_", notice)
        self.assertNotIn("BDS写入、回读和通知尝试先于用户窗口输出", notice)
        self.assertNotIn("完成这些不阻塞正文的后置交接提交后，向用户输出", index)
        self.assertNotIn("1. ChatGPT完成业务判断后，必须先提交唯一 BUSINESS_DECISION_SOURCE", protocol)
        self.assertIn("READY 后，必须先完成业务判断并冻结唯一", protocol)
        self.assertIn("先发布此前冻结的同一份完整正文，再继续后置交接", protocol)
        self.assertIn("后置交接不得阻断正文发布", protocol)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", protocol)
        self.assertNotIn("在用户窗口返回前，先执行后置交接，再输出", protocol)
        self.assertNotIn("BUSINESS_DECISION_SOURCE", protocol)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", protocol)
        self.assertNotIn("formal_completion_path", protocol)
        self.assertIn("def formal_decision_report_event", center)
        self.assertIn("return None", center[center.index("def formal_decision_report_event"):center.index("def formal_decision_report_event")+220])
        self.assertIn("DECISION_EVENT_PATH", center)
        self.assertIn('if os.environ.get("DECISION_EVENT_PATH", "").strip():', center)
        self.assertIn("Formal Decision delivery is allowed only from an exact canonical review event", center)
        self.assertIn("if strict_formal_completion_enabled() and not event_path:", center)
        self.assertIn("new Formal Decision production source cannot use legacy events/decisions writer", sync)
        self.assertNotIn("DECISION_EVENT_PATH", self.text)


    def test_scheduled_trade_review_process_returns_canonical_publication(self):
        sync = (ROOT / "scripts" / "process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn('result["canonical_publication"] = f"events/reviews/{market_date}.json"', sync)
        self.assertIn('str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION"', sync)
        self.assertIn('market_date = str(request.get("market_date") or "").strip()', sync)
        self.assertIn('SCHEDULED_REVIEW_COMPLETION requires market_date', sync)


if __name__ == "__main__":
    unittest.main()

