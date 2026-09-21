from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import manual_formal_decision_completion as completion
from scripts import process_state_sync_request as state_sync


SOURCE_REQUEST = {
    "request_id": "20260915_1447_manual_intraday_chat",
    "request_type": "MARKET_QUOTE_REFRESH",
    "interaction_scenario": "INTRADAY",
    "source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
    "requested_at_beijing": "2026-09-15T14:47:00+08:00",
    "market_date": "2026-09-15",
    "query_intent": "EXPLICIT_LATEST",
}
SNAPSHOT_PATH = "data/market/snapshots/2026-09-15_144842.json"


def formal_decision():
    return {
        "decision_id": "20260915_1447_manual_intraday_chat_decision",
        "data_as_of_beijing": "2026-09-15T14:48:42+08:00",
        "candidate_code": "561980",
        "candidate_name": "半导体设备ETF",
        "main_candidate": "半导体设备ETF（561980）",
        "opportunity_status": "Trial机会",
        "risk_permission": "禁止新增",
        "lifecycle": "",
        "managed_position_reviews": [],
        "capital_competition": {
            "next_unit_capital_use": "保留现金",
            "full_competition_completed": True,
            "releasable_capital_reviewed": True,
            "post_action_deployable_cash": 10000.0,
            "future_opportunity_capacity": "仍可承载后续Trial/Confirm",
            "cash_opportunity_cost": "保留现金会放弃当前合法候选可能产生的收益与信息获取价值",
            "alternative_capital_use_review": "已比较新Trial、直接Confirm、持仓ETF追加、现有持仓继续占资、低效率资本释放迁移及其他MASTER允许状态与现金",
            "concentration_account_structure_effect": "不增加集中度",
            "selected_state_reason": "现金优于当前可执行候选",
            "new_amount_yuan": 0,
            "zero_amount_decisive_reason": "没有候选在完整资本竞争后优于现金",
            "compared_capital_states": [
                {
                    "state_name": "维持现有组合+现金",
                    "capital_action": "不新增",
                    "remaining_deployable_cash": 10000.0,
                    "why_not_selected": "已选中", "opportunity_cost_if_selected": "选择该状态将放弃其他合法资本状态的潜在收益、信息价值或现金选择权"
                },
                {
                    "state_name": "半导体设备ETF新增Trial+剩余现金",
                    "capital_action": "新增5000元",
                    "remaining_deployable_cash": 5000.0,
                    "why_not_selected": "风险许可不允许新增", "opportunity_cost_if_selected": "选择该状态将放弃其他合法资本状态的潜在收益、信息价值或现金选择权"
                }
            ],
            "held_etf_add_capital_reviews": [],
            "capital_release_migrations": []
        },
    }


class ManualCompletionEnvelopeTests(unittest.TestCase):
    def test_matched_request_keeps_parent_separate_from_completion_identity_and_binds_pit(self):
        payload = completion.build_completion_request(SOURCE_REQUEST, formal_decision(), SNAPSHOT_PATH)
        self.assertEqual(payload["request_id"], "20260915_1447_manual_intraday_chat__formal_completion")
        self.assertEqual(payload["parent_request_id"], SOURCE_REQUEST["request_id"])
        self.assertNotEqual(payload["request_id"], payload["parent_request_id"])
        self.assertEqual(payload["consumed_snapshot"], SNAPSHOT_PATH)
        self.assertEqual(payload["request_type"], "STATE_SYNC_ONLY")
        self.assertEqual(payload["requested_at_beijing"], SOURCE_REQUEST["requested_at_beijing"])

    def test_no_decision_or_missing_snapshot_does_not_create_completion(self):
        with self.assertRaises(ValueError):
            completion.build_completion_request(SOURCE_REQUEST, {}, SNAPSHOT_PATH)
        with self.assertRaises(ValueError):
            completion.build_completion_request(SOURCE_REQUEST, formal_decision(), "")

    def test_non_manual_request_cannot_enter_manual_completion_path(self):
        with self.assertRaises(ValueError):
            completion.build_completion_request({**SOURCE_REQUEST, "source": "SCHEDULED_ACTOR"}, formal_decision(), SNAPSHOT_PATH)

    def test_user_continue_formal_analysis_can_complete_same_manual_chain(self):
        source = {
            **SOURCE_REQUEST,
            "request_id": "20260921_user_continue_intraday_discovery",
            "source": "CHATGPT_USER_CONTINUE",
            "intent": "FORMAL_INTRADAY_ANALYSIS",
            "query_intent": "FORMAL_INTRADAY_ANALYSIS",
        }
        payload = completion.build_completion_request(source, formal_decision(), SNAPSHOT_PATH)
        self.assertEqual(payload["parent_request_id"], source["request_id"])
        self.assertEqual(payload["source"], "CHATGPT_MANUAL_FORMAL_COMPLETION")

    def test_user_continue_without_formal_intent_fails_closed(self):
        source = {**SOURCE_REQUEST, "source": "CHATGPT_USER_CONTINUE", "query_intent": "MARKET_QUOTE_REFRESH"}
        with self.assertRaises(ValueError):
            completion.build_completion_request(source, formal_decision(), SNAPSHOT_PATH)

    def test_completion_identity_must_be_distinct_from_parent(self):
        with self.assertRaises(ValueError):
            completion.build_completion_request(
                SOURCE_REQUEST, formal_decision(), SNAPSHOT_PATH,
                completion_request_id=SOURCE_REQUEST["request_id"],
            )


