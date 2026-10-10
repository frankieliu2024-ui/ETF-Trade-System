from __future__ import annotations

import unittest
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import scheduled_review_completion as completion


def full_review():
    return {
        "market_date": "2026-09-23",
        "review_scope": "FULL_DAY",
        "review_version": "V2.2.31_CLOSE_REVIEW",
        "reviewed_at_beijing": "2026-09-23T20:30:00+08:00",
        "risk_permission": "允许Trial，但完整资本竞争后新增0元",
        "opportunity_status": "无新增正式机会",
        "main_candidate": "半导体设备ETF（561980）",
        "lifecycle": {"半导体设备ETF（561980）": "持有管理"},
        "holding_actions": {"半导体设备ETF（561980）": "持有28,900份；不释放资本"},
        "amount_yuan": 0,
        "action": "新增0元；卖出0份；释放旧仓0元。",
        "zero_amount_decisive_reason": "完整资本竞争后现金更优",
        "data_time": {
            "a_share_effective_close_beijing": "2026-09-23T15:00:00+08:00",
            "close_snapshot": "data/market/snapshots/2026-09-23_152738.json",
            "close_data_status": "VERIFIED_SESSION_CLOSE",
        },
        "account_close": {"cash": 18011.14, "deployable_cash": 18011.14},
        "capital_efficiency": "维持现有组合与现金",
        "capital_efficiency_reason": "没有迁移状态优于现状",
        "next_unit_capital_use": "保留现金",
        "max_risk": "科技成长共同风险",
        "error_reason": "趋势持续性判断可能错误",
        "information_gap": "当前无影响决策的关键数据缺口。",
        "most_fragile_hypothesis": "当前结构可以延续",
        "overseas_overestimate": "本次未依赖足以改变动作的海外信息",
        "overseas_a_share_divergence": "存在非同步阶段差异",
        "case_mode": "CONTINUATION_NO_NEW_CASE",
        "review_boundary": "只持久化本节点已形成的正式复盘结论",
    }


