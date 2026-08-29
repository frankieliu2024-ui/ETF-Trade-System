#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE_DIR = ROOT / "events" / "research" / "daily_features"
OUT_DIR = ROOT / "research" / "reports" / "generated" / "section6_revalidation"
HORIZONS = (1, 3, 5, 10)
TECH_CODES = {"561980", "588000", "159781"}


def f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def load_panel():
    by_code = defaultdict(list)
    dates = []
    for path in sorted(FEATURE_DIR.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = str(obj.get("market_date") or path.stem)
        dates.append(d)
        for x in obj.get("features") or []:
            code = str(x.get("code") or "")
            if not code:
                continue
            row = {
                "date": d,
                "code": code,
                "name": str(x.get("name") or code),
                "open": f(x.get("open")),
                "high": f(x.get("high")),
                "low": f(x.get("low")),
                "close": f(x.get("close")),
                "prev_close": f(x.get("prev_close")),
                "amount": f(x.get("amount")),
                "change_pct": f(x.get("close_return_pct")),
            }
            if row["change_pct"] is None:
                row["change_pct"] = pct(row["close"], row["prev_close"])
            if row["high"] is not None and row["low"] is not None and row["high"] != row["low"] and row["close"] is not None:
                row["clv"] = (row["close"] - row["low"]) / (row["high"] - row["low"])
            else:
                row["clv"] = None
            by_code[code].append(row)
    for rows in by_code.values():
        rows.sort(key=lambda r: r["date"])
    return by_code, sorted(set(dates))


def rolling_vals(rows, idx, key, n, include_current=False):
    end = idx + 1 if include_current else idx
    start = max(0, end - n)
    return [rows[j].get(key) for j in range(start, end) if rows[j].get(key) is not None]


def breakout_high(rows, idx, n):
    c = rows[idx]["close"]
    prev = rolling_vals(rows, idx, "close", n - 1, include_current=False)
    return c is not None and len(prev) >= n - 1 and c >= max(prev)


def amount_ratio(rows, idx, n=20, include_current=False):
    cur = rows[idx]["amount"]
    hist = rolling_vals(rows, idx, "amount", n, include_current=include_current)
    avg = mean(hist)
    return None if cur is None or avg in (None, 0) else cur / avg


def sma_close(rows, idx, n=20, include_current=True):
    vals = rolling_vals(rows, idx, "close", n, include_current=include_current)
    return mean(vals) if len(vals) >= n else None


def ret_n(rows, idx, n):
    if idx - n < 0:
        return None
    return pct(rows[idx]["close"], rows[idx - n]["close"])


def prior_range_amp(rows, idx, n=10):
    if idx < n:
        return None
    w = rows[idx-n:idx]
    hs = [r["high"] for r in w if r["high"] is not None]
    ls = [r["low"] for r in w if r["low"] is not None]
    if len(hs) < n or len(ls) < n or min(ls) <= 0:
        return None
    return (max(hs) / min(ls) - 1.0) * 100.0


def five_up(rows, idx):
    if idx < 4:
        return False
    w = rows[idx-4:idx+1]
    return all((r.get("change_pct") or 0) > 0 for r in w)


def classify(rows, idx):
    r = rows[idx]
    clv = r["clv"]
    ret5 = ret_n(rows, idx, 5)
    amt20_ex = amount_ratio(rows, idx, 20, include_current=False)
    amt20_in = amount_ratio(rows, idx, 20, include_current=True)
    ma20 = sma_close(rows, idx, 20, include_current=True)
    prev_ma20 = sma_close(rows, idx-1, 20, include_current=True) if idx > 0 else None
    amp10 = prior_range_amp(rows, idx, 10)
    daily = r["change_pct"]

    # Frozen MASTER baseline. Primary operational interpretation uses only data
    # visible at T close and trailing windows ending before T where comparison
    # leakage would otherwise mechanically dilute the current observation.
    healthy = breakout_high(rows, idx, 60) and amt20_ex is not None and amt20_ex >= 1.0 and clv is not None and clv >= 0.70 and ret5 is not None and ret5 <= 10.0

    prev_close = rows[idx-1]["close"] if idx > 0 else None
    recovery = (
        prev_close is not None and prev_ma20 is not None and prev_close < prev_ma20
        and r["close"] is not None and ma20 is not None and r["close"] >= ma20
        and amt20_ex is not None and amt20_ex >= 1.0
        and clv is not None and clv >= 0.70
    )

    consolidation = (
        amp10 is not None and amp10 <= 6.0
        and breakout_high(rows, idx, 20)
        and amt20_ex is not None and amt20_ex >= 1.2
        and clv is not None and clv >= 0.75
    )

    tech = (
        r["code"] in TECH_CODES
        and daily is not None and daily >= 3.0
        and clv is not None and clv >= 0.75
        and amt20_ex is not None and 1.0 <= amt20_ex <= 1.8
        and ret5 is not None and ret5 <= 8.0
    )

    consecutive = five_up(rows, idx) and ret5 is not None and ret5 >= 10.0

    # Semantic sensitivity: include T in the 20d amount average. Kept separate
    # so ambiguity is visible rather than optimized after seeing outcomes.
    healthy_amt_inclusive = breakout_high(rows, idx, 60) and amt20_in is not None and amt20_in >= 1.0 and clv is not None and clv >= 0.70 and ret5 is not None and ret5 <= 10.0

    return {
        "healthy_breakout": healthy,
        "volume_recovery": recovery,
        "consolidation_breakout": consolidation,
        "tech_risk_appetite": tech,
        "consecutive_rise_risk_review": consecutive,
        "sensitivity_healthy_amount20_inclusive": healthy_amt_inclusive,
    }


def enrich_future(by_code):
    all_rows = []
    for code, rows in by_code.items():
        for i, r in enumerate(rows):
            x = dict(r)
            x["signals"] = classify(rows, i)
            x["future"] = {}
            for h in HORIZONS:
                if i + h >= len(rows) or r["close"] in (None, 0):
                    continue
                end = rows[i+h]["close"]
                rr = pct(end, r["close"])
                path = rows[i+1:i+h+1]
                lows = [q["low"] for q in path if q["low"] is not None]
                highs = [q["high"] for q in path if q["high"] is not None]
                mae = pct(min(lows), r["close"]) if lows else None
                mfe = pct(max(highs), r["close"]) if highs else None
                x["future"][h] = {"return_pct": rr, "mae_pct": mae, "mfe_pct": mfe}
            all_rows.append(x)
    # Cross-sectional future return median by signal date/horizon.
    med = {}
    for h in HORIZONS:
        grouped = defaultdict(list)
        for r in all_rows:
            q = r["future"].get(h)
            if q and q.get("return_pct") is not None:
                grouped[r["date"]].append(q["return_pct"])
        med[h] = {d: median(xs) for d, xs in grouped.items()}
    for r in all_rows:
        for h, q in r["future"].items():
            m = med[h].get(r["date"])
            q["relative_to_pool_median_pct_points"] = None if m is None or q["return_pct"] is None else q["return_pct"] - m
    return all_rows


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(mean(vals), 4),
        "median": round(median(vals), 4),
        "positive_rate": round(sum(v > 0 for v in vals) / len(vals), 4),
        "p10": round(sorted(vals)[max(0, int(len(vals)*0.10)-1)], 4),
        "p90": round(sorted(vals)[min(len(vals)-1, int(len(vals)*0.90))], 4),
    }


