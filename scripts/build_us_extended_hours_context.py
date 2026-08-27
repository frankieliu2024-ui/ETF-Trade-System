from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
NEW_YORK = ZoneInfo("America/New_York")
BEIJING = ZoneInfo("Asia/Shanghai")
RUNTIME_POLICY = ROOT / "config" / "runtime_policy.json"

# Broad-market extended-hours proxies are always allowed. Individual companies are
# never hard-coded here: query-time industry names may be supplied through
# US_EXTENDED_SYMBOLS, e.g. INTC,NVDA,AMD, but remain conditional evidence only.
BASE_PROXIES = {
    "QQQ": {"name": "纳指100ETF代理", "role": "NASDAQ100_EXTENDED_HOURS_PROXY"},
    "SOXX": {"name": "半导体ETF代理", "role": "SEMICONDUCTOR_EXTENDED_HOURS_PROXY"},
}


def request_chart(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        "?range=5d&interval=5m&includePrePost=true&events=div%2Csplits"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def session_for_et(dt_et: datetime) -> str:
    minute = dt_et.hour * 60 + dt_et.minute
    if 4 * 60 <= minute < 9 * 60 + 30:
        return "PRE_MARKET"
    if 9 * 60 + 30 <= minute < 16 * 60:
        return "REGULAR"
    if 16 * 60 <= minute <= 20 * 60:
        return "POST_MARKET"
    return "OFF_SESSION"


def extract_rows(payload: dict) -> tuple[list[dict], dict]:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows: list[dict] = []
    for i, ts in enumerate(timestamps):
        values = {}
        for field in ("open", "high", "low", "close", "volume"):
            series = quote.get(field) or []
            values[field] = series[i] if i < len(series) else None
        if values["close"] is None:
            continue
        dt_utc = datetime.fromtimestamp(int(ts), timezone.utc)
        dt_et = dt_utc.astimezone(NEW_YORK)
        dt_bj = dt_utc.astimezone(BEIJING)
        rows.append({
            **values,
            "timestamp": int(ts),
            "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "as_of_local": dt_et.isoformat(timespec="seconds"),
            "as_of_beijing": dt_bj.isoformat(timespec="seconds"),
            "market_date_local": dt_et.date().isoformat(),
            "session": session_for_et(dt_et),
        })
    return rows, result.get("meta", {})


def pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, 4)


def classify_freshness(latest: dict, now: datetime, fresh_limit: int, degraded_limit: int) -> tuple[str, str, int]:
    """Return quality_status, freshness_status and age_seconds.

    During an active PRE/REGULAR/POST session the normal age thresholds apply.
    Once the US post-market session has completed, its last valid bar remains the
    authoritative completed-session reference for the next A-share pre-market.
    It must retain its real provider timestamp and must never be described as a
    current live quote merely because a later workflow reruns.
    """
    latest_dt = datetime.fromtimestamp(latest["timestamp"], timezone.utc)
    age_seconds = max(0, int((now - latest_dt).total_seconds()))
    current_et = now.astimezone(NEW_YORK)
    current_phase = session_for_et(current_et)
    latest_session = latest["session"]
    latest_et = latest_dt.astimezone(NEW_YORK)

    freshness = "FRESH" if age_seconds <= fresh_limit else ("DEGRADED" if age_seconds <= degraded_limit else "STALE")

    # Active market phases must obey the live freshness clock.
    if current_phase in {"PRE_MARKET", "REGULAR", "POST_MARKET"}:
        return freshness, freshness, age_seconds

    # Off-session after the same local trading day's close: the completed regular
    # or post-market observation is a valid session reference, not a stale live quote.
    if latest_et.date() == current_et.date() and latest_session in {"REGULAR", "POST_MARKET"}:
        return "PASS", "SESSION_REFERENCE", age_seconds

    # On weekends / holidays or before the next US session starts, preserve the
    # last completed US trading-session reference, but label it explicitly as previous.
    if latest_session in {"REGULAR", "POST_MARKET"}:
        return "PASS", "PREVIOUS_SESSION_REFERENCE", age_seconds

    return freshness, freshness, age_seconds


