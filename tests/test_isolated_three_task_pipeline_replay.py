"""Isolated full-pipeline replay for the three ETF task notification paths.

This module is test-only. It models the production stage order without creating a
second production workflow or writing real GitHub/PushPlus state.
"""
from __future__ import annotations

import hashlib
import unittest
from dataclasses import dataclass
from typing import Callable

from scripts import notification_center
from scripts import scheduled_review_completion as completion


@dataclass
class ReplayState:
    task_id: str
    request_id: str
    task_run_id: str
    occurrence_id: str
    market_date: str = "2026-09-23"
    market_snapshot: dict | None = None
    query_context: dict | None = None
    decision_context: dict | None = None
    formal_gate: dict | None = None
    final_content: str = ""
    completion: dict | None = None
    event: dict | None = None
    report: dict | None = None
    notification_id: str | None = None


class IsolatedThreeTaskPipeline:
    def __init__(self, state: ReplayState):
        self.state = state
        self._pushplus_fail_once = False
        self._pushplus_attempts = 0

    def establish_request(self):
        self.state.market_snapshot = {
            "request_id": self.state.request_id,
            "task_run_id": self.state.task_run_id,
            "market_date": self.state.market_date,
            "status": "READY",
        }

    def build_facts(self):
        if not self.state.market_snapshot:
            raise RuntimeError("market_snapshot_required")
        self.state.query_context = {
            "request_id": self.state.request_id,
            "task_run_id": self.state.task_run_id,
            "status": "READY",
        }
        self.state.decision_context = {
            "request_id": self.state.request_id,
            "task_run_id": self.state.task_run_id,
            "status": "READY",
        }

    def establish_gate(self):
        if self.state.task_id == "ETF正式决策":
            if not self.state.decision_context:
                raise RuntimeError("decision_context_required")
            self.state.formal_gate = {
                "request_id": self.state.request_id,
                "reply_freezable": True,
                "status": "READY",
            }
        else:
            self.state.formal_gate = {
                "request_id": self.state.request_id,
                "status": "TERMINAL_REVIEW_READY",
            }

    def freeze_content(self):
        if not self.state.formal_gate:
            raise RuntimeError("gate_required")
        if self.state.formal_gate["request_id"] != self.state.request_id:
            raise RuntimeError("gate_identity_mismatch")
        title = {
            "ETF系统复核": "【ETF系统复核】",
            "ETF交易复盘": "【ETF交易复盘】",
            "ETF正式决策": "【ETF正式决策】",
        }[self.state.task_id]
        if self.state.task_id == "ETF正式决策":
            sections = [
                "【判断时点与事实边界】", "【当前正式判断】", "【风险与市场状态】",
                "【三层监测】", "【全市场发现逐只评估】", "【观察ETF逐只评估】",
                "【机会ETF逐只评估】", "【全市场机会与资本竞争】", "【现金管理】",
                "【全部实际持仓】", "【最终管理动作】", "【关键风险与改变判断的条件】",
                "【信息不足在哪里】", "【执行边界】", "【置信度】", "【最可能出错的地方】",
            ]
            self.state.final_content = "【ETF正式决策】2026-09-23 20:30 正式判断\n" + "\n".join(sections)
        else:
            self.state.final_content = f"{title}2026-09-23 20:30 模拟正文"

    def write_completion(self):
        if not self.state.final_content:
            raise RuntimeError("final_content_required")
        common = {
            "request_id": self.state.request_id,
            "task_run_id": self.state.task_run_id,
            "occurrence_id": self.state.occurrence_id,
            "market_date": self.state.market_date,
        }
        if self.state.task_id == "ETF系统复核":
            self.state.completion = completion.build_system_review_completion_request(
                {"status": "PASS"},
                self.state.request_id,
                "2026-09-23T20:30:00+08:00",
                self.state.task_id,
                self.state.task_run_id,
                self.state.final_content,
                occurrence_id=self.state.occurrence_id,
            )
        elif self.state.task_id == "ETF交易复盘":
            review = {
                "market_date": self.state.market_date,
                "review_scope": "FULL_DAY",
                "review_version": "V2.2.31_CLOSE_REVIEW",
                "reviewed_at_beijing": "2026-09-23T20:30:00+08:00",
                "risk_permission": "允许Trial",
                "opportunity_status": "无新增正式机会",
                "main_candidate": "现金",
                "lifecycle": {"现金": "持有管理"},
                "holding_actions": {"现金": "保留"},
                "amount_yuan": 0,
                "action": "不新增",
                "data_time": {"close_snapshot": "simulated://close"},
                "account_close": {"cash": 100000},
                "capital_efficiency": "保留现金",
                "capital_efficiency_reason": "模拟",
                "next_unit_capital_use": "等待",
                "max_risk": "模拟",
                "error_reason": "模拟",
                "information_gap": "无",
                "most_fragile_hypothesis": "模拟",
                "overseas_overestimate": "未使用",
                "overseas_a_share_divergence": "模拟",
                "case_mode": "CONTINUATION_NO_NEW_CASE",
                "review_boundary": "模拟验收",
            }
            self.state.completion = completion.build_trade_review_completion_request(
                review, self.state.request_id,
                "2026-09-23T20:30:00+08:00", self.state.market_date,
                "", None, self.state.final_content, self.state.task_id,
                self.state.task_run_id, self.state.occurrence_id,
                "AUTOMATIC_SCHEDULED_TASK",
            )
        else:
            review = {
                "market_date": self.state.market_date,
                "review_scope": "FULL_DAY",
                "review_version": "V2.2.31_CLOSE_REVIEW",
                "reviewed_at_beijing": "2026-09-23T20:30:00+08:00",
                "risk_permission": "允许Trial",
                "opportunity_status": "无新增正式机会",
                "main_candidate": "现金",
                "lifecycle": {"现金": "持有管理"},
                "holding_actions": {"现金": "持有"},
                "amount_yuan": 0,
                "action": "不新增",
                "data_time": {"close_snapshot": "simulated://close"},
                "account_close": {"cash": 100000},
                "capital_efficiency": "保留现金",
                "capital_efficiency_reason": "模拟",
                "next_unit_capital_use": "等待",
                "max_risk": "模拟",
                "error_reason": "模拟",
                "information_gap": "无",
                "most_fragile_hypothesis": "模拟",
                "overseas_overestimate": "未使用",
                "overseas_a_share_divergence": "模拟",
                "case_mode": "CONTINUATION_NO_NEW_CASE",
                "review_boundary": "模拟验收",
                "source": "SIMULATED_E2E",
                "fact_binding": {
                    "request_id": self.state.request_id,
                    "market_snapshot_status": "READY",
                    "fact_chain_status": "READY",
                    "query_context_status": "READY",
                    "decision_context_status": "READY",
                    "objects_status": "READY",
                    "gate_status": "READY",
                },
            }
            self.state.completion = completion.build_formal_review_completion_request(
                review, self.state.request_id,
                "2026-09-23T20:30:00+08:00", self.state.market_date,
                "", None, self.state.final_content, self.state.task_id,
                self.state.task_run_id, self.state.occurrence_id,
                "AUTOMATIC_SCHEDULED_TASK",
            )
        binding = self.state.completion["presentation_binding"]
        self._assert_binding(binding)

    def _assert_binding(self, binding):
        for key in ("request_id", "task_run_id", "occurrence_id"):
            if binding[key] != getattr(self.state, key):
                raise RuntimeError(f"binding_mismatch:{key}")
        expected = hashlib.sha256(self.state.final_content.encode()).hexdigest()
        if binding["content_hash"] != expected or binding["full_content"] != self.state.final_content:
            raise RuntimeError("content_binding_mismatch")

    def publish_event(self):
        if not self.state.completion:
            raise RuntimeError("completion_required")
        binding = self.state.completion["presentation_binding"]
        self.state.event = dict(binding)

    def project_report(self):
        if not self.state.event:
            raise RuntimeError("event_required")
        self.state.report = notification_center.build_report_from_handoff({
            "schema_version": "1.0",
            "report_type": self.state.event["report_type"],
            "task_id": self.state.task_id,
            "task_run_id": self.state.task_run_id,
            "generated_at": "2026-09-23T20:30:00+08:00",
            "effective_market_date": self.state.market_date,
            "title": notification_center.canonical_report_title(self.state.event["report_type"]),
            "summary": "isolated replay",
            "full_content": self.state.event["full_content"],
        })

    def notify(self):
        if not self.state.report:
            raise RuntimeError("report_required")
        valid, reason = notification_center.validate_report_delivery_request(self.state.report)
        if not valid:
            raise RuntimeError(reason)
        self._pushplus_attempts += 1
        if self._pushplus_fail_once and self._pushplus_attempts == 1:
            raise TimeoutError("simulated_pushplus_timeout")
        self.state.notification_id = f"simulated:{self.state.report['idempotency_key']}"


