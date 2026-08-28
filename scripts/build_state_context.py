from __future__ import annotations

import json
import os
from pathlib import Path

from build_intraday_path_features import build as build_intraday_path_features
from build_minute_path_features import build as build_minute_path_features
from build_market_regime_context import build as build_market_regime_context
from build_market_structure_context import build as build_market_structure_context
from build_research_features import build as build_research_features
from build_research_evidence_delta import build as build_research_evidence_delta
from build_research_master_feedback import build as build_research_master_feedback
from build_execution_quality import build as build_execution_quality
from build_skfolio_risk_evidence import build as build_skfolio_risk_evidence
from build_etf_share_flow_evidence import build as build_etf_share_flow_evidence
from build_margin_financing_evidence import build as build_margin_financing_evidence
from build_active_return_evidence import build as build_active_return_evidence
from build_research_contribution_audit import build as build_research_contribution_audit
from build_research_execution_bridge import build as build_research_execution_bridge
from build_phase4_automation import build as build_phase4_automation
from state_manager import atomic_json_write, build_dashboard_candidate, build_decision_context


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def compact_skfolio_risk(evidence: dict) -> dict:
    account = evidence.get("current_account") or {}
    release = [
        {
            "display_name": x.get("display_name"),
            "risk_side_release_efficiency": x.get("risk_side_release_efficiency"),
            "account_etf_weight": x.get("account_etf_weight"),
        }
        for x in (account.get("risk_side_release_efficiency") or [])
    ]
    return {
        "status": evidence.get("status"),
        "mode": evidence.get("mode"),
        "risk_data_end": evidence.get("risk_data_end"),
        "data_alignment": evidence.get("data_alignment"),
        "use_in_current_decision": evidence.get("use_in_current_decision", False),
        "current_account_high_corr_components": account.get("high_corr_components") or [],
        "risk_side_release_efficiency": release,
        "interpretation_rule": evidence.get("interpretation_rule"),
        "decision_eligible": False,
        "use_as_decision_evidence": evidence.get("use_in_current_decision", False),
        "can_generate_decision_independently": False,
        "trade_signal": None,
        "portfolio_target": None,
    }


def compact_share_flow(evidence: dict) -> dict:
    usable = bool(evidence.get("use_in_current_decision", False))
    if not usable:
        return {
            "status": evidence.get("status"),
            "mode": evidence.get("mode"),
            "use_in_current_decision": False,
            "price_as_of_market_date": evidence.get("price_as_of_market_date"),
            "share_fact_latest_date": evidence.get("share_fact_latest_date"),
            "degraded_reason": evidence.get("degraded_reason") or evidence.get("error") or evidence.get("status_reason"),
            "interpretation_rule": "当前证据不可用于决策时只保留状态和原因，不把空排名或旧值塞入正式决策上下文。",
            "decision_eligible": False,
            "trade_signal": None,
            "trial_confirm": None,
            "portfolio_target": None,
        }
    ranking = [
        {
            "display_name": x.get("display_name"),
            "rank": x.get("conditional_increment_rank"),
            "rank_value": x.get("conditional_increment_rank_value"),
        }
        for x in (evidence.get("items") or [])
    ]
    return {
        "status": evidence.get("status"),
        "mode": evidence.get("mode"),
        "use_in_current_decision": True,
        "price_as_of_market_date": evidence.get("price_as_of_market_date"),
        "share_fact_latest_date": evidence.get("share_fact_latest_date"),
        "coverage": evidence.get("coverage"),
        "conditional_increment_ranking": ranking,
        "interpretation_rule": evidence.get("interpretation_rule"),
        "decision_eligible": False,
        "use_as_decision_evidence": evidence.get("use_in_current_decision", False),
        "can_generate_decision_independently": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
    }


