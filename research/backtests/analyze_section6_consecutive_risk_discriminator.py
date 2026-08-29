#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict

from revalidate_section6_baselines import load_panel, enrich_future, stats, OUT_DIR, amount_ratio, pct


def summarize(vals):
    return stats([v for v in vals if v is not None])


def main():
    by_code, dates = load_panel()
    panel = enrich_future(by_code)
    lookup = defaultdict(dict)
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            lookup[code][r["date"]] = i

    daily_pool = defaultdict(list)
    for code, rows in by_code.items():
        for r in rows:
            if r.get("change_pct") is not None:
                daily_pool[r["date"]].append(r["change_pct"])
    daily_med = {d: sorted(xs)[len(xs)//2] for d, xs in daily_pool.items() if xs}

    events = []
    for r in panel:
        if not r["signals"].get("consecutive_rise_risk_review"):
            continue
        rows = by_code[r["code"]]
        i = lookup[r["code"]][r["date"]]
        if i + 5 >= len(rows):
            continue
        t1 = rows[i+1]
        t5 = rows[i+5]
        if t1.get("close") in (None, 0) or t5.get("close") is None:
            continue
        t1_ret = t1.get("change_pct")
        med = daily_med.get(t1["date"])
        t1_rel = None if t1_ret is None or med is None else t1_ret - med
        t1_amt = amount_ratio(rows, i+1, 20, include_current=False)
        t1_clv = t1.get("clv")
        post_ret = pct(t5["close"], t1["close"])
        path = rows[i+2:i+6]
        lows = [q.get("low") for q in path if q.get("low") is not None]
        highs = [q.get("high") for q in path if q.get("high") is not None]
        post_mae = pct(min(lows), t1["close"]) if lows else None
        post_mfe = pct(max(highs), t1["close"]) if highs else None
        events.append({
            "code": r["code"], "signal_date": r["date"], "t1_date": t1["date"],
            "t1_ret": t1_ret, "t1_rel": t1_rel, "t1_clv": t1_clv, "t1_amt": t1_amt,
            "post_ret": post_ret, "post_mae": post_mae, "post_mfe": post_mfe,
        })

    rules = {
        "ALL": lambda e: True,
        "T1_NEGATIVE": lambda e: e["t1_ret"] is not None and e["t1_ret"] < 0,
        "T1_RELATIVE_WEAK": lambda e: e["t1_rel"] is not None and e["t1_rel"] < 0,
        "T1_LOW_CLV": lambda e: e["t1_clv"] is not None and e["t1_clv"] < 0.50,
        "T1_NEGATIVE_AND_LOW_CLV": lambda e: e["t1_ret"] is not None and e["t1_ret"] < 0 and e["t1_clv"] is not None and e["t1_clv"] < 0.50,
        "T1_NEGATIVE_HIGH_VOLUME": lambda e: e["t1_ret"] is not None and e["t1_ret"] < 0 and e["t1_amt"] is not None and e["t1_amt"] >= 1.0,
        "T1_STILL_STRONG": lambda e: e["t1_ret"] is not None and e["t1_ret"] >= 0 and e["t1_clv"] is not None and e["t1_clv"] >= 0.50,
    }

    groups = {}
    for name, fn in rules.items():
        xs = [e for e in events if fn(e)]
        groups[name] = {
            "n": len(xs),
            "t1_to_t5_return": summarize([e["post_ret"] for e in xs]),
            "t1_to_t5_mae": summarize([e["post_mae"] for e in xs]),
            "t1_to_t5_mfe": summarize([e["post_mfe"] for e in xs]),
        }

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_CONSECUTIVE_RISE_SECOND_STAGE_DISCRIMINATOR",
        "date_range": [dates[0], dates[-1]],
        "signal_definition_frozen": True,
        "parameter_optimization": False,
        "decision_node": "T+1 complete close after a T-day consecutive-rise risk-review event",
        "groups": groups,
        "boundary": "Second-stage diagnostic only. Candidate observations do not create sell rules or permissions.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "section6_consecutive_risk_discriminator.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    labels = {
        "ALL":"全部风险复核事件",
        "T1_NEGATIVE":"次日收跌",
        "T1_RELATIVE_WEAK":"次日弱于ETF池中位数",
        "T1_LOW_CLV":"次日CLV<0.50",
        "T1_NEGATIVE_AND_LOW_CLV":"次日收跌且CLV<0.50",
        "T1_NEGATIVE_HIGH_VOLUME":"次日收跌且成交额>=此前20日均值",
        "T1_STILL_STRONG":"次日不跌且CLV>=0.50",
    }
    lines = [
        "# 连续上涨风险复核：T+1二次判别研究（自动生成）",
        "",
        "冻结现行‘连续5日上涨且5日累计>=10%’风险复核定义；不调阈值。研究节点改为风险复核后的下一个完整交易日收盘（T+1），比较简单、事前定义的价格/承接迹象是否能区分风险释放与赢家右尾。",
        "",
        "|T+1观察|样本|T+1→T+5收益|后续MAE|后续MFE|",
        "|---|---:|---:|---:|---:|",
    ]
    for k in labels:
        z = groups[k]
        lines.append(f"|{labels[k]}|{z['n']}|{z['t1_to_t5_return'].get('mean')}%|{z['t1_to_t5_mae'].get('mean')}%|{z['t1_to_t5_mfe'].get('mean')}%|")
    lines += [
        "",
        "## 边界",
        "",
        "- T+1信息仅可用于T+1及之后的复核，绝不能回填成T日已知证据。",
        "- 本轮只使用固定、易解释的次日观察，不进行阈值网格搜索，不按结果挑最优规则。",
        "- 若‘次日仍强’保留明显右尾，而‘次日转弱’显著增加后续下行，则支持把连续上涨从单层风险提示升级为‘先复核、再等确认’的证据结构；仍不构成机械卖出。",
        "- 本研究不产生风险许可、Trial/Confirm、金额、卖出份额或订单。",
        "",
    ]
    (OUT_DIR / "section6_consecutive_risk_discriminator.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "events": len(events), "groups": {k:{"n":v['n'],"ret":v['t1_to_t5_return'].get('mean'),"mae":v['t1_to_t5_mae'].get('mean'),"mfe":v['t1_to_t5_mfe'].get('mean')} for k,v in groups.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
