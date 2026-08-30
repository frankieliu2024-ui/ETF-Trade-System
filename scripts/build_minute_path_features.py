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
    from tencent_quote import fetch_tencent_quotes
except ModuleNotFoundError:
    from scripts.state_manager import now_utc, read_current
    from scripts.tencent_quote import fetch_tencent_quotes

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")
UA = "ETF-Trade-System minute-path"
POLICY_PATH = Path("config/minute_path_validation_policy.json")
LATE_SESSION_REFERENCE_MINUTE = 14 * 60 + 30
SCHEDULE_NODE_MAP = {
    "30 2 * * 1-5": "10:30",
    "45 5 * * 1-5": "13:45",
    "20 6 * * 1-5": "14:20",
    "30 6 * * 1-5": "14:30",
    "55 6 * * 1-5": "14:55",
}


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def quality_requirements(root: Path) -> dict:
    policy = load_json(root / POLICY_PATH, {})
    q = policy.get("quality_requirements") or {}
    required = {
        "etf_coverage_ratio": float(q["etf_coverage_ratio"]),
        "freshness_max_seconds": float(q["freshness_max_seconds"]),
        "quote_alignment_max_abs_diff_pct": float(q["quote_alignment_max_abs_diff_pct"]),
        "quote_comparable_max_seconds": float(q["quote_comparable_max_seconds"]),
    }
    return required


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


def quote_datetime(quote_row: dict | None):
    if not quote_row or quote_row.get("provider_timestamp_ms") is None:
        return None
    try:
        return datetime.fromtimestamp(float(quote_row["provider_timestamp_ms"]) / 1000.0, tz=BEIJING)
    except (TypeError, ValueError, OSError):
        return None


def is_lunch_break(a: datetime, b: datetime):
    a_min = a.hour * 60 + a.minute
    b_min = b.hour * 60 + b.minute
    return a.date() == b.date() and a_min <= 690 and b_min >= 780


def continuity_summary(points: list[dict]):
    duplicates, seen, gaps, unexpected = [], set(), [], []
    for point in points:
        key = point["dt"].isoformat(timespec="minutes")
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    for a, b in zip(points, points[1:]):
        gap = (b["dt"] - a["dt"]).total_seconds() / 60.0
        gaps.append(gap)
        if gap > 1.01 and not is_lunch_break(a["dt"], b["dt"]):
            unexpected.append({"from": a["dt"].isoformat(timespec="minutes"), "to": b["dt"].isoformat(timespec="minutes"), "gap_minutes": round(gap, 2)})
    return {
        "pass": not duplicates and not unexpected,
        "duplicate_minute_count": len(duplicates),
        "duplicate_minutes": duplicates[:20],
        "unexpected_gap_count": len(unexpected),
        "unexpected_gaps": unexpected[:20],
        "median_gap_minutes": round(median(gaps), 2) if gaps else None,
        "max_gap_minutes": round(max(gaps), 2) if gaps else None,
    }


def point_in_time_filter(points: list[dict], quote_row: dict | None, now_bj: datetime | None = None):
    now_bj = now_bj or datetime.now(BEIJING)
    qdt = quote_datetime(quote_row)
    cutoff = qdt or now_bj
    usable = [x for x in points if x["dt"] <= cutoff]
    excluded = [x for x in points if x["dt"] > cutoff]
    return usable, {
        "pass": bool(usable),
        "cutoff_source": "SIMULTANEOUS_TENCENT_QUOTE" if qdt else "LOCAL_CAPTURE_TIME",
        "cutoff_as_of_beijing": cutoff.isoformat(timespec="seconds"),
        "excluded_future_bucket_count": len(excluded),
        "excluded_future_bucket_times": [x["dt"].isoformat(timespec="seconds") for x in excluded[:5]],
        "rule": "只消费不晚于同时腾讯quote provider timestamp的分钟点；更晚标签视为当前开放或未来分钟桶。",
    }