def compact_margin_financing(evidence: dict) -> dict:
    return {
        "status": evidence.get("status"),
        "mode": evidence.get("mode"),
        "use_in_current_decision": evidence.get("use_in_current_decision", False),
        "fact_latest_date": evidence.get("fact_latest_date"),
        "provider": evidence.get("provider"),
        "primary_evidence": evidence.get("primary_evidence") or {},
        "secondary_evidence": evidence.get("secondary_evidence") or {},
        "applies_to": evidence.get("applies_to") or [],
        "validated_reference": evidence.get("validated_reference") or {},
        "interpretation_rule": evidence.get("interpretation_rule"),
        "decision_eligible": False,
        "use_as_decision_evidence": evidence.get("use_in_current_decision", False),
        "can_generate_decision_independently": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
    }


def compact_contribution_audit(audit: dict) -> dict:
    return {
        "status": audit.get("status"),
        "mode": audit.get("mode"),
        "rule": audit.get("rule"),
        "decision_attribution": audit.get("decision_attribution") or {},
        "trade_attribution": audit.get("trade_attribution") or {},
        "evidence_use_counts": audit.get("evidence_use_counts") or {},
        "recent_trades": audit.get("recent_trades") or [],
        "redundancy_review": audit.get("redundancy_review") or {},
    }


def select_intraday_path_features(root: Path) -> tuple[dict, dict]:
    discrete = build_intraday_path_features(root)
    minute = None
    minute_error = None
    try:
        minute = build_minute_path_features(root)
    except Exception as exc:
        minute_error = str(exc)[-1000:]

    quality = (minute or {}).get("quality_summary") or {}
    minute_ready = bool(
        minute
        and minute.get("status") == "READY"
        and minute.get("coverage_ratio") == 1.0
        and quality.get("formal_gate_pass") is True
    )
    if minute_ready:
        selected = {
            **minute,
            "mode": "TENCENT_1M_ETF_PATH_PRODUCTION_EVIDENCE",
            "production_selection": {
                "selected_source": "TENCENT_1M",
                "fallback_source": "DISCRETE_SNAPSHOT_PATH",
                "selection_reason": "minute_quality_gate_pass",
                "formal_latest_price_source_unchanged": True,
                "decision_boundary": "分钟源仅增强日内路径、极值时序和成交承接证据；正式最新价继续由quote router决定，不产生风险许可、金额或交易动作。",
            },
        }
    else:
        selected = {
            **discrete,
            "production_selection": {
                "selected_source": "DISCRETE_SNAPSHOT_PATH",
                "preferred_source": "TENCENT_1M",
                "selection_reason": "minute_unavailable_or_quality_gate_failed",
                "minute_status": (minute or {}).get("status") if minute else "ERROR",
                "minute_coverage_ratio": (minute or {}).get("coverage_ratio") if minute else None,
                "minute_quality_summary": quality,
                "minute_error": minute_error,
                "formal_latest_price_source_unchanged": True,
                "decision_boundary": "腾讯分钟证据不可用或质量门不通过时自动回退既有离散路径，不阻断正式决策。",
            },
        }
    diagnostics = {
        "selected_source": (selected.get("production_selection") or {}).get("selected_source"),
        "minute_status": (minute or {}).get("status") if minute else "ERROR",
        "minute_coverage_ratio": (minute or {}).get("coverage_ratio") if minute else None,
        "minute_formal_gate_pass": quality.get("formal_gate_pass"),
        "minute_error": minute_error,
        "discrete_status": discrete.get("status"),
    }
    return selected, diagnostics


