from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

try:
    from state_manager import now_utc, read_current
except ModuleNotFoundError:
    from scripts.state_manager import now_utc, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
UA = "ETF-Trade-System/2.2.16 minute-path"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(new, old):
    n, o = safe_float(new), safe_float(old)
    if n is None or o in (None, 0.0):
        return None
    return (n / o - 1.0) * 100.0


def round4(value):
    return None if value is None else round(float(value), 4)


def universe(root: Path):
    cfg = load_json(root / "config/market/etf_monitor_universe.json", {})
    return [(str(x.get("code")), str(x.get("name")), str(x.get("thscode"))) for x in (cfg.get("objects") or []) if x.get("code")]


def latest_snapshot(root: Path, current: dict):
    rel = str(current.get("latest_snapshot") or "")
    snap = load_json(root / rel, {}) if rel else {}
    rows = {str(x.get("symbol")): x for x in (snap.get("rows") or []) if x.get("symbol")}
    return rel, snap, rows


def provider_symbol(thscode: str):
    code, suffix = thscode.upper().split(".", 1)
    return ("sh" if suffix == "SH" else "sz") + code


def in_session(dt: datetime, market_date: str):
    if dt.date().isoformat() != market_date:
        return False
    m = dt.hour * 60 + dt.minute
    return 570 <= m <= 690 or 780 <= m <= 900


def parse_time(text: str):
    try:
        return datetime.fromisoformat(str(text))
    except Exception:
        return None


def fetch_one(code: str, name: str, thscode: str, market_date: str, formal_row: dict):
    symbol = provider_symbol(thscode)
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?" + urllib.parse.urlencode({"code": symbol, "r": str(time.time())})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/", "Accept": "application/json,text/plain,*/*"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=6) as resp:
        raw = resp.read()
        if int(resp.status) != 200:
            raise RuntimeError(f"HTTP {resp.status}")
    elapsed = time.monotonic() - t0
    payload = json.loads(raw.decode("utf-8", "replace"))
    if payload.get("code") != 0:
        raise RuntimeError(f"provider code={payload.get('code')} msg={payload.get('msg')}")
    node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
    date_text = str(node.get("date") or "")
    points = []
    for raw_row in node.get("data") or []:
        parts = str(raw_row).split()
        if len(parts) < 3:
            continue
        dt = datetime.strptime(f"{date_text} {parts[0]}", "%Y%m%d %H%M").replace(tzinfo=BEIJING)
        if not in_session(dt, market_date):
            continue
        points.append({"dt": dt, "price": float(parts[1]), "cum_volume": float(parts[2]), "cum_amount": float(parts[3]) if len(parts) >= 4 else None})
    if not points:
        raise RuntimeError("no same-day A-share minute points")
    points.sort(key=lambda x: x["dt"])
    first, latest = points[0], points[-1]
    low = min(points, key=lambda x: x["price"])
    high = max(points, key=lambda x: x["price"])
    latest_dt = latest["dt"]
    formal_dt = parse_time(formal_row.get("as_of_beijing"))
    minute_lag = None
    if formal_dt is not None:
        minute_lag = max(0.0, (formal_dt - latest_dt).total_seconds() / 60.0)
    recent_ref = min(points, key=lambda x: abs((x["dt"] - (latest_dt.replace(second=0, microsecond=0))).total_seconds() + 600)) if len(points) > 1 else first
    target_ts = latest_dt.timestamp() - 600
    candidates = [x for x in points if x["dt"].timestamp() <= target_ts]
    recent_ref = candidates[-1] if candidates else first
    recent_minutes = max(1.0, (latest_dt - recent_ref["dt"]).total_seconds() / 60.0)
    recent_move = pct(latest["price"], recent_ref["price"])
    slope10 = None if recent_move is None else recent_move / recent_minutes * 10.0
    amount_delta = None
    volume_delta = None
    if latest.get("cum_amount") is not None and recent_ref.get("cum_amount") is not None:
        amount_delta = latest["cum_amount"] - recent_ref["cum_amount"]
    if latest.get("cum_volume") is not None and recent_ref.get("cum_volume") is not None:
        volume_delta = latest["cum_volume"] - recent_ref["cum_volume"]
    gaps = [(b["dt"] - a["dt"]).total_seconds() / 60.0 for a, b in zip(points, points[1:])]
    recovery = pct(latest["price"], low["price"])
    retreat = pct(latest["price"], high["price"])
    path_change = pct(latest["price"], first["price"])
    structure = []
    if low["dt"] < high["dt"] and (recovery or 0) >= 0.3 and slope10 is not None and slope10 >= 0:
        structure.append({"label": "V_RECOVERY_CANDIDATE", "evidence": {"source": "tencent_1m", "recovery_from_path_low_pct": round4(recovery)}})
    return {
        "symbol": code,
        "name": name,
        "asset_class": "ETF",
        "status": "READY",
        "provider": "tencent_qq",
        "provider_endpoint": "web.ifzq.gtimg.cn/appstock/app/minute/query",
        "request_seconds": round(elapsed, 3),
        "sample_count": len(points),
        "first_as_of_beijing": first["dt"].isoformat(timespec="seconds"),
        "latest_as_of_beijing": latest["dt"].isoformat(timespec="seconds"),
        "first_price": first["price"],
        "latest_price": latest["price"],
        "path_change_pct": round4(path_change),
        "path_low": low["price"],
        "path_low_as_of_beijing": low["dt"].isoformat(timespec="seconds"),
        "path_high": high["price"],
        "path_high_as_of_beijing": high["dt"].isoformat(timespec="seconds"),
        "recovery_from_path_low_pct": round4(recovery),
        "retreat_from_path_high_pct": round4(retreat),
        "recent_interval_minutes": round(recent_minutes, 2),
        "recent_move_pct": round4(recent_move),
        "recent_slope_pct_per_10m": round4(slope10),
        "recent_volume_delta": round4(volume_delta),
        "recent_amount_delta": round4(amount_delta),
        "relative_to_indices": {},
        "minute_lag_vs_formal_quote_minutes": round(minute_lag, 2) if minute_lag is not None else None,
        "sampling": {
            "coverage": "HIGH",
            "median_gap_minutes": round(median(gaps), 2) if gaps else None,
            "max_gap_minutes": round(max(gaps), 2) if gaps else None,
            "source_granularity": "1m",
            "limitation": "腾讯minute/query提供每分钟末笔价格及累计成交量/额，不提供分钟OHLC；分钟内瞬时影线仍可能遗漏。日内路径与极值时序优先于原离散脉冲，但正式最新价仍以quote router为准。",
        },
        "structure_candidates": structure,
    }


