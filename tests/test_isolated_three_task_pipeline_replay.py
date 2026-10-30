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
            # This deterministic fixture intentionally satisfies the current
            # user-visible Formal Decision contract. Keep the production
            # validator strict; do not replace this with headings-only prose.
            self.state.final_content = """【ETF正式决策】2026-09-23 20:30 正式判断
【判断时点与事实边界】
本测试使用隔离模拟事实，不代表真实市场或账户结论。
【当前正式判断】
模拟判断：风险许可仅允许审慎管理；Trial不授予，Confirm不授予，新增部署0元。
【风险与市场状态】
风险许可为谨慎观察，理由是模拟成长方向承压且缺少连续承接证据；本段仅验证正文合同。
【三层监测】
海外、区域市场与本地市场均使用隔离模拟描述，不形成真实行情判断。
【全市场发现逐只评估】
1）模拟候选甲：当前身份为Discovery，尚未升级为观察ETF或机会ETF。
【观察ETF逐只评估】
1）模拟观察乙：保留观察身份，等待连续强度与回撤证据。
【机会ETF逐只评估】
本次正式机会对象为0只，未形成机会对象。对任何尚未取得资格的模拟对象，Trial方案须达到5,000元标准并证明相对0元的事前收益风险优势；当前未达到资格，决定性缺口是缺少连续趋势和承接证据。直接Confirm尚未成立，未证明独立Confirm标准及其相对Trial、0元的边际优势，因此选择0元。下一复核节点为下一次正式决策。
【全市场机会与资本竞争】
最近可执行候选为模拟候选甲，身份为Discovery；其仅有初步观察依据，决定性证据缺口是资格、持续性和成交承接尚未确认。现金保留、持仓持有、追加、部分减持、全部退出和新机会均纳入相较比较。当前现金保留是下一单位资本最有效用途，因为机会尚未取得资格，现有持仓也未证明追加优于现金；资本效率与机会成本均支持暂不迁移。持仓继续占资、追加或减持释放，必须分别与现金和机会比较；现有证据不足以证明其他用途胜出。若后续资格、风险收益和承接证据改善，下一复核节点重新比较。
【现金管理】
模拟现金保持可用，不构成永久不投资的结论。
【全部实际持仓】
1）模拟持仓甲：当前比较继续持有、追加、部分减持和全部退出。相较现金与机会替代用途，现有证据不足以支持继续持有胜出；若趋势和风险证据恶化，建议部分减持而非自动交易。若当前没有该持仓、按零持仓重新配置资本，需重新比较现金与机会。迁移成本、失败风险、上行尾部和账户功能均纳入评估；本测试建议部分减持仅为模拟字段，不构成交易指令。
【最终管理动作】
模拟动作仅用于测试链路，不产生真实交易授权。
【关键风险与改变判断的条件】
模拟风险条件改变时重新评估，不自动下单。
【信息不足在哪里】
本测试不提供真实市场、账户、成交或证券事实。
【执行边界】
本测试不提交订单、不调仓、不改变权限。
【置信度】
仅对测试夹具覆盖的正文合同有确定性。
【最可能出错的地方】
若未来正文合同变化而测试夹具未同步更新，隔离回放应继续失败并提示维护测试夹具。"""

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
