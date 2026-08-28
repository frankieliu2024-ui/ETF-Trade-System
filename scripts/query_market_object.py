from __future__ import annotations

"""Single-object market query entry.

The command separates formal monitored objects from explicit user requests.
It never changes the monitoring universe, trading rules, permissions, or orders.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from market_data_guard import validate_market_row
    from market_quote_router import build_market_quote_context
    from multi_source_market import _request_json
except ModuleNotFoundError:
    from scripts.market_data_guard import validate_market_row
    from scripts.market_quote_router import build_market_quote_context
    from scripts.multi_source_market import _request_json

BEIJING = ZoneInfo("Asia/Shanghai")
ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()

# These are query aliases, not a monitoring universe.
ALIASES = {
    "标普500": ("SPX", "^GSPC", "US", "标普500指数"),
    "标普500指数": ("SPX", "^GSPC", "US", "标普500指数"),
    "SPX": ("SPX", "^GSPC", "US", "标普500指数"),
    "美光科技": ("MU", "MU", "US", "美光科技"),
    "MU": ("MU", "MU", "US", "美光科技"),
    "英伟达": ("NVDA", "NVDA", "US", "英伟达"),
    "NVDA": ("NVDA", "NVDA", "US", "英伟达"),
    "台积电": ("TSM", "TSM", "US", "台积电"),
    "TSM": ("TSM", "TSM", "US", "台积电"),
    "纳斯达克100": ("NDX", "^NDX", "US", "纳斯达克100指数"),
    "纳斯达克100指数": ("NDX", "^NDX", "US", "纳斯达克100指数"),
    "NDX": ("NDX", "^NDX", "US", "纳斯达克100指数"),
}

MARKET_BY_SUFFIX = {
    ".HK": ("HK", "中国香港"),
    ".TW": ("TW", "中国台湾"),
    ".T": ("JP", "日本"),
    ".JP": ("JP", "日本"),
    ".KS": ("KR", "韩国"),
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_input(value: str) -> tuple[str, str, str, str]:
    raw = value.strip()
    alias = ALIASES.get(raw.upper()) or ALIASES.get(raw)
    if alias:
        return alias
    upper = raw.upper()
    if upper.isdigit() and len(upper) == 6:
        market = "CN"
        suffix = ".SH" if upper.startswith(("5", "6", "588")) else ".SZ"
        return upper, upper + suffix, market, upper + suffix
    for suffix, (market, _market_name) in MARKET_BY_SUFFIX.items():
        if upper.endswith(suffix):
            return upper, upper, market, upper
    return upper, upper, "US", upper


def display_market(market: str) -> str:
    return {"CN": "中国大陆", "HK": "中国香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国", "US": "美国"}.get(market, market)


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def age_status(timestamp: str, now: datetime, policy: dict) -> tuple[str, int | None]:
    if not timestamp:
        return "UNKNOWN", None
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = max(0, int((now.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return "UNKNOWN", None
    fresh = int(policy.get("fresh_max_age_seconds", 900))
    degraded = int(policy.get("degraded_max_age_seconds", 1500))
    return ("FRESH" if age <= fresh else "DEGRADED" if age <= degraded else "STALE"), age


def monitored_catalog(root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    universe = load_json(root / "config/market/etf_monitor_universe.json", {})
    for item in universe.get("objects") or []:
        code = str(item.get("code") or "").upper()
        if code:
            result[code] = {"code": code, "name": str(item.get("name") or code), "asset_type": "ETF", "market": "CN"}
    monitor = load_json(root / "config/market/market_monitor_config.json", {})
    indices = monitor.get("formal_index_layer", {}).get("required_objects", [])
    index_names = {
        "000001.SH": "上证指数", "399006.SZ": "创业板指", "NDX": "纳斯达克100指数",
        "SOX": "费城半导体指数", "N225": "日经225指数", "KOSPI": "韩国综合指数",
        "TWII": "台湾加权指数", "HSTECH": "恒生科技指数",
    }
    for code in indices:
        code = str(code).upper()
        result[code] = {"code": code, "name": index_names.get(code, code), "asset_type": "INDEX", "market": "CN" if code.endswith((".SH", ".SZ")) else "OVERSEAS"}
    account = load_json(root / "data/state/account_fact.json", {})
    for position in account.get("positions") or []:
        code = str(position.get("code") or "").upper()
        if int(position.get("quantity") or 0) > 0 and code:
            result[code] = {
                "code": code, "name": str(position.get("name") or code),
                "asset_type": str(position.get("asset_type") or "STOCK"), "market": "CN",
            }
    return result


def resolve_object(root: Path, requested: str) -> tuple[dict, str | None]:
    code, provider_symbol, market, default_name = normalize_input(requested)
    catalog = monitored_catalog(root)
    candidates = [code, provider_symbol, code.replace(".SH", "").replace(".SZ", "")]
    for candidate in candidates:
        if candidate in catalog:
            return {**catalog[candidate], "provider_symbol": provider_symbol}, "SYSTEM_MONITORED"
    return {
        "code": code, "name": default_name, "asset_type": "INDEX" if code in {"SPX", "NDX"} else "STOCK",
        "market": market, "provider_symbol": provider_symbol,
    }, "USER_REQUESTED"


def timestamp_to_beijing(timestamp: int | float | str) -> str:
    dt = datetime.fromtimestamp(float(timestamp), timezone.utc)
    return dt.astimezone(BEIJING).isoformat(timespec="seconds")


def yahoo_quote(root: Path, obj: dict, now: datetime, policy: dict) -> dict:
    provider_symbol = str(obj["provider_symbol"])
    payload = _request_json(provider_symbol, period="1d", interval="5m", include_prepost=True)
    result = (payload.get("chart", {}).get("result") or [])[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows = quote.get("open") or [], quote.get("high") or [], quote.get("low") or []
    closes, volumes = quote.get("close") or [], quote.get("volume") or []
    index = None
    for i in range(len(timestamps) - 1, -1, -1):
        if i < len(closes) and closes[i] is not None:
            index = i
            break
    if index is None:
        raise RuntimeError("provider returned no valid timestamped price")
    timestamp = timestamp_to_beijing(timestamps[index])
    close = closes[index]
    row = {
        "symbol": obj["code"], "provider_symbol": provider_symbol, "name": obj["name"],
        "provider_name": obj["name"], "provider": "yahoo_chart_api",
        "provider_timestamp_ms": int(float(timestamps[index]) * 1000),
        "open": opens[index] if index < len(opens) else None,
        "high": highs[index] if index < len(highs) else None,
        "low": lows[index] if index < len(lows) else None,
        "close": close, "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
        "volume": volumes[index] if index < len(volumes) else None,
        # Yahoo Chart does not expose a verified traded amount. Keep it null.
        "amount": None, "market_phase": "REGULAR",
    }
    valid, reason = validate_market_row(row, obj["code"], obj["name"], now=now.astimezone(timezone.utc), runtime_policy=policy, direct_only=True)
    status, age = age_status(timestamp, now, policy)
    if not valid:
        return {
            **format_quote(row, obj, timestamp, status, age, "USER_REQUESTED"),
            "quality_status": "DATA_UNAVAILABLE",
            "failure_reason": reason,
            "latest_valid_time": timestamp,
        }
    return format_quote(row, obj, timestamp, status, age, "USER_REQUESTED")


def format_quote(row: dict, obj: dict, timestamp: str, freshness: str, age: int | None, source_type: str) -> dict:
    return {
        "object_name": obj["name"], "object_code": obj["code"], "asset_type": obj["asset_type"],
        "market": display_market(obj["market"]), "market_code": obj["market"],
        "data_time_beijing": timestamp, "price": row.get("close"), "change": row.get("change"),
        "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
        "volume": row.get("volume"), "turnover": row.get("amount"),
        "provider": row.get("provider") or row.get("source") or "",
        "freshness": freshness, "freshness_age_seconds": age, "quality_status": row.get("quality_status") or "PASS",
        "source_type": source_type, "direct_quote": True,
        "failure_reason": row.get("failure_reason", ""),
        "latest_valid_time": row.get("latest_valid_time", timestamp),
    }


def system_quote(root: Path, obj: dict, now: datetime, force_refresh: bool) -> dict:
    context = build_market_quote_context(root, now=now, force_refresh=force_refresh, requested_symbols=[obj["code"]])
    normalized = obj["code"].replace(".SH", "").replace(".SZ", "").upper()
    selected = next((q for q in context.get("quotes", []) if str(q.get("symbol", "")).upper() in {obj["code"], normalized}), None)
    if selected is None:
        return {
            "object_name": obj["name"], "object_code": obj["code"], "asset_type": obj["asset_type"],
            "market": display_market(obj["market"]), "market_code": obj["market"],
            "data_time_beijing": "", "price": None, "change": None, "open": None, "high": None, "low": None,
            "volume": None, "turnover": None, "provider": "", "freshness": "UNKNOWN",
            "quality_status": "DATA_UNAVAILABLE", "source_type": "SYSTEM_MONITORED",
            "failure_reason": "no matching formal routed quote",
        }
    quote = dict(selected)
    return {
        "object_name": quote.get("name") or obj["name"], "object_code": obj["code"],
        "asset_type": obj["asset_type"], "market": display_market(obj["market"]), "market_code": obj["market"],
        "data_time_beijing": quote.get("data_time_beijing", ""), "price": quote.get("latest_price"),
        "change": quote.get("change"), "open": quote.get("open"), "high": quote.get("high"),
        "low": quote.get("low"), "volume": quote.get("volume"), "turnover": quote.get("turnover") or quote.get("amount"),
        "provider": quote.get("source", ""), "freshness": quote.get("freshness", "UNKNOWN"),
        "quality_status": quote.get("quality_status", "UNKNOWN"), "source_type": "SYSTEM_MONITORED",
        "direct_quote": quote.get("direct_quote", True), "failure_reason": "",
        "latest_valid_time": quote.get("data_time_beijing", ""),
    }


def write_query_context(root: Path, result: dict) -> None:
    path = root / "data/state/query_context.json"
    context = load_json(path, {})
    context["system_objects"] = context.get("system_objects", [])
    context["user_requested_objects"] = [result]
    context["last_market_query"] = {
        "requested_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "object_code": result.get("object_code"), "source_type": result.get("source_type"),
        "quality_status": result.get("quality_status"),
    }
    save_json(path, context)


def query(root: Path, requested: str, *, force_refresh: bool = False) -> dict:
    now = now_beijing()
    policy = load_json(root / "config/runtime_policy.json", {})
    obj, source_type = resolve_object(root, requested)
    if source_type == "SYSTEM_MONITORED":
        result = system_quote(root, obj, now, force_refresh=force_refresh)
    else:
        result = yahoo_quote(root, obj, now, policy) if obj["market"] == "US" else {
            "object_name": obj["name"], "object_code": obj["code"], "asset_type": obj["asset_type"],
            "market": display_market(obj["market"]), "market_code": obj["market"],
            "data_time_beijing": "", "price": None, "change": None, "open": None, "high": None, "low": None,
            "volume": None, "turnover": None, "provider": "", "freshness": "UNKNOWN",
            "quality_status": "DATA_UNAVAILABLE", "source_type": "USER_REQUESTED",
            "failure_reason": "no direct extension provider configured for this market",
        }
    write_query_context(root, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Query one formal or explicitly requested market object.")
    parser.add_argument("object", help="code or name, e.g. NDX, SPX, MU, 561980")
    parser.add_argument("--force-refresh", action="store_true", help="refresh a formal object through the existing query-time route")
    args = parser.parse_args()
    try:
        result = query(ROOT, args.object, force_refresh=args.force_refresh)
    except Exception as exc:
        result = {
            "object_name": args.object, "object_code": args.object.upper(), "asset_type": "UNKNOWN",
            "market": "未知市场", "data_time_beijing": "", "price": None, "change": None,
            "open": None, "high": None, "low": None, "volume": None, "turnover": None,
            "provider": "", "freshness": "UNKNOWN", "quality_status": "DATA_UNAVAILABLE",
            "source_type": "USER_REQUESTED", "failure_reason": str(exc)[-500:], "latest_valid_time": "",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
