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


def rel5(rows):
    return mod.stats([r["future"][5].get("relative_to_pool_median_pct_points") for r in rows if 5 in r.get("future", {})])


def group(rows, field):
    g = defaultdict(list)
    for r in rows:
        g[str(r.get(field))].append(r)
    return {k: {"count": len(v), "t5_relative": rel5(v)} for k, v in sorted(g.items())}


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    by_date = defaultdict(list)
    by_key = {(r["date"], r["code"]): r for r in panel}
    for r in panel:
        r["year"] = r["date"][:4]
        by_date[r["date"]].append(r)

    for d, rows in by_date.items():
        vals = [r.get("change_pct") for r in rows if r.get("change_pct") is not None]
        med = mod.median(vals)
        pos = sum((x or 0) > 0 for x in vals)
        share = pos / len(vals) if vals else None
        for r in rows:
            r["breadth_positive_share"] = share
            r["rel_pool_t"] = None if med is None or r.get("change_pct") is None else r["change_pct"] - med

    idx_map = {}
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            idx_map[(r["date"], code)] = (rows, i)

    c2 = []
    c3 = []
    for r in panel:
        rows, i = idx_map[(r["date"], r["code"])]
        amt_ratio = mod.amount_ratio(rows, i, 20, include_current=False)
        ma20 = mod.sma_close(rows, i, 20, include_current=True)
        prev_ma20 = mod.sma_close(rows, i-1, 20, include_current=True) if i > 0 else None
        prev_close = rows[i-1].get("close") if i > 0 else None
        breakout20 = mod.breakout_high(rows, i, 20)
        recovery20 = bool(prev_close is not None and prev_ma20 is not None and prev_close < prev_ma20 and r.get("close") is not None and ma20 is not None and r["close"] >= ma20)
        if amt_ratio is not None and amt_ratio >= 1.2 and (breakout20 or recovery20):
            c2.append(r)
        if r.get("breadth_positive_share") is not None and r["breadth_positive_share"] >= 0.65 and (r.get("rel_pool_t") or 0) >= 1.0:
            c3.append(r)

    old_keys = ["healthy_breakout", "volume_recovery", "consolidation_breakout", "tech_risk_appetite"]
    def overlap(rows):
        out = {}
        for k in old_keys:
            n = sum(bool(r.get("signals", {}).get(k)) for r in rows)
            out[k] = {"count": n, "share": round(n / len(rows), 4) if rows else 0}
        any_old = sum(any(r.get("signals", {}).get(k) for k in old_keys) for r in rows)
        out["any_old_6_2_positive_signal"] = {"count": any_old, "share": round(any_old / len(rows), 4) if rows else 0}
        return out

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_STAGE_E_VALIDATION",
        "date_range": [dates[0], dates[-1]],
        "c2_participation_positive_structure": {
            "count": len(c2),
            "overall": rel5(c2),
            "by_year": group(c2, "year"),
            "by_code": group(c2, "code"),
            "overlap_with_old_6_2": overlap(c2),
            "non_old_subset": rel5([r for r in c2 if not any(r.get("signals", {}).get(k) for k in old_keys)]),
        },
        "c3_strong_market_leader": {
            "count": len(c3),
            "overall": rel5(c3),
            "by_year": group(c3, "year"),
            "by_code": group(c3, "code"),
            "overlap_with_old_6_2": overlap(c3),
            "non_old_subset": rel5([r for r in c3 if not any(r.get("signals", {}).get(k) for k in old_keys)]),
        },
        "decision_boundary": "稳定性、重叠和非旧信号子集验证；不构成正式规则或交易信号。",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "section6_stage_e_validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["# 第6节阶段E：新增证据稳定性与去重验证（自动生成）", ""]
    for title, key in [("C2 成交参与扩大×正价格结构", "c2_participation_positive_structure"), ("C3 强市相对领涨", "c3_strong_market_leader")]:
        z = payload[key]
        lines += [f"## {title}", "", f"总样本：{z['count']}；T+5相对ETF池均值={z['overall'].get('mean')}%。", "", "年度："]
        for y, yz in z["by_year"].items():
            lines.append(f"- {y}: n={yz['count']}, T+5相对ETF池={yz['t5_relative'].get('mean')}%")
        lines += ["", "与旧6.2正向机会依据重叠："]
        for k, ov in z["overlap_with_old_6_2"].items():
            lines.append(f"- {k}: {ov['count']} / {z['count']} ({ov['share']:.2%})")
        lines += ["", f"剔除所有旧6.2正向信号后：T+5相对ETF池={z['non_old_subset'].get('mean')}%，n={z['non_old_subset'].get('n',0)}。", ""]
    lines += ["## 判读", "", "只有年度方向、对象分布和非旧信号子集仍有增量时，才值得作为新增正式证据；否则优先合并进旧证据或基础语义。"]
    (OUT / "section6_stage_e_validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "c2": len(c2), "c3": len(c3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
