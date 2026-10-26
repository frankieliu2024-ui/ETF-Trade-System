import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FormalDecisionNotificationChainContractTests(unittest.TestCase):
    def test_completion_push_is_the_primary_formal_notification_path(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        actor = (ROOT / "scripts" / "run_scheduled_formal_decision_actor.py").read_text(encoding="utf-8")
        self.assertIn("steps.workflow_review.outputs.review_event_path", workflow)
        self.assertIn("github.event_name == 'workflow_run'", workflow)
        self.assertIn("_wait_for_completion_push(canonical_path, started_at)", actor)
        self.assertIn("if not push.get(\"read_back\"):", actor)

    def test_completion_push_classifies_scheduled_review_by_contract(self):
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('payload.get("formal_fact_type") == "FORMAL_POST_CLOSE_REVIEW"', workflow)
        self.assertIn('binding.get("report_type") == "ETF_FORMAL_DECISION"', workflow)
        self.assertIn('completion_kind', workflow)
        self.assertIn("Scheduled Review completion push is not a Formal Decision wake", workflow)

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
        self.assertEqual(
            validate_user_visible_numbering(content),
            "user_visible_secondary_numbering_not_sequential",
        )

    def test_shared_numbering_rejects_secondary_without_parent(self):
        from scripts.notification_center import validate_user_visible_numbering
        content = """【ETF正式决策】

1）风险许可。"""
        self.assertEqual(
            validate_user_visible_numbering(content),
            "user_visible_secondary_without_primary_parent",
        )


    def test_formal_notification_binds_run_identity_and_exact_dispatch(self):
        center = (ROOT / "scripts" / "notification_center.py").read_text(encoding="utf-8")
        actor = (ROOT / "scripts" / "run_scheduled_formal_decision_actor.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        for field in ("task_run_id", "request_id", "occurrence_id", "content_hash"):
            self.assertIn(field, center)
        self.assertIn("expected_run_name", actor)
        self.assertIn("run-name:", workflow)


    def test_formal_actor_requires_same_run_notification_readback(self):
        actor = (ROOT / "scripts" / "run_scheduled_formal_decision_actor.py").read_text(encoding="utf-8")
        self.assertIn("handoff_ok = bool(dispatch.get(\"submitted\"))", actor)
        self.assertIn('bool(dispatch.get("read_back"))', actor)
        self.assertIn('"same_run_handoff_complete"', actor)
        self.assertIn("return 0 if handoff_ok else 2", actor)

    def test_formal_prompt_uses_simplified_handoff_contract(self):
        prompt = (ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/正式决策.txt").read_text(encoding="utf-8")
        self.assertIn("completion push是默认主路径", prompt)
        self.assertIn("content_hash只用于审计", prompt)
        self.assertNotIn("正文交接恢复合同", prompt)
        self.assertNotIn("运行中的FINAL_CONTENT原文", prompt)

    def test_formal_prompt_preserves_business_admission_boundaries(self):
        prompt = (ROOT / "docs/freezes/bundles/ETF_THREE_TASKS_FREEZE_20261020T1817/prompts/正式决策.txt").read_text(encoding="utf-8")
        self.assertIn("盘中快照年龄不超过15分钟", prompt)
        self.assertIn("超过25分钟且不存在合法降级事实", prompt)
        self.assertIn("Discovery候选必须经过本次有效市场快照重新核验", prompt)
        self.assertIn("机会ETF明确确认为0时", prompt)

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
        self.assertIn("Classify manually dispatched completion", workflow)
        self.assertIn("steps.dispatch_completion_kind.outputs.formal", workflow)
        self.assertIn("steps.dispatch_completion_kind.outputs.scheduled", workflow)

    def test_formal_actor_prefers_completion_push_and_uses_one_fallback(self):
        actor = (ROOT / "scripts" / "run_scheduled_formal_decision_actor.py").read_text(encoding="utf-8")
        handoff = actor.split("started_at = datetime.now(timezone.utc)", 1)[1]
        self.assertIn("canonical_path = _persist_completion_to_main(completion_path)", handoff)
        self.assertIn("push = _wait_for_completion_push(canonical_path, started_at)", handoff)
        self.assertIn("if not push.get(\"read_back\"):", handoff)
        self.assertIn("dispatch = _dispatch_notification(canonical_path)", handoff)


if __name__ == "__main__":
    unittest.main()
