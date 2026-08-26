from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
OUT = ROOT / "data/state/minute_source_poc.json"
UA = "ETF-Trade-System/2.2.16 minute-source-poc"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def universe():
    cfg = load_json(ROOT / "config/market/etf_monitor_universe.json", {})
    return [(str(x.get("code")), str(x.get("name")), str(x.get("thscode"))) for x in (cfg.get("objects") or []) if x.get("code")]


def current_snapshot_rows():
    current = load_json(ROOT / "data/state/CURRENT.json", {})
    rel = str(current.get("latest_snapshot") or "")
    snap = load_json(ROOT / rel, {}) if rel else {}
    rows = {str(x.get("symbol")): x for x in (snap.get("rows") or []) if x.get("symbol")}
    return current, rows


def market_prefix(thscode: str) -> str:
    code, suffix = thscode.upper().split(".", 1)
    return ("sh" if suffix == "SH" else "sz") + code


def secid(thscode: str) -> str:
    code, suffix = thscode.upper().split(".", 1)
    return ("1." if suffix == "SH" else "0.") + code


def request_json(url: str, timeout: float = 8.0, referer: str = "https://gu.qq.com/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer, "Accept": "application/json,text/plain,*/*"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        status = int(resp.status)
    elapsed = time.monotonic() - t0
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    text = raw.decode("utf-8", "replace").strip()
    if text.startswith("min_data_") and "=" in text:
        text = text.split("=", 1)[1].strip().rstrip(";")
    return json.loads(text), elapsed


def parse_dt(date_text: str, hm: str) -> datetime:
    return datetime.strptime(f"{date_text} {hm}", "%Y%m%d %H%M").replace(tzinfo=BEIJING)


def in_a_share_session(dt: datetime, market_date: str) -> bool:
    if dt.date().isoformat() != market_date:
        return False
    minute = dt.hour * 60 + dt.minute
    return (570 <= minute <= 690) or (780 <= minute <= 900)


def session_points(points: list[dict], market_date: str) -> list[dict]:
    return [x for x in points if in_a_share_session(x["dt"], market_date)]


def summarize_points(points: list[dict], formal_close, formal_as_of, market_date: str):
    points = session_points(points, market_date)
    if not points:
        raise RuntimeError("empty same-day A-share session minute rows")
    points.sort(key=lambda x: x["dt"])
    if len({x["dt"] for x in points}) != len(points):
        raise RuntimeError("duplicate minute timestamps")
    first, last = points[0], points[-1]
    low = min(points, key=lambda x: x.get("low", x["price"]))
    high = max(points, key=lambda x: x.get("high", x["price"]))
    last_price = last.get("close", last.get("price"))
    diff_pct = None
    try:
        if formal_close not in (None, 0):
            diff_pct = (float(last_price) / float(formal_close) - 1.0) * 100.0
    except Exception:
        pass
    expected_minutes = 242
    return {
        "sample_count": len(points),
        "expected_full_day_minute_points": expected_minutes,
        "coverage_ratio": round(min(1.0, len(points) / expected_minutes), 4),
        "first_as_of_beijing": first["dt"].isoformat(timespec="seconds"),
        "last_as_of_beijing": last["dt"].isoformat(timespec="seconds"),
        "last_price": last_price,
        "formal_snapshot_price": formal_close,
        "formal_snapshot_as_of_beijing": formal_as_of,
        "close_vs_formal_snapshot_pct": round(diff_pct, 4) if diff_pct is not None else None,
        "path_low": low.get("low", low["price"]),
        "path_low_as_of_beijing": low["dt"].isoformat(timespec="seconds"),
        "path_high": high.get("high", high["price"]),
        "path_high_as_of_beijing": high["dt"].isoformat(timespec="seconds"),
        "has_cumulative_volume": any(x.get("cum_volume") is not None for x in points),
        "has_cumulative_amount": any(x.get("cum_amount") is not None for x in points),
        "has_ohlc": all(all(k in x for k in ("open", "close", "high", "low")) for x in points),
        "session_filter": "09:30-11:30 and 13:00-15:00 Beijing, same market date only",
    }


def tencent_minute_query(thscode: str, formal_row: dict, market_date: str):
    symbol = market_prefix(thscode)
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?" + urllib.parse.urlencode({"code": symbol, "r": str(time.time())})
    payload, elapsed = request_json(url)
    if payload.get("code") != 0:
        raise RuntimeError(f"provider code={payload.get('code')} msg={payload.get('msg')}")
    node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
    date_text = str(node.get("date") or "")
    points = []
    for raw in node.get("data") or []:
        parts = str(raw).split()
        if len(parts) < 3:
            continue
        points.append({"dt": parse_dt(date_text, parts[0]), "price": float(parts[1]), "cum_volume": float(parts[2]), "cum_amount": float(parts[3]) if len(parts) >= 4 else None})
    summary = summarize_points(points, formal_row.get("close"), formal_row.get("as_of_beijing"), market_date)
    return {"status": "PASS", "provider": "tencent_qq", "endpoint": "web.ifzq.gtimg.cn/appstock/app/minute/query", "period": "1m transaction-price path", "request_seconds": round(elapsed, 3), **summary}


def tencent_mkline(thscode: str, formal_row: dict, market_date: str):
    symbol = market_prefix(thscode)
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?" + urllib.parse.urlencode({"param": f"{symbol},m1,,320"})
    payload, elapsed = request_json(url)
    if payload.get("code") != 0:
        raise RuntimeError(f"provider code={payload.get('code')} msg={payload.get('msg')}")
    node = (payload.get("data") or {}).get(symbol) or {}
    points = []
    for raw in node.get("m1") or []:
        if not isinstance(raw, list) or len(raw) < 6:
            continue
        dt = datetime.strptime(str(raw[0]), "%Y%m%d%H%M").replace(tzinfo=BEIJING)
        points.append({"dt": dt, "open": float(raw[1]), "close": float(raw[2]), "high": float(raw[3]), "low": float(raw[4]), "price": float(raw[2]), "volume": float(raw[5])})
    summary = summarize_points(points, formal_row.get("close"), formal_row.get("as_of_beijing"), market_date)
    return {"status": "PASS", "provider": "tencent_qq", "endpoint": "ifzq.gtimg.cn/appstock/app/kline/mkline", "period": "1m OHLC", "request_seconds": round(elapsed, 3), **summary}


def eastmoney_trends(thscode: str, formal_row: dict, market_date: str):
    params = {"secid": secid(thscode), "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "ut": "fa5fd1943c7b386f172d6893dbfba10b", "ndays": "1", "iscr": "0"}
    url = "https://push2.eastmoney.com/api/qt/stock/trends2/get?" + urllib.parse.urlencode(params)
    payload, elapsed = request_json(url, referer="https://quote.eastmoney.com/")
    points = []
    for raw in ((payload.get("data") or {}).get("trends") or []):
        p = str(raw).split(",")
        if len(p) < 7:
            continue
        dt = datetime.strptime(p[0], "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
        points.append({"dt": dt, "price": float(p[1]), "cum_volume": float(p[5]), "cum_amount": float(p[6])})
    summary = summarize_points(points, formal_row.get("close"), formal_row.get("as_of_beijing"), market_date)
    return {"status": "PASS", "provider": "eastmoney_push2", "endpoint": "push2.eastmoney.com/api/qt/stock/trends2/get", "period": "1m transaction-price path", "request_seconds": round(elapsed, 3), **summary}


def run_source(name: str, fn, items, snapshot_rows, market_date: str):
    results = []
    t0 = time.monotonic()
    for code, label, thscode in items:
        formal = snapshot_rows.get(code) or {}
        try:
            row = fn(thscode, formal, market_date)
            row.update({"code": code, "name": label, "thscode": thscode})
        except Exception as exc:
            row = {"code": code, "name": label, "thscode": thscode, "status": "FAILED", "error": str(exc)[-600:]}
        results.append(row)
    total_elapsed = time.monotonic() - t0
    passed = [x for x in results if x.get("status") == "PASS"]
    alignment = [abs(float(x["close_vs_formal_snapshot_pct"])) for x in passed if x.get("close_vs_formal_snapshot_pct") is not None]
    coverage = len(passed) / len(results) if results else 0.0
    mincov = min((float(x.get("coverage_ratio") or 0.0) for x in passed), default=0.0)
    avg_request = sum(float(x.get("request_seconds") or 0.0) for x in passed) / len(passed) if passed else None
    max_diff = max(alignment) if alignment else None
    ohlc_coverage = sum(1 for x in passed if x.get("has_ohlc") is True) / len(passed) if passed else 0.0
    amount_coverage = sum(1 for x in passed if x.get("has_cumulative_amount") is True) / len(passed) if passed else 0.0
    eligible = coverage >= 0.90 and mincov >= 0.95 and max_diff is not None and max_diff <= 0.30 and total_elapsed <= 15.0
    return {"source_id": name, "status": "PASS" if eligible else ("DEGRADED" if passed else "FAILED"), "decision_path_eligible": eligible, "coverage_ratio": round(coverage, 4), "minimum_minute_coverage_ratio": round(mincov, 4), "total_request_seconds": round(total_elapsed, 3), "average_request_seconds": round(avg_request, 3) if avg_request is not None else None, "max_abs_close_diff_pct": round(max_diff, 4) if max_diff is not None else None, "ohlc_coverage_ratio": round(ohlc_coverage, 4), "cumulative_amount_coverage_ratio": round(amount_coverage, 4), "items": results}


def main():
    now = datetime.now(BEIJING)
    current, snapshot_rows = current_snapshot_rows()
    market_date = str(current.get("market_date") or now.date().isoformat())
    items = universe()
    sources = [
        run_source("tencent_minute_query", tencent_minute_query, items, snapshot_rows, market_date),
        run_source("tencent_mkline_m1", tencent_mkline, items, snapshot_rows, market_date),
        run_source("eastmoney_direct_trends2", eastmoney_trends, items, snapshot_rows, market_date),
    ]
    eligible = [x for x in sources if x.get("decision_path_eligible")]
    eligible.sort(key=lambda x: (-float(x.get("ohlc_coverage_ratio") or 0), float(x.get("total_request_seconds") or 9999)))
    best = eligible[0]["source_id"] if eligible else None
    payload = {
        "generated_at_beijing": now.isoformat(timespec="seconds"),
        "market_date": market_date,
        "scope": "ETF_MINUTE_SOURCE_GITHUB_ACTIONS_POC",
        "status": "PASS" if best else "DEGRADED",
        "objective": "寻找GitHub Actions中稳定、低延迟的ETF分钟行情源，将离散日内路径升级为分钟级路径。",
        "selection_gate": {"required_etf_coverage_ratio": 0.90, "required_minimum_minute_coverage_ratio": 0.95, "max_close_alignment_diff_pct": 0.30, "max_total_request_seconds_for_11_etfs": 15.0, "note": "PoC通过只代表进入正式集成候选；正式启用前仍需多个真实盘中节点验证最新分钟及时性和连续稳定性。"},
        "sources": sources,
        "best_candidate": best,
        "recommended_role": "FORMAL_INTRADAY_PATH_EVIDENCE_CANDIDATE" if best else "KEEP_DISCRETE_PATH_AND_CONTINUE_SEARCH",
        "integration_boundary": "分钟源只增强历史位置+当日日内路径框架中的日内路径/极值时序/成交承接证据；正式最新价仍由现有quote router决定，横截面继续只作验证，不改变MASTER权限。",
        "tushare_status": "EXCLUDED_BY_USER",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "status": payload["status"], "best_candidate": best, "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
