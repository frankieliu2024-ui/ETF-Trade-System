from __future__ import annotations

"""Unified explicit market-object query entry.

Names or codes are resolved to a market object, then the existing market quote router
performs query-time acquisition. This file never changes the monitoring universe,
trading rules, provider permissions, lifecycle, or orders.
"""

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from market_quote_router import build_market_quote_context
except ModuleNotFoundError:
    from scripts.market_quote_router import build_market_quote_context

BEIJING = ZoneInfo("Asia/Shanghai")
ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()

# Small deterministic aliases for common objects and formal names. This is not a
# monitoring universe; unknown names fall through to online security resolution.
ALIASES = {
    "标普500": ("SPX", "^GSPC", "US", "标普500指数", "INDEX"),
    "标普500指数": ("SPX", "^GSPC", "US", "标普500指数", "INDEX"),
    "SPX": ("SPX", "^GSPC", "US", "标普500指数", "INDEX"),
    "纳斯达克100": ("NDX", "^NDX", "US", "纳斯达克100指数", "INDEX"),
    "纳斯达克100指数": ("NDX", "^NDX", "US", "纳斯达克100指数", "INDEX"),
    "NDX": ("NDX", "^NDX", "US", "纳斯达克100指数", "INDEX"),
    "沪深300": ("000300", "000300.SH", "CN", "沪深300指数", "INDEX"),
    "沪深300指数": ("000300", "000300.SH", "CN", "沪深300指数", "INDEX"),
    "美光科技": ("MU", "MU", "US", "美光科技", "STOCK"),
    "MU": ("MU", "MU", "US", "美光科技", "STOCK"),
    "英伟达": ("NVDA", "NVDA", "US", "英伟达", "STOCK"),
    "NVDA": ("NVDA", "NVDA", "US", "英伟达", "STOCK"),
    "台积电": ("TSM", "TSM", "US", "台积电", "STOCK"),
    "TSM": ("TSM", "TSM", "US", "台积电", "STOCK"),
    "三星电子": ("005930.KS", "005930.KS", "KR", "三星电子", "STOCK"),
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def display_market(market: str) -> str:
    return {"CN": "中国大陆", "HK": "中国香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国", "US": "美国"}.get(market, market)


def _market_from_symbol(symbol: str, exchange: str = "") -> str:
    upper = symbol.upper()
    if upper.endswith((".SS", ".SZ", ".SH")):
        return "CN"
    if upper.endswith(".HK"):
        return "HK"
    if upper.endswith(".TW"):
        return "TW"
    if upper.endswith(".T"):
        return "JP"
    if upper.endswith((".KS", ".KQ")):
        return "KR"
    if exchange.upper() in {"SHH", "SHZ", "SSE", "SZSE"}:
        return "CN"
    if exchange.upper() in {"KSC", "KOE", "KRX"}:
        return "KR"
    return "US"


def _provider_symbol(symbol: str, market: str) -> str:
    upper = symbol.upper()
    if market == "CN" and upper.endswith(".SS"):
        return upper[:-3] + ".SH"
    return upper


def _asset_type(quote_type: str) -> str:
    return {"ETF": "ETF", "INDEX": "INDEX", "EQUITY": "STOCK", "MUTUALFUND": "FUND"}.get(str(quote_type).upper(), "STOCK")


def monitored_catalog(root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    universe = load_json(root / "config/market/etf_monitor_universe.json", {})
    for item in universe.get("objects") or []:
        code = str(item.get("code") or "").upper()
        if code:
            result[code] = {"code": code, "provider_symbol": str(item.get("thscode") or code), "name": str(item.get("name") or code), "asset_type": "ETF", "market": "CN"}
    monitor = load_json(root / "config/market/market_monitor_config.json", {})
    indices = monitor.get("formal_index_layer", {}).get("required_objects", [])
    names = {"000001.SH": "上证指数", "399006.SZ": "创业板指", "000688.SH": "科创50指数", "NDX": "纳斯达克100指数", "SOX": "费城半导体指数", "N225": "日经225指数", "KOSPI": "韩国综合指数", "TWII": "台湾加权指数", "HSTECH": "恒生科技指数"}
    for code in indices:
        code = str(code).upper()
        market = "CN" if code.endswith((".SH", ".SZ")) else {"N225": "JP", "KOSPI": "KR", "TWII": "TW", "HSTECH": "HK"}.get(code, "US")
        result[code] = {"code": code, "provider_symbol": code, "name": names.get(code, code), "asset_type": "INDEX", "market": market}
    account = load_json(root / "data/state/account_fact.json", {})
    for position in account.get("positions") or []:
        code = str(position.get("code") or "").upper()
        if int(position.get("quantity") or 0) > 0 and code:
            suffix = ".SH" if code.startswith(("5", "6")) else ".SZ"
            result[code] = {"code": code, "provider_symbol": code + suffix, "name": str(position.get("name") or code), "asset_type": str(position.get("asset_type") or "STOCK"), "market": "CN"}
    return result


def _local_resolve(root: Path, requested: str) -> tuple[dict, str] | None:
    raw = requested.strip()
    catalog = monitored_catalog(root)
    folded = raw.casefold()
    for item in catalog.values():
        if folded in {str(item.get("code", "")).casefold(), str(item.get("provider_symbol", "")).casefold(), str(item.get("name", "")).casefold()}:
            return dict(item), "SYSTEM_MONITORED"
    alias = ALIASES.get(raw.upper()) or ALIASES.get(raw)
    if alias:
        code, provider_symbol, market, name, asset_type = alias
        return {"code": code, "provider_symbol": provider_symbol, "market": market, "name": name, "asset_type": asset_type}, "USER_REQUESTED"
    upper = raw.upper().replace(".SS", ".SH")
    if upper.endswith((".SH", ".SZ")):
        return {"code": upper.split(".")[0], "provider_symbol": upper, "market": "CN", "name": raw, "asset_type": "STOCK"}, "USER_REQUESTED"
    if upper.endswith((".HK", ".TW", ".T", ".KS", ".KQ")):
        market = _market_from_symbol(upper)
        return {"code": upper, "provider_symbol": upper, "market": market, "name": raw, "asset_type": "STOCK"}, "USER_REQUESTED"
    if upper.isalpha() or upper.startswith("^"):
        return {"code": upper, "provider_symbol": upper, "market": "US", "name": raw, "asset_type": "STOCK"}, "USER_REQUESTED"
    return None


def _online_resolve(requested: str) -> dict:
    """Resolve a human security name only; quote acquisition remains in the formal router."""
    url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": requested, "quotesCount": 10, "newsCount": 0})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ETF-Trade-System security-resolver"})
    with urllib.request.urlopen(req, timeout=8) as response:
        payload = json.load(response)
    candidates = []
    for row in payload.get("quotes") or []:
        symbol = str(row.get("symbol") or "").strip()
        quote_type = str(row.get("quoteType") or "").upper()
        if not symbol or quote_type not in {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}:
            continue
        market = _market_from_symbol(symbol, str(row.get("exchange") or ""))
        name = str(row.get("longname") or row.get("shortname") or symbol)
        provider_symbol = _provider_symbol(symbol, market)
        score = 0
        folded = requested.casefold()
        if symbol.casefold() == folded:
            score += 100
        if name.casefold() == folded:
            score += 80
        if folded in name.casefold():
            score += 20
        if market in {"CN", "HK", "TW", "JP", "KR", "US"}:
            score += 5
        candidates.append((score, {"code": provider_symbol.split(".")[0] if market == "CN" else symbol.upper(), "provider_symbol": provider_symbol, "market": market, "name": name, "asset_type": _asset_type(quote_type)}))
    if not candidates:
        raise RuntimeError(f"无法把名称解析为受支持的证券对象：{requested}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def resolve_object(root: Path, requested: str) -> tuple[dict, str]:
    local = _local_resolve(root, requested)
    if local:
        return local
    return _online_resolve(requested), "USER_REQUESTED"


def _normalized_symbol(value: str) -> str:
    return str(value or "").upper().replace(".SS", ".SH")


def routed_quote(root: Path, obj: dict, source_type: str, now: datetime) -> dict:
    requested_symbol = str(obj.get("provider_symbol") or obj["code"])
    context = build_market_quote_context(root, now=now, force_refresh=True, requested_symbols=[requested_symbol])
    targets = {_normalized_symbol(requested_symbol), _normalized_symbol(obj["code"]), _normalized_symbol(obj["code"]).replace(".SH", "").replace(".SZ", "")}
    selected = next((q for q in context.get("quotes", []) if _normalized_symbol(q.get("symbol", "")) in targets or _normalized_symbol(q.get("symbol", "")).replace(".SH", "").replace(".SZ", "") in targets), None)
    if selected is None:
        failure = next((x for x in context.get("refresh_failures", []) if _normalized_symbol(x.get("symbol", "")) in targets), {})
        return {"object_name": obj["name"], "object_code": obj["code"], "asset_type": obj["asset_type"], "market": display_market(obj["market"]), "market_code": obj["market"], "data_time_beijing": "", "price": None, "change": None, "open": None, "high": None, "low": None, "volume": None, "turnover": None, "provider": "", "freshness": "UNKNOWN", "quality_status": "DATA_UNAVAILABLE", "source_type": source_type, "failure_reason": str(failure.get("error") or "no matching routed quote")}
    return {
        "object_name": selected.get("name") or obj["name"], "object_code": obj["code"], "provider_symbol": requested_symbol,
        "asset_type": obj["asset_type"], "market": display_market(obj["market"]), "market_code": obj["market"],
        "data_time_beijing": selected.get("data_time_beijing", ""), "price": selected.get("latest_price"), "change": selected.get("change"),
        "open": selected.get("open"), "high": selected.get("high"), "low": selected.get("low"), "volume": selected.get("volume"),
        "turnover": selected.get("turnover") or selected.get("amount"), "provider": selected.get("source", ""),
        "freshness": selected.get("freshness", "UNKNOWN"), "quality_status": selected.get("quality_status", "UNKNOWN"),
        "source_type": source_type, "direct_quote": selected.get("direct_quote", True), "failure_reason": "",
        "market_phase": selected.get("market_phase"), "market_status_cn": selected.get("market_status_cn"), "data_nature_cn": selected.get("data_nature_cn"),
        "minute_path_context": selected.get("minute_path_context"), "minute_path_source": selected.get("minute_path_source"),
        "refresh_source": selected.get("refresh_source"), "resolution_source": "LOCAL_OR_SECURITY_SEARCH",
    }


def write_query_context(root: Path, result: dict) -> None:
    path = root / "data/state/query_context.json"
    context = load_json(path, {})
    context["system_objects"] = context.get("system_objects", [])
    context["user_requested_objects"] = [result] if result.get("source_type") == "USER_REQUESTED" else []
    context["last_market_query"] = {"requested_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"), "object_code": result.get("object_code"), "source_type": result.get("source_type"), "quality_status": result.get("quality_status")}
    save_json(path, context)


def query(root: Path, requested: str, *, force_refresh: bool = True) -> dict:
    now = datetime.now(BEIJING)
    obj, source_type = resolve_object(root, requested)
    # Explicit user queries always go through query-time acquisition. The flag is
    # retained for CLI/API compatibility but does not downgrade an explicit query to cache-only.
    result = routed_quote(root, obj, source_type, now)
    write_query_context(root, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Query one market object by security name or code.")
    parser.add_argument("object", help="security name or code, e.g. 沪深300指数, 证券ETF, 三星电子, NVDA")
    parser.add_argument("--force-refresh", action="store_true", help="compatibility flag; explicit queries already refresh at query time")
    args = parser.parse_args()
    result = query(ROOT, args.object, force_refresh=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
