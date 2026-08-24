from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def build(root: Path = ROOT) -> dict:
    decision_dir = root / "events" / "decisions"
    outcome_dir = root / "events" / "research" / "decision_outcomes"
    daily_dir = root / "events" / "research" / "daily_features"

    decisions = [load_json(p, {}) for p in sorted(decision_dir.glob("*.json"))] if decision_dir.exists() else []
    outcomes = [load_json(p, {}) for p in sorted(outcome_dir.glob("*.json"))] if outcome_dir.exists() else []
    daily = [load_json(p, {}) for p in sorted(daily_dir.glob("*.json"))] if daily_dir.exists() else []

    t1 = sum(1 for x in outcomes if (x.get("results") or {}).get("T_plus_1_close_return_pct") is not None)
    t3 = sum(1 for x in outcomes if (x.get("results") or {}).get("T_plus_3_close_return_pct") is not None)
    t5 = sum(1 for x in outcomes if (x.get("results") or {}).get("T_plus_5_close_return_pct") is not None)

    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "RESEARCH_TO_MASTER_MAINTENANCE_FEED",
        "read_only": True,
        "automatic_master_update": False,
        "purpose": "把研究层长期积累组织成MASTER维护输入；不创建隐藏规则、不自动升级统计规律、不产生交易动作。",
        "current_inventory": {
            "daily_feature_days": len([x for x in daily if x.get("market_date")]),
            "formal_decision_events": len([x for x in decisions if x.get("decision_id")]),
            "decision_outcomes": len([x for x in outcomes if x.get("decision_id")]),
            "outcomes_with_T_plus_1": t1,
            "outcomes_with_T_plus_3": t3,
            "outcomes_with_T_plus_5": t5,
        },
        "promotion_gate": [
            "数据质量通过：来源、时点、市场阶段和样本定义可复核",
            "样本充分：不是单次盈利、单次亏损或少量偶然结果",
            "逻辑稳定：跨时段/市场状态后仍具有解释力，不依赖单一最优参数",
            "执行可转化：能够改善现有风险许可、机会判断、金额资金判断、持仓管理或卖出分析，而不是增加平行入口",
            "风险不增加：不会绕过现有MASTER风险边界或自动扩大交易权限",
        ],
        "possible_destinations": {
            "experience": "只有解释价值但尚不足以改变稳定执行方式的研究结论，进入经验库/OBS。",
            "opportunity_basis": "稳定改善机会判断质量的高质量专项研究，可经正式审查吸收到MASTER第6章既有机会判断依据。",
            "rule_optimization": "多个真实CASE反复暴露同类问题，或高质量专项研究证明既有规则存在系统性缺陷时，可经正式维护修改MASTER既有章节。",
        },
        "candidates": [],
        "candidate_rule": "机器只维护证据库存和审查门槛；具体MASTER维护候选必须由ChatGPT基于专项研究或多个CASE形成明确建议后写入，不自动从胜率、排名、单一阈值或相关性生成规则。",
    }


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "research_master_candidates.json", result)
    print(json.dumps({"ok": True, "candidate_count": len(result.get("candidates") or []), **result.get("current_inventory", {})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
