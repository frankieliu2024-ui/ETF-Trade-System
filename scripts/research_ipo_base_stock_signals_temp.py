from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "research/backtests/ipo_base_stock_signal_validation.json"
RAW = ROOT / "data/market/audit/ipo_base_stock_signal_research"

STOCKS = {
    "300750": {"name": "宁德时代", "benchmark": "sz399006", "benchmark_name": "创业板指"},
    "601138": {"name": "工业富联", "benchmark": "sh000001", "benchmark_name": "上证指数"},
}
START = "20200101"
VALIDATION_START = pd.Timestamp("2024-01-01")
HORIZONS = (5, 10)


def sf(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise RuntimeError("empty historical frame")
    aliases = {
        "日期": "date", "date": "date", "Date": "date",
        "开盘": "open", "open": "open", "Open": "open",
        "收盘": "close", "close": "close", "Close": "close",
        "最高": "high", "high": "high", "High": "high",
        "最低": "low", "low": "low", "Low": "low",
        "成交量": "volume", "volume": "volume", "Volume": "volume",
        "成交额": "amount", "amount": "amount", "Amount": "amount",
    }
    rename = {c: aliases[c] for c in df.columns if c in aliases}
    x = df.rename(columns=rename).copy()
    required = {"date", "close"}
    if not required.issubset(x.columns):
        raise RuntimeError(f"missing columns {required - set(x.columns)} from {list(df.columns)}")
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    for c in ["open", "close", "high", "low", "volume", "amount"]:
        if c in x.columns:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    return x.reset_index(drop=True)


def fetch_index(ak, symbol: str, start: str, end: str) -> tuple[pd.DataFrame, str]:
    errors = []
    for name, call in [
        ("stock_zh_index_daily_em", lambda: ak.stock_zh_index_daily_em(symbol=symbol)),
        ("stock_zh_index_daily", lambda: ak.stock_zh_index_daily(symbol=symbol)),
    ]:
        try:
            x = normalize(call())
            lo, hi = pd.Timestamp(start), pd.Timestamp(end)
            x = x[(x["date"] >= lo) & (x["date"] <= hi)].copy()
            if len(x) >= 300:
                return x, name
        except Exception as exc:
            errors.append(f"{name}:{exc}")
    raise RuntimeError("index fetch failed: " + " | ".join(errors)[-1200:])


def build_frame(stock: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    s = stock.copy()
    b = bench[["date", "close"]].rename(columns={"close": "benchmark_close"})
    x = s.merge(b, on="date", how="inner").sort_values("date").reset_index(drop=True)
    x["ret5"] = x["close"].pct_change(5)
    x["ret20"] = x["close"].pct_change(20)
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma60"] = x["close"].rolling(60).mean()
    x["prior60_high"] = x["high"].shift(1).rolling(60).max() if "high" in x else x["close"].shift(1).rolling(60).max()
    x["prior60_low"] = x["low"].shift(1).rolling(60).min() if "low" in x else x["close"].shift(1).rolling(60).min()
    if "volume" in x:
        x["volume_ratio20"] = x["volume"] / x["volume"].shift(1).rolling(20).mean()
    else:
        x["volume_ratio20"] = np.nan
    for h in HORIZONS:
        x[f"fwd_stock_{h}"] = x["close"].shift(-h) / x["close"] - 1.0
        x[f"fwd_bench_{h}"] = x["benchmark_close"].shift(-h) / x["benchmark_close"] - 1.0
        x[f"fwd_excess_{h}"] = x[f"fwd_stock_{h}"] - x[f"fwd_bench_{h}"]
    return x


def masks(x: pd.DataFrame) -> dict[str, tuple[str, pd.Series]]:
    vr = x["volume_ratio20"].fillna(0)
    return {
        "TREND_CONTINUATION": ("POSITIVE", (x["close"] > x["ma20"]) & (x["ma20"] > x["ma60"]) & (x["ret20"] >= 0.05)),
        "VOLUME_BREAKOUT_60D": ("POSITIVE", (x["close"] >= x["prior60_high"] * 0.995) & (vr >= 1.10)),
        "UPTREND_PULLBACK": ("POSITIVE", (x["close"] > x["ma60"]) & (x["ma20"] > x["ma60"]) & (x["ret5"] <= -0.03) & (x["ret20"] > 0)),
        "OVERSOLD_REVERSAL": ("POSITIVE", (x["ret5"] <= -0.08) & (x["close"] < x["ma20"])),
        "WEAK_TREND": ("NEGATIVE", (x["close"] < x["ma20"]) & (x["ma20"] < x["ma60"]) & (x["ret20"] <= -0.05)),
        "VOLUME_BREAKDOWN_60D": ("NEGATIVE", (x["close"] <= x["prior60_low"] * 1.005) & (vr >= 1.10)),
    }


def stats(x: pd.DataFrame, mask: pd.Series, h: int, start, end=None) -> dict:
    m = mask & (x["date"] >= pd.Timestamp(start))
    if end is not None:
        m &= x["date"] < pd.Timestamp(end)
    vals = x.loc[m, f"fwd_excess_{h}"].dropna()
    stock = x.loc[m, f"fwd_stock_{h}"].dropna()
    if vals.empty:
        return {"n": 0, "mean_excess_pct": None, "median_excess_pct": None, "positive_excess_rate": None, "mean_stock_return_pct": None}
    return {
        "n": int(len(vals)),
        "mean_excess_pct": round(float(vals.mean() * 100), 4),
        "median_excess_pct": round(float(vals.median() * 100), 4),
        "positive_excess_rate": round(float((vals > 0).mean()), 4),
        "mean_stock_return_pct": round(float(stock.mean() * 100), 4) if not stock.empty else None,
    }


def evaluate(x: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    candidates = []
    validated = []
    baseline = {h: stats(x, pd.Series(True, index=x.index), h, VALIDATION_START) for h in HORIZONS}
    for signal_id, (direction, mask) in masks(x).items():
        train = {h: stats(x, mask, h, x["date"].min(), VALIDATION_START) for h in HORIZONS}
        val = {h: stats(x, mask, h, VALIDATION_START) for h in HORIZONS}
        reasons = []
        for h in HORIZONS:
            v, t, b = val[h], train[h], baseline[h]
            if v["n"] < 12:
                reasons.append(f"H{h}_VALIDATION_N_LT_12")
                continue
            if t["n"] < 12:
                reasons.append(f"H{h}_TRAIN_N_LT_12")
                continue
            sign = 1 if direction == "POSITIVE" else -1
            vm = v["mean_excess_pct"]
            tm = t["mean_excess_pct"]
            med = v["median_excess_pct"]
            edge = None if vm is None or b["mean_excess_pct"] is None else vm - b["mean_excess_pct"]
            hit = v["positive_excess_rate"] if direction == "POSITIVE" else (None if v["positive_excess_rate"] is None else 1 - v["positive_excess_rate"])
            if vm is None or sign * vm <= 0.50:
                reasons.append(f"H{h}_MEAN_EXCESS_WEAK")
            if tm is None or sign * tm <= 0:
                reasons.append(f"H{h}_TRAIN_SIGN_NOT_STABLE")
            if med is None or sign * med <= 0:
                reasons.append(f"H{h}_MEDIAN_NOT_ALIGNED")
            if edge is None or sign * edge <= 0.35:
                reasons.append(f"H{h}_NO_INCREMENT_VS_UNCONDITIONAL")
            if hit is None or hit < 0.55:
                reasons.append(f"H{h}_HIT_RATE_LT_55PCT")
        eligible = not reasons
        current_match = bool(mask.iloc[-1]) if len(mask) else False
        item = {
            "signal_id": signal_id,
            "direction": direction,
            "definition_fixed_ex_ante": True,
            "train": train,
            "validation": val,
            "validation_unconditional_baseline": baseline,
            "decision_eligible": eligible,
            "production_context_integration": eligible,
            "current_completed_bar_match": current_match,
            "rejection_reasons": sorted(set(reasons)),
            "trade_signal": None,
        }
        candidates.append(item)
        if eligible:
            validated.append(item)
    return candidates, validated


def main() -> int:
    import akshare as ak

    now = datetime.now(BEIJING)
    end_date = (now.date() - timedelta(days=1)).strftime("%Y%m%d") if now.hour < 15 else now.date().strftime("%Y%m%d")
    stock_market = json.loads((ROOT / "data/state/stock_market_context.json").read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    outputs = []
    all_validated = []
    for code, cfg in STOCKS.items():
        raw = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=START, end_date=end_date, adjust="")
        stock = normalize(raw)
        bench, bench_api = fetch_index(ak, cfg["benchmark"], START, end_date)
        frame = build_frame(stock, bench)
        candidates, validated = evaluate(frame)
        market = (stock_market.get("objects") or {}).get(code) or {}
        last_hist = sf(stock.iloc[-1]["close"])
        current_prev = sf(market.get("prev_close"))
        identity_gap = None if last_hist is None or current_prev in (None, 0) else (last_hist / current_prev - 1) * 100
        identity_pass = identity_gap is not None and abs(identity_gap) <= 0.50
        if not identity_pass:
            for item in validated:
                item["decision_eligible"] = False
                item["production_context_integration"] = False
                item["rejection_reasons"] = sorted(set(item["rejection_reasons"] + ["LATEST_HISTORY_IDENTITY_ALIGNMENT_FAILED"]))
            validated = []
        item = {
            "code": code,
            "display_name": f"{cfg['name']}（{code}）",
            "benchmark": f"{cfg['benchmark_name']}（{cfg['benchmark']}）",
            "data_source": {"stock_adapter": "akshare", "stock_upstream": "eastmoney", "stock_interface": "stock_zh_a_hist", "adjustment": "NONE_RAW_PIT_SAFE", "benchmark_interface": bench_api},
            "sample": {"start": frame["date"].min().date().isoformat(), "end": frame["date"].max().date().isoformat(), "rows": int(len(frame)), "validation_start": VALIDATION_START.date().isoformat()},
            "latest_identity_check": {"historical_last_close": last_hist, "formal_current_prev_close": current_prev, "gap_pct": round(identity_gap, 4) if identity_gap is not None else None, "status": "PASS" if identity_pass else "FAILED"},
            "candidate_signals": candidates,
            "validated_signal_ids": [x["signal_id"] for x in validated],
            "validated_stock_specific_signal_status": "VALIDATED_RESEARCH_SIGNAL_AVAILABLE" if validated else "NO_STABLE_INCREMENTAL_SIGNAL",
            "decision_eligible": bool(validated),
            "production_context_integration": bool(validated),
            "current_completed_bar_matches": [x["signal_id"] for x in validated if x.get("current_completed_bar_match")],
            "automatic_trade": False,
            "trade_signal": None,
        }
        outputs.append(item)
        all_validated.extend([{"code": code, "name": cfg["name"], **x} for x in validated])
        (RAW / f"{code}_study_summary.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "schema_version": "1.0",
        "generated_at_beijing": now.isoformat(timespec="seconds"),
        "mode": "IPO_BASE_STOCK_SPECIFIC_SIGNAL_PIT_VALIDATION",
        "point_in_time": True,
        "research_only_execution": True,
        "automatic_trade": False,
        "trade_signal": None,
        "research_design": {
            "fixed_signal_families": list(masks(pd.DataFrame({"close":[1.0]*70,"ma20":[1.0]*70,"ma60":[1.0]*70,"ret20":[0.0]*70,"ret5":[0.0]*70,"prior60_high":[1.0]*70,"prior60_low":[1.0]*70,"volume_ratio20":[1.0]*70})).keys()),
            "validation_start": VALIDATION_START.date().isoformat(),
            "forward_horizons_trading_days": list(HORIZONS),
            "promotion_gate": "both 5d and 10d: train/validation >=12 signals; validation mean excess >=0.50pp in direction; median aligned; incremental edge vs unconditional >=0.35pp; directional hit rate >=55%; training sign aligned; latest object identity check PASS",
            "multiple_testing_control": "small fixed interpretable family; no parameter grid search; no optimizer; no composite score",
            "boundary": "研究信号只作为执行证据，不生成风险许可、Trial/Confirm、金额、卖出份额或订单。",
        },
        "stocks": outputs,
        "execution_eligible_signal_count": len(all_validated),
        "execution_eligible_signals": all_validated,
        "decision_eligible": bool(all_validated),
        "production_context_integration": bool(all_validated),
        "conclusion": "存在通过固定PIT门禁的个股专项研究信号，可进入只读研究执行桥。" if all_validated else "两只打新底仓在本轮固定PIT信号族中均未形成足够稳定的增量证据；保留研究结论但不进入执行信号。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUT.relative_to(ROOT)), "execution_eligible_signal_count": len(all_validated), "stocks": [{"code": x["code"], "status": x["validated_stock_specific_signal_status"], "validated": x["validated_signal_ids"], "current_matches": x["current_completed_bar_matches"]} for x in outputs]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
