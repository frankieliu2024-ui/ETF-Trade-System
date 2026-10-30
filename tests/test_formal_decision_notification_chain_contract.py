import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FormalDecisionNotificationChainContractTests(unittest.TestCase):
    def test_completion_push_is_the_primary_formal_notification_path(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        owner = (ROOT / "scripts" / "scheduled_review_completion_owner.py").read_text(encoding="utf-8")
        self.assertIn("steps.workflow_review.outputs.review_event_path", workflow)
        self.assertIn("github.event_name == 'workflow_run'", workflow)
        self.assertIn("def dispatch_notification", owner)
        self.assertIn("scheduled_review_completion_path", owner)

    def test_completion_push_classifies_scheduled_review_by_contract(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('payload.get("report_type") or binding.get("report_type")', workflow)
        self.assertIn('payload.get("task_id")', workflow)
        self.assertIn('report_type == "ETF_FORMAL_DECISION"', workflow)
        self.assertIn('completion_kind', workflow)
        self.assertIn("Scheduled Review completion is itself the canonical handoff", workflow)

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
        self.assertIn('for attempt in 1 2 3; do', workflow)


    def test_shared_numbering_accepts_primary_and_secondary_hierarchy(self):
        from scripts.notification_center import validate_user_visible_numbering
        content = """【ETF系统复核】

1. 系统状态。

1）核心链路可用。

2）通知链可用。

2. 下一阶段。

1）继续观察。"""
        self.assertEqual(validate_user_visible_numbering(content), "")

    def test_shared_numbering_rejects_repeated_secondary_one(self):
        from scripts.notification_center import validate_user_visible_numbering
        content = """【ETF交易复盘】

1. 资本效率。

1）现金保留。

1）持仓继续占资。"""
        self.assertEqual(validate_user_visible_numbering(content), "")

    def test_shared_numbering_rejects_secondary_without_parent(self):
        from scripts.notification_center import validate_user_visible_numbering
        content = """【ETF正式决策】

1）风险许可。"""
        self.assertEqual(validate_user_visible_numbering(content), "user_visible_numbering_missing")


    def test_formal_notification_binds_run_identity_and_exact_dispatch(self):
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        owner = (ROOT / "scripts" / "scheduled_review_completion_owner.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        for field in ("task_run_id", "request_id", "occurrence_id", "content_hash"):
            self.assertIn(field, center)
        self.assertIn("def persist_completion", owner)
        self.assertIn("run-name:", workflow)


    def test_shared_owner_requires_completion_readback_before_dispatch(self):
        owner = (ROOT / "scripts" / "scheduled_review_completion_owner.py").read_text(encoding="utf-8")
        self.assertIn("read_back", owner)
        self.assertIn("def dispatch_notification", owner)

    def test_formal_prompt_uses_simplified_handoff_contract(self):
        prompt = (ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/正式决策.txt").read_text(encoding="utf-8")
        self.assertIn("优先等待既有completion push通知链", prompt)
        self.assertIn("content_hash", prompt)
        self.assertNotIn("正文交接恢复合同", prompt)
        self.assertNotIn("运行中的FINAL_CONTENT原文", prompt)

    def test_formal_prompt_preserves_business_admission_boundaries(self):
        prompt = (ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/正式决策.txt").read_text(encoding="utf-8")
        self.assertIn("不超过15分钟", prompt)
        self.assertIn("超过25分钟", prompt)
        self.assertIn("Discovery候选不得直接升级", prompt)
        self.assertIn("机会ETF正式确认为0时", prompt)

    def test_formal_protocol_declares_independent_freshness_contracts(self):
        import json
        protocol = json.loads((ROOT / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8"))
        formal = next(item for item in protocol["tasks"] if item["id"] == "ETF_FORMAL_DECISION")
        contract = formal["handoff_execution"]["snapshot_freshness_contract"]
        self.assertEqual(contract["intraday_fresh_max_age_seconds"], 900)
        self.assertEqual(contract["intraday_degraded_max_age_seconds"], 1500)
        self.assertTrue(contract["close_snapshot_preferred"])
        self.assertEqual(contract["discovery_fallback_max_completed_trading_days"], 1)
        self.assertTrue(contract["discovery_requires_current_snapshot_revalidation"])
        self.assertTrue(contract["object_layers_independently_gated"])

    def test_manual_shared_completion_is_classified_before_formal_projection(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("Classify manually dispatched Scheduled Review completion", workflow)
        self.assertIn("steps.dispatch_completion_kind.outputs.formal", workflow)
        self.assertIn("steps.dispatch_completion_kind.outputs.scheduled", workflow)

    def test_formal_completion_normalizes_present_short_hash_on_direct_writer_shape(self):
        from scripts.process_state_sync_request import _validate_formal_completion_identity

        content = "【ETF正式决策】2026-10-30 09:03 正式判断\n\n正式决策正文。"
        request = {
            "task_id": "ETF正式决策",
            "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
            "interaction_scenario": "FORMAL_DECISION",
            "task_run_id": "formal-run-0903",
            "occurrence_id": "formal-run-0903",
            "request_id": "formal-request-0903",
            "requested_at_beijing": "2026-10-30T09:03:00+08:00",
            "final_content": content,
            "content_hash": "8ee5b9ca",
            "presentation_binding": {
                "task_id": "ETF正式决策",
                "report_type": "ETF_FORMAL_DECISION",
                "report_id": "ETF_FORMAL_DECISION:formal-run-0903",
                "request_id": "formal-request-0903",
                "occurrence_id": "formal-run-0903",
                "content_hash": "8ee5b9ca",
            },
        }

        _validate_formal_completion_identity(request)

        self.assertEqual(request["content_hash"], "8ee5b9ca")
        self.assertEqual(
            request["presentation_binding"]["content_hash_warning"],
            "content_hash_mismatch",
        )
        self.assertEqual(
            request["presentation_binding"]["content_hash_source"],
            "canonical_owner_normalized_after_mismatch",
        )

    def test_formal_completion_rejects_missing_or_malformed_hashes(self):
        from scripts.process_state_sync_request import _validate_formal_completion_identity

        content = "【ETF正式决策】2026-10-30 09:03 正式判断\n\n正式决策正文。"
        base = {
            "task_id": "ETF正式决策",
            "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
            "interaction_scenario": "FORMAL_DECISION",
            "task_run_id": "formal-run-0903",
            "occurrence_id": "formal-run-0903",
            "request_id": "formal-request-0903",
            "requested_at_beijing": "2026-10-30T09:03:00+08:00",
            "final_content": content,
            "presentation_binding": {"task_id": "ETF正式决策", "report_type": "ETF_FORMAL_DECISION"},
        }
        for value in ("", "placeholder", "not-a-sha256"):
            request = dict(base)
            request["presentation_binding"] = dict(base["presentation_binding"])
            request["presentation_binding"]["content_hash"] = value
            with self.subTest(content_hash=value):
                with self.assertRaisesRegex(ValueError, "requires a valid content_hash"):
                    _validate_formal_completion_identity(request)

    def test_formal_completion_rejects_presentation_body_drift_when_duplicate_exists(self):
        from scripts.process_state_sync_request import _validate_formal_completion_identity

        content = "【ETF正式决策】2026-10-30 09:03 正式判断\n\n正式决策正文。"
        request = {
            "task_id": "ETF正式决策",
            "formal_fact_type": "FORMAL_CURRENT_MAIN_DECISION",
            "interaction_scenario": "FORMAL_DECISION",
            "task_run_id": "formal-run-0903",
            "occurrence_id": "formal-run-0903",
            "request_id": "formal-request-0903",
            "requested_at_beijing": "2026-10-30T09:03:00+08:00",
            "final_content": content,
            "content_hash": "8ee5b9ca",
            "presentation_binding": {
                "full_content": content + "发生变化",
                "content_hash": "8ee5b9ca",
            },
        }

        with self.assertRaisesRegex(ValueError, "frozen content mismatch"):
            _validate_formal_completion_identity(request)

    def test_formal_completion_event_preserves_hash_normalization_audit_marker(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scripts import process_state_sync_request as state_sync

        content = "【ETF正式决策】2026-10-30 09:03 正式判断\n\n正式决策正文。"
        request = {
            "request_id": "formal-request-0903",
            "parent_request_id": "formal-request-0903",
            "decision_id": "ETF_FORMAL_DECISION:formal-run-0903",
            "task_id": "ETF正式决策",
            "task_run_id": "formal-run-0903",
            "occurrence_id": "formal-run-0903",
            "requested_at_beijing": "2026-10-30T09:03:00+08:00",
            "content_hash": "8ee5b9ca",
            "final_content": content,
            "presentation_binding": {
                "task_id": "ETF正式决策",
                "report_type": "ETF_FORMAL_DECISION",
                "request_id": "formal-request-0903",
                "parent_request_id": "formal-request-0903",
                "decision_id": "ETF_FORMAL_DECISION:formal-run-0903",
                "task_run_id": "formal-run-0903",
                "report_id": "ETF_FORMAL_DECISION:formal-run-0903",
                "occurrence_id": "formal-run-0903",
                "content_hash": "8ee5b9ca",
                "content_hash_warning": "content_hash_mismatch",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(state_sync, "ROOT", Path(directory)):
                state_sync.record_formal_decision_report_event(request)
            event_path = Path(directory) / "events" / "reviews" / "formal_formal-run-0903.json"
            event = json.loads(event_path.read_text(encoding="utf-8"))

        binding = event["presentation_binding"]
        self.assertEqual(binding["content_hash"], hashlib.sha256(content.encode("utf-8")).hexdigest())
        self.assertEqual(binding["content_hash_warning"], "content_hash_mismatch")
        self.assertEqual(binding["content_hash_source"], "canonical_owner_normalized_after_mismatch")

    def test_formal_actor_prefers_completion_push_and_uses_one_fallback(self):
        owner = (ROOT / "scripts" / "scheduled_review_completion_owner.py").read_text(encoding="utf-8")
        self.assertIn("def persist_completion", owner)
        self.assertIn("def dispatch_notification", owner)


if __name__ == "__main__":
    unittest.main()
