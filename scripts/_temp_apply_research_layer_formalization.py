from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing: {path}: {old[:80]!r}')
    text = text.replace(old, new, 1)
    path.write_text(text, encoding='utf-8')

master = ROOT / 'ETF规则_MASTER.md'
replace_once(master,
    '# ETF波段交易系统 V2.2.17 规则 MASTER\n\n> 更新日期：2026-08-27  \n> 定位：V2.2.17 研究证据执行桥与打新底仓资本比较版；本文件为现行交易规则唯一来源。',
    '# ETF波段交易系统 V2.2.18 规则 MASTER\n\n> 更新日期：2026-08-27  \n> 定位：V2.2.18 研究证据层正式化版；本文件为现行交易规则唯一来源。')

research_section = '''\n### 6.4 研究证据层与正式转化目录\n\n研究层是MASTER执行时的**证据供应层**，不是平行交易系统。正式链条固定为：`历史／专项研究 → Point-in-Time与数据质量验证 → 稳健性和增量验证 → 正式研究转化审查 → research_execution_summary → 既有买入／卖出链`。研究证据可以直接改变候选比较、机会强弱、持仓风险收益、卖出判断、金额、资金来源和卖出资金去向，但不得单独生成风险许可、Trial／Confirm、金额、卖出份额或订单；`use_as_decision_evidence=true`只表示可以参与完整判断，`can_generate_decision_independently=false`表示没有独立交易权。动态数值、当日状态、回测统计和最新触发情况不复制到MASTER，分别由`research_execution_summary.json`及对应研究证据文件维护。\n\n当前已经完成正式研究转化、允许进入执行判断的研究证据如下；目录登记只确认“可使用及如何使用”，不代表当前必然看多、看空或触发交易：\n\n|正式研究证据|正式用途|使用边界|\n|-|-|-|\n|历史验证的ETF市场结构|机会判断、持仓复核与风险收益解释|按6.2既有结构使用；仅完整收盘数据；不得绕过风险许可和生命周期|\n|融资余额5日变化|A股风险资产ETF的市场级杠杆资金主证据，参与机会强弱、风险收益和资本比较|交易日T融资事实从T+1使用；必须与ETF自身结构、相对强弱、生命周期及替代机会共同判断；不得独立产生交易|\n|融资买入5日均值相对20日均值|融资融券辅助证据，用于判断杠杆资金加速／减速是否增强或削弱主证据|辅助于融资余额主证据，不单独形成方向、金额或卖出动作|\n|组合风险研究证据|识别高相关风险篮子、共同风险因子及边际风险释放效率，参与集中度、持仓和资本来源比较|只说明风险占用与相关性；不得以优化器目标权重、风险贡献或相关性机械再平衡|\n|主动收益／资本迁移研究证据|比较旧仓继续占用资本与独立新机会之间的机会成本，并保护有效赢家右尾|不得按横截面排名机械买强卖弱；旧仓卖出与新机会买入必须分别通过完整决议|\n|宁德时代（300750）超跌反转专项证据|当前打新底仓的持有价值、新增候选和资本来源／去向比较|定义为完整日线下过去5个交易日累计跌幅不高于-8%且收盘低于20日均线；只作为研究证据，不自动买入、Trial或Confirm；当前是否触发读取研究执行桥|\n\n以下研究截至当前**未完成正式转化或已终止／否决**，不得因为存在研究文件而进入执行：股指期货IF／IC／IM基差与OI仍需逐合约、到期日明确的最终验证；A股市场宽度因历史PIT数据不足终止本轮纳入；ETF份额流未形成稳定独立增量且当前不可作为正式决策证据；静态成分股领先研究受历史成分PIT约束；美股科技跨市场信号在既有控制后未形成额外稳定增量；工业富联（601138）60日放量突破及宁德时代（300750）60日放量突破因2026YTD年度稳健性反向失效，不进入正式执行证据；AlphaGen、PySR、Qlib等未通过稳定增量门禁的研究同样保持research-only。\n\n正式研究目录的维护遵循“**转化才登记，撤销也登记**”：新研究只有经过8.1审查并明确允许进入当前判断后，才可写入上表；若后续稳健性复核显示失效，应从“已正式转化”移至未转化／撤销说明，同时保留历史审计。机器执行桥出现新的正式研究证据而MASTER没有对应登记，属于研究登记漂移，系统一致性检查至少WARNING；如果机器层因此获得MASTER未授权的独立交易能力，则属于FAIL。\n'''
replace_once(master, '\n## 7. 卖出管理\n', research_section + '\n## 7. 卖出管理\n')
replace_once(master,
    '|V2.2.17|研究证据执行桥与打新底仓资本比较版|当前版本与现行交易规则|\n\nV2.2.17将研究层从“可读取证据”进一步收敛为“归纳后直接参与执行判断的只读证据”：覆盖ETF与当前打新底仓，明确打新底仓新增机会的资金来源比较和卖出后的资金去向比较；不增加自动交易、评分、平行许可或机械轮动。',
    '|V2.2.17|研究证据执行桥与打新底仓资本比较版|历史|\n|V2.2.18|研究证据层正式化版|当前版本与现行交易规则|\n\nV2.2.18在不增加交易入口的前提下，将研究层的正式转化结果集中登记到MASTER：明确已转化证据、research-only／终止证据、机器语义和登记漂移门禁。动态研究数值继续由研究执行桥维护，MASTER只保存稳定使用规则与转化状态。')

