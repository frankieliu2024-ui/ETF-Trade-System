from pathlib import Path

BRIDGE = r'''from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUTPUT = ROOT / "data/state/research_execution_summary.json"


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


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    research_context = read_json(root / "data/state/research_context.json", {})
    decision_context = read_json(root / "data/state/decision_context.json", {})
    account = read_json(root / "data/state/account_fact.json", {})
    stock_context = read_json(root / "data/state/stock_context.json", {})
    stock_market = read_json(root / "data/state/stock_market_context.json", {})

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
            "validated_stock_specific_signal_status": "NO_MATCHED_VALIDATED_SIGNAL" if not research_context.get("ipo_base_stock_research") else "SEE_RESEARCH_CONTEXT",
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
'''


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing replacement anchor in {path}: {old[:100]}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("scripts/build_research_execution_bridge.py").write_text(BRIDGE, encoding="utf-8")

replace_once("scripts/build_state_context.py", "from build_research_contribution_audit import build as build_research_contribution_audit\nfrom build_phase4_automation import build as build_phase4_automation", "from build_research_contribution_audit import build as build_research_contribution_audit\nfrom build_research_execution_bridge import build as build_research_execution_bridge\nfrom build_phase4_automation import build as build_phase4_automation")
replace_once("scripts/build_state_context.py", '"decision_boundary": "研究层向当前决策提供市场环境、事实、历史价格位置、日内路径、相对强弱、证据变化、共同风险、经验证的ETF份额、融资杠杆与主动收益方法证据，并记录研究是否真实改变决策；不得绕过MASTER生成交易动作。",', '"decision_boundary": "研究层向当前决策提供经过归纳的市场、ETF与打新底仓研究证据，可直接改变候选比较、风险收益、持仓/卖出、金额及资金来源/去向判断；不得绕过MASTER生成交易动作或自动交易。",')
replace_once("scripts/build_state_context.py", '"current_decision_use": "正式盘中先读市场层指数/宽度/风格，再做候选比较；候选比较必须先读假设/相关性、历史位置、日内路径和风险收益，再读相对强弱与当日涨幅。正式决策只记录最多3项真正改变判断的research_evidence_used。",', '"current_decision_use": "正式盘中先读市场层，再统一比较现金、持仓/观察ETF与当前打新底仓；研究结论先归纳后进入执行判断，正式决策只记录真正改变机会、持有/卖出、金额或资金来源/去向的research_evidence_used。",')
replace_once("scripts/build_state_context.py", 'atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)\n    print(json.dumps({', 'atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)\n    research_execution_summary = build_research_execution_bridge(ROOT)\n    print(json.dumps({')
replace_once("scripts/build_state_context.py", '"research_master_candidate_count": len(research_master.get("candidates") or []),\n        "trade_decision_generated": False,', '"research_master_candidate_count": len(research_master.get("candidates") or []),\n        "research_execution_bridge_status": research_execution_summary.get("mode"),\n        "ipo_base_stock_research_count": len(research_execution_summary.get("ipo_base_stock_evidence") or []),\n        "trade_decision_generated": False,')
replace_once("scripts/build_state_context.py", '"重新统一比较持仓ETF、观察ETF与现金，不自动沿用上一轮主候选",', '"重新统一比较持仓ETF、观察ETF、当前打新底仓与现金，不自动沿用上一轮主候选",')

replace_once("scripts/check_research_integration.py", '    execution_quality = text("scripts/build_execution_quality.py")\n    backfill = text("scripts/backfill_research_daily_history.py")', '    execution_quality = text("scripts/build_execution_quality.py")\n    execution_bridge = text("scripts/build_research_execution_bridge.py")\n    backfill = text("scripts/backfill_research_daily_history.py")')
anchor = '''    check(\n        "research:no_trade_authority",\n        all(token in research_builder for token in ["decision_output_generated", "不自动修改MASTER", "不生成风险许可"]),\n        "research builder remains read-only and cannot generate trading authority",\n    )\n'''
addition = anchor + '''    check(\n        "research:execution_bridge_wired",\n        "build_research_execution_bridge" in state_context and all(token in execution_bridge for token in ["RESEARCH_TO_EXECUTION_READ_ONLY_BRIDGE", "research_conclusions_must_be_synthesized", "ipo_base_stock_evidence", "stock_buy_capital_source_rule", "stock_sell_destination_rule"]),\n        "research conclusions must be synthesized into execution evidence covering IPO base stocks and capital source/destination without creating orders",\n    )\n    check(\n        "research:execution_bridge_no_auto_trade",\n        all(token in execution_bridge for token in ["automatic_trade\\\": False", "trade_signal\\\": None", "separate_decisions_rule"]),\n        "research-to-execution bridge must remain read-only and must not auto trade or mechanically rotate capital",\n    )\n    check(\n        "research:master_ipo_base_capital_bridge",\n        all(token in text("ETF规则_MASTER.md") for token in ["研究结论先归纳", "打新底仓个股出现独立、可证伪", "卖出后资金去向", "研究证据可以直接改变"]),\n        "MASTER must explicitly connect synthesized research evidence to IPO base stock capital-source and capital-destination decisions",\n    )\n'''
replace_once("scripts/check_research_integration.py", anchor, addition)
replace_once("scripts/check_research_integration.py", '    candidates = load("data/state/research_master_candidates.json", None)\n    if isinstance(candidates, dict):', '    execution_summary = load("data/state/research_execution_summary.json", None)\n    if isinstance(execution_summary, dict):\n        check("research:runtime_execution_summary_read_only", execution_summary.get("read_only") is True and execution_summary.get("automatic_trade") is False and execution_summary.get("trade_signal") is None, "research_execution_summary must be read-only, decision-usable evidence without automatic trading")\n    candidates = load("data/state/research_master_candidates.json", None)\n    if isinstance(candidates, dict):')

