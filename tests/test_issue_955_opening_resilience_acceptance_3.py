from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scripts import build_query_context as query
from scripts import process_state_sync_request as state_sync


def test_actual_position_gap_is_action_specific_fail_closed_not_discovery_global_failure():
    request = {
        "request_id": "formal-position-gap", "requested_at_beijing": "2026-09-29T10:00:00+08:00",
        "requested_by": "CHATGPT_USER_INTERACTION", "query_intent": "FORMAL_DECISION",
        "_request_file": "requests/live_snapshot/formal-position-gap.json",
    }
    account = {"status": "VALID", "positions": [
        {"asset_type": "ETF", "code": "159981", "quantity": 2800}
    ]}
    decision = {"analysis_coverage": {
        "held_etfs_total": 1, "held_etfs_available": 0, "account_stocks_total": 0,
        "account_stocks_available": 0, "missing_held_etfs": ["159981"], "missing_account_stocks": [],
    }}
    market_quote = {"decision_freshness": {"post_request": True, "resolved_post_request": True}, "quotes": []}
    discovery = {"status": "READY", "candidates": [
        {"code": "159992", "formal_quote_status": "SESSION_REFERENCE_READY"}
    ]}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "config/market").mkdir(parents=True)
        (root / "config/market/etf_monitor_universe.json").write_text(
            json.dumps({"objects": []}), encoding="utf-8"
        )
        pack = query.build_decision_fact_pack(root, request, {"market_date": "2026-09-29"},
                                              account, decision, market_quote, formal_discovery=discovery)
    assert pack["formal_action_readiness"]["ready"] is False
    assert "ACTUAL_POSITION_MARKET_COVERAGE_INCOMPLETE" in pack["formal_action_readiness"]["blockers"]
    assert pack["formal_etf_discovery"]["candidates"][0]["code"] == "159992"


def test_request_bound_domain_keeps_all_observation_discovery_objects_for_capital_competition():
    request = {"decision_work_package": {"problem_graph": [
        {"problem_id": "OBSERVATION:513180"},
        {"problem_id": "OBSERVATION:159992"},
        {"problem_id": "DISCOVERY:588080", "formal_quote_status": "READY"},
        {"problem_id": "DISCOVERY:159127", "formal_quote_status": "UNAVAILABLE"},
    ]}}
    required = state_sync.request_bound_etf_opportunity_reviews(request)
    assert required == {
        "513180": "OBSERVED_ETF", "159992": "OBSERVED_ETF",
        "588080": "OBSERVATION_EVALUATION_INPUT", "159127": "OBSERVATION_EVALUATION_INPUT",
    }


def test_capital_contract_requires_full_competition_flag_and_releasable_capital_review():
    base = {
        "next_unit_capital_use": "现金", "post_action_deployable_cash": 20000,
        "future_opportunity_capacity": "保留Trial/Confirm承载能力",
        "cash_opportunity_cost": "可能错过机会", "alternative_capital_use_review": "已比较",
        "concentration_account_structure_effect": "不增加集中度",
        "selected_state_reason": "现金更优", "new_amount_yuan": 0,
        "zero_amount_decisive_reason": "无更优合法用途",
        "compared_capital_states": [
            {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 20000,
             "why_not_selected": "已选中", "opportunity_cost_if_selected": "可能错过机会"}
        ],
        "etf_opportunity_reviews": [],
    }
    missing_full = dict(base, full_competition_completed=False, releasable_capital_reviewed=True)
    assert "full capital competition" in state_sync.validate_capital_competition_contract(
        missing_full, {"positions": []}, required_etf_opportunities={}
    ).lower()
    missing_release = dict(base, full_competition_completed=True, releasable_capital_reviewed=False)
    assert "releasable capital" in state_sync.validate_capital_competition_contract(
        missing_release, {"positions": []}, required_etf_opportunities={}
    ).lower()


def test_discovery_unavailable_does_not_erase_request_bound_review_requirement():
    request = {"decision_work_package": {"problem_graph": [
        {"problem_id": "DISCOVERY:159127", "formal_quote_status": "UNAVAILABLE"}
    ]}}
    required = state_sync.request_bound_etf_opportunity_reviews(request)
    assert required == {"159127": "OBSERVATION_EVALUATION_INPUT"}
    capital = {
        "next_unit_capital_use": "现金", "full_competition_completed": True,
        "releasable_capital_reviewed": True, "post_action_deployable_cash": 20000,
        "future_opportunity_capacity": "保留选择权", "cash_opportunity_cost": "可能错过右尾",
        "alternative_capital_use_review": "已比较合法资本状态",
        "concentration_account_structure_effect": "不增加集中度",
        "selected_state_reason": "对象事实不足以支持部署", "new_amount_yuan": 0,
        "zero_amount_decisive_reason": "对象正式事实不可用",
        "compared_capital_states": [
            {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 20000,
             "why_not_selected": "已选中", "opportunity_cost_if_selected": "可能错过右尾"},
            {"state_name": "159127", "capital_action": "不部署", "remaining_deployable_cash": 20000,
             "why_not_selected": "正式行情不可用", "opportunity_cost_if_selected": "失去验证机会"},
        ],
        "etf_opportunity_reviews": [
            {"security_code": "159127", "category": "OBSERVATION_EVALUATION_INPUT",
             "opportunity_status": "无机会", "conclusion": "本节点不部署",
             "reason": "正式行情不可用，仅限制该对象"}
        ],
    }
    assert state_sync.validate_capital_competition_contract(
        capital, {"positions": []}, required_etf_opportunities=required
    ) == ""
