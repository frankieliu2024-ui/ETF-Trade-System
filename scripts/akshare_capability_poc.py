from __future__ import annotations

import json
import os
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "data" / "state" / "akshare_poc.json"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(new, old):
    n, o = safe_float(new), safe_float(old)
    if n is None or o in (None, 0.0):
        return None
    return (n / o - 1.0) * 100.0


def col(df, names):
    for name in names:
        if name in df.columns:
            return name
    return None


def current_snapshot():
    current = load_json(ROOT / "data/state/CURRENT.json", {})
    rel = str(current.get("latest_snapshot") or "")
    snap = load_json(ROOT / rel, {}) if rel else {}
    rows = {str(x.get("symbol")): x for x in (snap.get("rows") or []) if x.get("symbol")}
    return current, snap, rows


def universe():
    cfg = load_json(ROOT / "config/market/etf_monitor_universe.json", {})
    return [(str(x.get("code")), str(x.get("name"))) for x in (cfg.get("objects") or []) if x.get("code")]


def minute_path(ak, code: str, market_date: str):
    start = f"{market_date} 09:30:00"
    end = f"{market_date} 15:00:00"
    t0 = time.monotonic()
    df = ak.fund_etf_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
    elapsed = time.monotonic() - t0
    if df is None or df.empty:
        raise RuntimeError("empty 1-minute ETF frame")
    tc = col(df, ["时间", "日期", "datetime", "time"])
    oc = col(df, ["开盘", "open"])
    cc = col(df, ["收盘", "close", "最新价"])
    hc = col(df, ["最高", "high"])
    lc = col(df, ["最低", "low"])
    vc = col(df, ["成交量", "volume"])
    ac = col(df, ["成交额", "amount"])
    required = [tc, oc, cc, hc, lc]
    if any(x is None for x in required):
        raise RuntimeError(f"missing required columns: {list(df.columns)}")
    rows = []
    for _, r in df.iterrows():
        dt = datetime.fromisoformat(str(r[tc])).replace(tzinfo=BEIJING)
        if dt.date().isoformat() != market_date:
            continue
        o, c, h, l = [safe_float(r[x]) for x in (oc, cc, hc, lc)]
        if None in (o, c, h, l) or not (l <= min(o, c) <= max(o, c) <= h):
            raise RuntimeError(f"invalid OHLC at {dt.isoformat()}")
        rows.append({"dt": dt, "open": o, "close": c, "high": h, "low": l,
                     "volume": safe_float(r[vc]) if vc else None, "amount": safe_float(r[ac]) if ac else None})
    if not rows:
        raise RuntimeError("no same-day minute rows")
    rows.sort(key=lambda x: x["dt"])
    timestamps = [x["dt"] for x in rows]
    if len(set(timestamps)) != len(timestamps):
        raise RuntimeError("duplicate minute timestamps")
    low_row = min(rows, key=lambda x: x["low"])
    high_row = max(rows, key=lambda x: x["high"])
    first, last = rows[0], rows[-1]
    if low_row["dt"] < high_row["dt"]:
        sequence = "LOW_THEN_HIGH"
    elif high_row["dt"] < low_row["dt"]:
        sequence = "HIGH_THEN_LOW"
    else:
        sequence = "SAME_OR_UNRESOLVED"
    expected_close = datetime.combine(last["dt"].date(), dtime(15, 0), tzinfo=BEIJING)
    close_lag = max(0.0, (expected_close - last["dt"]).total_seconds())
    return {
        "status": "PASS",
        "adapter": "akshare",
        "upstream_source": "eastmoney",
        "interface": "fund_etf_hist_min_em",
        "period": "1m",
        "request_seconds": round(elapsed, 3),
        "sample_count": len(rows),
        "first_as_of_beijing": first["dt"].isoformat(timespec="seconds"),
        "last_as_of_beijing": last["dt"].isoformat(timespec="seconds"),
        "last_close": last["close"],
        "path_low": low_row["low"],
        "path_low_as_of_beijing": low_row["dt"].isoformat(timespec="seconds"),
        "path_high": high_row["high"],
        "path_high_as_of_beijing": high_row["dt"].isoformat(timespec="seconds"),
        "extreme_sequence": sequence,
        "path_change_pct": round(pct(last["close"], first["open"]), 4) if pct(last["close"], first["open"]) is not None else None,
        "recovery_from_path_low_pct": round(pct(last["close"], low_row["low"]), 4) if pct(last["close"], low_row["low"]) is not None else None,
        "retreat_from_path_high_pct": round(pct(last["close"], high_row["high"]), 4) if pct(last["close"], high_row["high"]) is not None else None,
        "close_lag_seconds": close_lag,
    }


