#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from revalidate_section6_baselines import load_panel, enrich_future, stats, OUT_DIR

TECH_CODES = ("561980", "588000", "159781")


def metric(rows, key="return_pct"):
    vals = [r["future"][5].get(key) for r in rows if 5 in r["future"] and r["future"][5].get(key) is not None]
    return stats(vals)


def rel(rows):
    vals = [r["future"][5].get("relative_to_pool_median_pct_points") for r in rows if 5 in r["future"] and r["future"][5].get("relative_to_pool_median_pct_points") is not None]
    return stats(vals)


def summarize(rows):
    return {"n": len(rows), "abs_t5": metric(rows), "rel_t5": rel(rows)}


def main():
    by_code, dates = load_panel()
    panel = enrich_future(by_code)
    sig = [r for r in panel if r["signals"].get("tech_risk_appetite") and 5 in r["future"]]
    by_code_sig = defaultdict(list)
    for r in sig:
        by_code_sig[r["code"]].append(r)

    groups = {code: summarize(by_code_sig.get(code, [])) for code in TECH_CODES}
    groups["EX_561980"] = summarize([r for r in sig if r["code"] != "561980"])
    groups["ONLY_561980"] = summarize([r for r in sig if r["code"] == "561980"])
    groups["ALL_TECH_SIGNAL"] = summarize(sig)

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_TECH_OBJECT_CONCENTRATION",
        "date_range": [dates[0], dates[-1]],
        "groups": groups,
        "interpretation_boundary": "Object-concentration diagnostic only; does not alter MASTER or formal evidence qualification.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "section6_tech_object_concentration.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 科技风险偏好改善：对象集中度检验（自动生成）",
        "",
        "本轮冻结现行信号定义，只检查总体结果是否主要由半导体设备ETF（561980）驱动，不调参。",
        "",
        "|分组|样本|T+5均值|T+5相对ETF池均值|",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "561980":"半导体设备ETF（561980）",
        "588000":"科创50ETF（588000）",
        "159781":"科创创业ETF（159781）",
        "EX_561980":"剔除半导体设备ETF（561980）",
        "ONLY_561980":"仅半导体设备ETF（561980）",
        "ALL_TECH_SIGNAL":"全部科技信号",
    }
    for k in ("561980","588000","159781","EX_561980","ONLY_561980","ALL_TECH_SIGNAL"):
        z = groups[k]
        lines.append(f"|{labels[k]}|{z['n']}|{z['abs_t5'].get('mean')}%|{z['rel_t5'].get('mean')}%|")
    lines += [
        "",
        "## 判读边界",
        "",
        "- 若剔除561980后仍保持正相对增量，说明旧科技结构并非完全等同于561980对象效应；若显著收缩，则应在后续MASTER重构中收窄其通用性表述。",
        "- 当前历史面板不能伪造后来才上线的561980核心成分动态领先和实时海外残差；与这些云端原生证据的增量关系应使用可重建历史材料或上线后的真实前瞻样本继续验证。",
        "- 本研究不产生风险许可、Trial/Confirm、金额、卖出份额或订单。",
        "",
    ]
    (OUT_DIR / "section6_tech_object_concentration.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True, "groups": {k:{"n":v['n'],"abs":v['abs_t5'].get('mean'),"rel":v['rel_t5'].get('mean')} for k,v in groups.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
