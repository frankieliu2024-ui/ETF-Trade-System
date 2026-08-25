from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")

MARKET_ZONES = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "US": "America/New_York",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
}

MARKET_NAMES = {
    "CN": "中国大陆",
    "HK": "中国香港",
    "TW": "中国台湾",
    "JP": "日本",
    "KR": "韩国",
    "US": "美国",
}

DISPLAY_STATUS = {
    "CN": {
        "OPENING_AUCTION": "A股开盘前（集合竞价）",
        "REGULAR": "A股交易中",
        "MIDDAY_BREAK": "A股午间休市",
        "OFF_SESSION": "A股收盘",
    },
    "HK": {"PRE_OPEN": "港股开盘前", "REGULAR": "港股交易中", "MIDDAY_BREAK": "港股午间休市", "OFF_SESSION": "港股收盘"},
    "TW": {"PRE_OPEN": "台股开盘前", "REGULAR": "台股交易中", "OFF_SESSION": "台股收盘"},
    "JP": {"PRE_OPEN": "日股开盘前", "REGULAR": "日股交易中", "MIDDAY_BREAK": "日股午间休市", "OFF_SESSION": "日股收盘"},
    "KR": {"PRE_OPEN": "韩股开盘前", "REGULAR": "韩股交易中", "OFF_SESSION": "韩股收盘"},
    "US": {
        "PRE_MARKET": "美股盘前",
        "REGULAR": "美股交易中",
        "POST_MARKET": "美股盘后",
        "OFF_SESSION": "美股休市中",
    },
}


@dataclass
class MarketQuoteRoute:
    market: str
    phase: str
    source_priority: list[str]
    semantic_rule: str
    market_status_cn: str


def _as_local(now: datetime, market: str) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    return now.astimezone(ZoneInfo(MARKET_ZONES[market]))


def _minute(local: datetime) -> int:
    return local.hour * 60 + local.minute


def _in_window(value: int, start: int, end: int) -> bool:
    return start <= value < end


def market_phase(market: str, now: datetime | None = None) -> str:
    """Return internal phase; user-facing code must use display_market_status()."""
    if market not in MARKET_ZONES:
        return "UNKNOWN"
    local = _as_local(now or datetime.now(BEIJING), market)
    if local.weekday() >= 5:
        return "OFF_SESSION"

    minute = _minute(local)
    if market == "CN":
        if _in_window(minute, 9 * 60 + 15, 9 * 60 + 30):
            return "OPENING_AUCTION"
        if _in_window(minute, 9 * 60 + 30, 11 * 60 + 30) or _in_window(minute, 13 * 60, 15 * 60):
            return "REGULAR"
        if _in_window(minute, 11 * 60 + 30, 13 * 60):
            return "MIDDAY_BREAK"
        return "OFF_SESSION"

    if market == "HK":
        if _in_window(minute, 9 * 60 + 30, 12 * 60) or _in_window(minute, 13 * 60, 16 * 60):
            return "REGULAR"
        if _in_window(minute, 12 * 60, 13 * 60):
            return "MIDDAY_BREAK"
        return "PRE_OPEN" if minute < 9 * 60 + 30 else "OFF_SESSION"
    if market == "TW":
        return "REGULAR" if _in_window(minute, 9 * 60, 13 * 60 + 30) else ("PRE_OPEN" if minute < 9 * 60 else "OFF_SESSION")
    if market == "JP":
        if _in_window(minute, 9 * 60, 11 * 60 + 30) or _in_window(minute, 12 * 60 + 30, 15 * 60 + 30):
            return "REGULAR"
        if _in_window(minute, 11 * 60 + 30, 12 * 60 + 30):
            return "MIDDAY_BREAK"
        return "PRE_OPEN" if minute < 9 * 60 else "OFF_SESSION"
    if market == "KR":
        return "REGULAR" if _in_window(minute, 9 * 60, 15 * 60 + 30) else ("PRE_OPEN" if minute < 9 * 60 else "OFF_SESSION")
    if market == "US":
        if _in_window(minute, 4 * 60, 9 * 60 + 30):
            return "PRE_MARKET"
        if _in_window(minute, 9 * 60 + 30, 16 * 60):
            return "REGULAR"
        if _in_window(minute, 16 * 60, 20 * 60):
            return "POST_MARKET"
        return "OFF_SESSION"
    return "UNKNOWN"


def display_market_status(market: str, phase: str) -> str:
    return DISPLAY_STATUS.get(market, {}).get(phase, "市场状态未知")


def route(market: str, phase: str | None = None, now: datetime | None = None) -> MarketQuoteRoute:
    resolved = phase or market_phase(market, now)
    if market == "US":
        if resolved == "REGULAR":
            sources = ["overseas_context", "realtime_snapshot"]
            rule = "返回正式现金盘最新实时行情"
        elif resolved in {"PRE_MARKET", "POST_MARKET"}:
            sources = ["us_extended_hours_context", "overseas_context"]
            rule = "返回扩展时段行情，并保留最近正式收盘作为基准"
        else:
            sources = ["overseas_context", "us_extended_hours_context"]
            rule = "返回最近正式收盘行情"
    else:
        sources = ["overseas_context", "market_snapshot", "market_archive"]
        rule = "按对象所在市场阶段返回最新有效行情；休市/开盘前使用最近正式收盘"
    return MarketQuoteRoute(market, resolved, sources, rule, display_market_status(market, resolved))


