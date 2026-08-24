from __future__ import annotations

import json
import os
from pathlib import Path

from build_intraday_path_features import build as build_intraday_path_features
from build_research_features import build as build_research_features
from state_manager import atomic_json_write, build_dashboard_candidate, build_decision_context


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    path_features = build_intraday_path_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "intraday_path_features.json", path_features)
    research = build_research_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_context.json", {
        **research,
        "read_only": True,
        "decision_boundary": "研究层只提供事实、相对强弱、数据质量和决策结果归因；不得绕过MASTER生成交易动作。",
        "paths": {
            "daily_features": "events/research/daily_features/<market_date>.json",
            "relative_strength": "data/state/relative_strength.json",
            "provider_metrics": "data/state/provider_metrics.json",
            "decision_events": "events/decisions/<decision_id>.json",
            "decision_outcomes": "events/research/decision_outcomes/<decision_id>.json",
        },
    })
    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    context["research_context_file"] = "data/state/research_context.json"
    context["relative_strength_file"] = "data/state/relative_strength.json"
    context["research_boundary"] = "研究层只作为客观证据与复盘输入；交易权限仍仅由MASTER与ChatGPT正式决策链产生。"
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
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
