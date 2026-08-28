from __future__ import annotations

import json
from pathlib import Path

try:
    from build_market_structure_context_legacy import ROOT, build as _legacy_build
    from minute_context_production import acceptance_interpretation, build as _minute_build
except ModuleNotFoundError:
    from scripts.build_market_structure_context_legacy import ROOT, build as _legacy_build
    from scripts.minute_context_production import acceptance_interpretation, build as _minute_build


def build(root: Path = ROOT) -> dict:
    base = _legacy_build(root)
    try:
        minute = _minute_build(root)
    except Exception as exc:
        base["minute_acceptance_integration"] = {
            "status": "FALLBACK",
            "target_count": len(base.get("items") or []),
            "ready_count": 0,
            "error": str(exc)[-500:],
            "base_turnover_logic_unchanged": True,
        }
        return base

    minute_map = {str(x.get("symbol")): (x.get("evidence") or {}) for x in (minute.get("acceptance_items") or [])}
    ready_codes, fallback_codes = [], []
    for item in base.get("items") or []:
        code = str(item.get("code") or "")
        evidence = minute_map.get(code) or {}
        turnover = item.get("turnover_acceptance_context") or {}
        if evidence.get("status") == "READY":
            recent = evidence.get("recent_10m") or {}
            prior = evidence.get("prior_10m") or {}
            comp = evidence.get("comparison") or {}
            turnover["recent_10m_marginal_acceptance"] = {
                "status": "READY",
                "window_end_beijing": evidence.get("window_end_beijing"),
                "recent_10m_price_change_pct": recent.get("price_change_pct"),
                "recent_10m_amount_delta": recent.get("amount_delta"),
                "recent_10m_volume_delta": recent.get("volume_delta"),
                "prior_10m_price_change_pct": prior.get("price_change_pct"),
                "prior_10m_amount_delta": prior.get("amount_delta"),
                "prior_10m_volume_delta": prior.get("volume_delta"),
                "amount_expansion_ratio": comp.get("amount_expansion_ratio"),
                "volume_expansion_ratio": comp.get("volume_expansion_ratio"),
                "amount_relation": comp.get("amount_relation"),
                "recent_price_direction": comp.get("recent_price_direction"),
                "interpretation_cn": acceptance_interpretation(evidence),
                "source": "tencent_qq minute/query",
                "role": "SECOND_HORIZON_MARGINAL_ACCEPTANCE_EVIDENCE",
                "decision_boundary": evidence.get("interpretation_boundary"),
            }
            item["turnover_acceptance_context"] = turnover
            item["combined_interpretation_cn"] = (str(item.get("combined_interpretation_cn") or "").rstrip("。") + "；" + acceptance_interpretation(evidence)).replace("；。", "；")
            ready_codes.append(code)
        else:
            turnover["recent_10m_marginal_acceptance"] = {
                "status": evidence.get("status") or "MISSING",
                "interpretation_cn": "分钟级边际承接不可用，继续使用原20日时间归一化成交承接。",
                "role": "FALLBACK_TO_BASE_TURNOVER_CONTEXT",
            }
            item["turnover_acceptance_context"] = turnover
            fallback_codes.append(code)

    base["schema_version"] = "1.2"
    base["mode"] = "HISTORICAL_TREND_PLUS_INTRADAY_SEQUENCE_PLUS_TWO_HORIZON_TURNOVER_CONTEXT"
    base["minute_acceptance_integration"] = {
        "status": "READY" if not fallback_codes and ready_codes else "DEGRADED_WITH_OBJECT_FALLBACK",
        "target_count": len(base.get("items") or []),
        "ready_count": len(ready_codes),
        "ready_codes": ready_codes,
        "fallback_codes": fallback_codes,
        "base_20d_time_normalized_turnover_preserved": True,
        "minute_10m_role": "最近10分钟 vs 紧邻前10分钟，用于描述资金参与边际增强/减弱",
        "no_composite_score": True,
        "no_trade_signal": True,
        "decision_boundary": minute.get("decision_boundary"),
    }
    contract = base.get("candidate_selection_contract") or {}
    contract["turnover_rule"] = "成交承接保留两种互补时间尺度：20日时间归一化成交进度回答当日整体活跃度；腾讯1分钟构造的最近10分钟相对前10分钟回答边际参与变化。两者不得机械合成总分，也不得单独产生交易动作。"
    base["candidate_selection_contract"] = contract
    return base


if __name__ == "__main__":
    print(json.dumps(build(ROOT), ensure_ascii=False))