class CapitalCompetitionContractTests(unittest.TestCase):
    def test_zero_amount_requires_full_competition_reason(self):
        decision = formal_decision()
        decision["capital_competition"].pop("zero_amount_decisive_reason")
        error = state_sync.validate_capital_competition_contract(
            decision["capital_competition"], {"positions": []}
        )
        self.assertIn("zero_amount_decisive_reason", error)

    def test_full_competition_must_be_completed(self):
        decision = formal_decision()
        decision["capital_competition"]["full_competition_completed"] = False
        error = state_sync.validate_capital_competition_contract(
            decision["capital_competition"], {"positions": []}
        )
        self.assertIn("full_competition_completed", error)

    def test_requires_multiple_executable_capital_states(self):
        decision = formal_decision()
        decision["capital_competition"]["compared_capital_states"] = decision["capital_competition"]["compared_capital_states"][:1]
        error = state_sync.validate_capital_competition_contract(
            decision["capital_competition"], {"positions": []}
        )
        self.assertIn("at least two executable states", error)

    def test_held_etf_add_capital_review_is_required_for_every_held_etf(self):
        decision = formal_decision()
        account = {
            "positions": [
                {"security_code": "561980", "security_name": "半导体设备ETF", "quantity": 1000}
            ]
        }
        error = state_sync.validate_capital_competition_contract(
            decision["capital_competition"], account
        )
        self.assertIn("held_etf_add_capital_reviews", error)


