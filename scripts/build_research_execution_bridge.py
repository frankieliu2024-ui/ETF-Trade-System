from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_json
    from build_561980_component_lead_evidence import build as build_561980_component_lead_evidence
    from build_if_ic_basis_evidence import build as build_if_ic_basis_evidence
    from build_300750_oversold_reversal_evidence import build as build_300750_oversold_reversal_evidence
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_json
    from scripts.build_561980_component_lead_evidence import build as build_561980_component_lead_evidence
    from scripts.build_if_ic_basis_evidence import build as build_if_ic_basis_evidence
    from scripts.build_300750_oversold_reversal_evidence import build as build_300750_oversold_reversal_evidence

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUTPUT = ROOT / "data/state/research_execution_summary.json"
STOCK_SIGNAL_CONCLUSION = ROOT / "research/backtests/ipo_base_stock_specific_signal_conclusion.json"


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def _research_conclusion_digest(root: Path, research_context: dict) -> dict:
    validated = research_context.get("validated_evidence_summary") or {}
    runtime = []
    for key, value in validated.items():
        if not isinstance(value, dict):
            continue
        runtime.append({
            "evidence_id": key,
            "status": value.get("status"),
            "use_in_current_decision": bool(value.get("use_in_current_decision", value.get("status") in {"READY", "DEGRADED", "DEGRADED_WITH_OBJECT_FALLBACK"})),
            "decision_eligible": value.get("decision_eligible"),
            "use_as_decision_evidence": bool(value.get("use_in_current_decision", value.get("status") in {"READY", "DEGRADED", "DEGRADED_WITH_OBJECT_FALLBACK"})),
            "can_generate_decision_independently": False,
            "trade_signal": None,
        })

    research_only = []
    execution_eligible = []
    for path in sorted((root / "research/backtests").glob("*.json")):
        obj = read_json(path, {})
        if not isinstance(obj, dict):
            continue
        conclusion = obj.get("conclusion") or obj.get("research_interpretation")
        if not conclusion:
            continue
        item = {
            "source": str(path.relative_to(root)).replace("\\", "/"),
            "mode": obj.get("mode"),
            "research_interpretation": obj.get("research_interpretation"),
            "conclusion": obj.get("conclusion"),
            "decision_eligible": bool(obj.get("decision_eligible", False)),
            "use_as_decision_evidence": bool(obj.get("decision_eligible", False) or obj.get("production_context_integration", False)),
            "can_generate_decision_independently": False,
            "production_context_integration": bool(obj.get("production_context_integration", False)),
            "trade_signal": None,
        }
        if item["decision_eligible"] or item["production_context_integration"]:
            execution_eligible.append(item)
        else:
            research_only.append(item)

    return {
        "rule": "研究结论先归纳为可执行证据摘要，再进入正式判断；研究结论可以改变风险收益、候选比较、持有/卖出、金额与资金来源/去向，但不能单独生成订单或自动交易。静态正式结论负责资格与历史验证，当前状态敏感证据必须读取动态证据对象。",
        "runtime_validated_evidence": runtime,
        "execution_eligible_backtest_conclusions": execution_eligible,
        "research_only_backtest_conclusions": research_only,
        "automatic_promotion": False,
        "semantic_contract": {
            "use_as_decision_evidence": "可进入完整判断",
            "can_generate_decision_independently": "是否可独立形成交易决议；研究证据固定为false",
            "static_conclusion_vs_dynamic_evidence": "静态结论只证明资格；涉及当前触发、增强或削弱的证据必须使用formal_dynamic_evidence中的动态状态，不得沿用研究日的current_*字段",
        },
        "trade_signal": None,
    }