def summarize(rows):
    keys = [
        "healthy_breakout", "volume_recovery", "consolidation_breakout",
        "tech_risk_appetite", "consecutive_rise_risk_review",
        "sensitivity_healthy_amount20_inclusive",
    ]
    out = {}
    for key in keys:
        selected = [r for r in rows if r["signals"].get(key)]
        item = {
            "signal_count": len(selected),
            "codes": dict(sorted({c: sum(r["code"] == c for r in selected) for c in {r["code"] for r in selected}}.items())),
            "years": dict(sorted({y: sum(r["date"].startswith(y) for r in selected) for y in {r["date"][:4] for r in selected}}.items())),
            "horizons": {},
        }
        for h in HORIZONS:
            eligible = [r for r in selected if h in r["future"]]
            item["horizons"][str(h)] = {
                "absolute_return": stats([r["future"][h]["return_pct"] for r in eligible]),
                "relative_to_pool": stats([r["future"][h]["relative_to_pool_median_pct_points"] for r in eligible]),
                "mae": stats([r["future"][h]["mae_pct"] for r in eligible]),
                "mfe": stats([r["future"][h]["mfe_pct"] for r in eligible]),
            }
        out[key] = item
    return out


def markdown(summary, panel_rows, codes, dates):
    lines = [
        "# MASTER 6.2 五项历史机会依据：云端PIT baseline再验证（自动生成）",
        "",
        f"- 覆盖日期：{dates[0] if dates else 'N/A'} 至 {dates[-1] if dates else 'N/A'}",
        f"- ETF×交易日记录：{len(panel_rows)}",
        f"- 对象数：{len(codes)}",
        "- 信号日信息集：只使用T日完整收盘及T日前可见历史；未来收益从T收盘到T+h收盘，仅用于研究评价。",
        "- 本报告是baseline结果，不构成MASTER修改；参数未按结果优化。",
        "",
        "|依据|命中数|T+1均值|T+3均值|T+5均值|T+10均值|T+5相对池均值|T+5 MAE均值|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "healthy_breakout":"健康突破",
        "volume_recovery":"放量修复",
        "consolidation_breakout":"整理突破",
        "tech_risk_appetite":"科技风险偏好改善",
        "consecutive_rise_risk_review":"连续上涨风险复核",
        "sensitivity_healthy_amount20_inclusive":"健康突破（成交额20日均值含T敏感性）",
    }
    def v(key, h, typ, field="mean"):
        z = summary[key]["horizons"].get(str(h), {}).get(typ, {})
        x = z.get(field)
        return "NA" if x is None else f"{x:.3f}%"
    for key in labels:
        lines.append(
            f"|{labels[key]}|{summary[key]['signal_count']}|{v(key,1,'absolute_return')}|{v(key,3,'absolute_return')}|{v(key,5,'absolute_return')}|{v(key,10,'absolute_return')}|{v(key,5,'relative_to_pool')}|{v(key,5,'mae')}|"
        )
    lines += [
        "",
        "## 解释边界",
        "",
        "1. 命中数过少时不作稳定性结论；必须继续看年度/对象分布及后续增量、消融和市场状态分层。",
        "2. `健康突破`成交额20日均值的历史文字存在“是否包含T日”的实现歧义，因此预注册主口径为不含T日，并同时输出含T日敏感性；不得按表现挑选口径。",
        "3. `放量修复`按真正的“前一日收盘低于前一日MA20、T日收盘站上T日MA20”识别，而非静态“收盘位于MA20上方”。",
        "4. `整理突破`的前10日振幅按T日前10个完整交易日的最高价/最低价计算；20日收盘新高要求T收盘达到此前19个收盘价的最高水平。",
        "5. `连续上涨风险复核`不能仅按未来收益决定去留；本baseline只提供后续收益/MAE/MFE，正式研究还必须比较持有右尾损失与风险缓释价值。",
        "6. 下一阶段将把五项依据与通用趋势、相对强弱、日内路径、分钟边际承接及6.4正式证据做增量/替代关系检验。",
        "",
    ]
    return "\n".join(lines)


def main():
    by_code, dates = load_panel()
    panel = enrich_future(by_code)
    summary = summarize(panel)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_SECTION6_BASELINE_REVALIDATION",
        "point_in_time": True,
        "parameter_optimization": False,
        "date_range": [dates[0], dates[-1]] if dates else [],
        "panel_row_count": len(panel),
        "codes": sorted(by_code),
        "summary": summary,
        "decision_boundary": "Research-only baseline; no risk permission, Trial/Confirm, amount, sell share or order authority.",
    }
    (OUT_DIR / "section6_baseline_revalidation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "section6_baseline_revalidation.md").write_text(markdown(summary, panel, sorted(by_code), dates) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "date_range": payload["date_range"], "panel_row_count": len(panel), "signal_counts": {k:v["signal_count"] for k,v in summary.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
