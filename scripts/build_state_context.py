from __future__ import annotations

import json
import os
from pathlib import Path

from build_intraday_path_features import build as build_intraday_path_features
from build_research_features import build as build_research_features
from build_research_master_feedback import build as build_research_master_feedback
from state_manager import atomic_json_write, build_dashboard_candidate, build_decision_context


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    path_features = build_intraday_path_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "intraday_path_features.json", path_features)

    research = build_research_features(ROOT)
    research_master = build_research_master_feedback(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_master_candidates.json", research_master)
    atomic_json_write(ROOT / "data" / "state" / "research_context.json", {
        **research,
        "read_only": True,
        "decision_boundary": "研究层直接向当前决策提供事实、相对强弱、日内路径、数据质量和结果归因证据；不得绕过MASTER生成交易动作。",
        "current_decision_use": "研究证据必须参与机会判断、统一资本比较、持仓资本效率和必要的正式输出解释；单一排名、单一相对强弱或研究统计不得机械产生动作。",
        "master_feedback": "研究层可形成MASTER维护输入，但只有通过MASTER第8.1正式研究转化机制的高质量专项研究，或多个真实CASE反复暴露的同类问题，才允许正式修改MASTER。",
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
            "relative_strength": "data/state/relative_strength.json",
            "provider_metrics": "data/state/provider_metrics.json",
            "decision_events": "events/decisions/<decision_id>.json",
            "decision_outcomes": "events/research/decision_outcomes/<decision_id>.json",
            "master_candidates": "data/state/research_master_candidates.json",
        },
    })

    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    context["research_master_candidates"] = research_master
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
        "research_master_candidate_count": len(research_master.get("candidates") or []),
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