def quote_alignment(latest: dict, quote_row: dict | None, requirements: dict):
    qdt = quote_datetime(quote_row)
    quote_price = safe_float((quote_row or {}).get("last_price"))
    if qdt is None or quote_price is None:
        return {"status": "UNAVAILABLE", "pass": False, "reason": "simultaneous Tencent quote missing timestamp or price"}
    delta_seconds = abs((qdt - latest["dt"]).total_seconds())
    diff_pct = pct(latest["price"], quote_price)
    comparable = delta_seconds <= requirements["quote_comparable_max_seconds"]
    passed = bool(comparable and diff_pct is not None and abs(diff_pct) <= requirements["quote_alignment_max_abs_diff_pct"])
    return {
        "status": "PASS" if passed else ("NOT_COMPARABLE" if not comparable else "FAIL"),
        "pass": passed,
        "minute_price": latest["price"],
        "quote_price": quote_price,
        "quote_as_of_beijing": qdt.isoformat(timespec="seconds"),
        "timestamp_delta_seconds": round(delta_seconds, 3),
        "abs_diff_pct": round4(abs(diff_pct)) if diff_pct is not None else None,
        "max_abs_diff_pct": requirements["quote_alignment_max_abs_diff_pct"],
        "comparable_max_seconds": requirements["quote_comparable_max_seconds"],
        "note": "该校验只确认minute/query与同时腾讯quote的同源一致性；正式最新价仍由quote router决定。",
    }


def late_session_context(points: list[dict]) -> dict:
    latest = points[-1]
    latest_minute = latest["dt"].hour * 60 + latest["dt"].minute
    if latest_minute < LATE_SESSION_REFERENCE_MINUTE:
        return {
            "status": "NOT_YET_AVAILABLE",
            "reference_clock_beijing": "14:30",
            "move_from_reference_pct": None,
            "decision_boundary": "14:30前不构造尾盘方向；不得使用未来分钟桶。",
        }
    eligible = [x for x in points if x["dt"].hour * 60 + x["dt"].minute >= LATE_SESSION_REFERENCE_MINUTE]
    if not eligible:
        return {"status": "DEGRADED", "reference_clock_beijing": "14:30", "move_from_reference_pct": None, "reason": "14:30 minute unavailable"}
    reference = eligible[0]
    return {
        "status": "READY",
        "reference_clock_beijing": "14:30",
        "reference_as_of_beijing": reference["dt"].isoformat(timespec="seconds"),
        "reference_price": reference["price"],
        "latest_as_of_beijing": latest["dt"].isoformat(timespec="seconds"),
        "latest_price": latest["price"],
        "move_from_reference_pct": round4(pct(latest["price"], reference["price"])),
        "reference_sampling_role": "FIRST_PIT_ELIGIBLE_MINUTE_AT_OR_AFTER_14_30",
        "decision_boundary": "仅描述14:30后截至当前PIT时点的分钟价格变化；研究分组不得独立生成交易动作。",
    }


