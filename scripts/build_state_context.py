from __future__ import annotations

import json
import os
from pathlib import Path

from build_intraday_path_features import build as build_intraday_path_features
from build_research_features import build as build_research_features
from build_research_evidence_delta import build as build_research_evidence_delta
from build_research_master_feedback import build as build_research_master_feedback
from build_execution_quality import build as build_execution_quality
from build_skfolio_risk_evidence import build as build_skfolio_risk_evidence
from build_etf_share_flow_evidence import build as build_etf_share_flow_evidence
from build_margin_financing_evidence import build as build_margin_financing_evidence
from build_active_return_evidence import build as build_active_return_evidence
from build_research_contribution_audit import build as build_research_contribution_audit
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


def main() -> None:
    path_features = build_intraday_path_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "intraday_path_features.json", path_features)

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
        "decision_boundary": "研究层向当前决策提供事实、相对强弱、日内路径、证据变化、共同风险、经验证的ETF份额、融资杠杆与主动收益方法证据，并记录研究是否真实改变决策；不得绕过MASTER生成交易动作。",
        "current_decision_use": "研究证据及其相对上一节点变化参与机会、金额、持仓和卖出判断；正式决策只记录最多3项真正改变判断的research_evidence_used。没有改变判断的研究不记贡献，缺少显式记录不得自动推断贡献。",
        "master_feedback": "研究层可形成MASTER维护输入，但只有通过MASTER第8.1正式研究转化机制的高质量专项研究，或多个真实CASE反复暴露的同类问题，才允许正式修改MASTER。",
        "optimization_principle": "不打造完美交易系统；复杂度只有在改善事前收益效率、风险边界、执行质量或复盘学习时才保留。长期未改变任何正式决策且无独立风险/复盘价值的研究模块才进入删除审查。",
        "validated_evidence_summary": {
            "skfolio_risk": skfolio_summary,
            "etf_share_flow": share_flow_summary,
            "margin_financing": margin_financing_summary,
            "active_return": active_return,
        },
        "research_contribution_audit": contribution_summary,
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
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
    context.setdefault("research_evidence", {})["skfolio_risk_evidence"] = skfolio_summary
    context["research_evidence"]["etf_share_flow_evidence"] = share_flow_summary
    context["research_evidence"]["margin_financing_evidence"] = margin_financing_summary
    context["research_evidence"]["active_return_evidence"] = active_return
    context["research_evidence"]["research_contribution_audit"] = contribution_summary
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
    atomic_json_write(ROOT / "data" / "state" / "dashboard_update_candidate.json", candidate)
    atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)
    print(json.dumps({
        "ok": True,
        "account_fact_status": context["account_fact_status"],
        "intraday_path_status": path_features.get("status"),
        "intraday_path_feature_count": len(path_features.get("features") or []),
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
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
