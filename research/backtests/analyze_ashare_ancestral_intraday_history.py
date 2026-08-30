#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research" / "backtests" / "revalidate_section6_baselines.py"
UNIVERSE = ROOT / "config" / "market" / "etf_monitor_universe.json"
OUT = ROOT / "research" / "reports" / "generated" / "section6_revalidation"
HORIZONS = (1, 3, 5, 10)

spec = importlib.util.spec_from_file_location("section6_base", BASE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def pct(a, b):
    a, b = f(a), f(b)
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def market_id(code: str) -> str:
    return "1" if code.startswith(("5", "6")) else "0"


def load_universe():
    obj = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    rows = obj.get("objects") or obj.get("etfs") or []
    out = []
    for x in rows:
        code = str(x.get("code") or "")
        if len(code) == 6:
            out.append((code, str(x.get("name") or code)))
    return out


def fetch_5m(code: str):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"{market_id(code)}.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "5",
        "fqt": "0",
        "beg": "20240101",
        "end": "20500101",
        "lmt": "20000",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "_": str(int(time.time() * 1000)),
    }
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={
        "User-Agent": "Mozilla/5.0 ETF-Trade-System research-only",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    node = payload.get("data") or {}
    bars = []
    for s in node.get("klines") or []:
        p = s.split(",")
        if len(p) < 7:
            continue
        dt = datetime.strptime(p[0], "%Y-%m-%d %H:%M")
        bars.append({
            "dt": dt, "date": dt.date().isoformat(), "time": dt.strftime("%H:%M"),
            "open": f(p[1]), "close": f(p[2]), "high": f(p[3]), "low": f(p[4]),
            "volume": f(p[5]), "amount": f(p[6]),
        })
    return bars


def at_or_after(bars, hhmm):
    xs = [x for x in bars if x["time"] >= hhmm]
    return xs[0] if xs else None


def intraday_features(code, name, bars_by_date, daily_by_date, prior20_high):
    out = []
    for d, bars in sorted(bars_by_date.items()):
        day = daily_by_date.get(d)
        if not day or not bars:
            continue
        prev = day.get("prev_close")
        if prev in (None, 0):
            continue
        bars = sorted(bars, key=lambda x: x["dt"])
        first, last = bars[0], bars[-1]
        b1000 = at_or_after(bars, "10:00")
        b1430 = at_or_after(bars, "14:30")
        if not b1000 or not b1430:
            continue
        hi = max(x["high"] for x in bars if x["high"] is not None)
        lo = min(x["low"] for x in bars if x["low"] is not None)
        gap = pct(first["open"], prev)
        r1000 = pct(b1000["close"], prev)
        close_ret = pct(last["close"], prev)
        from_open_1000 = pct(b1000["close"], first["open"])
        tail = pct(last["close"], b1430["close"])
        recovery = pct(last["close"], lo)
        retreat = pct(last["close"], hi)
        clv = None if hi == lo else (last["close"] - lo) / (hi - lo)
        sig = {}
        sig["high_gap_fast_pullback"] = gap is not None and gap >= 1.0 and from_open_1000 is not None and from_open_1000 <= -1.0
        sig["high_gap_hold_continue"] = gap is not None and gap >= 1.0 and b1000["close"] >= first["open"] and last["close"] >= b1000["close"]
        sig["low_gap_v_recovery"] = gap is not None and gap <= -1.0 and recovery is not None and recovery >= 1.5 and last["close"] >= prev
        sig["low_gap_continue_weak"] = gap is not None and gap <= -1.0 and last["close"] <= first["open"] * 0.995
        sig["high_gap_fill"] = gap is not None and gap >= 1.0 and lo <= prev
        sig["high_gap_no_fill"] = gap is not None and gap >= 1.0 and lo > prev
        sig["early_strong_pm_weak"] = r1000 is not None and r1000 >= 1.0 and pct(last["close"], b1000["close"]) <= -1.0
        sig["early_strong_pm_reaccelerate"] = r1000 is not None and r1000 >= 1.0 and pct(last["close"], b1000["close"]) >= 0.5
        sig["v_recovery_intraday"] = pct(lo, prev) is not None and pct(lo, prev) <= -2.0 and recovery is not None and recovery >= 1.5 and clv is not None and clv >= 0.65
        sig["spike_reversal_no_reclaim"] = pct(hi, prev) is not None and pct(hi, prev) >= 2.0 and retreat is not None and retreat <= -1.5 and clv is not None and clv <= 0.35
        sig["high_zone_consolidation"] = pct(hi, prev) is not None and pct(hi, prev) >= 2.0 and retreat is not None and retreat >= -0.75 and clv is not None and clv >= 0.70
        sig["tail_rally"] = tail is not None and tail >= 0.8
        sig["tail_selloff"] = tail is not None and tail <= -0.8

        ph = prior20_high.get(d)
        first_break_idx = None
        if ph is not None:
            for i, x in enumerate(bars):
                if x["close"] is not None and x["close"] > ph:
                    first_break_idx = i
                    break
        sig["breakout_hold_60m"] = False
        sig["breakout_fail_60m"] = False
        if first_break_idx is not None:
            j = first_break_idx + 12
            if j < len(bars):
                sig["breakout_hold_60m"] = bars[j]["close"] > ph
                sig["breakout_fail_60m"] = bars[j]["close"] <= ph

        out.append({
            "date": d, "code": code, "name": name, "gap_pct": gap, "r1000_pct": r1000,
            "close_ret_pct": close_ret, "tail_pct": tail, "clv_5m": clv,
            "signals": sig,
        })
    return out


def stats(vals):
    return mod.stats([v for v in vals if v is not None])


def main():
    by_code, dates = mod.load_panel()
    panel = mod.enrich_future(by_code)
    pmap = {(r["date"], r["code"]): r for r in panel}
    daily_maps = {c: {r["date"]: r for r in rows} for c, rows in by_code.items()}
    prior20 = {}
    for c, rows in by_code.items():
        z = {}
        for i, r in enumerate(rows):
            hist = [q.get("high") for q in rows[max(0, i-20):i] if q.get("high") is not None]
            z[r["date"]] = max(hist) if len(hist) >= 20 else None
        prior20[c] = z

    availability = {}
    intraday_rows = []
    errors = {}
    for code, name in load_universe():
        try:
            bars = fetch_5m(code)
            by_d = defaultdict(list)
            for x in bars:
                by_d[x["date"]].append(x)
            availability[code] = {
                "name": name, "bar_count": len(bars), "date_count": len(by_d),
                "first": bars[0]["dt"].isoformat() if bars else None,
                "last": bars[-1]["dt"].isoformat() if bars else None,
            }
            intraday_rows.extend(intraday_features(code, name, by_d, daily_maps.get(code, {}), prior20.get(code, {})))
        except Exception as exc:
            errors[code] = repr(exc)

    # Cross-sectional 10:00 and close strength expansion/fade.
    by_date = defaultdict(list)
    for r in intraday_rows:
        by_date[r["date"]].append(r)
    for d, xs in by_date.items():
        med10 = mod.median([x["r1000_pct"] for x in xs if x.get("r1000_pct") is not None])
        medc = mod.median([x["close_ret_pct"] for x in xs if x.get("close_ret_pct") is not None])
        for x in xs:
            if med10 is None or medc is None or x.get("r1000_pct") is None or x.get("close_ret_pct") is None:
                x["signals"]["relative_strength_expand"] = False
                x["signals"]["relative_strength_fade"] = False
                continue
            rel10 = x["r1000_pct"] - med10
            relc = x["close_ret_pct"] - medc
            x["signals"]["relative_strength_expand"] = relc - rel10 >= 1.0
            x["signals"]["relative_strength_fade"] = relc - rel10 <= -1.0

    keys = [
        "high_gap_fast_pullback", "high_gap_hold_continue", "low_gap_v_recovery", "low_gap_continue_weak",
        "high_gap_fill", "high_gap_no_fill", "early_strong_pm_weak", "early_strong_pm_reaccelerate",
        "v_recovery_intraday", "spike_reversal_no_reclaim", "high_zone_consolidation", "tail_rally", "tail_selloff",
        "breakout_hold_60m", "breakout_fail_60m", "relative_strength_expand", "relative_strength_fade",
    ]
    result = {}
    for key in keys:
        selected = [r for r in intraday_rows if r["signals"].get(key) and (r["date"], r["code"]) in pmap]
        item = {"count": len(selected), "dates": len(set(r["date"] for r in selected)), "codes": len(set(r["code"] for r in selected)), "horizons": {}}
        for h in HORIZONS:
            vals_abs, vals_rel, vals_mae, vals_mfe = [], [], [], []
            for r in selected:
                q = pmap[(r["date"], r["code"])].get("future", {}).get(h)
                if not q:
                    continue
                vals_abs.append(q.get("return_pct")); vals_rel.append(q.get("relative_to_pool_median_pct_points")); vals_mae.append(q.get("mae_pct")); vals_mfe.append(q.get("mfe_pct"))
            item["horizons"][str(h)] = {"absolute": stats(vals_abs), "relative": stats(vals_rel), "mae": stats(vals_mae), "mfe": stats(vals_mfe)}
        result[key] = item

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_ASHARE_ANCESTRAL_INTRADAY_HISTORY",
        "provider": "EASTMONEY_PUBLIC_5M_KLINE",
        "provider_role": "research_only_external_historical_intraday_evidence_not_production_quote",
        "daily_pit_range": [dates[0], dates[-1]],
        "availability": availability,
        "errors": errors,
        "joined_intraday_rows": len(intraday_rows),
        "candidate_results": result,
        "decision_boundary": "Research-only historical intraday screen. No MASTER change, no trading permission, no amount/sell quantity, no production state write.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ashare_ancestral_intraday_history.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# A股祖训：历史5分钟日内验证（research-only）", "",
        f"provider=Eastmoney public 5m historical kline；可连接ETF={len(availability)}，失败={len(errors)}，与正式daily PIT联结行={len(intraday_rows)}。", "",
        "|候选|样本|日期|对象|T+1相对池|T+3相对池|T+5相对池|T+10相对池|T+5 MAE|T+5 MFE|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in keys:
        z = result[key]
        def m(h, k="relative"):
            return z["horizons"].get(str(h), {}).get(k, {}).get("mean")
        lines.append(f"|{key}|{z['count']}|{z['dates']}|{z['codes']}|{m(1)}|{m(3)}|{m(5)}|{m(10)}|{m(5,'mae')}|{m(5,'mfe')}|")
    lines += ["", "## 边界", "", "- 阈值在运行前冻结，只做祖训原型筛选，不做事后调参。", "- 只有跨日期、跨对象且样本量足够的候选才允许继续解释；小样本只记录，不形成结论。", "- 外部5分钟数据只用于research-only历史验证，不替代正式行情provider或CURRENT。"]
    (OUT / "ashare_ancestral_intraday_history.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "availability": availability, "errors": errors, "rows": len(intraday_rows), "candidates": {k: {"n": v["count"], "t5_rel": v["horizons"]["5"]["relative"].get("mean")} for k, v in result.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
