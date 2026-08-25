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
        "use_in_current_decision": evidence.get("use_in_current_decision", False),
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

    research_master = build_research_master_feedback(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_master_candidates.json", research_master)
    atomic_json_write(ROOT / "data" / "state" / "research_context.json", {
        **research,
        "read_only": True,
        "decision_boundary": "研究层直接向当前决策提供事实、相对强弱、日内路径、证据变化、共同风险与风险侧边际资本信息、经专项验证的ETF份额变化收益增强证据、数据质量、判断/执行归因证据；不得绕过MASTER生成交易动作。",
        "current_decision_use": "研究证据及其相对上一节点的变化必须参与机会判断、统一资本比较、持仓资本效率和必要的正式输出解释；单一排名、单一相对强弱、单一份额变化、单一风险贡献或研究统计不得机械产生动作。",
        "master_feedback": "研究层可形成MASTER维护输入，但只有通过MASTER第8.1正式研究转化机制的高质量专项研究，或多个真实CASE反复暴露的同类问题，才允许正式修改MASTER。",
        "optimization_principle": "不打造完美交易系统；复杂度只有在改善事前收益效率、风险边界、执行质量或复盘学习时才保留。最高目标仍是在可接受风险范围内实现可实现收益最大化。",
        "validated_evidence_summary": {
            "skfolio_risk": skfolio_summary,
            "etf_share_flow": share_flow_summary,
        },
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
            "relative_strength": "data/state/relative_strength.json",
            "evidence_delta": "data/state/research_evidence_delta.json",
            "evidence_history": "events/research/evidence_snapshots/<market_date>/*.json",
            "execution_quality": "data/state/execution_quality.json",
            "skfolio_risk_evidence": "data/state/skfolio_risk_evidence.json",
            "etf_share_flow_evidence": "data/state/etf_share_flow_evidence.json",
            "historical_backfill_status": "data/state/historical_backfill_status.json",
            "provider_metrics": "data/state/provider_metrics.json",
            "decision_events": "events/decisions/<decision_id>.json",
            "decision_outcomes": "events/research/decision_outcomes/<decision_id>.json",
            "master_candidates": "data/state/research_master_candidates.json",
        },
    })

    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    context.setdefault("research_evidence", {})["skfolio_risk_evidence"] = skfolio_summary
    context["research_evidence"]["etf_share_flow_evidence"] = share_flow_summary
    context["skfolio_risk_evidence_file"] = "data/state/skfolio_risk_evidence.json"
    context["etf_share_flow_evidence_file"] = "data/state/etf_share_flow_evidence.json"
    context["research_master_candidates"] = research_master
    context["execution_quality"] = execution_quality
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
        "execution_quality_sample_count": execution_quality.get("execution_cost_sample_count", 0),
        "research_master_candidate_count": len(research_master.get("candidates") or []),
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