def build_symbol(symbol: str, name: str, role: str, conditional: bool) -> dict:
    rows, meta = extract_rows(request_chart(symbol))
    if not rows:
        raise RuntimeError("no valid intraday rows")
    latest = rows[-1]
    regular_rows = [r for r in rows if r["session"] == "REGULAR" and r.get("market_date_local") == latest.get("market_date_local")]
    if not regular_rows:
        regular_rows = [r for r in rows if r["session"] == "REGULAR"]
    previous_regular_close = meta.get("previousClose")
    regular_open = None
    if regular_rows:
        regular_open = regular_rows[0].get("open") if regular_rows[0].get("open") is not None else regular_rows[0].get("close")
    regular_close = regular_rows[-1]["close"] if regular_rows else previous_regular_close
    regular_market_date = regular_rows[-1].get("market_date_local") if regular_rows else latest.get("market_date_local")
    now = datetime.now(timezone.utc)
    try:
        policy = json.loads(RUNTIME_POLICY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        policy = {"fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500}
    fresh_limit = int(policy.get("fresh_max_age_seconds", 900))
    degraded_limit = int(policy.get("degraded_max_age_seconds", 1500))
    quality_status, freshness_status, age_seconds = classify_freshness(latest, now, fresh_limit, degraded_limit)
    current_phase = session_for_et(now.astimezone(NEW_YORK))
    return {
        "symbol": symbol,
        "name": name,
        "reference_role": role,
        "conditional_industry_object": conditional,
        "provider": "yahoo_chart_api",
        "market_timezone": "America/New_York",
        "market_phase_of_latest": latest["session"],
        "quality_status": quality_status,
        "freshness_status": freshness_status,
        "data_age_seconds": age_seconds,
        "current_market_phase": current_phase,
        "latest": latest,
        "previous_regular_close_reference": previous_regular_close,
        "regular_session_open_reference": regular_open,
        "regular_session_close_reference": regular_close,
        "regular_session_market_date": regular_market_date,
        "regular_session_change_vs_previous_close_pct": pct_change(regular_close, previous_regular_close),
        "regular_session_change_from_open_pct": pct_change(regular_close, regular_open),
        "extended_change_vs_regular_close_pct": pct_change(latest.get("close"), regular_close),
        "decision_note": (
            "扩展时段价格只作前置信号。PRE_MARKET/POST_MARKET流动性和价格发现质量低于正式现金盘；"
            "SESSION_REFERENCE/PREVIOUS_SESSION_REFERENCE只表示已结束时段的最后有效参考，不是当前实时价格；"
            "不得把个股或ETF扩展时段涨跌直接等同于NDX/SOX正式指数涨跌，也不得单独生成A股ETF动作。"
        ),
    }


def build() -> dict:
    requested = [x.strip().upper() for x in os.environ.get("US_EXTENDED_SYMBOLS", "").split(",") if x.strip()]
    specs: dict[str, tuple[str, str, bool]] = {
        symbol: (spec["name"], spec["role"], False) for symbol, spec in BASE_PROXIES.items()
    }
    for symbol in requested:
        if symbol not in specs:
            specs[symbol] = (symbol, "CONDITIONAL_US_INDUSTRY_STOCK", True)

    objects: dict[str, dict] = {}
    passes = 0
    for symbol, (name, role, conditional) in specs.items():
        try:
            record = build_symbol(symbol, name, role, conditional)
            passes += 1
        except Exception as exc:
            record = {
                "symbol": symbol,
                "name": name,
                "reference_role": role,
                "conditional_industry_object": conditional,
                "provider": "yahoo_chart_api",
                "market_timezone": "America/New_York",
                "quality_status": "FAILED",
                "error": str(exc)[-500:],
            }
        objects[symbol] = record

    now = datetime.now(timezone.utc)
    accepted_quality = {"PASS", "FRESH"}
    return {
        "generated_at": now_utc(),
        "generated_at_beijing": now.astimezone(BEIJING).isoformat(timespec="seconds"),
        "scope": "US_EXTENDED_HOURS_CONTEXT",
        "quality_status": (
            "PASS" if passes == len(specs) and all(v.get("quality_status") in accepted_quality for v in objects.values())
            else ("DEGRADED" if passes else "FAILED")
        ),
        "base_proxies": list(BASE_PROXIES.keys()),
        "conditional_symbols": requested,
        "objects": objects,
        "session_definition": {
            "PRE_MARKET_ET": "04:00-09:30",
            "REGULAR_ET": "09:30-16:00",
            "POST_MARKET_ET": "16:00-20:00",
            "timezone": "America/New_York",
        },
        "a_share_time_rule": (
            "美股扩展时段按America/New_York自动处理夏令时/冬令时。A股早盘前通常能看到上一美股现金盘及盘后信息；"
            "盘后结束后的最后有效价格按SESSION_REFERENCE保存真实provider时点，不继续套用盘中15分钟新鲜度；"
            "下一美股交易日PRE_MARKET通常在北京时间A股收盘后才开始，因此不得把美国盘前误称为当天A股上午的同步领先信号。"
        ),
        "decision_boundary": (
            "NDX/SOX用于上一正式现金盘结构；QQQ/SOXX及条件美股个股用于扩展时段前置信号。"
            "扩展时段必须继续经过A股本地传导与目标ETF自身反馈后才可进入机会判断。"
        ),
    }


def main() -> None:
    context = build()
    atomic_json_write(ROOT / "data" / "state" / "us_extended_hours_context.json", context)
    print(json.dumps({
        "ok": True,
        "generated_at_beijing": context["generated_at_beijing"],
        "quality_status": context["quality_status"],
        "objects": {k: v.get("quality_status", "FAILED") for k, v in context["objects"].items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
