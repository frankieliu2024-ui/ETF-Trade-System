from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {text.count(old)}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    # 1) Reuse the existing Yahoo 5d payload: expose the immediately prior completed close for NDX/SOX return calculation.
    replace_once(
        "scripts/build_overseas_context.py",
        '        row.update({\n            "volume": ((quote.get("volume") or [None])[idx] if idx < len(quote.get("volume") or []) else None),\n',
        '        previous_close = None\n        close_values = quote.get("close") or []\n        for j in range(idx - 1, -1, -1):\n            candidate = close_values[j] if j < len(close_values) else None\n            if candidate is not None:\n                previous_close = candidate\n                break\n        row.update({\n            "volume": ((quote.get("volume") or [None])[idx] if idx < len(quote.get("volume") or []) else None),\n            "previous_close": previous_close,\n'
    )

    # 2) Wire converted research into the canonical state builder after margin evidence is available.
    replace_once(
        "scripts/build_state_context.py",
        'from build_margin_financing_evidence import build as build_margin_financing_evidence\n',
        'from build_margin_financing_evidence import build as build_margin_financing_evidence\nfrom build_low_cost_alpha_evidence import build as build_low_cost_alpha_evidence\n'
    )
    replace_once(
        "scripts/build_state_context.py",
        '    margin_financing_summary = compact_margin_financing(margin_financing)\n\n    active_return = build_active_return_evidence(ROOT)\n',
        '    margin_financing_summary = compact_margin_financing(margin_financing)\n\n    low_cost_alpha = build_low_cost_alpha_evidence(ROOT)\n    atomic_json_write(ROOT / "data" / "state" / "low_cost_alpha_evidence.json", low_cost_alpha)\n\n    active_return = build_active_return_evidence(ROOT)\n'
    )
    replace_once(
        "scripts/build_state_context.py",
        '            "margin_financing": margin_financing_summary,\n            "active_return": active_return,\n',
        '            "margin_financing": margin_financing_summary,\n            "opening_residual_561980": low_cost_alpha.get("opening_residual_561980") or {},\n            "selling_exhaustion": low_cost_alpha.get("selling_exhaustion") or {},\n            "margin_feedback_interaction": low_cost_alpha.get("margin_feedback_interaction") or {},\n            "active_return": active_return,\n'
    )
    replace_once(
        "scripts/build_state_context.py",
        '            "margin_financing_evidence": "data/state/margin_financing_evidence.json",\n',
        '            "margin_financing_evidence": "data/state/margin_financing_evidence.json",\n            "low_cost_alpha_evidence": "data/state/low_cost_alpha_evidence.json",\n            "low_cost_alpha_conversion_review": "research/backtests/low_cost_alpha_formal_conversion_review.json",\n'
    )

    # 3) Registry drift gate: the new runtime evidence is only legal when MASTER carries the same scoped registration.
    replace_once(
        "scripts/check_system_consistency_core.py",
        '        "ipo_base_stock_specific": "宁德时代（300750）超跌反转专项证据",\n',
        '        "ipo_base_stock_specific": "宁德时代（300750）超跌反转专项证据",\n        "opening_residual_561980": "半导体设备ETF（561980）海外开盘定价残差证据",\n        "selling_exhaustion": "缩量下跌但收离低点证据",\n        "margin_feedback_interaction": "融资扩张×ETF相对反馈证据",\n'
    )
    replace_once(
        "scripts/check_system_consistency_core.py",
        '        for evidence_id in ("margin_financing", "skfolio_risk", "active_return"):\n',
        '        for evidence_id in ("margin_financing", "skfolio_risk", "active_return", "opening_residual_561980", "selling_exhaustion", "margin_feedback_interaction"):\n'
    )
    replace_once(
        "scripts/check_system_consistency_core.py",
        '"scripts/build_overseas_context.py", "scripts/build_us_extended_hours_context.py", "scripts/build_query_context.py",',
        '"scripts/build_overseas_context.py", "scripts/build_us_extended_hours_context.py", "scripts/build_low_cost_alpha_evidence.py", "scripts/build_query_context.py",'
    )
    replace_once(
        "scripts/check_system_consistency_core.py",
        '"scripts/build_overseas_context.py", "scripts/build_overseas_runtime_health.py", "scripts/build_query_context.py",',
        '"scripts/build_overseas_context.py", "scripts/build_overseas_runtime_health.py", "scripts/build_low_cost_alpha_evidence.py", "scripts/build_query_context.py",'
    )

    # 4) MASTER: scoped research conversion only. No risk/permission/lifecycle/amount/sell rule changes.
    replace_once(
        "ETF规则_MASTER.md",
        '# ETF波段交易系统 V2.2.21 规则 MASTER\n\n> 更新日期：2026-08-28  \n> 定位：V2.2.21 决策闭环与Point-in-Time收敛版；本文件为现行交易规则唯一来源。',
        '# ETF波段交易系统 V2.2.22 规则 MASTER\n\n> 更新日期：2026-08-28  \n> 定位：V2.2.22 研究证据定向转化版；本文件为现行交易规则唯一来源。'
    )
    replace_once(
        "ETF规则_MASTER.md",
        '|宁德时代（300750）超跌反转专项证据|当前打新底仓的持有价值、新增候选和资本来源／去向比较|定义为完整日线下过去5个交易日累计跌幅不高于-8%且收盘低于20日均线；只作为研究证据，不自动买入、Trial或Confirm；当前是否触发读取研究执行桥|\n',
        '|宁德时代（300750）超跌反转专项证据|当前打新底仓的持有价值、新增候选和资本来源／去向比较|定义为完整日线下过去5个交易日累计跌幅不高于-8%且收盘低于20日均线；只作为研究证据，不自动买入、Trial或Confirm；当前是否触发读取研究执行桥|\n|半导体设备ETF（561980）海外开盘定价残差证据|A股开盘时比较上一已完成SOX／NDX现金盘与半导体设备ETF（561980）实际开盘定价，参与短周期机会强弱和风险收益判断|仅561980；使用截至2026-08-21固定PIT校准；正残差只表示相对历史传导估计偏低定价，不是买入阈值；仍执行“外部结构→本地传导→ETF自身反馈→机会判断”，不得独立产生交易|\n|缩量下跌但收离低点证据|完整收盘日线后的抛压衰竭候选，参与机会、持仓风险收益和下一单位资本比较|ETF层；当日下跌、成交量低于自身前20日常态并处于历史低量环境、且CLV≥0.55时形成证据；只表示抛压可能衰竭，不证明反转、不机械买入；须与趋势、日内路径、承接和风险收益共同判断|\n|融资扩张×ETF相对反馈证据|融资余额5日状态扩张时，以前一完整交易日ETF相对ETF池中位数的强弱作为未来3—5日持续性的条件增强证据|仅561980／588000／159781／159992／159326，588000历史稳定性最高，515880不纳入；融资非扩张只表示本交互不激活，不形成反向看空；融资T日事实从T+1使用，不得机械买强|\n'
    )
    replace_once(
        "ETF规则_MASTER.md",
        '以下研究截至当前**未完成正式转化或已终止／否决**，不得因为存在研究文件而进入执行：股指期货IF／IC／IM基差与OI仍需逐合约、到期日明确的最终验证；A股市场宽度因历史PIT数据不足终止本轮纳入；ETF份额流未形成稳定独立增量且当前不可作为正式决策证据；静态成分股领先研究受历史成分PIT约束；美股科技跨市场信号在既有控制后未形成额外稳定增量；工业富联（601138）60日放量突破及宁德时代（300750）60日放量突破因2026YTD年度稳健性反向失效，不进入正式执行证据；AlphaGen、PySR、Qlib等未通过稳定增量门禁的研究同样保持research-only。',
        '以下研究截至当前**未完成正式转化或已终止／否决**，不得因为存在研究文件而进入执行：股指期货IF／IC／IM基差与OI仍需逐合约、到期日明确的最终验证；A股市场宽度因历史PIT数据不足终止本轮纳入；ETF份额流未形成稳定独立增量且当前不可作为正式决策证据；静态成分股领先研究受历史成分PIT约束；通用美股科技跨市场信号在既有控制后未形成额外稳定增量，仅半导体设备ETF（561980）的开盘定价残差作为单对象、低置信度补充证据完成定向转化；弱市异常抗跌在60交易日滚动PIT复核后未形成稳定3／5／10日增量；横截面离散度×资本迁移在收紧为同一fold同时满足正迁移与相对改善后失效；工业富联（601138）60日放量突破及宁德时代（300750）60日放量突破因2026YTD年度稳健性反向失效，不进入正式执行证据；AlphaGen、PySR、Qlib等未通过稳定增量门禁的研究同样保持research-only。'
    )
    replace_once(
        "ETF规则_MASTER.md",
        '|V2.2.21|决策闭环与Point-in-Time收敛版|当前版本与现行交易规则|\n\nV2.2.21在V2.2.20综合证据强制消费基础上，进一步固化三项执行闭环：',
        '|V2.2.21|决策闭环与Point-in-Time收敛版|历史|\n|V2.2.22|研究证据定向转化版|当前版本与现行交易规则|\n\nV2.2.21在V2.2.20综合证据强制消费基础上，进一步固化三项执行闭环：'
    )
    replace_once(
        "ETF规则_MASTER.md",
        'V2.2.21在V2.2.20综合证据强制消费基础上，进一步固化三项执行闭环：正式节点优先识别相对上一有效节点新增／增强／减弱／失效的证据，避免静态背景重复占据判断；机会、Confirm、持仓和资本迁移必须面对当前最强反证、最可能失效假设与真正决定性数据缺口，反证未破坏假设时不得以一般不确定性无限等待；全部正式判断、CASE和反事实复盘统一遵守Point-in-Time，不允许事后信息改写原决策信息集。该版本不新增评分、固定权重、许可、金额档或交易入口。',
        'V2.2.21在V2.2.20综合证据强制消费基础上，进一步固化三项执行闭环：正式节点优先识别相对上一有效节点新增／增强／减弱／失效的证据，避免静态背景重复占据判断；机会、Confirm、持仓和资本迁移必须面对当前最强反证、最可能失效假设与真正决定性数据缺口，反证未破坏假设时不得以一般不确定性无限等待；全部正式判断、CASE和反事实复盘统一遵守Point-in-Time，不允许事后信息改写原决策信息集。该版本不新增评分、固定权重、许可、金额档或交易入口。\n\nV2.2.22完成三项定向研究转化：半导体设备ETF（561980）海外开盘定价残差、ETF层缩量下跌但收离低点的抛压衰竭证据、融资扩张环境下的ETF相对反馈证据。三者只进入既有研究执行桥并增强完整机会／持仓／资本比较；弱市异常抗跌和横截面离散度×资本迁移继续否决。该版本不改变风险许可、Trial／Confirm、生命周期、固定金额档、卖出规则或账户约束，不新增综合评分、机械阈值交易或自动下单能力。'
    )

    # 5) System index routes the new evidence without creating a new normative domain.
    replace_once(
        "ETF_SYSTEM_INDEX.md",
        '- 正式研究证据源：`data/state/margin_financing_evidence.json`、`data/state/skfolio_risk_evidence.json`、`data/state/active_return_evidence.json`、`research/backtests/ipo_base_stock_specific_signal_conclusion.json`；研究文件存在不等于正式转化，是否允许参与执行以MASTER第6.4和执行桥语义共同约束。',
        '- 正式研究证据源：`data/state/margin_financing_evidence.json`、`data/state/skfolio_risk_evidence.json`、`data/state/active_return_evidence.json`、`data/state/low_cost_alpha_evidence.json`、`research/backtests/ipo_base_stock_specific_signal_conclusion.json`；低成本主动收益新证据的正式转化审查记录为`research/backtests/low_cost_alpha_formal_conversion_review.json`，半导体设备ETF（561980）开盘残差固定PIT校准为`research/backtests/opening_residual_561980_production_calibration.json`；研究文件存在不等于正式转化，是否允许参与执行以MASTER第6.4和执行桥语义共同约束。'
    )

    print("applied low-cost alpha formal conversion patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