def build(root: Path = ROOT):
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    snapshot_rel, snapshot, formal_rows = latest_snapshot(root, current)
    base = {
        "generated_at": now_utc(),
        "generated_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "market_date": market_date,
        "source_snapshot": snapshot_rel,
        "mode": "TENCENT_1M_ETF_PATH_AUXILIARY",
        "read_only": True,
        "decision_boundary": "仅增强ETF日内路径、极值时序和近10分钟成交承接证据；不替代正式最新价，不产生风险许可、金额或交易动作。",
        "fallback_rule": "单只ETF分钟源失败或时效不合格时，自动回退既有intraday_path_features离散脉冲，不阻断正式决策。",
    }
    if not market_date:
        return {**base, "status": "MISSING", "features": []}
    started = time.monotonic()
    results = []
    items = universe(root)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one, code, name, thscode, market_date, formal_rows.get(code) or {}): (code, name) for code, name, thscode in items}
        for future in as_completed(futures):
            code, name = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"symbol": code, "name": name, "asset_class": "ETF", "status": "FAILED", "provider": "tencent_qq", "error": str(exc)[-500:]})
    results.sort(key=lambda x: x.get("symbol", ""))
    passed = [x for x in results if x.get("status") == "READY"]
    coverage = len(passed) / len(items) if items else 0.0
    now_bj = datetime.now(BEIJING)
    minute = now_bj.hour * 60 + now_bj.minute
    live_phase = (570 <= minute <= 690) or (780 <= minute <= 900)
    freshness_ok = True
    if live_phase:
        freshness_ok = all((x.get("minute_lag_vs_formal_quote_minutes") is None or float(x.get("minute_lag_vs_formal_quote_minutes")) <= 3.0) for x in passed)
    return {
        **base,
        "status": "READY" if coverage >= 0.90 and freshness_ok else "DEGRADED",
        "coverage_ratio": round(coverage, 4),
        "freshness_ok": freshness_ok,
        "fetch_seconds": round(time.monotonic() - started, 3),
        "features": results,
    }


def main():
    payload = build(ROOT)
    target = ROOT / "data/state/minute_path_features.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "status": payload.get("status"), "coverage_ratio": payload.get("coverage_ratio"), "fetch_seconds": payload.get("fetch_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
