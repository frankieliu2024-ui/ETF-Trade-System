#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research/backtests/revalidate_section6_baselines.py"
OUT = ROOT / "research/reports/generated/section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def s(vals):
    return mod.stats([v for v in vals if v is not None])


def rel5(rows):
    return s([r["future"][5].get("relative_to_pool_median_pct_points") for r in rows if 5 in r.get("future", {})])


def abs5(rows):
    return s([r["future"][5].get("return_pct") for r in rows if 5 in r.get("future", {})])


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    by_key = {(r["date"], r["code"]): r for r in panel}
    by_date = defaultdict(list)
    for r in panel:
        by_date[r["date"]].append(r)

    # T-day cross-sectional facts, all PIT-available at T close.
    date_ctx = {}
    for d, rows in by_date.items():
        rets = [r.get("change_pct") for r in rows if r.get("change_pct") is not None]
        med = mod.median(rets)
        pos = sum((x or 0) > 0 for x in rets)
        date_ctx[d] = {
            "median_return": med,
            "positive_share": (pos / len(rets)) if rets else None,
        }
        for r in rows:
            r["rel_pool_t"] = None if med is None or r.get("change_pct") is None else r["change_pct"] - med

    # Previous-day map and rolling local features.
    idx_map = {}
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            idx_map[(r["date"], code)] = (rows, i)

    c1 = []
    c2 = []
    c3 = []
    c4 = []
    for r in panel:
        key = (r["date"], r["code"])
        rows, i = idx_map.get(key, (None, None))
        if rows is None:
            continue
        prev = by_key.get((rows[i-1]["date"], r["code"])) if i and i > 0 else None
        delta_rel = None if not prev or r.get("rel_pool_t") is None or prev.get("rel_pool_t") is None else r["rel_pool_t"] - prev["rel_pool_t"]
        if delta_rel is not None:
            if delta_rel >= 1.0:
                c1.append(("IMPROVING", r))
            elif delta_rel <= -1.0:
                c1.append(("WEAKENING", r))

        amt_ratio = mod.amount_ratio(rows, i, 20, include_current=False)
        ma20 = mod.sma_close(rows, i, 20, include_current=True)
        prev_ma20 = mod.sma_close(rows, i-1, 20, include_current=True) if i and i > 0 else None
        prev_close = rows[i-1].get("close") if i and i > 0 else None
        breakout20 = mod.breakout_high(rows, i, 20)
        recovery20 = bool(prev_close is not None and prev_ma20 is not None and prev_close < prev_ma20 and r.get("close") is not None and ma20 is not None and r["close"] >= ma20)
        positive_structure = bool(breakout20 or recovery20)
        if amt_ratio is not None:
            if amt_ratio >= 1.2 and positive_structure:
                c2.append(("EXPANDING_WITH_POSITIVE_STRUCTURE", r))
            elif amt_ratio >= 1.2 and not positive_structure:
                c2.append(("EXPANDING_WITHOUT_POSITIVE_STRUCTURE", r))
            elif amt_ratio < 0.8 and positive_structure:
                c2.append(("CONTRACTING_WITH_POSITIVE_STRUCTURE", r))

        breadth = (date_ctx.get(r["date"]) or {}).get("positive_share")
        rel = r.get("rel_pool_t")
        if breadth is not None and rel is not None:
            if breadth <= 0.35 and rel >= 1.0:
                c3.append(("WEAK_MARKET_RESILIENCE", r))
            elif breadth <= 0.35 and rel <= -1.0:
                c3.append(("WEAK_MARKET_LAGGARD", r))
            elif breadth >= 0.65 and rel >= 1.0:
                c3.append(("STRONG_MARKET_LEADER", r))
            elif breadth >= 0.65 and rel <= -1.0:
                c3.append(("STRONG_MARKET_LAGGARD", r))

        ret20 = mod.ret_n(rows, i, 20)
        clv = r.get("clv")
        day_ret = r.get("change_pct")
        if ret20 is not None and clv is not None and day_ret is not None:
            trend = "UPTREND" if ret20 > 0 and ma20 is not None and r.get("close") is not None and r["close"] >= ma20 else "NON_UPTREND"
            quality = "HIGH_QUALITY_CLOSE" if clv >= 0.70 and day_ret > 0 else ("LOW_QUALITY_CLOSE" if clv <= 0.30 else "MIXED_CLOSE")
            c4.append((f"{trend}__{quality}", r))

    def grouped(pairs):
        groups = defaultdict(list)
        for label, row in pairs:
            groups[label].append(row)
        return {
            k: {"count": len(v), "t5_absolute": abs5(v), "t5_relative_to_pool": rel5(v)}
            for k, v in sorted(groups.items())
        }

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_STAGE_C_NEW_EVIDENCE",
        "date_range": [dates[0], dates[-1]],
        "panel_rows": len(panel),
        "c1_relative_strength_delta": grouped(c1),
        "c2_participation_x_structure": grouped(c2),
        "c3_breadth_x_relative_feedback": grouped(c3),
        "c4_trend_x_repair_quality": grouped(c4),
        "c5_financing_futures_interaction": {
            "status": "NOT_RUN_IN_DAILY_FEATURE_PANEL",
            "reason": "融资和IF/IC历史事实不在daily_features同一逐日PIT面板中；不得为了完成计划伪造历史对齐。该项转入已有专项历史数据与上线后forward evidence的条件化验证。",
        },
        "decision_boundary": "研究初筛，不生成交易权限、阈值、评分或动作；阈值仅为预注册的粗分组，用于判断是否值得进入稳健性/去重研究。",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "section6_stage_c_new_evidence.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 第6节阶段C：云端时代新增候选证据初筛（自动生成）", "",
        f"日期：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。", "",
        "本研究不优化参数，只用简单预注册分组判断新信息是否值得继续。所有未来收益仅用于研究评价。", "",
    ]
    for title, key in [
        ("C1 相对强弱边际变化", "c1_relative_strength_delta"),
        ("C2 成交参与变化 × 价格结构", "c2_participation_x_structure"),
        ("C3 ETF池宽度 × 个体反馈", "c3_breadth_x_relative_feedback"),
        ("C4 趋势状态 × 修复质量", "c4_trend_x_repair_quality"),
    ]:
        lines += [f"## {title}", "", "|分组|样本|T+5均值|T+5相对ETF池|", "|---|---:|---:|---:|"]
        for label, z in payload[key].items():
            a = z["t5_absolute"].get("mean")
            rr = z["t5_relative_to_pool"].get("mean")
            lines.append(f"|{label}|{z['count']}|{a if a is not None else 'NA'}%|{rr if rr is not None else 'NA'}%|")
        lines.append("")
    lines += [
        "## C5 融资／IF-IC × ETF自身反馈", "",
        payload["c5_financing_futures_interaction"]["reason"], "",
        "## 解释边界", "",
        "- C1关注相对强弱的变化，而不是静态排名。",
        "- C2只问成交参与是否在已有结构背景下增加信息，不把放量本身视为买入。",
        "- C3的宽度只代表正式ETF运行全集，不冒充全A股宽度。",
        "- C4用于识别旧五项信号背后的更稳定语义，不直接替代旧规则。",
        "- 任何正结果还必须经过年度/对象稳定性、条件化增量、去重和资本效率验证后才可能进入正式证据目录。",
    ]
    (OUT / "section6_stage_c_new_evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": "section6_stage_c_new_evidence", "panel_rows": len(panel)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
