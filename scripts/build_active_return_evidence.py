from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(root: Path) -> dict:
    path = root / "research" / "backtests" / "active_return_stage1_validation.json"
    if not path.exists():
        return {
            "status": "NOT_AVAILABLE",
            "mode": "VALIDATED_ACTIVE_RETURN_METHOD_EVIDENCE",
            "use_in_current_decision": False,
            "decision_eligible": False,
            "trade_signal": None,
        }
    result = load_json(path)
    conclusions = result.get("conclusions") or {}
    mig = result.get("active_migration_spread") or {}
    tail = result.get("right_tail_holding") or {}
    leader = result.get("leadership_continuation") or {}
    passing = set(result.get("passing_hypotheses") or [])

    def horizon_view(block: dict, h: str) -> dict:
        x = block.get(h) or {}
        return {
            "n": x.get("n"),
            "mean": x.get("mean"),
            "median": x.get("median"),
            "positive_rate": x.get("positive_rate"),
            "positive_folds": x.get("positive_folds_with_n10") if "positive_folds_with_n10" in x else x.get("positive_folds_with_n8"),
        }

    migration_cost20 = {}
    for h in ("10", "20"):
        x = mig.get(h) or {}
        migration_cost20[h] = {
            **horizon_view(mig, h),
            "mean_net_spread_after_20bps": ((x.get("cost_scenarios_bps_round_trip") or {}).get("20") or {}).get("mean_net_spread"),
            "folds": x.get("folds") or {},
        }

    right_tail = {h: {**horizon_view(tail, h), "folds": (tail.get(h) or {}).get("folds") or {}} for h in ("3", "5", "10", "20")}

    return {
        "status": "READY" if passing else "NO_VALIDATED_INCREMENT",
        "mode": "VALIDATED_ACTIVE_RETURN_METHOD_EVIDENCE",
        "use_in_current_decision": bool(passing),
        "validated_reference": {
            "research_id": result.get("research_id"),
            "generated_at_beijing": result.get("generated_at_beijing"),
            "path": "research/backtests/active_return_stage1_validation.json",
            "daily_feature_days": (result.get("data_quality") or {}).get("daily_feature_days"),
            "point_in_time": (result.get("data_quality") or {}).get("point_in_time"),
        },
        "leadership_continuation": {
            "status": (conclusions.get("leadership_continuation") or {}).get("status"),
            "interpretation": "单纯处于5日和20日横截面强势前列，没有形成足够稳定的跨阶段增量；排名本身不得作为买入或加仓理由。",
            "selected_horizons": {h: horizon_view(leader, h) for h in ("5", "10", "20")},
        },
        "active_migration_spread": {
            "status": (conclusions.get("active_migration_spread") or {}).get("status"),
            "passing_horizons": (conclusions.get("active_migration_spread") or {}).get("passing_horizons") or [],
            "selected_horizons": migration_cost20,
            "interpretation": "当旧仓已经按独立证据弱化、且新机会已经独立通过完整买入链时，强候选相对弱候选的机会成本差值得明确比较；历史10日/20日跨度在20bps成本情景后仍为正，但2024样本为负，因此不得机械买强卖弱或按排名轮动。",
        },
        "right_tail_holding": {
            "status": (conclusions.get("right_tail_holding") or {}).get("status"),
            "passing_horizons": (conclusions.get("right_tail_holding") or {}).get("passing_horizons") or [],
            "selected_horizons": right_tail,
            "interpretation": "已经处于强趋势且创20日收盘新高的ETF，历史上仍保留正的后续相对收益；创新高本身不是止盈或减仓理由。若原假设、结构和新增资本效率仍有效，应优先保护右尾；但2024部分中长周期为负，不得把创新高反过来机械定义为买点。",
        },
        "regime_note": "两个通过结论都存在阶段差异：2025–2026明显强于2024。正式决策必须结合当前结构、风险收益、资金证据和替代机会，不把历史均值当成固定阈值。",
        "decision_effect_contract": {
            "allowed": [
                "增强或削弱旧仓继续占用资本的机会成本判断",
                "增强保护强势持仓右尾的理由",
                "在候选已独立成立后辅助比较下一单位资本用途"
            ],
            "prohibited": [
                "按横截面排名机械买强卖弱",
                "仅因创新高买入或加仓",
                "直接生成Trial、Confirm、金额、卖出或风险许可"
            ]
        },
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
    }
