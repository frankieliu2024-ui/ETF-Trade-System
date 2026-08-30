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
        day = str(obj.get("market_date") or "")
        if not day or day > market_date or str(obj.get("quality_status") or "").upper() != "PASS":
            continue
        row = next((x for x in (obj.get("features") or []) if str(x.get("code") or "") == code), None)
        if row and _num(row.get("close")) is not None and _num(row.get("amount")) is not None:
            rows.append({"market_date": day, **row})
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
        "decision_market_date": market_date or None,
        "completed_bar_date": None,
        "use_as_decision_evidence": False,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "formal_role": "只增强已经存在的突破／真实修复结构，不单独生成风险许可、Trial／Confirm、金额、卖出份额或订单。",
        "pit_boundary": "使用不晚于决策时点的最近完整T日收盘及T日前已存在日线；T+1盘中继续使用T日已完成证据，不把T+1未完成日线前视为收盘事实。",
        "calibration_boundary": "1.2倍仅为已审查研究粗分组，不是独立机械交易阈值；正式判断优先解释成交参与是否相对自身常态扩大。放量上涨后的弱收盘位置本身不得机械解释为派发或应快速减仓。",
        "failure_contract": "输入不足或不可验证时降级并省略增强方向，不沿用无法重新构造的旧active状态。",
    }
    if len(rows) < 21:
        return {**base, "status": "DEGRADED", "reason": "latest completed daily feature or 20-day prior history unavailable", "enhancement_active": None}

    i = len(rows) - 1
    current = rows[i]
    completed_bar_date = str(current.get("market_date") or "")
    prior20 = rows[i - 20:i]
    amounts = [_num(x.get("amount")) for x in prior20]
    current_amount = _num(current.get("amount"))
    if current_amount is None or any(v in (None, 0.0) for v in amounts):
        return {**base, "completed_bar_date": completed_bar_date or None, "status": "DEGRADED", "reason": "amount history unavailable", "enhancement_active": None}
    avg20 = sum(amounts) / 20.0
    amount_ratio = current_amount / avg20 if avg20 else None

    current_close = _num(current.get("close"))
    prev_close = _num(rows[i - 1].get("close"))
    ma20 = _sma_close(rows, i, 20)
    prev_ma20 = _sma_close(rows, i - 1, 20)
    prior20_closes = [_num(x.get("close")) for x in rows[i - 20:i]]
    if current_close is None or prev_close is None or ma20 is None or prev_ma20 is None or any(v is None for v in prior20_closes):
        return {**base, "completed_bar_date": completed_bar_date or None, "status": "DEGRADED", "reason": "price structure history unavailable", "enhancement_active": None}

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
        "completed_bar_date": completed_bar_date,
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
            "最近完整收盘的成交参与扩大且已有突破／真实修复结构，作为结构确认增强证据；若当日上涨但收盘位置偏弱，不得仅据此解释为派发或机械减仓。"
            if enhancement_active else
            "最近完整收盘未同时满足成交参与扩大与有效结构确认，不激活该增强证据；不形成反向看空。"
        ),
        "source": "events/research/daily_features + market_structure_context",
    }


def _attach_c2(root: Path, base: dict) -> None:
    market_date = str(base.get("market_date") or "")
    ready, degraded, active, completed_dates = [], [], [], []
    for item in base.get("items") or []:
        evidence = _c2_evidence(root, item, market_date)
        item["participation_structure_confirmation"] = evidence
        code = str(item.get("code") or "")
        if evidence.get("status") == "READY":
            ready.append(code)
            if evidence.get("completed_bar_date"):
                completed_dates.append(str(evidence.get("completed_bar_date")))
            if evidence.get("enhancement_active") is True:
                active.append(code)
        else:
            degraded.append(code)
    base["formal_c2_dynamic_evidence"] = {
        "evidence_id": "participation_expansion_with_positive_structure",
        "display_name": "成交参与扩大 × 有效价格结构确认",
        "status": "READY" if ready and not degraded else ("DEGRADED_WITH_OBJECT_FALLBACK" if ready else "DEGRADED"),
        "decision_market_date": market_date or None,
        "latest_completed_bar_date": max(completed_dates) if completed_dates else None,
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
        "decision_boundary": "最近完整收盘只作为已有突破／真实修复结构的确认增强；研究1.2倍分组不升级为独立机械买卖阈值；放量上涨后的弱收盘本身不视为派发或卖出条件；失败不沿用无法重建的旧方向。",
    }


