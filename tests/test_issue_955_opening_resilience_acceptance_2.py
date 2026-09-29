from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import build_query_context as query
from scripts import process_state_sync_request as state_sync


def test_account_gate_uses_canonical_account_fact_not_market_plugin():
    current = {"market_date": "2026-09-29"}
    account = {"status": "VALID", "updated_at": "2026-09-29T08:50:00+08:00"}
    policy = {"account_fact_same_market_date_required": True}
    gate = query.account_gate_status(current, account, policy)
    assert gate["can_use_current_account_fact"] is True
    assert gate["requires_user_broker_screenshot"] is False
    plan = query.build_read_plan(current, account, policy, {"status": "FRESH"})
    assert "data/state/account_fact.json" in plan["required_reads"]
    assert "券商截图只确定账户、持仓、现金和成交事实" in plan["market_gate"]["broker_screenshot_rule"]


def test_invalid_account_fact_is_action_changing_stop_boundary():
    current = {"market_date": "2026-09-29"}
    account = {"status": "INVALID", "updated_at": "2026-09-29T08:50:00+08:00"}
    gate = query.account_gate_status(current, account, {"account_fact_same_market_date_required": True})
    assert gate["can_use_current_account_fact"] is False
    assert gate["requires_user_broker_screenshot"] is True
    assert gate["reason"] == "ACCOUNT_NOT_VALID"


def test_full_capital_competition_survives_local_discovery_unavailable():
    request = {"decision_work_package": {"problem_graph": [
        {"problem_id": "OBSERVATION:513180"},
        {"problem_id": "DISCOVERY:159127", "formal_quote_status": "UNAVAILABLE"},
        {"problem_id": "DISCOVERY:159992", "formal_quote_status": "SESSION_REFERENCE_READY"},
    ]}}
    required = state_sync.request_bound_etf_opportunity_reviews(request)
    assert required == {
        "513180": "OBSERVED_ETF", "159127": "OBSERVATION_EVALUATION_INPUT",
        "159992": "OBSERVATION_EVALUATION_INPUT",
    }
    capital = {
        "next_unit_capital_use": "现金", "full_competition_completed": True,
        "releasable_capital_reviewed": True, "post_action_deployable_cash": 20000,
        "future_opportunity_capacity": "保留Trial承载能力",
        "cash_opportunity_cost": "可能错过合法候选右尾",
        "alternative_capital_use_review": "已比较现金、Observation、Discovery、持仓追加、继续持仓和可释放资本",
        "concentration_account_structure_effect": "不增加集中度",
        "selected_state_reason": "当前现金更优", "new_amount_yuan": 0,
        "zero_amount_decisive_reason": "当前无满足执行证据的新增用途",
        "compared_capital_states": [
            {"state_name": "现金", "capital_action": "保留", "remaining_deployable_cash": 20000,
             "why_not_selected": "已选中", "opportunity_cost_if_selected": "可能错过候选右尾"},
            {"state_name": "Observation/Discovery", "capital_action": "不部署", "remaining_deployable_cash": 20000,
             "why_not_selected": "部分对象仅参考或不可用", "opportunity_cost_if_selected": "可能错过验证收益"},
        ],
        "etf_opportunity_reviews": [
            {"security_code": "513180", "category": "OBSERVED_ETF", "opportunity_status": "观察机会",
             "conclusion": "继续观察", "reason": "假设仍有信息价值"},
            {"security_code": "159127", "category": "OBSERVATION_EVALUATION_INPUT", "opportunity_status": "无机会",
             "conclusion": "本节点不部署", "reason": "对象正式行情不可用，仅局部限制该对象"},
            {"security_code": "159992", "category": "OBSERVATION_EVALUATION_INPUT", "opportunity_status": "观察机会",
             "conclusion": "进入结构评估但不执行", "reason": "SESSION_REFERENCE可评价但不支持当前金额份额"},
        ],
    }
    assert state_sync.validate_capital_competition_contract(
        capital, {"positions": []}, required_etf_opportunities=required
    ) == ""


def test_provider_or_plugin_empty_is_not_encoded_as_no_risk_or_no_opportunity():
    root = Path(__file__).parents[1]
    query_text = (root / "scripts/build_query_context.py").read_text(encoding="utf-8")
    assert "EXPLICIT_DATA_LIMIT_OR_MISSING" in query_text
    assert "部分对象补采成功时逐对象使用最新有效证据" in query_text
    assert "不得为了统一时点把成功补采对象整体退回旧快照" in query_text


def test_user_visible_business_reply_boundary_is_not_backend_log_contract():
    root = Path(__file__).parents[1]
    source = (root / "scripts/business_decision_source.py").read_text(encoding="utf-8")
    assert "reply_ready" in source
    assert "source_durable" in source
    # The business source contract may carry audit metadata internally, but reply
    # readiness must be an explicit business boundary rather than inferred from
    # workflow/run/provider logging.
    assert "BUSINESS_DECISION_SOURCE" in source