def fetch_one(code: str, name: str, thscode: str, market_date: str, formal_row: dict, quote_row: dict | None, requirements: dict):
    if os.environ.get("MINUTE_PATH_FAULT_SYMBOL") == code:
        raise RuntimeError("injected minute-path object failure")
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
    raw_points = []
    for raw_row in node.get("data") or []:
        parts = str(raw_row).split()
        if len(parts) < 3:
            continue
        dt = datetime.strptime(f"{date_text} {parts[0]}", "%Y%m%d %H%M").replace(tzinfo=BEIJING)
        if in_session(dt, market_date):
            raw_points.append({"dt": dt, "price": float(parts[1]), "cum_volume": float(parts[2]), "cum_amount": float(parts[3]) if len(parts) >= 4 else None})
    if not raw_points:
        raise RuntimeError("no same-day A-share minute points")
    raw_points.sort(key=lambda x: x["dt"])
    points, pit = point_in_time_filter(raw_points, quote_row)
    if not points:
        raise RuntimeError("no point-in-time eligible minute points")

    first, latest = points[0], points[-1]
    low = min(points, key=lambda x: x["price"])
    high = max(points, key=lambda x: x["price"])
    latest_dt = latest["dt"]
    freshness_ref = quote_datetime(quote_row) or datetime.now(BEIJING)
    latest_age_seconds = (freshness_ref - latest_dt).total_seconds()
    freshness_pass = 0.0 <= latest_age_seconds <= requirements["freshness_max_seconds"]

    formal_dt = parse_time(formal_row.get("as_of_beijing"))
    formal_quote_delta_minutes = abs((formal_dt - latest_dt).total_seconds()) / 60.0 if formal_dt is not None else None
    target_ts = latest_dt.timestamp() - 600
    candidates = [x for x in points if x["dt"].timestamp() <= target_ts]
    recent_ref = candidates[-1] if candidates else first
    recent_minutes = max(1.0, (latest_dt - recent_ref["dt"]).total_seconds() / 60.0)
    recent_move = pct(latest["price"], recent_ref["price"])
    slope10 = None if recent_move is None else recent_move / recent_minutes * 10.0
    amount_delta = latest["cum_amount"] - recent_ref["cum_amount"] if latest.get("cum_amount") is not None and recent_ref.get("cum_amount") is not None else None
    volume_delta = latest["cum_volume"] - recent_ref["cum_volume"] if latest.get("cum_volume") is not None and recent_ref.get("cum_volume") is not None else None
    continuity = continuity_summary(points)
    alignment = quote_alignment(latest, quote_row, requirements)
    item_quality_pass = bool(pit["pass"] and freshness_pass and continuity["pass"] and alignment["pass"])
    recovery, retreat, path_change = pct(latest["price"], low["price"]), pct(latest["price"], high["price"]), pct(latest["price"], first["price"])
    structure = []
    if low["dt"] < high["dt"] and (recovery or 0) >= 0.3 and slope10 is not None and slope10 >= 0:
        structure.append({"label": "V_RECOVERY_CANDIDATE", "evidence": {"source": "tencent_1m", "recovery_from_path_low_pct": round4(recovery)}})
    return {
        "symbol": code,
        "name": name,
        "thscode": thscode,
        "asset_class": "ETF",
        "status": "READY",
        "item_quality_pass": item_quality_pass,
        "provider": "tencent_qq",
        "provider_endpoint": "web.ifzq.gtimg.cn/appstock/app/minute/query",
        "request_seconds": round(elapsed, 3),
        "sample_count": len(points),
        "raw_sample_count": len(raw_points),
        "first_as_of_beijing": first["dt"].isoformat(timespec="seconds"),
        "latest_as_of_beijing": latest_dt.isoformat(timespec="seconds"),
        "latest_age_seconds_at_validation": round(latest_age_seconds, 3),
        "freshness_pass": freshness_pass,
        "freshness_max_seconds": requirements["freshness_max_seconds"],
        "point_in_time": pit,
        "first_price": first["price"],
        "latest_price": latest["price"],
        "path_change_pct": round4(path_change),
        "path_low": low["price"],
        "path_low_as_of_beijing": low["dt"].isoformat(timespec="seconds"),
        "path_high": high["price"],
        "path_high_as_of_beijing": high["dt"].isoformat(timespec="seconds"),
        "recovery_from_path_low_pct": round4(recovery),
        "retreat_from_path_high_pct": round4(retreat),
        "late_session_context": late_session_context(points),
        "recent_interval_minutes": round(recent_minutes, 2),
        "recent_move_pct": round4(recent_move),
        "recent_slope_pct_per_10m": round4(slope10),
        "recent_volume_delta": round4(volume_delta),
        "recent_amount_delta": round4(amount_delta),
        "relative_to_indices": {},
        "formal_snapshot_reference": {"as_of_beijing": formal_row.get("as_of_beijing"), "price": safe_float(formal_row.get("close")), "minute_time_delta_minutes": round(formal_quote_delta_minutes, 2) if formal_quote_delta_minutes is not None else None, "role": "context_only_not_live_alignment_gate"},
        "quote_alignment": alignment,
        "sampling": {"coverage": "HIGH", "continuity_pass": continuity["pass"], "duplicate_minute_count": continuity["duplicate_minute_count"], "duplicate_minutes": continuity["duplicate_minutes"], "unexpected_gap_count": continuity["unexpected_gap_count"], "unexpected_gaps": continuity["unexpected_gaps"], "median_gap_minutes": continuity["median_gap_minutes"], "max_gap_minutes": continuity["max_gap_minutes"], "source_granularity": "1m", "limitation": "腾讯minute/query提供每分钟末笔价格及累计成交量/额，不提供分钟OHLC；当前开放/未来分钟桶已按Point-in-Time过滤。"},
        "structure_candidates": structure,
    }