def main() -> None:
    path_features, path_selection = select_intraday_path_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "intraday_path_features.json", path_features)

    market_regime = build_market_regime_context(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "market_regime_context.json", market_regime)

    market_structure = build_market_structure_context(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "market_structure_context.json", market_structure)

    research = build_research_features(ROOT)
    evidence_delta = build_research_evidence_delta(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_evidence_delta.json", evidence_delta)
    execution_quality = build_execution_quality(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "execution_quality.json", execution_quality)

    skfolio_risk = build_skfolio_risk_evidence(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "skfolio_risk_evidence.json", skfolio_risk)
    skfolio_summary = compact_skfolio_risk(skfolio_risk)

    share_flow = build_etf_share_flow_evidence(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "etf_share_flow_evidence.json", share_flow)
    share_flow_summary = compact_share_flow(share_flow)

    margin_financing = build_margin_financing_evidence(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "margin_financing_evidence.json", margin_financing)
    margin_financing_summary = compact_margin_financing(margin_financing)

    active_return = build_active_return_evidence(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "active_return_evidence.json", active_return)

    contribution_audit = build_research_contribution_audit(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_contribution_audit.json", contribution_audit)
    contribution_summary = compact_contribution_audit(contribution_audit)

    research_master = build_research_master_feedback(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_master_candidates.json", research_master)
    atomic_json_write(ROOT / "data" / "state" / "research_context.json", {
        **research,
        "read_only": True,
        "decision_boundary": "研究层向当前决策提供经过归纳的市场、ETF与打新底仓研究证据，可直接改变候选比较、风险收益、持仓/卖出、金额及资金来源/去向判断；不得绕过MASTER生成交易动作或自动交易。",
        "current_decision_use": "正式盘中先读市场层，再统一比较现金、持仓/观察ETF与当前打新底仓；研究结论先归纳后进入执行判断，正式决策只记录真正改变机会、持有/卖出、金额或资金来源/去向的research_evidence_used。",
        "market_regime_context": market_regime,
        "candidate_selection_contract": market_structure.get("candidate_selection_contract") or {},
        "market_structure_summary": {
            "status": market_structure.get("status"),
            "as_of_beijing": market_structure.get("as_of_beijing"),
            "item_count": len(market_structure.get("items") or []),
            "history_available_count": sum(1 for x in (market_structure.get("items") or []) if (x.get("historical_context") or {}).get("status") == "READY"),
        },
        "master_feedback": "研究层可形成MASTER维护输入，但只有通过MASTER第8.1正式研究转化机制的高质量专项研究，或多个真实CASE反复暴露的同类问题，才允许正式修改MASTER。",
        "optimization_principle": "不打造完美交易系统；复杂度只有在改善事前收益效率、风险边界、执行质量或复盘学习时才保留。长期未改变任何正式决策且无独立风险/复盘价值的研究模块才进入删除审查。",
        "validated_evidence_summary": {
            "market_regime": market_regime,
            "market_structure": market_structure,
            "skfolio_risk": skfolio_summary,
            "etf_share_flow": share_flow_summary,
            "margin_financing": margin_financing_summary,
            "active_return": active_return,
        },
        "research_contribution_audit": contribution_summary,
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
            "market_regime_context": "data/state/market_regime_context.json",
            "market_structure_context": "data/state/market_structure_context.json",
            "relative_strength": "data/state/relative_strength.json",
            "evidence_delta": "data/state/research_evidence_delta.json",
            "evidence_history": "events/research/evidence_snapshots/<market_date>/*.json",
            "execution_quality": "data/state/execution_quality.json",
            "skfolio_risk_evidence": "data/state/skfolio_risk_evidence.json",
            "etf_share_flow_evidence": "data/state/etf_share_flow_evidence.json",
            "margin_financing_evidence": "data/state/margin_financing_evidence.json",
            "active_return_evidence": "data/state/active_return_evidence.json",
            "research_contribution_audit": "data/state/research_contribution_audit.json",
            "historical_backfill_status": "data/state/historical_backfill_status.json",
            "provider_metrics": "data/state/provider_metrics.json",
            "decision_events": "events/decisions/<decision_id>.json",
            "decision_outcomes": "events/research/decision_outcomes/<decision_id>.json",
            "master_candidates": "data/state/research_master_candidates.json",
        },
    })

    # Phase 4 is a deterministic downstream state build; it does not call providers or create actions.
    phase4 = build_phase4_automation(ROOT)
    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    context.setdefault("research_evidence", {})["market_regime_context"] = market_regime
    context["research_evidence"]["market_structure_context"] = market_structure
    context["research_evidence"]["skfolio_risk_evidence"] = skfolio_summary
    context["research_evidence"]["etf_share_flow_evidence"] = share_flow_summary
    context["research_evidence"]["margin_financing_evidence"] = margin_financing_summary
    context["research_evidence"]["active_return_evidence"] = active_return
    context["research_evidence"]["research_contribution_audit"] = contribution_summary
    context["market_regime_context"] = market_regime
    context["candidate_selection_contract"] = market_structure.get("candidate_selection_contract") or {}
    context["market_regime_context_file"] = "data/state/market_regime_context.json"
    context["market_structure_context_file"] = "data/state/market_structure_context.json"
    context["skfolio_risk_evidence_file"] = "data/state/skfolio_risk_evidence.json"
    context["etf_share_flow_evidence_file"] = "data/state/etf_share_flow_evidence.json"
    context["margin_financing_evidence_file"] = "data/state/margin_financing_evidence.json"
    context["active_return_evidence_file"] = "data/state/active_return_evidence.json"
    context["research_contribution_audit_file"] = "data/state/research_contribution_audit.json"
    context["research_master_candidates"] = research_master
    context["execution_quality"] = execution_quality
    context["formal_decision_research_attribution_contract"] = {
        "field": "research_evidence_used",
        "max_items": 3,
        "item_fields": ["evidence_id", "change", "decision_effect"],
        "rule": "只记录实际改变本次机会、金额、持仓或卖出判断的研究证据；若研究没有实质贡献，显式写空列表。不得自动把decision_context中存在的证据视为已使用。",
    }
    required_context_present = {
        "market_level_analysis": market_regime.get("status") in {"READY", "DEGRADED"} and bool(market_regime.get("indices")),
        "candidate_selection_contract": bool(context.get("candidate_selection_contract")),
        "market_structure_context": market_structure.get("status") == "READY" and bool(market_structure.get("items")),
        "historical_position_and_trend": all(
            (x.get("historical_context") or {}).get("historical_zone")
            and (x.get("historical_context") or {}).get("trend_state")
            for x in (market_structure.get("items") or [])
        ),
        "intraday_path_and_extreme_sequence": all(
            (x.get("intraday_context") or {}).get("morphology")
            and (x.get("intraday_context") or {}).get("extreme_sequence")
            for x in (market_structure.get("items") or [])
        ),
        "time_normalized_turnover_acceptance": all(
            (x.get("turnover_acceptance_context") or {}).get("status") in {"READY", "DEGRADED"}
            for x in (market_structure.get("items") or [])
        ),
    }
    context["formal_intraday_response_contract"] = {
        "schema_version": "1.1",
        "scope": "FORMAL_INTRADAY_DECISION",
        "rule": "正式盘中回复必须先完成市场层分析，再形成账户风险、生命周期和唯一主候选；不得只读取价格、持仓/观察ETF、涨幅或上一轮主候选。",
        "required_before_main_candidate": [
            "先读取market_regime_context，完成上证指数（000001）、创业板指（399006）、ETF市场宽度和风格/风险偏好分析；必要时再读取海外/亚洲反馈",
            "读取candidate_selection_contract",
            "重新统一比较持仓ETF、观察ETF、当前打新底仓与现金，不自动沿用上一轮主候选",
            "对主候选显式解释历史位置与趋势、日内路径与极值时序、时间归一化成交承接、风险收益或下一单位资本效率",
            "横截面涨幅、名次与相对强弱仅作验证证据",
        ],
        "required_market_analysis": [
            "上证指数（000001）当日涨跌、日内位置与路径",
            "创业板指（399006）当日涨跌、日内位置与路径",
            "ETF全集上涨/下跌宽度与中位收益",
            "上证与创业板的风格差异和风险偏好",
            "海外/亚洲信息仅在时点有效且与当前假设相关时使用，并说明是否与A股反馈背离",
        ],
        "required_candidate_evidence": [
            "historical_position_and_trend",
            "intraday_path_and_extreme_sequence",
            "time_normalized_turnover_acceptance",
            "risk_reward_or_capital_efficiency",
        ],
        "missing_evidence_rule": "任一必需证据缺失时必须显式标记MISSING或DEGRADED并说明影响，不得静默跳过后直接维持或生成主候选。",
        "previous_candidate_rule": "previous_main_candidate只作连续性参考，不得自动继承；每个正式盘中节点必须重新进入统一比较。若继续保留上一主候选，必须说明本节点的新结构证据为何独立支持继续保留。",
        "cross_section_rule": "当日涨幅、横截面名次和相对指数强弱只能验证候选，不得产生主候选。",
        "market_analysis_rule": "市场层分析必须先于持仓和候选分析；禁止只围绕持仓ETF和观察ETF写回复而不解释指数、市场宽度及风格环境。",
        "refresh_rule": "EXPLICIT_LATEST/当前/最新行情请求只能在请求后新CURRENT满足requested_market_time后形成正式决策；正式决策data_as_of_beijing不得早于requested_market_time。",
        "submission_expectation": "若formal_decision包含main_candidate，回复/提交应能明确指出已消费market_regime_context、market_structure_context及上一主候选已重新比较；否则按DECISION_CONTEXT_INCOMPLETE处理。",
        "non_blocking": True,
        "decision_boundary": "本契约只约束ChatGPT盘中决策的读取与表达完整性，不生成风险许可、生命周期、机会状态、金额、卖出份额或订单。",
    }
    context["formal_intraday_context_completeness"] = {
        "status": "READY" if all(required_context_present.values()) else "DECISION_CONTEXT_INCOMPLETE",
        "required_context_present": required_context_present,
        "note": "该状态只证明机器上下文已具备市场层+标的层正式盘中分析所需结构，不等于ChatGPT已实际消费；正式回复仍必须遵守formal_intraday_response_contract。",
    }
    atomic_json_write(ROOT / "data" / "state" / "dashboard_update_candidate.json", candidate)
    atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)
    research_execution_summary = build_research_execution_bridge(ROOT)
    print(json.dumps({
        "ok": True,
        "account_fact_status": context["account_fact_status"],
        "intraday_path_status": path_features.get("status"),
        "intraday_path_feature_count": len(path_features.get("features") or []),
        "intraday_path_selected_source": path_selection.get("selected_source"),
        "minute_path_status": path_selection.get("minute_status"),
        "minute_path_coverage_ratio": path_selection.get("minute_coverage_ratio"),
        "minute_path_formal_gate_pass": path_selection.get("minute_formal_gate_pass"),
        "market_regime_status": market_regime.get("status"),
        "market_structure_status": market_structure.get("status"),
        "market_structure_item_count": len(market_structure.get("items") or []),
        "market_structure_history_available_count": sum(1 for x in (market_structure.get("items") or []) if (x.get("historical_context") or {}).get("status") == "READY"),
        "formal_intraday_context_completeness": context["formal_intraday_context_completeness"]["status"],
        "research_status": research.get("status"),
        "research_daily_feature_count": research.get("daily_feature_count", 0),
        "research_relative_strength_count": research.get("relative_strength_count", 0),
        "research_evidence_delta_count": len(evidence_delta.get("items") or []),
        "skfolio_risk_status": skfolio_risk.get("status"),
        "skfolio_use_in_current_decision": skfolio_risk.get("use_in_current_decision", False),
        "etf_share_flow_status": share_flow.get("status"),
        "etf_share_flow_use_in_current_decision": share_flow.get("use_in_current_decision", False),
        "margin_financing_status": margin_financing.get("status"),
        "margin_financing_use_in_current_decision": margin_financing.get("use_in_current_decision", False),
        "active_return_status": active_return.get("status"),
        "active_return_use_in_current_decision": active_return.get("use_in_current_decision", False),
        "research_attribution_explicit_coverage": (contribution_audit.get("decision_attribution") or {}).get("explicit_coverage_ratio", 0),
        "research_attributed_trade_count": (contribution_audit.get("trade_attribution") or {}).get("attributed_trade_count", 0),
        "execution_quality_sample_count": execution_quality.get("execution_cost_sample_count", 0),
        "research_master_candidate_count": len(research_master.get("candidates") or []),
        "research_execution_bridge_status": research_execution_summary.get("mode"),
        "ipo_base_stock_research_count": len(research_execution_summary.get("ipo_base_stock_evidence") or []),
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