def spot_check(ak, market_date: str, snap_rows: dict):
    t0 = time.monotonic()
    df = ak.fund_etf_spot_em()
    elapsed = time.monotonic() - t0
    if df is None or df.empty:
        raise RuntimeError("empty ETF spot frame")
    code_col = col(df, ["代码", "基金代码", "code"])
    price_col = col(df, ["最新价", "最新", "price"])
    if not code_col or not price_col:
        raise RuntimeError(f"spot missing code/price columns: {list(df.columns)}")
    mapping = {str(r[code_col]).zfill(6): safe_float(r[price_col]) for _, r in df.iterrows()}
    comparisons = []
    for code, row in snap_rows.items():
        if code not in mapping or mapping[code] is None or row.get("close") in (None, 0):
            continue
        diff = pct(mapping[code], row.get("close"))
        comparisons.append({"code": code, "akshare_price": mapping[code], "snapshot_price": row.get("close"), "diff_pct": round(diff, 4) if diff is not None else None})
    comparable = [x for x in comparisons if x.get("diff_pct") is not None]
    max_abs = max((abs(x["diff_pct"]) for x in comparable), default=None)
    return {"status": "PASS" if comparable else "DEGRADED", "adapter": "akshare", "upstream_source": "eastmoney", "interface": "fund_etf_spot_em", "request_seconds": round(elapsed, 3), "comparison_count": len(comparable), "max_abs_diff_pct": round(max_abs, 4) if max_abs is not None else None, "comparisons": comparable}


def main():
    import akshare as ak

    now = datetime.now(BEIJING)
    current, snapshot, snap_rows = current_snapshot()
    market_date = str(current.get("market_date") or now.date().isoformat())
    items = []
    for code, name in universe():
        try:
            result = minute_path(ak, code, market_date)
            result.update({"code": code, "name": name})
            snap = snap_rows.get(code) or {}
            if snap.get("close") not in (None, 0) and result.get("last_close") is not None:
                result["close_vs_formal_snapshot_pct"] = round(pct(result["last_close"], snap.get("close")), 4)
                result["formal_snapshot_as_of_beijing"] = snap.get("as_of_beijing")
            items.append(result)
        except Exception as exc:
            items.append({"code": code, "name": name, "status": "FAILED", "adapter": "akshare", "upstream_source": "eastmoney", "interface": "fund_etf_hist_min_em", "error": str(exc)[-500:]})

    ok_items = [x for x in items if x.get("status") == "PASS"]
    coverage = len(ok_items) / len(items) if items else 0.0
    aligned = [x for x in ok_items if x.get("close_vs_formal_snapshot_pct") is not None]
    alignment_pass = bool(aligned) and max(abs(float(x["close_vs_formal_snapshot_pct"])) for x in aligned) <= 0.30
    close_coverage_pass = bool(ok_items) and sum(1 for x in ok_items if float(x.get("close_lag_seconds") or 999999) <= 300) / len(ok_items) >= 0.90

    try:
        spot = spot_check(ak, market_date, {k: v for k, v in snap_rows.items() if k in {c for c, _ in universe()}})
    except Exception as exc:
        spot = {"status": "FAILED", "adapter": "akshare", "upstream_source": "eastmoney", "interface": "fund_etf_spot_em", "error": str(exc)[-500:]}

    minute_path_pass = coverage >= 0.90 and alignment_pass and close_coverage_pass
    spot_pass = spot.get("status") == "PASS" and (spot.get("max_abs_diff_pct") is not None and float(spot["max_abs_diff_pct"]) <= 0.30)
    status = "PASS" if minute_path_pass else "DEGRADED"
    payload = {
        "generated_at_beijing": now.isoformat(timespec="seconds"),
        "market_date": market_date,
        "status": status,
        "scope": "AKSHARE_DATA_CAPABILITY_POC",
        "tushare_status": "EXCLUDED_BY_USER",
        "dependency": {"akshare_version": getattr(ak, "__version__", "unknown")},
        "source_identity_rule": "AkShare是适配器，不视为独立于其上游的新行情源；本PoC接口上游记录为Eastmoney。",
        "etf_minute_path": {
            "status": "PASS" if minute_path_pass else "DEGRADED",
            "coverage_ratio": round(coverage, 4),
            "pass_count": len(ok_items),
            "total_count": len(items),
            "close_alignment_pass": alignment_pass,
            "close_coverage_pass": close_coverage_pass,
            "items": items,
            "promotion_recommendation": "FORMAL_INTRADAY_PATH_AUXILIARY_CANDIDATE" if minute_path_pass else "KEEP_POC_ONLY",
        },
        "etf_spot_fallback": {
            **spot,
            "promotion_recommendation": "ADAPTER_FALLBACK_CANDIDATE_AFTER_DIRECT_EASTMONEY" if spot_pass else "KEEP_POC_ONLY",
            "independence_note": "由于fund_etf_spot_em上游为Eastmoney，即使验证通过也只能作为技术适配fallback，不增加provider独立性。",
        },
        "decision_integration_gate": {
            "eligible": minute_path_pass,
            "required_role": "intraday_path_evidence",
            "formal_order_target": "市场环境 → 账户风险 → 生命周期 → 全部资本用途统一比较 → 唯一主候选 → 金额动作",
            "structure_framework_target": "历史位置 + 当日日内路径 + 横截面仅作验证",
            "rule": "只有ETF分钟路径PoC通过时才允许接入decision_context；AkShare分钟路径用于增强日内路径/极值时序，不替代腾讯正式最新价，也不改变MASTER交易权限。",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "minute_path": payload["etf_minute_path"]["status"], "spot": spot.get("status"), "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