def _stock_specific_signal_map(root: Path) -> dict[str, dict]:
    obj = read_json(root / "research/backtests/ipo_base_stock_specific_signal_conclusion.json", {})
    if not isinstance(obj, dict) or obj.get("mode") != "IPO_BASE_STOCK_SPECIFIC_SIGNAL_FINAL_CONCLUSION":
        return {}
    output = {}
    for stock in obj.get("stocks") or []:
        if not isinstance(stock, dict):
            continue
        code = str(stock.get("code") or "")
        if not code:
            continue
        validated = []
        for signal in stock.get("validated_signals") or []:
            if not isinstance(signal, dict) or not signal.get("decision_eligible"):
                continue
            validated.append({
                "signal_id": signal.get("signal_id"),
                "display_name": signal.get("display_name"),
                "direction": signal.get("direction"),
                "definition": signal.get("definition"),
                "current_completed_bar_match": None,
                "static_current_match_ignored": True,
                "primary_validation": signal.get("primary_validation"),
                "yearly_robustness": signal.get("yearly_robustness"),
                "use_as_decision_evidence": True,
                "can_generate_decision_independently": False,
                "automatic_trade": False,
                "trade_signal": None,
            })
        output[code] = {
            "status": stock.get("validated_stock_specific_signal_status") or ("VALIDATED_RESEARCH_SIGNAL_AVAILABLE" if validated else "NO_STABLE_INCREMENTAL_SIGNAL_AFTER_ROBUSTNESS"),
            "validated_signals": validated,
            "rejected_after_robustness": stock.get("rejected_after_robustness") or [],
            "current_completed_bar_matches": [],
            "source": "research/backtests/ipo_base_stock_specific_signal_conclusion.json",
            "data_cutoff": obj.get("data_cutoff"),
            "static_current_fields_are_historical_audit_only": True,
            "automatic_trade": False,
            "trade_signal": None,
        }
    return output


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    research_context = read_json(root / "data/state/research_context.json", {})
    decision_context = read_json(root / "data/state/decision_context.json", {})
    market_structure = read_json(root / "data/state/market_structure_context.json", {})
    account = read_json(root / "data/state/account_fact.json", {})
    stock_context = read_json(root / "data/state/stock_context.json", {})
    stock_market = read_json(root / "data/state/stock_market_context.json", {})
    stock_signal_map = _stock_specific_signal_map(root)

    component_lead = build_561980_component_lead_evidence(root)
    if_ic_basis = build_if_ic_basis_evidence(root)
    oversold_300750 = build_300750_oversold_reversal_evidence(root)
    intraday_path_risk = market_structure.get("formal_intraday_path_risk_review") or {
        "evidence_id": "intraday_path_risk_review",
        "display_name": "日内路径风险复核",
        "status": "DEGRADED",
        "use_in_current_decision": False,
        "use_as_decision_evidence": False,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "reason": "market_structure_context has no current formal intraday path risk review",
        "dynamic_owner": "data/state/market_structure_context.json",
    }
    atomic_json_write(root / "data/state/561980_component_lead_evidence.json", component_lead)
    atomic_json_write(root / "data/state/if_ic_basis_5d_evidence.json", if_ic_basis)
    atomic_json_write(root / "data/state/300750_oversold_reversal_evidence.json", oversold_300750)

    validated_summary = research_context.setdefault("validated_evidence_summary", {})
    validated_summary["component_lead_561980_3d"] = component_lead
    validated_summary["if_ic_basis_5d"] = if_ic_basis
    validated_summary["ipo_base_stock_oversold_reversal_300750"] = oversold_300750
    validated_summary["intraday_path_risk_review"] = intraday_path_risk
    paths = research_context.setdefault("paths", {})
    paths["component_lead_561980_3d"] = "data/state/561980_component_lead_evidence.json"
    paths["if_ic_basis_5d"] = "data/state/if_ic_basis_5d_evidence.json"
    paths["ipo_base_stock_oversold_reversal_300750"] = "data/state/300750_oversold_reversal_evidence.json"
    paths["intraday_path_risk_review"] = "data/state/market_structure_context.json"

    decision_research = decision_context.setdefault("research_evidence", {})
    decision_research["component_lead_561980_3d"] = component_lead
    decision_research["if_ic_basis_5d"] = if_ic_basis
    decision_research["ipo_base_stock_oversold_reversal_300750"] = oversold_300750
    decision_research["intraday_path_risk_review"] = intraday_path_risk
    decision_context["component_lead_561980_file"] = "data/state/561980_component_lead_evidence.json"
    decision_context["if_ic_basis_5d_file"] = "data/state/if_ic_basis_5d_evidence.json"
    decision_context["ipo_base_stock_oversold_reversal_300750_file"] = "data/state/300750_oversold_reversal_evidence.json"
    decision_context["intraday_path_risk_review_file"] = "data/state/market_structure_context.json"

    dynamic_formal_evidence = {
        "margin_financing": validated_summary.get("margin_financing") or {},
        "opening_residual_561980": validated_summary.get("opening_residual_561980") or {},
        "selling_exhaustion": validated_summary.get("selling_exhaustion") or {},
        "margin_feedback_interaction": validated_summary.get("margin_feedback_interaction") or {},
        "intraday_path_risk_review": intraday_path_risk,
        "if_ic_basis_5d": if_ic_basis,
        "ipo_base_stock_oversold_reversal_300750": oversold_300750,
        "component_lead_561980_3d": component_lead,
    }

    positions = {str(x.get("code") or ""): x for x in (account.get("positions") or []) if isinstance(x, dict) and x.get("code")}
    market_objects = stock_market.get("objects") or {}
    total_asset = _num(account.get("total_asset"))
    ipo_stocks = (((stock_context.get("default_stock_layer") or {}).get("ipo_base_stocks")) or [])

    stock_evidence = []
    total_ipo_value = 0.0
    for item in ipo_stocks:
        code = str(item.get("code") or "")
        if not code:
            continue
        pos = positions.get(code) or {}
        market = market_objects.get(code) or {}
        stock_research = stock_signal_map.get(code) or {}
        dynamic_stock_signal = oversold_300750 if code == "300750" else {}
        current_matches = []
        if code == "300750" and dynamic_stock_signal.get("status") == "READY" and dynamic_stock_signal.get("pattern_match") is True:
            current_matches = ["OVERSOLD_REVERSAL"]
        mv = _num(pos.get("market_value"))
        if mv is None:
            mv = _num(item.get("market_value"))
        if mv is not None:
            total_ipo_value += mv
        stock_evidence.append({
            "display_name": f"{item.get('name') or pos.get('name') or code}（{code}）",
            "code": code,
            "name": item.get("name") or pos.get("name"),
            "role": "IPO_BASE_STOCK",
            "role_status": item.get("role_status") or "CONFIRMED",
            "quantity": _num(pos.get("quantity")) if pos else _num(item.get("quantity")),
            "market_value": _round(mv, 2),
            "account_weight_pct": _round((mv / total_asset * 100.0) if mv is not None and total_asset else None, 2),
            "holding_pnl_pct": _round(_num(pos.get("holding_pnl_pct")), 3),
            "last_price": market.get("close") if market else pos.get("last_price"),
            "change_pct": _round(_num(market.get("change_pct")), 4),
            "as_of_beijing": market.get("as_of_beijing"),
            "quality_status": market.get("quality_status") or "MISSING",
            "use_in_current_decision": True,
            "research_scope": "CURRENT_FACT_CAPITAL_ROLE_AND_MATCHED_VALIDATED_RESEARCH",
            "validated_stock_specific_signal_status": stock_research.get("status") or ("NO_MATCHED_VALIDATED_SIGNAL" if not research_context.get("ipo_base_stock_research") else "SEE_RESEARCH_CONTEXT"),
            "validated_stock_specific_signals": stock_research.get("validated_signals") or [],
            "dynamic_stock_specific_signal": dynamic_stock_signal,
            "rejected_stock_specific_signals_after_robustness": stock_research.get("rejected_after_robustness") or [],
            "stock_specific_signal_current_completed_bar_matches": current_matches,
            "stock_specific_research_source": stock_research.get("source"),
            "stock_specific_research_data_cutoff": stock_research.get("data_cutoff"),
            "decision_effects_allowed": ["持有价值和风险收益复核", "是否作为新增资本候选", "是否作为ETF或其他机会的可释放资金来源", "卖出后资金进入现金还是独立成立的新机会"],
            "automatic_trade": False,
            "trade_signal": None,
        })

    digest = _research_conclusion_digest(root, research_context)
    portfolio = {
        "ipo_base_stock_market_value": _round(total_ipo_value, 2),
        "ipo_base_stock_share_of_total_asset_pct": _round((total_ipo_value / total_asset * 100.0) if total_asset else None, 2),
        "ipo_base_stock_count": len(stock_evidence),
        "account_total_asset": _round(total_asset, 2),
    }
    summary = {
        "schema_version": "1.2",
        "generated_at": now_utc(),
        "mode": "RESEARCH_TO_EXECUTION_READ_ONLY_BRIDGE",
        "read_only": True,
        "use_in_current_decision": True,
        "decision_output_generated": False,
        "automatic_trade": False,
        "trade_signal": None,
        "research_conclusions_must_be_synthesized": True,
        "conclusion_digest": digest,
        "formal_dynamic_evidence": dynamic_formal_evidence,
        "portfolio_exposure": portfolio,
        "ipo_base_stock_evidence": stock_evidence,
        "etf_object_evidence": {
            "component_lead_561980_3d": component_lead,
            "intraday_path_risk_review": intraday_path_risk,
        },
        "execution_bridge": {
            "direct_execution_contribution": True,
            "allowed_effects": ["改变候选比较和机会强弱", "改变持仓风险收益和卖出判断", "改变新增金额判断", "改变资金来源选择", "改变卖出资金去向选择"],
            "intraday_path_risk_rule": "日内路径风险复核可提高继续持有、追加资本和部分资本释放的风险收益复核强度；不得独立生成风险许可、Trial/Confirm、金额、卖出份额、退出或订单。",
            "strong_surface_rule": "高开守住、午后再加速、高位横住、突破后一定时间仍守住等强势表象只能作为结构/承接证据的一部分，不能独立升级Trial/Confirm或增加金额。",
            "v_recovery_rule": "V形修复仅表示相对继续走弱的风险/承接改善，不构成独立正收益买入证据。",
            "relative_strength_rule": "日内相对强弱扩大可用于候选比较，但不得机械买强卖弱或独立形成新增金额。",
            "stock_buy_capital_source_rule": "若打新底仓个股出现独立、可证伪且足以改变决策的明显新增证据，必须比较现金、低效率ETF及其他合法可释放资本；现金不是机械唯一来源，任何ETF卖出仍需独立通过卖出链。",
            "stock_sell_destination_rule": "若打新底仓个股出现足以支持降低风险或退出的证据，必须比较资金进入现金还是已经独立通过完整机会判断的ETF/其他合法机会；不得先卖出再寻找用途，也不得机械轮动。",
            "etf_buy_source_rule": "ETF机会成立而现金不足时，打新底仓只有在其自身继续持有的边际收益效率更低、账户打新/底仓功能不被破坏且卖出决议独立成立时，才可作为资金来源。",
            "separate_decisions_rule": "旧资产卖出与新资产买入始终分别决议；研究层只改变证据和比较结果，不自动联动两笔交易。",
        },
        "decision_boundary": "研究结论可直接贡献到执行判断，但不自动生成风险许可、Trial/Confirm、金额、卖出份额或订单；最终动作仍由MASTER完整买入/卖出链和用户人工下单决定。",
    }

    research_context["research_execution_summary"] = summary
    research_context["formal_dynamic_evidence"] = dynamic_formal_evidence
    research_context["ipo_base_stock_research"] = {
        "status": "READY" if stock_signal_map else "NO_VALIDATED_STOCK_SPECIFIC_RESEARCH",
        "source": "research/backtests/ipo_base_stock_specific_signal_conclusion.json" if stock_signal_map else None,
        "stocks": stock_signal_map,
        "automatic_trade": False,
        "trade_signal": None,
    }
    research_context["ipo_base_stock_research_coverage"] = {
        "status": "READY" if stock_evidence else "NO_CURRENT_IPO_BASE_STOCK",
        "object_count": len(stock_evidence),
        "rule": "研究层覆盖当前已确认打新底仓；有匹配的验证研究时与当前行情/账户事实合并，没有匹配研究时不得伪造买卖信号。当前触发状态必须来自动态证据，不得沿用静态研究结论中的历史current_*字段。",
    }
    decision_context.setdefault("research_evidence", {})["research_execution_summary"] = summary
    decision_context["formal_dynamic_research_evidence"] = dynamic_formal_evidence
    decision_context["ipo_base_stock_evidence"] = stock_evidence
    decision_context["research_execution_summary_file"] = "data/state/research_execution_summary.json"
    decision_context["unified_capital_reallocation_contract"] = summary["execution_bridge"]

    atomic_json_write(root / "data/state/research_context.json", research_context)
    atomic_json_write(root / "data/state/decision_context.json", decision_context)
    atomic_json_write(OUTPUT, summary)
    return summary


