#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research" / "backtests" / "revalidate_section6_baselines.py"
OUT = ROOT / "research" / "reports" / "generated" / "section6_revalidation"

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

HORIZONS = (1, 3, 5, 10)
OLD_KEYS = (
    "healthy_breakout",
    "volume_recovery",
    "consolidation_breakout",
    "tech_risk_appetite",
    "consecutive_rise_risk_review",
)


def wd(d: str) -> int:
    return date.fromisoformat(d).weekday()


def stats(vals):
    return mod.stats([v for v in vals if v is not None])


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def median(xs):
    return mod.median([x for x in xs if x is not None])


def rel(rows, h):
    return stats([r["future"][h].get("relative_to_pool_median_pct_points") for r in rows if h in r.get("future", {})])


def absret(rows, h):
    return stats([r["future"][h].get("return_pct") for r in rows if h in r.get("future", {})])


def mae(rows, h):
    return stats([r["future"][h].get("mae_pct") for r in rows if h in r.get("future", {})])


def mfe(rows, h):
    return stats([r["future"][h].get("mfe_pct") for r in rows if h in r.get("future", {})])


def consecutive_prior(rows, i, n, positive=True):
    if i < n:
        return False
    vals = [rows[j].get("change_pct") for j in range(i-n, i)]
    if any(v is None for v in vals):
        return False
    return all(v > 0 for v in vals) if positive else all(v < 0 for v in vals)


def day_level_black_thursday(panel):
    by_date = defaultdict(list)
    for r in panel:
        by_date[r["date"]].append(r)

    # Build one observation per date to avoid pseudoreplication across ETFs.
    daily = []
    for d, rows in sorted(by_date.items()):
        item = {"date": d, "weekday": wd(d), "n": len(rows), "h": {}}
        for h in HORIZONS:
            vals = [r["future"][h].get("return_pct") for r in rows if h in r.get("future", {}) and r["future"][h].get("return_pct") is not None]
            if vals:
                item["h"][h] = {"pool_mean_forward": mean(vals), "pool_median_forward": median(vals)}
        daily.append(item)

    # Approximate prior 3-session pool move using date-level median current-day return.
    cur_pool = {}
    for d, rows in by_date.items():
        vals = [r.get("change_pct") for r in rows if r.get("change_pct") is not None]
        cur_pool[d] = median(vals) if vals else None
    dates = [x["date"] for x in daily]
    idx = {d: i for i, d in enumerate(dates)}
    for x in daily:
        i = idx[x["date"]]
        prior = [cur_pool.get(dates[j]) for j in range(max(0, i-3), i)]
        x["prior3_pool_sum"] = sum(prior) if len(prior) == 3 and all(v is not None for v in prior) else None

    def summary(rows, h):
        mean_vals = [x["h"].get(h, {}).get("pool_mean_forward") for x in rows]
        med_vals = [x["h"].get(h, {}).get("pool_median_forward") for x in rows]
        return {"date_count": len([x for x in rows if h in x["h"]]), "pool_mean_forward": stats(mean_vals), "pool_median_forward": stats(med_vals)}

    out = {"unconditional": {}, "after_prior3_rise": {}, "after_prior3_fall": {}}
    th = [x for x in daily if x["weekday"] == 3]
    non = [x for x in daily if x["weekday"] != 3]
    th_rise = [x for x in th if x.get("prior3_pool_sum") is not None and x["prior3_pool_sum"] >= 3.0]
    non_rise = [x for x in non if x.get("prior3_pool_sum") is not None and x["prior3_pool_sum"] >= 3.0]
    th_fall = [x for x in th if x.get("prior3_pool_sum") is not None and x["prior3_pool_sum"] <= -3.0]
    non_fall = [x for x in non if x.get("prior3_pool_sum") is not None and x["prior3_pool_sum"] <= -3.0]

    for h in HORIZONS:
        out["unconditional"][str(h)] = {"thursday": summary(th, h), "non_thursday": summary(non, h)}
        out["after_prior3_rise"][str(h)] = {"thursday": summary(th_rise, h), "non_thursday": summary(non_rise, h)}
        out["after_prior3_fall"][str(h)] = {"thursday": summary(th_fall, h), "non_thursday": summary(non_fall, h)}
    return out


