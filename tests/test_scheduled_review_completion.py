from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