def build(root: Path = ROOT):
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    snapshot_rel, _, formal_rows = latest_snapshot(root, current)
    validation_schedule = str(os.environ.get("VALIDATION_SCHEDULE") or "").strip()
    event_name = str(os.environ.get("GITHUB_EVENT_NAME") or "LOCAL")
    requirements = quality_requirements(root)
    execution = {"github_run_id": os.environ.get("GITHUB_RUN_ID"), "github_event_name": event_name, "github_sha": os.environ.get("GITHUB_SHA"), "github_ref": os.environ.get("GITHUB_REF"), "validation_schedule": validation_schedule or None, "planned_node_beijing": SCHEDULE_NODE_MAP.get(validation_schedule, "AD_HOC"), "evidence_class": "SCHEDULED_REAL_INTRADAY" if event_name == "schedule" and validation_schedule in SCHEDULE_NODE_MAP else "SUPPLEMENTAL_AD_HOC"}
    base = {
        "schema_version": "1.3",
        "generated_at": now_utc(),
        "generated_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "market_date": market_date,
        "source_snapshot": snapshot_rel,
        "mode": "TENCENT_1M_ETF_PATH_VALIDATION_EVIDENCE",
        "read_only": True,
        "execution": execution,
        "decision_boundary": "分钟源只增强ETF日内路径、极值时序、14:30后路径和成交承接证据；正式最新价仍由quote router决定。",
        "fallback_rule": "生产消费按ETF对象级质量门选择；失败对象回退离散路径，不把单对象失败放大为全池失败。",
    }
    if not market_date:
        return {**base, "status": "MISSING", "quality_summary": {"formal_gate_pass": False, "requirements": requirements}, "features": []}

    started, results, items = time.monotonic(), [], universe(root)
    quote_rows, quote_error = {}, None
    try:
        quote_rows = fetch_tencent_quotes([thscode for _, _, thscode in items], timeout=6)
    except Exception as exc:
        quote_error = str(exc)[-500:]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one, code, name, thscode, market_date, formal_rows.get(code) or {}, quote_rows.get(thscode.upper()) if quote_rows else None, requirements): (code, name) for code, name, thscode in items}
        for future in as_completed(futures):
            code, name = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"symbol": code, "name": name, "asset_class": "ETF", "status": "FAILED", "item_quality_pass": False, "provider": "tencent_qq", "error": str(exc)[-500:]})
    results.sort(key=lambda x: x.get("symbol", ""))
    ready = [x for x in results if x.get("status") == "READY"]
    item_passed = [x for x in results if x.get("item_quality_pass") is True]
    coverage = len(ready) / len(items) if items else 0.0
    production_usable_ratio = len(item_passed) / len(items) if items else 0.0
    coverage_pass = bool(items) and coverage >= requirements["etf_coverage_ratio"]
    freshness_pass = bool(ready) and all(bool(x.get("freshness_pass")) for x in ready)
    continuity_pass = bool(ready) and all(bool((x.get("sampling") or {}).get("continuity_pass")) for x in ready)
    quote_alignment_pass = bool(ready) and all(bool((x.get("quote_alignment") or {}).get("pass")) for x in ready)
    point_in_time_pass = bool(ready) and all(bool((x.get("point_in_time") or {}).get("pass")) for x in ready)
    formal_gate_pass = coverage_pass and freshness_pass and continuity_pass and quote_alignment_pass and point_in_time_pass and len(item_passed) == len(items)
    quality_summary = {
        "coverage_pass": coverage_pass,
        "freshness_pass": freshness_pass,
        "continuity_pass": continuity_pass,
        "quote_alignment_pass": quote_alignment_pass,
        "point_in_time_pass": point_in_time_pass,
        "formal_gate_pass": formal_gate_pass,
        "production_usable_count": len(item_passed),
        "production_usable_ratio": round(production_usable_ratio, 4),
        "quote_batch_error": quote_error,
        "requirements": requirements,
        "policy_source": str(POLICY_PATH).replace("\\", "/"),
    }
    return {**base, "status": "READY" if formal_gate_pass else ("DEGRADED" if item_passed else "FAILED"), "coverage_ratio": round(coverage, 4), "quality_summary": quality_summary, "fetch_seconds": round(time.monotonic() - started, 3), "features": results}


def main():
    payload = build(ROOT)
    target = ROOT / "data/state/minute_path_features.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "status": payload.get("status"), "coverage_ratio": payload.get("coverage_ratio"), "production_usable_ratio": (payload.get("quality_summary") or {}).get("production_usable_ratio"), "formal_gate_pass": (payload.get("quality_summary") or {}).get("formal_gate_pass"), "fetch_seconds": payload.get("fetch_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