def main() -> None:
    summary = build(ROOT)
    component = (summary.get("etf_object_evidence") or {}).get("component_lead_561980_3d") or {}
    dynamic = summary.get("formal_dynamic_evidence") or {}
    print(json.dumps({
        "ok": True,
        "mode": summary.get("mode"),
        "ipo_base_stock_count": len(summary.get("ipo_base_stock_evidence") or []),
        "ipo_base_stock_share_of_total_asset_pct": (summary.get("portfolio_exposure") or {}).get("ipo_base_stock_share_of_total_asset_pct"),
        "component_lead_561980_status": component.get("status"),
        "component_lead_561980_value": component.get("leader_minus_etf_3d_lag1_pct_points"),
        "intraday_path_risk_status": (dynamic.get("intraday_path_risk_review") or {}).get("status"),
        "intraday_path_risk_active_codes": (dynamic.get("intraday_path_risk_review") or {}).get("active_codes"),
        "if_ic_basis_status": (dynamic.get("if_ic_basis_5d") or {}).get("status"),
        "oversold_300750_status": (dynamic.get("ipo_base_stock_oversold_reversal_300750") or {}).get("status"),
        "oversold_300750_match": (dynamic.get("ipo_base_stock_oversold_reversal_300750") or {}).get("pattern_match"),
        "automatic_trade": summary.get("automatic_trade"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