def candidate_controls(by_code, panel):
    row_map = {(r["date"], r["code"]): r for r in panel}
    three_up_weak = []
    three_up_other = []
    vol_weak = []
    vol_middle = []
    vol_strong = []
    vol_weak_non_old = []

    for code, rows in by_code.items():
        for i, base in enumerate(rows):
            r = row_map.get((base["date"], code))
            if not r:
                continue
            c = r.get("clv")
            close_ret = r.get("change_pct")
            amt = mod.amount_ratio(rows, i, 20, include_current=False)
            prior3up = consecutive_prior(rows, i, 3, True)
            old_hit = any(r.get("signals", {}).get(k) for k in OLD_KEYS)

            if prior3up and c is not None:
                if c <= 0.35:
                    three_up_weak.append(r)
                else:
                    three_up_other.append(r)

            if amt is not None and amt >= 1.2 and close_ret is not None and close_ret > 0 and c is not None:
                if c <= 0.35:
                    vol_weak.append(r)
                    if not old_hit:
                        vol_weak_non_old.append(r)
                elif c >= 0.75:
                    vol_strong.append(r)
                else:
                    vol_middle.append(r)

    def summarize(rows):
        return {
            "count": len(rows),
            "horizons": {
                str(h): {"absolute": absret(rows, h), "relative": rel(rows, h), "mae": mae(rows, h), "mfe": mfe(rows, h)}
                for h in HORIZONS
            },
        }

    return {
        "three_up_close_quality": {
            "weak_close": summarize(three_up_weak),
            "other_close": summarize(three_up_other),
            "interpretation": "Tests whether a weak close after three prior up days independently supports risk reduction, or whether winner-right-tail protection remains important.",
        },
        "volume_expand_positive_day_close_quality": {
            "weak_close": summarize(vol_weak),
            "middle_close": summarize(vol_middle),
            "strong_close": summarize(vol_strong),
            "weak_close_excluding_old_6_2": summarize(vol_weak_non_old),
            "interpretation": "Tests close-location discrimination under the same positive-day + amount-expansion background; excludes old 6.2 overlap as an incremental check.",
        },
    }


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_ASHARE_ANCESTRAL_RULES_CLOUD_STAGE2",
        "issue": 107,
        "date_range": [dates[0], dates[-1]],
        "panel_rows": len(panel),
        "black_thursday_date_level": day_level_black_thursday(panel),
        "sell_boundary_controls": candidate_controls(by_code, panel),
        "decision_boundary": "Research-only conditional controls. No trading permission, amount, sell quantity, ranking, or automatic action.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ashare_ancestral_rules_cloud_stage2.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def gm(group, h=5):
        return group[str(h)]["thursday"]["pool_median_forward"].get("mean")
    def ngm(group, h=5):
        return group[str(h)]["non_thursday"]["pool_median_forward"].get("mean")
    b = payload["black_thursday_date_level"]
    s = payload["sell_boundary_controls"]
    lines = [
        "# A股祖训云端Stage2：星期效应去伪样本 + 卖出边界条件对照",
        "",
        f"覆盖：{dates[0]} 至 {dates[-1]}；ETF×交易日={len(panel)}。星期效应按日期聚合，不把同一天多只ETF当成独立星期样本。",
        "",
        "## 黑色星期四：日期级结果",
        "",
        f"- 无条件：周四T+5等权ETF池横截面中位未来收益={gm(b['unconditional'])}%，非周四={ngm(b['unconditional'])}%。",
        f"- 前3个交易日ETF池累计中位涨幅≥3%：周四T+5={gm(b['after_prior3_rise'])}%，非周四={ngm(b['after_prior3_rise'])}%。",
        f"- 前3个交易日ETF池累计中位跌幅≤-3%：周四T+5={gm(b['after_prior3_fall'])}%，非周四={ngm(b['after_prior3_fall'])}%。",
        "",
        "若周四未相对同条件非周四呈稳定负差，则‘星期四’本身不应保留为风险祖训；真正有效的信息应回到涨跌状态、结构和承接。",
        "",
        "## 连涨后弱收盘：卖出边界",
        "",
    ]
    for name, z in s["three_up_close_quality"].items():
        if not isinstance(z, dict) or "horizons" not in z:
            continue
        lines.append(f"- {name}: n={z['count']}, T+5相对ETF池={z['horizons']['5']['relative'].get('mean')}%, T+5 MAE={z['horizons']['5']['mae'].get('mean')}%, T+5 MFE={z['horizons']['5']['mfe'].get('mean')}%")
    lines += ["", "## 放量上涨日的收盘质量：卖出边界", ""]
    for name, z in s["volume_expand_positive_day_close_quality"].items():
        if not isinstance(z, dict) or "horizons" not in z:
            continue
        lines.append(f"- {name}: n={z['count']}, T+5相对ETF池={z['horizons']['5']['relative'].get('mean')}%, T+5 MAE={z['horizons']['5']['mae'].get('mean')}%, T+5 MFE={z['horizons']['5']['mfe'].get('mean')}%")
    lines += [
        "",
        "## 判读边界",
        "",
        "1. 日期级星期效应用于检验日历标签是否有独立价值，不做p值挖掘或节假日后验分组。",
        "2. 卖出边界对照只比较同一背景下的收盘质量，不自动生成卖出动作；重点是是否存在机械减仓会损失赢家右尾的证据。",
        "3. 如果结果主要由单一对象或单一年度解释，Stage2不升级；需要进一步对象/年度稳定性或forward自然样本。",
        "4. 上午/下午、尾盘、分钟承接仍不在本阶段；覆盖审计通过后才进入Stage3。",
    ]
    (OUT / "ashare_ancestral_rules_cloud_stage2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "issue": 107, "panel_rows": len(panel)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