def _read_json(root: Path, relative: str, fallback: Any) -> Any:
    try:
        return json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _beijing_time(value: Any) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BEIJING).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return str(value)


def _freshness(timestamp: str, now: datetime, policy: dict[str, Any] | None = None) -> str:
    if not timestamp:
        return "UNKNOWN"
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = max(0, int((now.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return "UNKNOWN"
    policy = policy or {}
    fresh_limit = int(policy.get("fresh_max_age_seconds", 900))
    degraded_limit = int(policy.get("degraded_max_age_seconds", 1500))
    return "FRESH" if age <= fresh_limit else ("DEGRADED" if age <= degraded_limit else "STALE")


def _market_for_overseas(key: str, record: dict[str, Any]) -> str:
    timezone_name = str(record.get("market_timezone") or "")
    if timezone_name == "America/New_York" or key in {"NDX", "SOX"}:
        return "US"
    if timezone_name == "Asia/Hong_Kong" or key == "HSTECH":
        return "HK"
    if timezone_name == "Asia/Taipei" or key == "TWII":
        return "TW"
    if timezone_name == "Asia/Tokyo" or key == "N225":
        return "JP"
    if timezone_name == "Asia/Seoul" or key == "KOSPI":
        return "KR"
    return "UNKNOWN"


def _row_quote(row: dict[str, Any], market: str, route_info: MarketQuoteRoute, now: datetime, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    timestamp = _first_value(row.get("as_of_beijing"), row.get("provider_timestamp"), row.get("captured_at_beijing"), row.get("captured_at"))
    price = _first_value(row.get("latest"), row.get("last"), row.get("latest_price"), row.get("close"), row.get("price"))
    if isinstance(price, dict):
        timestamp = _first_value(price.get("as_of_beijing"), price.get("provider_timestamp"), timestamp)
        price = _first_value(price.get("close"), price.get("last"), price.get("price"))
    name = str(_first_value(row.get("name"), row.get("display_name"), row.get("security_name"), "") or "")
    symbol = str(_first_value(row.get("symbol"), row.get("code"), row.get("thscode"), "") or "")
    source = str(_first_value(row.get("provider_used"), row.get("provider"), row.get("source"), "") or "")
    return {
        "market": market,
        "market_name": MARKET_NAMES.get(market, "未知市场"),
        "symbol": symbol,
        "name": name,
        "latest_price": price,
        "data_time_beijing": _beijing_time(timestamp),
        "data_time_local": str(_first_value(row.get("as_of_local"), row.get("provider_timestamp_local"), "") or ""),
        "market_phase": route_info.phase,
        "market_status_cn": route_info.market_status_cn,
        "data_nature_cn": (
            "实时交易行情" if route_info.phase == "REGULAR" else
            "午间休市期间的上午最新有效行情" if route_info.phase == "MIDDAY_BREAK" else
            "盘前行情（附最近正式收盘基准）" if route_info.phase == "PRE_MARKET" else
            "盘后行情（附当日正式收盘）" if route_info.phase == "POST_MARKET" else
            "最近正式收盘行情"
        ),
        "source": source or "state_context",
        "freshness": _freshness(_beijing_time(timestamp), now, policy),
        "quality_status": str(row.get("quality_status") or row.get("status") or "UNKNOWN").upper(),
        "direct_quote": not bool(row.get("proxy") or row.get("is_proxy") or row.get("reference_role", "").endswith("PROXY")),
        "route_rule": route_info.semantic_rule,
    }


def _latest_overseas_record(key: str, market: str, phase: str, overseas: dict, extended: dict) -> dict[str, Any] | None:
    record = (overseas.get("objects") or {}).get(key)
    proxy_key = {"NDX": "QQQ", "SOX": "SOXX"}.get(key, key)
    extended_record = (extended.get("objects") or {}).get(proxy_key)
    if market == "US" and phase in {"PRE_MARKET", "POST_MARKET"} and extended_record:
        return {**extended_record, "symbol": key, "name": record.get("name", key), "reference_role": "EXTENDED_HOURS_PROXY"}
    return record or extended_record


def _query_refresh_needed(root: Path, symbols: list[str], now: datetime, policy: dict[str, Any]) -> bool:
    preference = policy.get("query_time_refresh_preference") or {}
    threshold = int(preference.get("active_market_max_age_seconds", 300))
    requested = {str(x).upper() for x in symbols if str(x).strip()}
    current = _read_json(root, "data/state/CURRENT.json", {})
    snapshot = _read_json(root, str(current.get("latest_snapshot") or ""), {})
    candidates: list[tuple[str, str]] = []
    for row in snapshot.get("rows") or []:
        candidates.append((str(row.get("symbol") or "").upper(), str(row.get("as_of_beijing") or row.get("captured_at_beijing") or "")))
    overseas = _read_json(root, "data/state/overseas_context.json", {})
    extended = _read_json(root, "data/state/us_extended_hours_context.json", {})
    for key, raw in (overseas.get("objects") or {}).items():
        latest = raw.get("latest") if isinstance(raw, dict) else {}
        candidates.append((str(key).upper(), str(latest.get("as_of_beijing") or "")))
    for key, raw in (extended.get("objects") or {}).items():
        latest = raw.get("latest") if isinstance(raw, dict) else {}
        candidates.append((str(key).upper(), str(latest.get("as_of_beijing") or "")))
    if requested:
        candidates = [x for x in candidates if x[0] in requested]
    if not candidates:
        return True
    active = False
    for symbol, timestamp in candidates:
        market = "CN" if symbol.isdigit() else ("US" if symbol in {"NDX", "SOX", "QQQ", "SOXX"} else _market_for_overseas(symbol, {}))
        phase = market_phase(market, now=now)
        if phase in {"REGULAR", "OPENING_AUCTION", "PRE_MARKET", "POST_MARKET"}:
            active = True
            if not timestamp:
                return True
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() > threshold:
                    return True
            except ValueError:
                return True
    return False


def build_market_quote_context(root: Path | str, now: datetime | None = None, *, force_refresh: bool = False, requested_symbols: list[str] | None = None) -> dict[str, Any]:
    """Build the single routed quote view from already-produced state; no network calls."""
    root = Path(root)
    query_time = now or datetime.now(BEIJING)
    if query_time.tzinfo is None:
        query_time = query_time.replace(tzinfo=BEIJING)
    current = _read_json(root, "data/state/CURRENT.json", {})
    policy = _read_json(root, "config/runtime_policy.json", {})
    snapshot = _read_json(root, str(current.get("latest_snapshot") or ""), {})
    overseas = _read_json(root, "data/state/overseas_context.json", {})
    extended = _read_json(root, "data/state/us_extended_hours_context.json", {})
    quotes: list[dict[str, Any]] = []

    should_refresh = force_refresh and _query_refresh_needed(root, requested_symbols or [], query_time, policy)
    if should_refresh:
        try:
            from scripts.query_time_market_refresh import refresh_market_quotes
        except ModuleNotFoundError:
            from query_time_market_refresh import refresh_market_quotes
        refreshed = refresh_market_quotes(root, requested_symbols or [], query_time)
        for quote in refreshed.get("quotes", []):
            if isinstance(quote, dict):
                quotes.append(quote)
        refresh_failures = refreshed.get("failures", [])
        refreshed_symbols = {str(x.get("symbol", "")).upper() for x in refreshed.get("quotes", []) if isinstance(x, dict)}
    else:
        refresh_failures = []
        refreshed_symbols = set()

    for row in snapshot.get("rows") or []:
        if str(row.get("symbol", "")).upper() in refreshed_symbols:
            continue
        if not isinstance(row, dict):
            continue
        asset_class = str(row.get("asset_class") or "").upper()
        if asset_class in {"ETF", "A_SHARE_INDEX", "STOCK"}:
            market = "CN"
            info = route("CN", now=query_time)
            quotes.append(_row_quote(row, market, info, query_time, policy))

    for key, raw in (overseas.get("objects") or {}).items():
        if str(key).upper() in refreshed_symbols:
            continue
        if not isinstance(raw, dict):
            continue
        market = _market_for_overseas(str(key), raw)
        if market == "UNKNOWN":
            continue
        info = route(market, now=query_time)
        selected = _latest_overseas_record(str(key), market, info.phase, overseas, extended)
        if selected:
            quote = _row_quote({**selected, "symbol": str(selected.get("symbol") or key)}, market, info, query_time, policy)
            quotes.append(quote)

    return {
        "generated_at_beijing": query_time.astimezone(BEIJING).isoformat(timespec="seconds"),
        "refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH" if should_refresh else "CACHED_STATE",
        "refresh_failures": refresh_failures,
        "route_version": "V1.0",
        "query_entry": "market_quote_router",
        "quotes": quotes,
        "markets": {market: {"phase": market_phase(market, query_time), "market_status_cn": display_market_status(market, market_phase(market, query_time))} for market in MARKET_ZONES},
        "selection_rule": "交易中返回最新实时；盘前返回盘前行情并附最近正式收盘；盘后返回盘后行情并附当日正式收盘；休市返回最近正式收盘。",
        "user_display_rule": "用户展示只使用中文市场状态和数据性质，不直接输出内部英文状态码。",
        "decision_boundary": "路由只提供事实与时点，不生成风险许可、Trial、Confirm、金额、卖出或其他交易动作。",
        "source_state_paths": ["data/state/CURRENT.json", "data/state/overseas_context.json", "data/state/us_extended_hours_context.json"],
    }