p = Path("ETF规则_MASTER.md")
master = p.read_text(encoding="utf-8")
master = master.replace("# ETF波段交易系统 V2.2.16 规则 MASTER", "# ETF波段交易系统 V2.2.17 规则 MASTER", 1)
master = master.replace("> 更新日期：2026-08-26  \n> 定位：V2.2.16 ETF研究池与数据接口正式吸收版；本文件为现行交易规则唯一来源。", "> 更新日期：2026-08-27  \n> 定位：V2.2.17 研究证据执行桥与打新底仓资本比较版；本文件为现行交易规则唯一来源。", 1)
old = '现金、持仓ETF、观察ETF、当前打新底仓个股和可释放资产持续参加统一比较，回答“如果现在重新分配下一单位资本，最有效用途是什么”。只比较假设、边际风险收益、集中度、资金占用和执行条件，不评分、不机械排名。统一比较属于机会判断、金额与资金判断和持仓管理的内在内容，不新增执行节点。旧仓卖出与新机会买入分别决议。'
new = old + '\n\n研究结论先归纳为少量能够改变决策的执行证据，再进入统一比较。研究证据可以直接改变候选比较、持仓风险收益、卖出判断、金额以及资金来源／去向，但不得单独生成风险许可、Trial／Confirm、金额、卖出份额或订单，不自动交易。正式决策只引用真正改变本次判断的研究结论，研究层已有但未改变决策的内容不重复堆叠。'
if old not in master: raise RuntimeError("MASTER 5.4 anchor missing")
master = master.replace(old, new, 1)
old = '第三层个股不设固定打新底仓名单。云端从当日已核验账户持仓中识别非ETF个股，并结合已确认资产角色判断是否属于打新底仓；新出现且角色未知的个股先标记待分类，不得仅凭代码、名称或历史持仓自动认定为打新底仓。已确认打新底仓自动进入行情监测和统一资本比较，数量归零后退出默认个股监测。'
new = old + ' 已确认打新底仓同时进入研究证据覆盖：当前行情、账户占比、持有收益风险及与其匹配的历史／专项研究均应进入正式统一比较；没有经验证的个股专项证据时必须明确缺口，不得伪造买卖信号。'
if old not in master: raise RuntimeError("MASTER 6.3 anchor missing")
master = master.replace(old, new, 1)
anchor2 = '海外、指数和产业链信息进入交易判断的顺序固定为：**外部结构（先完成时点对齐）→本地传导→目标ETF自身反馈→机会判断**。'
extra = '打新底仓个股出现独立、可证伪且足以改变决策的明显新增证据时，必须进入完整资本比较：若考虑新增，资金来源同时比较现金、低效率ETF及其他合法可释放资本，现金不是机械唯一来源，任何ETF卖出仍需独立通过卖出链；若考虑降低风险或退出，卖出后资金去向必须比较现金与已经独立成立的ETF或其他合法机会，不得先卖出再寻找用途，也不得机械轮动。ETF机会需要释放打新底仓资金时，同样必须先证明该底仓继续持有的边际收益效率更低且不破坏账户打新／底仓功能。旧资产卖出与新资产买入始终分别决议。\n\n'
if anchor2 not in master: raise RuntimeError("MASTER capital bridge anchor missing")
master = master.replace(anchor2, extra + anchor2, 1)
old = '专项研究经过数据质量、样本充分、逻辑稳定、执行可转化和风险不增加审查后，直接归入交易规则优化、机会判断依据或研究经验，不设置中间转化层。高质量专项不必先转为OBS或等待真实CASE；统计胜率或单一最优参数不得直接生成交易动作。'
new = old + '\n\n通过审查并允许用于当前决策的研究结论，不应停留在研究文件中等待人工二次翻译：机器层应将其归纳为可直接消费的研究证据摘要，并覆盖ETF与当前已确认打新底仓。该摘要可以进入机会、持有／卖出、金额和资本来源／去向判断，但保持只读、禁止自动下单；研究层与交易层之间不新增审批节点或综合评分。'
if old not in master: raise RuntimeError("MASTER 8.1 anchor missing")
master = master.replace(old, new, 1)
master = master.replace('|V2.2.16|ETF研究池与数据接口正式吸收版|当前版本与现行交易规则|', '|V2.2.16|ETF研究池与数据接口正式吸收版|历史|\n|V2.2.17|研究证据执行桥与打新底仓资本比较版|当前版本与现行交易规则|', 1)
master += '\n\nV2.2.17将研究层从“可读取证据”进一步收敛为“归纳后直接参与执行判断的只读证据”：覆盖ETF与当前打新底仓，明确打新底仓新增机会的资金来源比较和卖出后的资金去向比较；不增加自动交易、评分、平行许可或机械轮动。\n'
p.write_text(master, encoding="utf-8")

p = Path("ETF_SYSTEM_INDEX.md")
idx = p.read_text(encoding="utf-8")
anchor = '- `data/state/research_context.json`'
if anchor not in idx: raise RuntimeError("index research_context anchor missing")
idx = idx.replace(anchor, anchor + '\n- `data/state/research_execution_summary.json`：研究结论归纳后的只读执行证据桥，覆盖ETF与当前打新底仓；可改变正式判断但不自动交易。', 1)
p.write_text(idx, encoding="utf-8")
