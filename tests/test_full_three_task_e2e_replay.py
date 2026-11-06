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

    def test_verified_close_normalization_removes_unavailable_gap(self):
        from scripts.trade_review_semantic_guard import normalize_verified_close_data_time
        self.assertEqual(normalize_verified_close_data_time({"close_data_status": "VERIFIED_SESSION_CLOSE", "close_data_gap": "unavailable"}), {"close_data_status": "VERIFIED_SESSION_CLOSE"})
    def test_trade_review_blocks_discovery_claim_that_conflicts_with_read_object(self):
        review = full_review()
        review["formal_discovery"] = {"status": "READY", "candidates": [{"code": "159133"}]}
        with self.assertRaisesRegex(ValueError, "Discovery object"):
            completion.validate_trade_review_presentation_semantics(
                review, "本日没有可确认的独立全市场Discovery记录。"
            )

    def test_trade_review_allows_explicit_correction_of_discovery_misreading(self):
        review = full_review()
        review["formal_discovery"] = {"status": "READY", "candidates": [{"code": "159133"}]}
        completion.validate_trade_review_presentation_semantics(
            review, "不能把本次Discovery资料不足写成没有Discovery；本段仅说明复盘未消费该对象。"
        )

    def test_trade_review_blocks_nonzero_opportunity_as_zero(self):
        review = full_review()
        review["formal_opportunity"] = {"status": "READY", "opportunity_count": 2}
        with self.assertRaisesRegex(ValueError, "opportunity object"):
            completion.validate_trade_review_presentation_semantics(
                review, "机会ETF数量为0。"
            )

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
        content = """【ETF正式决策】2026-09-23 20:30 正式判断

【判断时点与事实边界】
模拟事实边界。

【当前正式判断】
模拟判断，建议新增部署0元，实际新增部署0元。

【风险与市场状态】
风险许可为允许Trial；当前不支持自动交易。

【三层监测】
模拟三层监测。

【全市场发现逐只评估】
1. 模拟发现对象（159148）：保留发现，等待下一节点。\n   （1）当前对象证据有限。

【观察ETF逐只评估】
1. 科创50ETF（588000）：继续观察，等待下一节点。\n   （1）继续保留其信息价值。

【机会ETF逐只评估】
未形成机会对象：Trial、Confirm均不授予，当前部署0元。

【全市场机会与资本竞争】
现金保留、持仓持有、追加、部分减持、全部退出和机会部署均进行比较。最近可执行候选为模拟发现对象；决定性证据缺口是尚未形成持续承接；下一复核节点为下次正式决策。基于当前证据和风险收益，现金的流动性和失败风险优于其他用途，是当前胜出用途和下一单位资本最有效用途；持仓相较现金和机会仍需比较机会成本、资本效率、迁移成本。

【现金管理】
保留现金。

【全部实际持仓】
1. 纳指ETF（159941）：继续持有。相较现金和机会，继续占资更优；若相对弱势持续则重新评估。继续持有优于立即释放；追加需新证据，部分减持和全部退出需独立条件。若从零重新进入，不优于当前等待，因为迁移成本、失败风险、上行尾部和账户分散功能仍需比较。\n   （1）当前管理结论继续有效。

【最终管理动作】
不自动交易。

【关键风险与改变判断的条件】
若风险扩大则重新判断。

【信息不足在哪里】
无。

【执行边界】
不自动交易。

【置信度】
中等。

【最可能出错的地方】
模拟测试。"""
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
