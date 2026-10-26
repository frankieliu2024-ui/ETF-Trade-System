import hashlib
import unittest

from scripts import notification_center
from scripts import scheduled_review_completion as completion


def full_review():
    return {
        "market_date": "2026-09-23",
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
        "source": "SIMULATED_E2E",
        "fact_binding": {
            "request_id": "sim-formal-request",
            "market_snapshot_status": "READY",
            "fact_chain_status": "READY",
            "query_context_status": "READY",
            "decision_context_status": "READY",
            "objects_status": "READY",
            "gate_status": "READY",
        },
    }


class SimulatedCanonicalChain:
    def __init__(self, completion_payload):
        self.completion = completion_payload
        self.event = None
        self.report = None
        self.notification_id = None

    def publish_event(self):
        if not self.completion:
            raise RuntimeError("completion_required")
        binding = self.completion["presentation_binding"]
        self.event = {
            "request_id": binding["request_id"],
            "task_run_id": binding["task_run_id"],
            "occurrence_id": binding["occurrence_id"],
            "content_hash": binding["content_hash"],
            "report_type": binding["report_type"],
            "full_content": binding["full_content"],
        }

    def publish_report(self):
        if self.event is None:
            raise RuntimeError("event_required")
        if self.event["request_id"] != self.completion["request_id"]:
            raise RuntimeError("event_request_identity_mismatch")
        self.report = notification_center.build_report_from_handoff({
            "schema_version": "1.0",
            "report_type": self.event["report_type"],
            "task_id": self.completion.get("task_id", self.completion["presentation_binding"]["task_id"]),
            "task_run_id": self.event["task_run_id"],
            "generated_at": self.completion["requested_at_beijing"],
            "effective_market_date": self.completion["market_date"],
            "title": notification_center.canonical_report_title(self.event["report_type"]),
            "summary": "simulated end-to-end",
            "full_content": self.event["full_content"],
        })

    def push(self):
        valid, reason = notification_center.validate_report_delivery_request(self.report)
        if not valid:
            raise RuntimeError(reason)
        self.notification_id = f"simulated:{self.report['idempotency_key']}"


class FullThreeTaskE2EReplayTests(unittest.TestCase):
    def _system(self):
        request_id = "sim-system-request"
        review = {"status": "PASS", "request_id": request_id, "market_date": "2026-09-23"}
        return completion.build_system_review_completion_request(
            review, request_id, "2026-09-23T19:30:00+08:00",
            "ETF系统复核", "sim-system-run", "FROZEN SYSTEM CONTENT",
        )

    def _trade(self):
        review = full_review()
        review.pop("fact_binding")
        return completion.build_trade_review_completion_request(
            review, "sim-trade-request", "2026-09-23T20:30:00+08:00",
            "2026-09-23", "", None, "FROZEN TRADE CONTENT",
            "ETF交易复盘", "sim-trade-run", "sim-trade-occ",
            "AUTOMATIC_SCHEDULED_TASK",
        )

    def _formal(self):
        request_id = "sim-formal-request"
        content = """【ETF正式决策】2026-09-23 20:30 正式判断\n\n【判断时点与事实边界】\n模拟事实边界。\n\n【当前正式判断】\n模拟判断。\n\n【风险与市场状态】\n模拟风险与市场状态。\n\n【三层监测】\n模拟三层监测。\n\n【全市场发现逐只评估】\n模拟发现评估。\n\n【观察ETF逐只评估】\n模拟观察评估。\n\n【机会ETF逐只评估】\n模拟机会评估。\n\n【全市场机会与资本竞争】\n模拟资本竞争。\n\n【现金管理】\n模拟现金管理。\n\n【全部实际持仓】\n模拟持仓评估。\n\n【最终管理动作】\n模拟最终动作。\n\n【关键风险与改变判断的条件】\n模拟风险条件。\n\n【信息不足在哪里】\n无。\n\n【执行边界】\n不自动交易。\n\n【置信度】\n中等。\n\n【最可能出错的地方】\n模拟测试。"""
        content = content.replace("【全市场发现逐只评估】\n模拟发现评估。", "【全市场发现逐只评估】\n1. 模拟发现对象（159148）：模拟评估。")
        content = content.replace("【观察ETF逐只评估】\n模拟观察评估。", "【观察ETF逐只评估】\n1. 模拟观察对象（588000）：模拟评估。")
        content = content.replace("【机会ETF逐只评估】\n模拟机会评估。", "【机会ETF逐只评估】\n1. 模拟机会对象（159148）：模拟评估。")
        content = content.replace("【全部实际持仓】\n模拟持仓评估。", "【全部实际持仓】\n1. 纳指ETF（159941）：模拟持仓评估。")
        content = content.replace("1. ", "1）")
        content = content.replace("模拟观察对象（588000）", "科创50ETF（588000）")
        return completion.build_formal_review_completion_request(
            full_review(), request_id, "2026-09-23T20:30:00+08:00",
            "2026-09-23", "", None, content, "ETF正式决策",
            "sim-formal-run", "sim-formal-occ", "AUTOMATIC_SCHEDULED_TASK",
        )

    def test_all_three_start_with_request_and_reach_pushplus_boundary(self):
        payloads = (self._system(), self._trade(), self._formal())
        for payload in payloads:
            binding = payload["presentation_binding"]
            self.assertEqual(payload["request_type"], "SCHEDULED_REVIEW_COMPLETION")
            self.assertEqual(binding["request_id"], payload["request_id"])
            self.assertEqual(
                binding["content_hash"],
                hashlib.sha256(binding["full_content"].encode("utf-8")).hexdigest(),
            )
            chain = SimulatedCanonicalChain(payload)
            chain.publish_event()
            chain.publish_report()
            chain.push()
            self.assertTrue(chain.notification_id)
            self.assertEqual(chain.event["task_run_id"], payload["task_run_id"])
            self.assertEqual(chain.report["full_content"], binding["full_content"])

    def test_formal_gate_failure_stops_before_completion(self):
        review = full_review()
        review["fact_binding"]["gate_status"] = "NOT_READY"
        with self.assertRaises(ValueError):
            completion.build_formal_review_completion_request(
                review, "sim-formal-request", "2026-09-23T20:30:00+08:00",
                "2026-09-23", "", None,
                "【ETF正式决策】2026-09-23 20:30 正式判断\n\n模拟正文",
                "ETF正式决策", "sim-formal-run", "sim-formal-occ",
                "AUTOMATIC_SCHEDULED_TASK",
            )

    def test_wrong_request_identity_cannot_cross_the_chain(self):
        payload = self._system()
        payload["presentation_binding"]["request_id"] = "other-request"
        chain = SimulatedCanonicalChain(payload)
        chain.publish_event()
        self.assertNotEqual(chain.event["request_id"], payload["request_id"])
        with self.assertRaises(RuntimeError):
            chain.publish_report()


if __name__ == "__main__":
    unittest.main()