class IsolatedThreeTaskPipelineReplayTests(unittest.TestCase):
    TASKS = ("ETF系统复核", "ETF交易复盘", "ETF正式决策")

    def _run(self, task_id):
        state = ReplayState(task_id, f"request-{task_id}", f"run-{task_id}", f"occ-{task_id}")
        pipeline = IsolatedThreeTaskPipeline(state)
        for stage in (pipeline.establish_request, pipeline.build_facts, pipeline.establish_gate,
                      pipeline.freeze_content, pipeline.write_completion, pipeline.publish_event,
                      pipeline.project_report, pipeline.notify):
            stage()
        self.assertIsNotNone(state.notification_id)
        self.assertEqual(state.event["request_id"], state.request_id)
        self.assertEqual(state.report["full_content"], state.final_content)

    def test_all_three_full_pipeline(self):
        for task_id in self.TASKS:
            with self.subTest(task_id=task_id):
                self._run(task_id)

    def test_formal_gate_failure_stops_before_content_and_completion(self):
        state = ReplayState("ETF正式决策", "request-fail", "run-fail", "occ-fail")
        pipeline = IsolatedThreeTaskPipeline(state)
        pipeline.establish_request()
        pipeline.build_facts()
        pipeline.state.decision_context = None
        with self.assertRaises(RuntimeError):
            pipeline.establish_gate()
        self.assertEqual(state.final_content, "")
        self.assertIsNone(state.completion)

    def test_pushplus_failure_recovers_without_new_completion_or_event(self):
        state = ReplayState("ETF交易复盘", "request-retry", "run-retry", "occ-retry")
        pipeline = IsolatedThreeTaskPipeline(state)
        for stage in (pipeline.establish_request, pipeline.build_facts, pipeline.establish_gate,
                      pipeline.freeze_content, pipeline.write_completion, pipeline.publish_event,
                      pipeline.project_report):
            stage()
        original_completion = state.completion
        original_event = state.event
        pipeline._pushplus_fail_once = True
        with self.assertRaises(TimeoutError):
            pipeline.notify()
        pipeline.notify()
        self.assertIs(state.completion, original_completion)
        self.assertIs(state.event, original_event)
        self.assertIsNotNone(state.notification_id)


if __name__ == "__main__":
    unittest.main()
