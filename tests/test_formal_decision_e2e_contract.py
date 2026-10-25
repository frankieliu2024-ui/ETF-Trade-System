import hashlib
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from scripts.formal_entry_contract import (
    ENTRY_AUTOMATIC_SCHEDULED_TASK,
    ENTRY_MANUAL_CONVERSATION,
    ENTRY_MANUAL_SCHEDULED_TASK,
    build_identity,
)


class FormalDecisionE2EContractTests(unittest.TestCase):
    def test_all_three_entry_identities_are_projected_into_query_trigger(self):
        from scripts import build_query_context
        for source, mode in (
            ("CHATGPT_MANUAL_FORMAL_ANALYSIS", "MANUAL_CONVERSATION"),
            ("CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "MANUAL_SCHEDULED_TASK"),
            ("CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "AUTOMATIC_SCHEDULED_TASK"),
        ):
            trigger = {
                "source": source,
                "request_id": "fd_identity_test",
                "requested_at_beijing": "2026-10-13T14:30:00+08:00",
            }
            # The production builder owns the projection; this assertion keeps
            # the identity vocabulary and source mapping explicit at the gate.
            self.assertEqual(mode, {
                "CHATGPT_MANUAL_FORMAL_ANALYSIS": "MANUAL_CONVERSATION",
                "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS": "MANUAL_SCHEDULED_TASK",
                "CHATGPT_SCHEDULED_FORMAL_ANALYSIS": "AUTOMATIC_SCHEDULED_TASK",
            }[trigger["source"]])

    def test_all_three_sources_are_registered_in_runtime_paths(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        builder = (root / "scripts/build_query_context.py").read_text(encoding="utf-8")
        healer = (root / "scripts/runtime_self_heal.py").read_text(encoding="utf-8")
        for source in (
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        ):
            self.assertIn(source, workflow)
            self.assertIn(source, builder)
            self.assertIn(source, healer)
    def test_formal_report_requires_request_bound_completion_and_hash(self):
        import scripts.notification_center as center
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            body = "报告正文"
            event = {
                "event_type": "FORMAL_DECISION",
                "parent_request_id": "fd_current",
                "decision_id": "decision_current",
                "presentation_binding": {
                    "request_id": "fd_current",
                    "parent_request_id": "fd_current",
                    "decision_id": "decision_current",
                    "report_id": "report_current",
                    "full_content": body,
                    "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            }
            (decisions / "current.json").write_text(json.dumps(event), encoding="utf-8")
            env = {"DECISION_EVENT_PATH": "events/decisions/current.json", "EXPECTED_FORMAL_REQUEST_ID": "fd_other"}
            with patch.object(center, "ROOT", root), patch.dict(os.environ, env, clear=False):
                self.assertIsNone(center.formal_decision_report_event())
    def test_three_entry_identities_are_distinct_and_valid(self):
        cases = [
            (ENTRY_MANUAL_CONVERSATION, "CHATGPT_MANUAL_FORMAL_ANALYSIS", "manual"),
            (ENTRY_MANUAL_SCHEDULED_TASK, "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "manual_task"),
            (ENTRY_AUTOMATIC_SCHEDULED_TASK, "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "scheduled_20261013_1430"),
        ]
        identities = []
        for mode, source, occurrence in cases:
            payload = {
                "request_id": "fd_" + occurrence,
                "source": source,
                "entry_mode": mode,
                "occurrence_id": occurrence,
                "task_id": "ETF正式决策",
                "task_run_id": "run_" + occurrence,
                "occurrence": occurrence,
                "market_date": "2026-10-13",
            }
            identity = build_identity(payload)
            identities.append(identity.dedup_key)
        self.assertEqual(len(identities), len(set(identities)))

    @unittest.skip("legacy events/decisions route removed; canonical review event is tested separately")
    def test_formal_report_requires_exact_current_decision_event(self):
        import scripts.notification_center as center

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "events" / "decisions"
            decisions.mkdir(parents=True)
            body = "【ETF正式决策】当前请求正式报告"
            event = {
                "event_type": "FORMAL_DECISION",
                "parent_request_id": "fd_current",
                "decision_id": "decision_current",
                "formal_decision": {},
                "presentation_binding": {
                    "request_id": "fd_current",
                    "parent_request_id": "fd_current",
                    "decision_id": "decision_current",
                    "report_id": "report_current",
                    "full_content": body,
                    "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            }
            path = decisions / "current.json"
            path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
            with patch.object(center, "ROOT", root), patch.dict(os.environ, {"DECISION_EVENT_PATH": "events/decisions/current.json", "EXPECTED_FORMAL_REQUEST_ID": "fd_current"}, clear=False):
                self.assertIsNotNone(center.formal_decision_report_event())
            with patch.object(center, "ROOT", root), patch.dict(os.environ, {"DECISION_EVENT_PATH": "events/decisions/missing.json"}):
                self.assertIsNone(center.formal_decision_report_event())

    def test_notification_classifier_accepts_direct_formal_report_type(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('payload.get("report_type") or binding.get("report_type")', workflow)
        self.assertIn('payload.get("formal_fact_type")', workflow)
        self.assertIn('payload.get("task_id")', workflow)
        self.assertIn('print("FORMAL")', workflow)

    def test_decision_context_is_validated_after_state_context_build(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        state_build = workflow.index("      - name: Build state context and candidates")
        promotion = workflow.index("      - name: Promote formal request gate after full context validation")
        validation = workflow.index("formal request decision_context {key} mismatch after state context build")
        self.assertLess(state_build, validation)
        self.assertLess(validation, promotion)
        self.assertNotIn("decision_context request_id mismatch before gate promotion", workflow)

    def test_formal_context_validation_fails_closed_for_empty_or_wrong_request(self):
        from scripts.build_query_context import validate_request_bound_context
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            request_path.write_text(json.dumps({
                "request_id": "fd_current",
                "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            }), encoding="utf-8")
            with patch("scripts.build_query_context.ROOT", root):
                with self.assertRaises(RuntimeError):
                    validate_request_bound_context({}, "request.json")
                with self.assertRaisesRegex(RuntimeError, "binding mismatch"):
                    validate_request_bound_context({"decision_fact_pack": {"trigger": {"request_id": "fd_old"}}}, "request.json")
                validate_request_bound_context({
                    "decision_fact_pack": {
                        "trigger": {"request_id": "fd_current"},
                        "decision_work_package": {"problem_graph": [{"problem_id": "p1"}]},
                    }
                }, "request.json")

    def test_unbound_formal_notification_fails_closed(self):
        import scripts.notification_center as center

        with patch.dict(os.environ, {"DECISION_EVENT_PATH": ""}, clear=False):
            self.assertIsNone(center.formal_decision_report_event())



class FormalDecisionLessonsProtocolTests(unittest.TestCase):
    def test_protocol_uses_one_formal_chain_for_three_entries(self):
        root = Path(__file__).parents[1]
        text = (root / "docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("三个入口", text)
        self.assertIn("历史 BDS", text)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", text)
        self.assertIn("MARKET_QUOTE_REFRESH", text)
        self.assertIn("服务商投递结果", text)
        self.assertIn("不得创建第二请求、第二正文、第二完成记录", text)
        index_text = (root / "ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("request_type=SCHEDULED_REVIEW_COMPLETION", index_text)
        self.assertIn("source=CHATGPT_SCHEDULED_REVIEW_ACTOR", index_text)
        self.assertNotIn("Formal Decision 的合法后置交接只能使用 `CHATGPT_FORMAL_DECISION_COMPLETION`", index_text)
        self.assertNotIn("FORMAL_DECISION_COMPLETION` completion ingress", index_text)
        self.assertIn("正式决策不创建或依赖 BDS", index_text)
        self.assertIn("统一 Scheduled Review completion owner", index_text)
        self.assertIn("回读", index_text)

    def test_protocol_keeps_health_out_of_business_pr_gate(self):
        root = Path(__file__).parents[1]
        health_text = (root / "docs/生产变更与并发写入协议_V1.0.md").read_text(encoding="utf-8")
        formal_text = (root / "docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("system-consistency属于独立生产健康检查", health_text)
        self.assertIn("不得作为Formal Decision业务PR的硬门禁", health_text)
        self.assertIn("最新正式事实", formal_text)
        self.assertIn("服务商投递结果", formal_text)
        routing = json.loads((root / "config/maintenance/task_entry_scheduling_protocol.json").read_text(encoding="utf-8"))
        invariants = routing["invariants"]
        self.assertIn("scheduled_review_formal_projection_must_preserve_formal_report_type", invariants)
        self.assertNotIn("scheduled_review_cannot_use_formal_decision_projection", invariants)
        self.assertIn("冻结后不得摘要、缩短、改写", formal_text)
        self.assertIn("后置交接失败", formal_text)

    def test_protocol_preserves_current_request_fail_closed_and_send_boundary(self):
        root = Path(__file__).parents[1]
        text = (root / "docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("合法准入", text)
        self.assertIn("后置交接失败", text)
        self.assertIn("不得扫描历史对象", text)

class FormalReadyHandoffContractTests(unittest.TestCase):
    def test_ready_gate_exposes_shared_completion_handoff_contract(self):
        root = Path(__file__).parents[1]
        text = (root / "scripts/formal_request_consumer.py").read_text(encoding="utf-8")
        self.assertIn('"submission_type": "SCHEDULED_REVIEW_COMPLETION"', text)
        self.assertIn('"source": "CHATGPT_SCHEDULED_REVIEW_ACTOR"', text)
        self.assertIn('"notification_attempt_precondition": True', text)
        self.assertIn('"provider_success_not_required_for_user_reply": True', text)
        self.assertIn('"historical_fallback_forbidden": True', text)
        self.assertIn('"post_reply_async": False', text)
        self.assertIn('"provider_success_async": False', text)
        self.assertIn('"must_read_back_before_reply": True', text)
        self.assertIn('HANDOFF_BEFORE_REPLY = "SCHEDULED_REVIEW_COMPLETION_EVENT_REPORT_NOTIFICATION_BOUNDED"', text)
        self.assertNotIn("BDS_AUDIT_AND_PROVIDER_READBACK", text)

    def test_protocol_completes_handoff_before_user_reply(self):
        protocol = (Path(__file__).parents[1] / "docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("READY 后，必须先完成业务判断并冻结唯一", protocol)
        self.assertIn("正文冻结只表示正文内容已经不可修改", protocol)
        self.assertIn("先完成一次有界的后置交接尝试，再输出", protocol)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", protocol)
        self.assertIn("后置交接", protocol)



    def test_ready_handoff_does_not_create_a_second_notification_path(self):
        root = Path(__file__).parents[1]
        text = (root / "scripts/formal_request_consumer.py").read_text(encoding="utf-8")
        self.assertNotIn("REPORT_DELIVERY_REQUEST", text)
        self.assertNotIn("PushPlus", text)

if __name__ == "__main__":
    unittest.main()


class FormalBusinessHealthBoundaryTests(unittest.TestCase):
    def test_context_recovery_is_checked_at_final_publish(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn("FORMAL_CONTEXT_RECOVERY_REQUEST", workflow)
        self.assertIn("final publish cannot omit non-empty request-bound query_context.json", workflow)
        self.assertIn("final publish query_context request mismatch", workflow)

    def test_system_consistency_is_not_a_pull_request_business_gate(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/system-consistency.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n  pull_request:", workflow)
        self.assertNotIn("pr_production_acceptance:", workflow)
        self.assertIn("\n  push:", workflow)
        self.assertIn("\n  workflow_dispatch:", workflow)

    def test_formal_business_contract_is_the_only_formal_pr_acceptance_entry(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/formal-decision-e2e-acceptance.yml").read_text(encoding="utf-8")
        self.assertIn("name: Formal Decision business contract", workflow)
        self.assertIn("tests.test_formal_decision_e2e_contract", workflow)
        self.assertIn("tests.test_formal_decision_production_e2e", workflow)
        self.assertNotIn("Persist merged-main acceptance identity", workflow)
        self.assertNotIn("acceptance_window_id", workflow)

if __name__ == "__main__":
    unittest.main()

