from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_json

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
            "use_in_current_decision": bool(value.get("use_in_current_decision", value.get("status") in {"READY", "DEGRADED"})),
            "decision_eligible": value.get("decision_eligible"),
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
            "production_context_integration": bool(obj.get("production_context_integration", False)),
            "trade_signal": None,
        }
        if item["decision_eligible"] or item["production_context_integration"]:
            execution_eligible.append(item)
        else:
            research_only.append(item)

    return {
        "rule": "研究结论先归纳为可执行证据摘要，再进入正式判断；研究结论可以改变风险收益、候选比较、持有/卖出、金额与资金来源/去向，但不能单独生成订单或自动交易。",
        "runtime_validated_evidence": runtime,
        "execution_eligible_backtest_conclusions": execution_eligible,
        "research_only_backtest_conclusions": research_only,
        "automatic_promotion": False,
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
                "current_completed_bar_match": bool(signal.get("current_completed_bar_match", False)),
                "primary_validation": signal.get("primary_validation"),
                "yearly_robustness": signal.get("yearly_robustness"),
                "automatic_trade": False,
                "trade_signal": None,
            })
        output[code] = {
            "status": stock.get("validated_stock_specific_signal_status") or ("VALIDATED_RESEARCH_SIGNAL_AVAILABLE" if validated else "NO_STABLE_INCREMENTAL_SIGNAL_AFTER_ROBUSTNESS"),
            "validated_signals": validated,
            "rejected_after_robustness": stock.get("rejected_after_robustness") or [],
            "current_completed_bar_matches": stock.get("current_completed_bar_matches") or [],
            "source": "research/backtests/ipo_base_stock_specific_signal_conclusion.json",
            "data_cutoff": obj.get("data_cutoff"),
            "automatic_trade": False,
            "trade_signal": None,
        }
    return output


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    research_context = read_json(root / "data/state/research_context.json", {})
    decision_context = read_json(root / "data/state/decision_context.json", {})
    account = read_json(root / "data/state/account_fact.json", {})
    stock_context = read_json(root / "data/state/stock_context.json", {})
    stock_market = read_json(root / "data/state/stock_market_context.json", {})
    stock_signal_map = _stock_specific_signal_map(root)

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
            "rejected_stock_specific_signals_after_robustness": stock_research.get("rejected_after_robustness") or [],
            "stock_specific_signal_current_completed_bar_matches": stock_research.get("current_completed_bar_matches") or [],
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
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "RESEARCH_TO_EXECUTION_READ_ONLY_BRIDGE",
        "read_only": True,
        "use_in_current_decision": True,
        "decision_output_generated": False,
        "automatic_trade": False,
        "trade_signal": None,
        "research_conclusions_must_be_synthesized": True,
        "conclusion_digest": digest,
        "portfolio_exposure": portfolio,
        "ipo_base_stock_evidence": stock_evidence,
        "execution_bridge": {
            "direct_execution_contribution": True,
            "allowed_effects": ["改变候选比较和机会强弱", "改变持仓风险收益和卖出判断", "改变新增金额判断", "改变资金来源选择", "改变卖出资金去向选择"],
            "stock_buy_capital_source_rule": "若打新底仓个股出现独立、可证伪且足以改变决策的明显新增证据，必须比较现金、低效率ETF及其他合法可释放资本；现金不是机械唯一来源，任何ETF卖出仍需独立通过卖出链。",
            "stock_sell_destination_rule": "若打新底仓个股出现足以支持降低风险或退出的证据，必须比较资金进入现金还是已经独立通过完整机会判断的ETF/其他合法机会；不得先卖出再寻找用途，也不得机械轮动。",
            "etf_buy_source_rule": "ETF机会成立而现金不足时，打新底仓只有在其自身继续持有的边际收益效率更低、账户打新/底仓功能不被破坏且卖出决议独立成立时，才可作为资金来源。",
            "separate_decisions_rule": "旧资产卖出与新资产买入始终分别决议；研究层只改变证据和比较结果，不自动联动两笔交易。",
        },
        "decision_boundary": "研究结论可直接贡献到执行判断，但不自动生成风险许可、Trial/Confirm、金额、卖出份额或订单；最终动作仍由MASTER完整买入/卖出链和用户人工下单决定。",
    }

    research_context["research_execution_summary"] = summary
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
        "rule": "研究层覆盖当前已确认打新底仓；有匹配的验证研究时与当前行情/账户事实合并，没有匹配研究时不得伪造买卖信号。",
    }
    decision_context.setdefault("research_evidence", {})["research_execution_summary"] = summary
    decision_context["ipo_base_stock_evidence"] = stock_evidence
    decision_context["research_execution_summary_file"] = "data/state/research_execution_summary.json"
    decision_context["unified_capital_reallocation_contract"] = summary["execution_bridge"]

    atomic_json_write(root / "data/state/research_context.json", research_context)
    atomic_json_write(root / "data/state/decision_context.json", decision_context)
    atomic_json_write(OUTPUT, summary)
    return summary


def main() -> None:
    summary = build(ROOT)
    print(json.dumps({"ok": True, "mode": summary.get("mode"), "ipo_base_stock_count": len(summary.get("ipo_base_stock_evidence") or []), "ipo_base_stock_share_of_total_asset_pct": (summary.get("portfolio_exposure") or {}).get("ipo_base_stock_share_of_total_asset_pct"), "automatic_trade": summary.get("automatic_trade")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