class FormalDecisionCrossFieldConsistencyTests(unittest.TestCase):
    def account_with_held_etf(self):
        return {
            "positions": [
                {"security_code": "561980", "security_name": "半导体设备ETF", "quantity": 1000}
            ]
        }

    def complete_decision_with_held_etf(self):
        decision = formal_decision()
        decision["managed_position_reviews"] = [
            {
                "security_code": "561980",
                "current_action": "持有",
                "holding_state_risk_reward_evidence": "继续占用资本仍合理",
                "holding_thesis_status": "原持有依据当前仍有效",
                "risk_reduction_or_exit_condition": "当前未达到降低风险或退出条件",
                "higher_efficiency_alternative": "当前不存在已经独立成立且效率更高的资本用途",
                "capital_occupancy_reason": "继续占用资本仍优于释放",
                "continued_holding_opportunity_cost": "继续持有将放弃现金、部分/全部释放及迁移到其他合法资本用途的价值",
                "capital_use": {"continued_holding_vs_cash": "继续持有优于释放", "alternative_capital_uses_review": "已比较现金、部分释放、全部释放、合法新Trial/直接Confirm、其他持仓追加及其他MASTER允许状态", "position_capital_states": {"HOLD": "继续持有", "REDUCE": "部分释放", "EXIT": "全部释放"}},
                "action_changes_now": False,
                "next_change_condition": "结构失效时重评",
            }
        ]
        decision["capital_competition"]["held_etf_add_capital_reviews"] = [
            {
                "security_code": "561980",
                "eligible_for_additional_capital_review": True,
                "conclusion": "不追加",
                "reason": "完整竞争后现金更优",
            }
        ]
        return decision

    def test_hold_cannot_fund_release_migration(self):
        decision = self.complete_decision_with_held_etf()
        decision["capital_competition"]["capital_release_migrations"] = [
            {
                "source": "半导体设备ETF（561980）",
                "amount_or_quantity": "1000份",
                "destination": "现金",
                "reason": "释放低效率资本",
            }
        ]
        error = state_sync.validate_formal_decision_cross_field_consistency(
            decision, self.account_with_held_etf()
        )
        self.assertIn("cannot HOLD", error)

    def test_release_migration_requires_releasable_review(self):
        decision = self.complete_decision_with_held_etf()
        decision["capital_competition"]["releasable_capital_reviewed"] = False
        decision["capital_competition"]["capital_release_migrations"] = [
            {
                "source": "半导体设备ETF（561980）",
                "amount_or_quantity": "1000份",
                "destination": "现金",
                "reason": "释放低效率资本",
            }
        ]
        error = state_sync.validate_formal_decision_cross_field_consistency(
            decision, self.account_with_held_etf()
        )
        self.assertIn("releasable_capital_reviewed", error)

    def test_selected_state_cash_must_match_post_action_cash(self):
        decision = formal_decision()
        decision["capital_competition"]["compared_capital_states"][0]["remaining_deployable_cash"] = 9000.0
        error = state_sync.validate_formal_decision_cross_field_consistency(
            decision, {"positions": []}
        )
        self.assertIn("conflicts with post_action_deployable_cash", error)

    def test_multiple_selected_states_fail(self):
        decision = formal_decision()
        decision["capital_competition"]["compared_capital_states"][1]["why_not_selected"] = "已选中"
        error = state_sync.validate_formal_decision_cross_field_consistency(
            decision, {"positions": []}
        )
        self.assertIn("multiple selected states", error)

    def test_release_source_must_be_current_account_asset(self):
        decision = formal_decision()
        decision["capital_competition"]["capital_release_migrations"] = [
            {
                "source": "不存在ETF（999999）",
                "amount_or_quantity": "1000份",
                "destination": "现金",
                "reason": "释放低效率资本",
            }
        ]
        error = state_sync.validate_formal_decision_cross_field_consistency(
            decision, {"positions": []}
        )
        self.assertIn("source is not a current account asset", error)

    def test_consistent_decision_passes(self):
        self.assertEqual(
            state_sync.validate_formal_decision_cross_field_consistency(
                formal_decision(), {"positions": []}
            ),
            "",
        )


class ManualFormalDecisionCanonicalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "events/decisions").mkdir(parents=True)
        (self.root / "data/state").mkdir(parents=True)
        self.snapshot = {
            "market_date": "2026-09-15",
            "quality_status": "PASS",
            "captured_at_beijing": "2026-09-15T14:48:42+08:00",
            "rows": [],
        }
        (self.root / SNAPSHOT_PATH).parent.mkdir(parents=True, exist_ok=True)
        (self.root / SNAPSHOT_PATH).write_text(json.dumps(self.snapshot), encoding="utf-8")
        request_dir = self.root / "requests/live_snapshot"
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / f"{SOURCE_REQUEST['request_id']}.json").write_text(
            json.dumps({**SOURCE_REQUEST, "require_post_request_snapshot": True}),
            encoding="utf-8",
        )
        account = self.root / "data/state/account_fact.json"
        account.write_text(json.dumps({"status": "VALID", "positions": []}), encoding="utf-8")
        (self.root / "data/state/query_context.json").write_text(
            json.dumps({
                "decision_fact_pack": {
                    "trigger": {"request_id": SOURCE_REQUEST["request_id"]},
                    "formal_analysis_availability": {"status": "AVAILABLE", "available": True, "global_blockers": [], "limitations": []},
                    "formal_action_readiness": {"status": "READY", "ready": True, "blockers": []},
                    "formal_reasoning_readiness": {"status": "READY", "ready": True, "blockers": []},
                }
            }), encoding="utf-8"
        )
        self.stack = mock.patch.multiple(
            state_sync,
            ROOT=self.root,
            ACCOUNT=account,
            _load_account_for_lifecycle_validation=mock.DEFAULT,
            validate_managed_position_lifecycle=mock.DEFAULT,
            validate_managed_position_review_contract=mock.DEFAULT,
            build_managed_position_projection=mock.DEFAULT,
            build_comparison_snapshot=mock.DEFAULT,
            resolve_hypothesis_id=mock.DEFAULT,
            select_point_in_time_snapshot=mock.DEFAULT,
        )
        self.mocks = self.stack.start()
        self.mocks["_load_account_for_lifecycle_validation"].return_value = {"status": "VALID", "positions": []}
        self.mocks["validate_managed_position_lifecycle"].return_value = ""
        self.mocks["validate_managed_position_review_contract"].return_value = ""
        self.mocks["build_managed_position_projection"].return_value = {"positions": []}
        self.mocks["build_comparison_snapshot"].return_value = {"items": []}
        self.mocks["resolve_hypothesis_id"].return_value = ("", "NO_HYPOTHESIS")
        self.mocks["select_point_in_time_snapshot"].return_value = (
            SNAPSHOT_PATH, self.snapshot, "CONSUMED_SNAPSHOT_VALIDATED"
        )

    def tearDown(self):
        self.stack.stop()
        self.temp.cleanup()

    def request(self, envelope_id):
        return {
            "request_id": envelope_id,
            "parent_request_id": SOURCE_REQUEST["request_id"],
            "request_type": "STATE_SYNC_ONLY",
            "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
            "interaction_scenario": "INTRADAY",
            "market_date": "2026-09-15",
            "requested_at_beijing": SOURCE_REQUEST["requested_at_beijing"],
            "persistence_available_at_beijing": "2026-09-15T14:49:00+08:00",
            "consumed_snapshot": SNAPSHOT_PATH,
            "formal_decision": formal_decision(),
        }

    def test_parent_identity_fingerprint_event_fields_and_retry_exactly_once(self):
        first_request = self.request("20260915_1447_manual_intraday_chat__formal_completion")
        retry_request = self.request("different_completion_envelope_retry")
        self.assertEqual(
            state_sync.record_formal_decision(first_request),
            (True, formal_decision()["decision_id"]),
        )
        self.assertEqual(
            state_sync.record_formal_decision(retry_request),
            (True, formal_decision()["decision_id"]),
        )
        files = list((self.root / "events/decisions").glob("*.json"))
        self.assertEqual(len(files), 1)
        event = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(event["request_id"], first_request["request_id"])
        self.assertEqual(event["parent_request_id"], SOURCE_REQUEST["request_id"])
        self.assertEqual(event["price_source_snapshot"], SNAPSHOT_PATH)
        self.assertEqual(event["point_in_time_status"], "CONSUMED_SNAPSHOT_VALIDATED")
        self.assertTrue(event["fingerprint"])

    def test_degraded_analysis_does_not_bypass_action_readiness(self):
        query_path = self.root / "data/state/query_context.json"
        query = json.loads(query_path.read_text(encoding="utf-8"))
        packet = query["decision_fact_pack"]
        packet["formal_analysis_availability"] = {
            "status": "DEGRADED", "available": True, "global_blockers": [],
            "limitations": ["ACCOUNT_FACT_NOT_READY_EXACT_AMOUNT_SHARE_BLOCKED"],
        }
        packet["formal_action_readiness"] = {
            "status": "NOT_READY", "ready": False, "blockers": ["ACCOUNT_FACT_NOT_READY"],
        }
        query_path.write_text(json.dumps(query), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "action inputs are not READY"):
            state_sync.record_formal_decision(self.request("degraded_analysis_envelope"))
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])

    def test_pre_request_snapshot_fails_closed_without_event(self):
        stale = {
            **self.snapshot,
            "captured_at_beijing": "2026-09-15T14:46:59+08:00",
            "provider_as_of": "2026-09-15T14:46:58+08:00",
        }
        self.mocks["select_point_in_time_snapshot"].return_value = (
            SNAPSHOT_PATH, stale, "CONSUMED_SNAPSHOT_VALIDATED"
        )
        with self.assertRaisesRegex(ValueError, "qualified post-request snapshot"):
            state_sync.record_formal_decision(self.request("pre_request_snapshot_envelope"))
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])

    def test_missing_durable_parent_request_fails_closed_without_event(self):
        request = self.request("missing_durable_parent_envelope")
        request["parent_request_id"] = "unknown_parent"
        with self.assertRaisesRegex(ValueError, "parent request is not durable"):
            state_sync.record_formal_decision(request)
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])

    def test_illegal_pit_fails_closed_without_event(self):
        self.mocks["select_point_in_time_snapshot"].return_value = ("", {}, "NO_PRIOR_SNAPSHOT")
        with self.assertRaises(ValueError):
            state_sync.record_formal_decision(self.request("illegal_pit_envelope"))
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])

    def test_retry_with_changed_pit_linkage_fails_closed_without_second_event(self):
        request = self.request("first_valid_envelope")
        self.assertTrue(state_sync.record_formal_decision(request)[0])
        self.mocks["select_point_in_time_snapshot"].return_value = (
            "data/market/snapshots/2026-09-15_145000.json",
            self.snapshot,
            "CONSUMED_SNAPSHOT_VALIDATED",
        )
        with self.assertRaisesRegex(ValueError, "changed its PIT/source snapshot linkage"):
            state_sync.record_formal_decision(self.request("changed_snapshot_retry"))
        self.assertEqual(len(list((self.root / "events/decisions").glob("*.json"))), 1)

    def test_envelope_identity_must_be_distinct_from_parent_in_canonical_writer(self):
        request = self.request(SOURCE_REQUEST["request_id"])
        with self.assertRaisesRegex(ValueError, "distinct envelope and parent"):
            state_sync.record_formal_decision(request)
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])

    def test_missing_parent_identity_fails_closed_without_event(self):
        request = self.request("missing_parent_envelope")
        request.pop("parent_request_id")
        with self.assertRaises(ValueError):
            state_sync.record_formal_decision(request)
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])


    def test_research_evidence_used_persists_as_ex_ante_fact(self):
        request = self.request("research_evidence_envelope")
        request["formal_decision"]["research_evidence_used"] = [
            {
                "evidence_id": "component_lead_561980_3d",
                "decision_effect": "WEAKEN",
                "security_code": "561980",
                "decisive": False,
                "evidence_as_of_beijing": "2026-09-15T14:40:00+08:00",
                "source": "data/state/research_execution_summary.json",
            }
        ]
        recorded, decision_id = state_sync.record_formal_decision(request)
        self.assertTrue(recorded)
        event = json.loads((self.root / "events/decisions" / f"{decision_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(
            event["formal_decision"]["research_evidence_used"],
            request["formal_decision"]["research_evidence_used"],
        )

    def test_explicit_no_research_evidence_is_valid(self):
        request = self.request("no_research_evidence_envelope")
        request["formal_decision"]["research_evidence_used"] = []
        recorded, decision_id = state_sync.record_formal_decision(request)
        self.assertTrue(recorded)
        event = json.loads((self.root / "events/decisions" / f"{decision_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(event["formal_decision"]["research_evidence_used"], [])

    def test_malformed_research_evidence_fails_closed(self):
        request = self.request("bad_research_evidence_envelope")
        request["formal_decision"]["research_evidence_used"] = [
            {"evidence_id": "E1", "decision_effect": "AUTO_BUY"}
        ]
        with self.assertRaisesRegex(ValueError, "research-evidence contract"):
            state_sync.record_formal_decision(request)
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])


    def test_cash_opportunity_cost_is_required(self):
        request = self.request("missing_cash_opportunity_cost")
        request["formal_decision"]["capital_competition"].pop("cash_opportunity_cost")
        with self.assertRaisesRegex(ValueError, "cash_opportunity_cost"):
            state_sync.record_formal_decision(request)

    def test_alternative_capital_use_review_is_required(self):
        request = self.request("missing_trial_information_value")
        request["formal_decision"]["capital_competition"].pop("alternative_capital_use_review")
        with self.assertRaisesRegex(ValueError, "alternative_capital_use_review"):
            state_sync.record_formal_decision(request)

    def test_each_compared_state_requires_opportunity_cost(self):
        request = self.request("missing_state_opportunity_cost")
        request["formal_decision"]["capital_competition"]["compared_capital_states"][0].pop("opportunity_cost_if_selected")
        with self.assertRaisesRegex(ValueError, "opportunity_cost_if_selected"):
            state_sync.record_formal_decision(request)

    def test_missing_formal_conclusion_creates_no_event(self):
        request = self.request("no_conclusion_envelope")
        request.pop("formal_decision")
        self.assertEqual(state_sync.record_formal_decision(request), (False, ""))
        self.assertEqual(list((self.root / "events/decisions").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
