from __future__ import annotations

import json
from pathlib import Path

try:
    from build_market_structure_context_legacy import ROOT, build as _legacy_build
    from minute_context_production import acceptance_interpretation, build as _minute_build
except ModuleNotFoundError:
    from scripts.build_market_structure_context_legacy import ROOT, build as _legacy_build
    from scripts.minute_context_production import acceptance_interpretation, build as _minute_build


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _daily_rows(root: Path, code: str, market_date: str, limit: int = 25) -> list[dict]:
    rows = []
    base = root / "events/research/daily_features"
    if not base.exists():
        return rows
    for path in sorted(base.glob("*.json"), reverse=True):
        obj = _load_json(path)
        date = str(obj.get("market_date") or "")
        if not date or date > market_date or str(obj.get("quality_status") or "").upper() != "PASS":
            continue
        row = next((x for x in (obj.get("features") or []) if str(x.get("code") or "") == code), None)
        if row and _num(row.get("close")) is not None and _num(row.get("amount")) is not None:
            rows.append({"market_date": date, **row})
            if len(rows) >= limit:
                break
    rows.reverse()
    return rows


def _sma_close(rows: list[dict], end_idx: int, window: int) -> float | None:
    if end_idx < 0:
        return None
    start = end_idx - window + 1
    if start < 0:
        return None
    vals = [_num(x.get("close")) for x in rows[start:end_idx + 1]]
    if any(v is None for v in vals) or len(vals) != window:
        return None
    return sum(vals) / window


def _c2_evidence(root: Path, item: dict, market_date: str) -> dict:
    code = str(item.get("code") or "")
    rows = _daily_rows(root, code, market_date, 25)
    base = {
        "evidence_id": "participation_expansion_with_positive_structure",
        "display_name": "成交参与扩大 × 有效价格结构确认",
        "completed_bar_date": market_date or None,
        "use_as_decision_evidence": False,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "formal_role": "只增强已经存在的突破／真实修复结构，不单独生成风险许可、Trial／Confirm、金额、卖出份额或订单。",
        "pit_boundary": "仅使用完整T日收盘及T日前已存在日线；完整收盘定义不得前视到T日盘中。",
        "calibration_boundary": "1.2倍仅为已审查研究粗分组，不是独立机械交易阈值；正式判断优先解释成交参与是否相对自身常态扩大。",
        "failure_contract": "输入不足或不可验证时降级并省略增强方向，不沿用上一交易日的active状态。",
    }
    if len(rows) < 21 or str(rows[-1].get("market_date") or "") != market_date:
        return {**base, "status": "DEGRADED", "reason": "latest complete daily feature or 20-day prior history unavailable", "enhancement_active": None}

    i = len(rows) - 1
    current = rows[i]
    prior20 = rows[i - 20:i]
    amounts = [_num(x.get("amount")) for x in prior20]
    current_amount = _num(current.get("amount"))
    if current_amount is None or any(v in (None, 0.0) for v in amounts):
        return {**base, "status": "DEGRADED", "reason": "amount history unavailable", "enhancement_active": None}
    avg20 = sum(amounts) / 20.0
    amount_ratio = current_amount / avg20 if avg20 else None

    current_close = _num(current.get("close"))
    prev_close = _num(rows[i - 1].get("close"))
    ma20 = _sma_close(rows, i, 20)
    prev_ma20 = _sma_close(rows, i - 1, 20)
    prior20_closes = [_num(x.get("close")) for x in rows[i - 20:i]]
    if current_close is None or prev_close is None or ma20 is None or prev_ma20 is None or any(v is None for v in prior20_closes):
        return {**base, "status": "DEGRADED", "reason": "price structure history unavailable", "enhancement_active": None}

    breakout20 = current_close > max(prior20_closes)
    true_recovery20 = prev_close < prev_ma20 and current_close >= ma20
    positive_structure = bool(breakout20 or true_recovery20)
    research_group_participation_expanded = bool(amount_ratio is not None and amount_ratio >= 1.2)
    enhancement_active = bool(positive_structure and research_group_participation_expanded)
    structure_type = "20D_BREAKOUT" if breakout20 else ("TRUE_MA20_RECOVERY" if true_recovery20 else "NONE")

    return {
        **base,
        "status": "READY",
        "use_as_decision_evidence": True,
        "completed_bar_date": str(current.get("market_date") or market_date),
        "amount": round(current_amount, 4),
        "prior_20d_average_amount": round(avg20, 4),
        "participation_ratio_vs_prior_20d": round(amount_ratio, 4) if amount_ratio is not None else None,
        "research_group_participation_expanded": research_group_participation_expanded,
        "breakout_20d": breakout20,
        "true_ma20_recovery": true_recovery20,
        "positive_structure": positive_structure,
        "structure_type": structure_type,
        "enhancement_active": enhancement_active,
        "direction": "ENHANCE" if enhancement_active else "NOT_ACTIVE",
        "interpretation_cn": (
            "完整收盘后成交参与扩大且已有突破／真实修复结构，作为结构确认增强证据。"
            if enhancement_active else
            "当前完整收盘未同时满足成交参与扩大与有效结构确认，不激活该增强证据；不形成反向看空。"
        ),
        "source": "events/research/daily_features + market_structure_context",
    }


def _attach_c2(root: Path, base: dict) -> None:
    market_date = str(base.get("market_date") or "")
    ready, degraded, active = [], [], []
    for item in base.get("items") or []:
        evidence = _c2_evidence(root, item, market_date)
        item["participation_structure_confirmation"] = evidence
        code = str(item.get("code") or "")
        if evidence.get("status") == "READY":
            ready.append(code)
            if evidence.get("enhancement_active") is True:
                active.append(code)
        else:
            degraded.append(code)
    base["formal_c2_dynamic_evidence"] = {
        "evidence_id": "participation_expansion_with_positive_structure",
        "display_name": "成交参与扩大 × 有效价格结构确认",
        "status": "READY" if ready and not degraded else ("DEGRADED_WITH_OBJECT_FALLBACK" if ready else "DEGRADED"),
        "completed_bar_date": market_date or None,
        "ready_codes": ready,
        "degraded_codes": degraded,
        "active_codes": active,
        "use_in_current_decision": True,
        "use_as_decision_evidence": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "dynamic_owner": "data/state/market_structure_context.json",
        "research_source": "research/backtests/section6_c2_participation_structure_formal_conversion_review.json",
        "decision_boundary": "完整收盘后只作为已有突破／真实修复结构的确认增强；研究1.2倍分组不升级为独立机械买卖阈值；失败不沿用旧方向。",
    }


def build(root: Path = ROOT) -> dict:
    base = _legacy_build(root)
    _attach_c2(root, base)
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

    base["schema_version"] = "1.3"
    base["mode"] = "UNIFIED_STRUCTURE_WITH_FORMAL_C2_AND_TWO_HORIZON_TURNOVER_CONTEXT"
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
    contract["turnover_rule"] = "成交承接保留两种互补时间尺度：20日时间归一化成交进度回答当日整体活跃度；腾讯1分钟构造的最近10分钟相对前10分钟回答边际参与变化。两者不得机械合成总分，也不得单独产生交易动作。完整收盘后，成交参与扩大只有与已存在的突破／真实修复结构同时出现时才作为C2确认增强。"
    base["candidate_selection_contract"] = contract
    return base


if __name__ == "__main__":
    print(json.dumps(build(ROOT), ensure_ascii=False))