def _path_risk_evidence(item: dict, path: dict, market_date: str) -> dict:
    code = str(item.get("code") or "")
    intraday = item.get("intraday_context") or {}
    sampling = path.get("sampling") or {}
    coverage = str(sampling.get("coverage") or "").upper()
    production_source = str(path.get("production_source") or "")
    late = path.get("late_session_context") or {}
    tail_move = _num(late.get("move_from_reference_pct"))
    path_high = _num(path.get("path_high"))
    latest_price = _num(item.get("price"))
    current_return = _num(item.get("current_return_pct"))
    prev_close = None
    if latest_price is not None and current_return is not None and abs(1.0 + current_return / 100.0) > 1e-12:
        prev_close = latest_price / (1.0 + current_return / 100.0)
    high_return = ((path_high / prev_close - 1.0) * 100.0) if path_high is not None and prev_close not in (None, 0.0) else None
    retreat = _num(path.get("retreat_from_path_high_pct"))
    day_position = _num(intraday.get("day_range_position"))
    high_time = str(path.get("path_high_as_of_beijing") or "")
    latest_time = str(path.get("latest_as_of_beijing") or item.get("as_of_beijing") or "")
    high_precedes_latest = bool(high_time and latest_time and high_time < latest_time)

    path_quality_usable = bool(path and coverage in {"HIGH", "MEDIUM"})
    if not path_quality_usable:
        return {
            "evidence_id": "intraday_path_risk_review",
            "display_name": "日内路径风险复核",
            "status": "DEGRADED",
            "code": code,
            "market_date": market_date or None,
            "use_as_decision_evidence": False,
            "can_generate_decision_independently": False,
            "automatic_trade": False,
            "trade_signal": None,
            "risk_review_active": None,
            "reason": "intraday path sampling unavailable or LOW",
            "dynamic_owner": "data/state/market_structure_context.json",
            "upstream_owner": "data/state/intraday_path_features.json",
            "failure_contract": "路径质量不足时不沿用上一节点active状态，只保留DEGRADED。",
        }

    # These are the fixed research group boundaries used in #107 robustness checks.
    # They classify a review state only and never become mechanical trading thresholds.
    tail_rally_group = bool(late.get("status") == "READY" and tail_move is not None and tail_move >= 0.8)
    spike_reversal_group = bool(
        high_precedes_latest
        and high_return is not None and high_return >= 2.0
        and retreat is not None and retreat <= -1.5
        and day_position is not None and day_position <= 0.35
    )
    active_components = []
    if tail_rally_group:
        active_components.append("LATE_SESSION_RALLY_REVIEW")
    if spike_reversal_group:
        active_components.append("SPIKE_REVERSAL_NO_RECLAIM_REVIEW")
    active = bool(active_components)
    confidence = "HIGH" if production_source == "TENCENT_1M" and coverage == "HIGH" else "MEDIUM"
    return {
        "evidence_id": "intraday_path_risk_review",
        "display_name": "日内路径风险复核",
        "status": "READY",
        "code": code,
        "market_date": market_date or None,
        "as_of_beijing": latest_time or None,
        "use_as_decision_evidence": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "risk_review_active": active,
        "direction": "INCREASE_RISK_REWARD_REVIEW" if active else "NOT_ACTIVE",
        "active_components": active_components,
        "confidence": confidence,
        "production_source": production_source or "UNKNOWN",
        "sampling_coverage": coverage,
        "late_session": late,
        "path_high_return_vs_prev_close_pct": round(high_return, 4) if high_return is not None else None,
        "retreat_from_path_high_pct": round(retreat, 4) if retreat is not None else None,
        "day_range_position": round(day_position, 4) if day_position is not None else None,
        "research_group_flags": {
            "late_session_rally_ge_0_8pct": tail_rally_group,
            "spike_ge_2pct_retreat_le_minus1_5pct_low_close_position": spike_reversal_group,
        },
        "interpretation_cn": (
            "命中已验证的日内路径风险复核分组，应提高对继续持有、追加资本、赢家右尾与部分资本释放的风险收益复核强度；不得由该证据机械卖出、固定比例减仓、退出或禁止新增。"
            if active else
            "当前未命中已验证的日内路径风险复核分组；不形成反向看多，也不降低其他风险证据权重。"
        ),
        "calibration_boundary": "0.8%、2%、-1.5%和低区间位置仅是#107固定研究粗分组，用于识别复核状态，不是独立交易阈值。",
        "strong_surface_boundary": "高开守住、午后再加速、高位横住或突破后一定时间仍守住，只能作为结构/承接的一部分，不得独立升级Trial/Confirm或增加金额。",
        "v_recovery_boundary": "V形修复只表示相对继续走弱的风险/承接改善，不证明正收益机会，不得据此抄底或升级Trial/Confirm。",
        "relative_strength_boundary": "日内相对强弱扩大只用于候选比较和相对保护解释，不得机械买强卖弱。",
        "dynamic_owner": "data/state/market_structure_context.json",
        "upstream_owner": "data/state/intraday_path_features.json",
        "research_source": "research/backtests/ashare_ancestral_capital_evidence_formal_conversion.json",
        "failure_contract": "上游路径缺失、LOW采样或对象级分钟质量失败时按现有离散fallback重新计算；仍不足则DEGRADED且不沿用旧active状态。",
    }