class ScheduledReviewCompletionTests(unittest.TestCase):
    def test_full_day_review_builds_existing_state_sync_envelope(self):
        payload = completion.build_completion_request(
            full_review(),
            "20260923_2030_scheduled_trade_review__formal_completion",
            "2026-09-23T20:30:00+08:00",
        )
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_POST_CLOSE_REVIEW")
        self.assertEqual(payload["interaction_scenario"], "POST_CLOSE_REVIEW")
        self.assertEqual(payload["source"], "CHATGPT_SCHEDULED_REVIEW_ACTOR")
        self.assertEqual(payload["formal_review"], full_review())

    def test_20260918_style_abbreviated_review_fails_before_persistence(self):
        abbreviated = {
            "date": "2026-09-18",
            "reviewed_at_beijing": "2026-09-18T20:30:00+08:00",
            "summary": "六项实际持仓继续持有",
            "cash": 18009.45,
            "actions": {"561980": "HOLD"},
            "new_capital_yuan": 0,
        }
        with self.assertRaisesRegex(ValueError, "review_scope=FULL_DAY"):
            completion.build_completion_request(
                abbreviated, "20260918_2030_scheduled_trade_review", "2026-09-18T20:30:00+08:00"
            )

    def test_missing_formal_review_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "completed formal_review payload is required"):
            completion.build_completion_request(
                {}, "20260923_2030_scheduled_trade_review", "2026-09-23T20:30:00+08:00"
            )

    def test_morning_stage_cannot_enter_full_day_completion(self):
        review = full_review()
        review["review_scope"] = "MORNING_SESSION_STAGE_ONLY"
        with self.assertRaisesRegex(ValueError, "review_scope=FULL_DAY"):
            completion.build_completion_request(
                review, "20260923_1230_scheduled_trade_review", "2026-09-23T12:30:00+08:00"
            )

    def test_market_date_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "market_date must match"):
            completion.build_completion_request(
                full_review(),
                "20260923_2030_scheduled_trade_review",
                "2026-09-23T20:30:00+08:00",
                market_date="2026-09-22",
            )

    def test_missing_close_snapshot_fails_closed(self):
        review = full_review()
        review["data_time"] = {"close_data_status": "VERIFIED_SESSION_CLOSE"}
        with self.assertRaisesRegex(ValueError, "close_snapshot is required"):
            completion.build_completion_request(
                review, "20260923_2030_scheduled_trade_review", "2026-09-23T20:30:00+08:00"
            )

    def test_trade_completion_binds_frozen_final_content(self):
        payload = completion.build_completion_request(
            full_review(), "trade-run", "2026-09-23T20:30:00+08:00",
            final_content="FROZEN TRADE REPORT", task_id="ETF交易复盘", task_run_id="trade-run",
        )
        self.assertEqual(payload["presentation_binding"]["report_type"], "ETF_TRADE_REVIEW")
        self.assertEqual(payload["presentation_binding"]["full_content"], "FROZEN TRADE REPORT")

    def test_formal_decision_completion_projects_shared_report(self):
        payload = completion.build_completion_request(
            full_review(), "formal-run", "2026-09-23T20:30:00+08:00",
            final_content="FROZEN FORMAL REPORT", task_id="ETF正式决策", task_run_id="formal-run",
        )
        payload["presentation_binding"]["report_type"] = "ETF_FORMAL_DECISION"
        report = completion.build_report_delivery_from_completion(payload)
        self.assertEqual(report["report_type"], "ETF_FORMAL_DECISION")
        self.assertEqual(report["title"], "【ETF正式决策】")
        self.assertEqual(report["full_content"], "FROZEN FORMAL REPORT")
        self.assertEqual(report["idempotency_key"], "ETF_FORMAL_DECISION:formal-run")
        self.assertEqual(
            report["content_hash"],
            hashlib.sha256(b"FROZEN FORMAL REPORT").hexdigest(),
        )
        self.assertTrue(report["no_trade_authority"])

    def test_system_review_completion_is_business_completion_not_report_handoff(self):
        payload = completion.build_system_review_completion_request(
            {"status": "PASS"}, "system-request", "2026-09-26T19:30:00+08:00",
            "ETF系统复核", "system-run", "FROZEN SYSTEM REPORT",
        )
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_SYSTEM_REVIEW")
        self.assertEqual(payload["presentation_binding"]["report_type"], "ETF_SYSTEM_REVIEW")
        self.assertNotIn("report_delivery", payload)
        self.assertNotIn("report_handoff", payload)
        from scripts.runtime_session_gate import classify_live_snapshot_request
        self.assertEqual(classify_live_snapshot_request(payload), "STATE_SYNC_ONLY")


    def test_system_completion_publishes_canonical_event_consumed_by_notification_center(self):
        from scripts import notification_center
        from scripts import process_state_sync_request as state_sync
        payload = completion.build_system_review_completion_request(
            {"status": "PASS"}, "system-request", "2026-09-26T19:30:00+08:00",
            "ETF系统复核", "system-run", "FROZEN SYSTEM REPORT",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(state_sync, "ROOT", root):
                recorded, replay = state_sync.record_scheduled_system_review(payload)
                self.assertTrue(recorded)
                self.assertFalse(replay)
                recorded, replay = state_sync.record_scheduled_system_review(payload)
                self.assertTrue(recorded)
                self.assertTrue(replay)
            with patch.object(notification_center, "REVIEW_EVENT_DIR", root / "events" / "reviews"):
                event = notification_center.report_delivery_event()
            self.assertEqual(event["report_type"], "ETF_SYSTEM_REVIEW")
            self.assertEqual(event["content"], "FROZEN SYSTEM REPORT")
            self.assertEqual(event["idempotency_key"], "ETF_SYSTEM_REVIEW:system-run")



    def test_both_scheduled_review_fact_types_share_notification_wake_contract(self):
        workflow = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        self.assertIn('{"FORMAL_SCHEDULED_REVIEW", "FORMAL_SCHEDULED_SYSTEM_REVIEW"}', workflow)
        self.assertIn("python scripts/run_guarded_notification.py center --mode event", workflow)
        self.assertNotIn("requests/report_delivery", workflow[workflow.index("Deliver canonical Scheduled Review completion through notification center"):])



    def test_system_review_projects_same_frozen_content_to_report_delivery(self):
        completion = build_system_review_completion_request(
            {"status": "PASS", "a_share_latest_formal_market_date": "2026-09-24"},
            "system-review-1", "2026-09-26T18:30:00+08:00",
            "ETF系统复核", "run-1", "FINAL-CONTENT"
        )
        report = build_report_delivery_from_completion(completion)
        self.assertEqual(report["report_type"], "ETF_SYSTEM_REVIEW")
        self.assertEqual(report["full_content"], completion["presentation_binding"]["full_content"])
        self.assertEqual(report["idempotency_key"], "ETF_SYSTEM_REVIEW:run-1")
        self.assertEqual(
            report["content_hash"],
            __import__("hashlib").sha256(b"FINAL-CONTENT").hexdigest(),
        )
        self.assertTrue(report["no_trade_authority"])



    def test_exact_review_event_binding_wins_over_later_directory_event(self):
        from scripts import notification_center
        with TemporaryDirectory() as directory:
            root = Path(directory)
            review_dir = root / "events" / "reviews"
            review_dir.mkdir(parents=True)
            def event(name, run_id, content):
                body = {"occurrence_id": run_id, "presentation_binding": {
                    "report_type": "ETF_TRADE_REVIEW", "task_id": "ETF交易复盘",
                    "task_run_id": run_id, "full_content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                }}
                (review_dir / name).write_text(json.dumps(body), encoding="utf-8")
            event("2026-09-26-a.json", "run-a", "FINAL A")
            event("2026-09-26-z.json", "run-b", "FINAL B")
            with patch.object(notification_center, "ROOT", root),                  patch.object(notification_center, "REVIEW_EVENT_DIR", review_dir),                  patch.dict(os.environ, {"REVIEW_EVENT_PATH": "events/reviews/2026-09-26-a.json"}, clear=False):
                projected = notification_center.report_delivery_event()
            self.assertEqual(projected["task_run_id"], "run-a")
            self.assertEqual(projected["content"], "FINAL A")
            self.assertEqual(projected["content_hash"], hashlib.sha256(b"FINAL A").hexdigest())

    def test_exact_review_event_malformed_binding_fails_closed(self):
        from scripts import notification_center
        with TemporaryDirectory() as directory:
            root = Path(directory)
            review_dir = root / "events" / "reviews"
            review_dir.mkdir(parents=True)
            (review_dir / "bad.json").write_text(json.dumps({
                "occurrence_id": "run-a",
                "presentation_binding": {"report_type": "ETF_TRADE_REVIEW",
                                          "task_id": "ETF交易复盘", "task_run_id": "run-a",
                                          "full_content": "FINAL A", "content_hash": "wrong"}
            }), encoding="utf-8")
            with patch.object(notification_center, "ROOT", root),                  patch.object(notification_center, "REVIEW_EVENT_DIR", review_dir),                  patch.dict(os.environ, {"REVIEW_EVENT_PATH": "events/reviews/bad.json"}, clear=False):
                self.assertIsNone(notification_center.report_delivery_event())

    def test_formal_decision_event_is_bound_from_market_snapshot_workflow_run(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("decision_paths", workflow)
        self.assertIn("decision_completion", workflow)
        self.assertIn("steps.workflow_review.outputs.decision_event_path", workflow)
        self.assertIn("events/decisions/*.json", workflow)

    def test_multiple_triggering_review_events_fail_closed_in_workflow(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn('expected exactly one review event', workflow)
        self.assertIn('review_event_path=', workflow)
        self.assertIn('REVIEW_EVENT_PATH:', workflow)



    def test_workflow_run_binds_downstream_producer_commit_before_exact_review_path(self):
        producer = (ROOT / ".github" / "workflows" / "market-snapshot.yml").read_text(encoding="utf-8")
        notifier = (ROOT / ".github" / "workflows" / "decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("[producer-run:${GITHUB_RUN_ID}]", producer)
        self.assertIn("PRODUCER_RUN_ID:", notifier)
        self.assertIn("git log origin/main --format='%H%x09%s'", notifier)
        self.assertIn('--grep="[producer-run:${PRODUCER_RUN_ID}]"', notifier)
        self.assertIn('git diff-tree --no-commit-id --name-only -r "$downstream_sha"', notifier)
        self.assertIn("REVIEW_EVENT_PATH:", notifier)
        self.assertNotIn('git show --format= --name-only "$PRODUCER_SHA"', notifier)


    def test_distinct_review_occurrences_with_same_task_run_id_are_not_absorbed(self):
        from scripts import notification_center
        with TemporaryDirectory() as directory:
            root = Path(directory)
            review_dir = root / "events" / "reviews"
            review_dir.mkdir(parents=True)
            def write_event(name, occurrence, content):
                body = {"occurrence_id": occurrence, "presentation_binding": {
                    "report_type": "ETF_SYSTEM_REVIEW", "task_id": "ETF系统复核",
                    "task_run_id": "stable-scheduled-task", "full_content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                }}
                (review_dir / name).write_text(json.dumps(body), encoding="utf-8")
            write_event("a.json", "occurrence-a", "FINAL A")
            write_event("b.json", "occurrence-b", "FINAL B")
            with patch.object(notification_center, "ROOT", root), patch.object(notification_center, "REVIEW_EVENT_DIR", review_dir), patch.dict(os.environ, {"REVIEW_EVENT_PATH": "events/reviews/a.json"}, clear=False):
                first = notification_center.report_delivery_event()
            with patch.object(notification_center, "ROOT", root), patch.object(notification_center, "REVIEW_EVENT_DIR", review_dir), patch.dict(os.environ, {"REVIEW_EVENT_PATH": "events/reviews/b.json"}, clear=False):
                second = notification_center.report_delivery_event()
            self.assertEqual(first["task_run_id"], second["task_run_id"])
            self.assertNotEqual(first["source_event_id"], second["source_event_id"])
            self.assertEqual(first["idempotency_key"], "ETF_SYSTEM_REVIEW:occurrence-a")
            self.assertEqual(second["idempotency_key"], "ETF_SYSTEM_REVIEW:occurrence-b")
            self.assertEqual(first["content"], "FINAL A")
            self.assertEqual(second["content"], "FINAL B")


    def test_system_review_rejects_false_trade_mapping_dependency(self):
        with self.assertRaisesRegex(ValueError, "canonically linked trades"):
            completion.build_system_review_completion_request(
                {"status": "PASS", "execution_reconciliation": "QUALITY_ENRICHMENT_PARTIAL"},
                "system-request", "2026-09-29T08:37:00+08:00",
                "ETF系统复核", "system-run",
                "【需要用户处理】请确认能源化工ETF（159981）5,700份买入对应Trial还是Confirm。",
            )

    def test_system_review_rejects_paraphrased_mapping_request_from_semantics(self):
        review = {
            "status": "PASS",
            "execution_reconciliation": "QUALITY_ENRICHMENT_PARTIAL",
            "user_attribution_requirement": "MAPPING_REQUIRED",
            "canonical_trade_attribution": {"trades": [
                {"code": "561980", "quantity": 15300, "decision_identity_status": "KNOWN",
                 "linked_decision_id": "20260928_104100_chatgpt_formal_decision_decision",
                 "execution_quality_status": "PARTIAL"},
                {"code": "561980", "quantity": 13600, "decision_identity_status": "KNOWN",
                 "linked_decision_id": "20260928_120100_chatgpt_formal_decision_decision_140700",
                 "execution_quality_status": "PARTIAL"},
            ]},
        }
        with self.assertRaisesRegex(ValueError, "canonically linked trades"):
            completion.build_system_review_completion_request(
                review, "system-request", "2026-09-29T19:31:00+08:00",
                "ETF系统复核", "system-run",
                "【需要用户处理】请把昨日两笔561980卖出分别和相应的正式判断对上。",
            )

    def test_system_review_rejects_user_trial_confirm_classification_when_link_known(self):
        review = {
            "status": "PASS",
            "user_attribution_requirement": "NONE",
            "canonical_trade_attribution": {"trades": [
                {"code": "159981", "quantity": 5700, "decision_identity_status": "KNOWN",
                 "linked_decision_id": "20260928_104100_chatgpt_formal_decision_decision",
                 "decision_lifecycle": "Confirm", "asks_user_to_classify_trial_confirm": True},
            ]},
        }
        with self.assertRaisesRegex(ValueError, "Trial/Confirm"):
            completion.build_system_review_completion_request(
                review, "system-request", "2026-09-29T19:31:00+08:00",
                "ETF系统复核", "system-run",
                "【需要用户处理】请说明这笔159981买入属于哪个仓位阶段。",
            )

    def test_system_review_allows_genuinely_missing_required_mapping(self):
        review = {
            "status": "PASS",
            "user_attribution_requirement": "MAPPING_REQUIRED",
            "canonical_trade_attribution": {"trades": [
                {"code": "TEST", "quantity": 100, "decision_identity_status": "AMBIGUOUS",
                 "decision_identity_required": True, "execution_quality_status": "PARTIAL"},
            ]},
        }
        payload = completion.build_system_review_completion_request(
            review, "system-request", "2026-09-29T19:31:00+08:00",
            "ETF系统复核", "system-run",
            "【需要用户处理】存在一笔无法由正式事实唯一归因的成交，需要补充确认。",
        )
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_SYSTEM_REVIEW")

    def test_system_review_rejects_confirmation_required_reconciliation_state(self):
        with self.assertRaisesRegex(ValueError, "execution-quality gaps"):
            completion.build_system_review_completion_request(
                {"status": "PASS", "execution_reconciliation": "CONFIRMATION_REQUIRED"},
                "system-request", "2026-09-29T08:37:00+08:00",
                "ETF系统复核", "system-run", "FROZEN SYSTEM REPORT",
            )

    def test_system_review_rejects_renewed_formal_decision_authority(self):
        with self.assertRaisesRegex(ValueError, "must not renew or extend"):
            completion.build_system_review_completion_request(
                {"status": "PASS", "execution_reconciliation": "QUALITY_ENRICHMENT_PARTIAL"},
                "system-request", "2026-09-29T08:37:00+08:00",
                "ETF系统复核", "system-run",
                "【下一阶段】继续维持禁止新增，直至新的正式判断改变风险许可。",
            )

    def test_system_review_rejects_trade_need_as_only_formal_decision_trigger(self):
        with self.assertRaisesRegex(ValueError, "sole Formal Decision trigger"):
            completion.build_system_review_completion_request(
                {"status": "PASS"},
                "system-request", "2026-09-30T08:36:00+08:00",
                "ETF系统复核", "system-run",
                "【下一阶段】只有出现新的交易需要时才进入正式判断。",
            )

    def test_system_review_allows_material_reassessment_conditions_without_trade_authority(self):
        payload = completion.build_system_review_completion_request(
            {"status": "PASS"},
            "system-request", "2026-09-30T08:36:00+08:00",
            "ETF系统复核", "system-run",
            "【下一阶段】若市场、风险、机会、持仓资本效率或账户事实出现重要变化，"
            "或用户主动要求重新判断，再进入新的正式判断；本次复核不产生交易权限。",
        )
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_SYSTEM_REVIEW")

    def test_system_review_requires_cutoff_when_source_as_of_facts_are_bound(self):
        review = {
            "status": "PASS",
            "source_facts_as_of": {"e2e": "2026-09-30T08:35:00+08:00"},
        }
        with self.assertRaisesRegex(ValueError, "requires fact_cutoff_at_beijing"):
            completion.build_system_review_completion_request(
                review, "system-request", "2026-09-30T08:36:00+08:00",
                "ETF系统复核", "system-run", "FROZEN SYSTEM REPORT",
            )

    def test_system_review_rejects_source_fact_newer_than_frozen_cutoff(self):
        review = {
            "status": "PASS",
            "fact_cutoff_at_beijing": "2026-09-30T08:36:00+08:00",
            "source_facts_as_of": {
                "system_consistency": "2026-09-29T14:31:00+08:00",
                "e2e": "2026-09-30T08:37:38+08:00",
            },
        }
        with self.assertRaisesRegex(ValueError, "newer than frozen fact cutoff"):
            completion.build_system_review_completion_request(
                review, "system-request", "2026-09-30T08:36:00+08:00",
                "ETF系统复核", "system-run", "FROZEN SYSTEM REPORT",
            )

    def test_system_review_accepts_coherent_source_fact_cutoff(self):
        review = {
            "status": "PASS",
            "fact_cutoff_at_beijing": "2026-09-30T08:36:00+08:00",
            "source_facts_as_of": {
                "system_consistency": "2026-09-29T14:31:00+08:00",
                "e2e": "2026-09-29T22:49:37+08:00",
                "account": "2026-09-29T18:44:00+08:00",
            },
        }
        payload = completion.build_system_review_completion_request(
            review, "system-request", "2026-09-30T08:36:00+08:00",
            "ETF系统复核", "system-run",
            "【数据边界】本报告基于08:36冻结前已完成的正式事实；冻结后的新事实由后续节点吸收。",
        )
        self.assertEqual(payload["system_review"]["fact_cutoff_at_beijing"], "2026-09-30T08:36:00+08:00")

    def test_system_review_accepts_historical_decision_attribution_with_quality_gap(self):
        payload = completion.build_system_review_completion_request(
            {"status": "PASS", "execution_reconciliation": "QUALITY_ENRICHMENT_PARTIAL"},
            "system-request", "2026-09-29T08:37:00+08:00",
            "ETF系统复核", "system-run",
            "【成交归因】159981买入5,700份已绑定昨日10:41正式判断的Confirm；"
            "561980两笔卖出已有正式决策身份。执行质量字段仍有缺口，不影响账户事实。",
        )
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["formal_fact_type"], "FORMAL_SCHEDULED_SYSTEM_REVIEW")


if __name__ == "__main__":
    unittest.main()