index = ROOT / 'ETF_SYSTEM_INDEX.md'
replace_once(index,
    '- `data/state/research_execution_summary.json`：研究结论归纳后的只读执行证据桥，覆盖ETF与当前打新底仓；可改变正式判断但不自动交易。',
    '- `data/state/research_execution_summary.json`：研究结论归纳后的只读执行证据桥，覆盖ETF与当前打新底仓；可改变正式判断但不自动交易。MASTER第6.4登记“哪些研究已正式转化及其稳定使用边界”，本文件与研究执行桥保存路径，动态数值和当前触发不复制进MASTER。\n- 正式研究证据源：`data/state/margin_financing_evidence.json`、`data/state/skfolio_risk_evidence.json`、`data/state/active_return_evidence.json`、`research/backtests/ipo_base_stock_specific_signal_conclusion.json`；研究文件存在不等于正式转化，是否允许参与执行以MASTER第6.4和执行桥语义共同约束。')

bridge = ROOT / 'scripts/build_research_execution_bridge.py'
text = bridge.read_text(encoding='utf-8')
text = text.replace('"decision_eligible": value.get("decision_eligible"),\n            "trade_signal": None,', '"decision_eligible": value.get("decision_eligible"),\n            "use_as_decision_evidence": bool(value.get("use_in_current_decision", value.get("status") in {"READY", "DEGRADED"})),\n            "can_generate_decision_independently": False,\n            "trade_signal": None,')
text = text.replace('"decision_eligible": bool(obj.get("decision_eligible", False)),\n            "production_context_integration": bool(obj.get("production_context_integration", False)),', '"decision_eligible": bool(obj.get("decision_eligible", False)),\n            "use_as_decision_evidence": bool(obj.get("decision_eligible", False) or obj.get("production_context_integration", False)),\n            "can_generate_decision_independently": False,\n            "production_context_integration": bool(obj.get("production_context_integration", False)),')
text = text.replace('"automatic_trade": False,\n                "trade_signal": None,', '"use_as_decision_evidence": True,\n                "can_generate_decision_independently": False,\n                "automatic_trade": False,\n                "trade_signal": None,', 1)
text = text.replace('"automatic_promotion": False,\n        "trade_signal": None,', '"automatic_promotion": False,\n        "semantic_contract": {"use_as_decision_evidence": "可进入完整判断", "can_generate_decision_independently": "是否可独立形成交易决议；研究证据固定为false"},\n        "trade_signal": None,')
bridge.write_text(text, encoding='utf-8')

state = ROOT / 'scripts/build_state_context.py'
text = state.read_text(encoding='utf-8')
text = text.replace('"decision_eligible": False,\n        "trade_signal": None,', '"decision_eligible": False,\n        "use_as_decision_evidence": evidence.get("use_in_current_decision", False),\n        "can_generate_decision_independently": False,\n        "trade_signal": None,')
state.write_text(text, encoding='utf-8')

checker = ROOT / 'scripts/check_system_consistency_core.py'
text = checker.read_text(encoding='utf-8')
anchor = '    for path in FORMAL_FILES:\n        check(f"formal_file:{path}", (ROOT / path).exists(), "exists" if (ROOT / path).exists() else "missing")\n'
insert = '''    for path in FORMAL_FILES:\n        check(f"formal_file:{path}", (ROOT / path).exists(), "exists" if (ROOT / path).exists() else "missing")\n\n    master_text = read_text("ETF规则_MASTER.md")\n    research_registry_markers = {\n        "margin_financing": "融资余额5日变化",\n        "skfolio_risk": "组合风险研究证据",\n        "active_return": "主动收益／资本迁移研究证据",\n        "ipo_base_stock_specific": "宁德时代（300750）超跌反转专项证据",\n    }\n    for evidence_id, marker in research_registry_markers.items():\n        check(f"research_registry:{evidence_id}", marker in master_text, f"MASTER registry contains {marker}", warning=True)\n    bridge_path = ROOT / "data/state/research_execution_summary.json"\n    if bridge_path.exists():\n        bridge_state = read_json("data/state/research_execution_summary.json")\n        runtime_ids = {str(x.get("evidence_id")) for x in ((bridge_state.get("conclusion_digest") or {}).get("runtime_validated_evidence") or []) if isinstance(x, dict) and x.get("use_in_current_decision")}\n        for evidence_id in ("margin_financing", "skfolio_risk", "active_return"):\n            if evidence_id in runtime_ids:\n                marker = research_registry_markers[evidence_id]\n                check(f"research_registry_drift:{evidence_id}", marker in master_text, f"runtime evidence {evidence_id} is registered in MASTER", warning=True)\n        exec_items = ((bridge_state.get("conclusion_digest") or {}).get("execution_eligible_backtest_conclusions") or [])\n        stock_exec = any(isinstance(x, dict) and str(x.get("source") or "").endswith("ipo_base_stock_specific_signal_conclusion.json") for x in exec_items)\n        if stock_exec:\n            check("research_registry_drift:ipo_base_stock_specific", research_registry_markers["ipo_base_stock_specific"] in master_text, "execution-eligible stock-specific research is registered in MASTER", warning=True)\n        check("research_semantics:no_independent_trade", bridge_state.get("automatic_trade") is False and bridge_state.get("trade_signal") is None, "research bridge remains read-only and cannot auto-trade")\n'''
if anchor not in text:
    raise SystemExit('consistency checker anchor missing')
text = text.replace(anchor, insert, 1)
checker.write_text(text, encoding='utf-8')

print('research-layer formalization patch applied')