def _attach_intraday_path_risk_review(root: Path, base: dict) -> None:
    path_obj = _load_json(root / "data/state/intraday_path_features.json")
    path_map = {str(x.get("symbol") or ""): x for x in (path_obj.get("features") or []) if x.get("symbol")}
    market_date = str(base.get("market_date") or "")
    ready, degraded, active = [], [], []
    for item in base.get("items") or []:
        code = str(item.get("code") or "")
        evidence = _path_risk_evidence(item, path_map.get(code) or {}, market_date)
        item["intraday_path_risk_review"] = evidence
        if evidence.get("status") == "READY":
            ready.append(code)
            if evidence.get("risk_review_active") is True:
                active.append(code)
        else:
            degraded.append(code)
    base["formal_intraday_path_risk_review"] = {
        "evidence_id": "intraday_path_risk_review",
        "display_name": "日内路径风险复核",
        "status": "READY" if ready and not degraded else ("DEGRADED_WITH_OBJECT_FALLBACK" if ready else "DEGRADED"),
        "market_date": market_date or None,
        "ready_codes": ready,
        "degraded_codes": degraded,
        "active_codes": active,
        "use_in_current_decision": True,
        "use_as_decision_evidence": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "dynamic_owner": "data/state/market_structure_context.json",
        "upstream_owner": "data/state/intraday_path_features.json",
        "research_source": "research/backtests/ashare_ancestral_capital_evidence_formal_conversion.json",
        "decision_boundary": "尾盘拉升与冲高回落未重新站回只提高持仓、追加资本和资本释放的风险收益复核强度；不独立生成风险许可、Trial/Confirm、金额、卖出份额或订单。",
        "failure_contract": "对象级路径不可用时复用现有分钟→离散fallback；仍无法重建则该对象DEGRADED且不沿用旧方向。",
    }
    contract = base.get("candidate_selection_contract") or {}
    contract["strong_surface_rule"] = "高开守住、午后再加速、高位横住、突破后一定时间仍守住等强势表象只能作为结构/承接证据的一部分，不能独立升级Trial/Confirm或增加金额。"
    contract["v_recovery_rule"] = "V形修复只说明相对继续走弱有所改善，不是独立正收益买入证据；不得看到V形就抄底或自动升级Trial/Confirm。"
    contract["relative_strength_rule"] = "日内相对强弱扩大可改善候选比较，但不得机械买强卖弱，也不能单独形成新增资本金额。"
    base["candidate_selection_contract"] = contract


def build(root: Path = ROOT) -> dict:
    base = _legacy_build(root)
    _attach_c2(root, base)
    _attach_intraday_path_risk_review(root, base)
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

    base["schema_version"] = "1.4"
    base["mode"] = "UNIFIED_STRUCTURE_WITH_FORMAL_C2_INTRADAY_RISK_AND_TWO_HORIZON_TURNOVER_CONTEXT"
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
    contract["turnover_rule"] = "成交承接保留两种互补时间尺度：20日时间归一化成交进度回答当日整体活跃度；腾讯1分钟构造的最近10分钟相对前10分钟回答边际参与变化。两者不得机械合成总分，也不得单独产生交易动作。最近完整收盘的成交参与扩大只有与已存在的突破／真实修复结构同时出现时才作为C2确认增强；放量上涨后的弱收盘本身不得机械解释为派发。"
    base["candidate_selection_contract"] = contract
    return base


if __name__ == "__main__":
    print(json.dumps(build(ROOT), ensure_ascii=False))
