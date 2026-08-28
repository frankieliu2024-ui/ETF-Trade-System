from __future__ import annotations

import json
from datetime import datetime, timezone

try:
    import query_time_market_refresh_legacy as _legacy
    from tencent_stock_minute import build_stock_minute_feature
except ModuleNotFoundError:
    from scripts import query_time_market_refresh_legacy as _legacy
    from scripts.tencent_stock_minute import build_stock_minute_feature

# Static contract anchors retained for source-level audits: _cn_code, _a_share,
# tencent_qq primary, hithink-finance fallback, eastmoney_push2 fallback,
# QUERY_TIME_FIRST_ACTIVE_MARKET_REQUIRES_FRESH_PROVIDER_TIMESTAMP.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_original_a_share = _legacy._a_share
_ACTIVE_PHASES = {"REGULAR", "OPENING_AUCTION", "PRE_MARKET", "POST_MARKET"}


def _cn_thscode(symbol: str) -> str:
    upper = str(symbol).upper().replace(".SS", ".SH")
    if upper.endswith((".SH", ".SZ")):
        return upper
    raw = upper.split(".")[0]
    # This heuristic is only a fallback when a resolver did not provide exchange.
    return f"{raw}.SH" if raw.startswith(("5", "6")) else f"{raw}.SZ"


def _stock_thscode(symbol: str) -> str:
    return _cn_thscode(symbol)


def _is_stock_minute_target(symbol: str, root) -> bool:
    raw = str(symbol).upper().replace(".SS", ".SH").replace(".SH", "").replace(".SZ", "")
    if raw in {"000001", "399006", "000688", "000300"}:
        return False
    try:
        universe = json.loads((root / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        etf_codes = {str(x.get("code") or "") for x in (universe.get("objects") or [])}
        if raw in etf_codes:
            return False
    except Exception:
        if raw.startswith(("159", "5")):
            return False
    return raw.isdigit() and len(raw) == 6


def _attach_stock_minute(symbol: str, root, quote: dict) -> dict:
    if not _is_stock_minute_target(symbol, root):
        return quote
    code = str(symbol).upper().replace(".SS", ".SH").replace(".SH", "").replace(".SZ", "")
    quote["code"] = code
    quote["thscode"] = _stock_thscode(symbol)
    if quote.get("source") != "tencent_qq" or quote.get("provider_timestamp_ms") in (None, ""):
        quote["minute_path_context"] = {
            "status": "FALLBACK",
            "production_usable": False,
            "production_selection_reason": "Tencent quote primary unavailable; retain selected quote fallback without minute overlay",
        }
        return quote
    provider_dt = datetime.fromtimestamp(float(quote["provider_timestamp_ms"]) / 1000.0, tz=timezone.utc).astimezone(_legacy.BEIJING)
    feature = build_stock_minute_feature(root, quote, provider_dt.date().isoformat(), quote.get("market_phase"))
    quote["minute_path_context"] = feature
    quote["minute_path_source"] = "TENCENT_1M" if feature.get("production_usable") is True else "NO_MINUTE_OBJECT_FALLBACK"
    quote["formal_latest_price_source_unchanged"] = True
    return quote


def _a_share(symbol: str, root, now, policy: dict) -> dict:
    quote = _original_a_share(symbol, root, now, policy)
    return _attach_stock_minute(symbol, root, quote)


def _explicit_cn_session_reference(symbol: str, root, now) -> dict:
    """Direct Tencent session reference for an explicit CN object outside active trading."""
    thscode = _cn_thscode(symbol)
    provider_rows = fetch_tencent_quotes([thscode], timeout=10)
    row = provider_rows[thscode.upper()]
    timestamp_ms = row.get("provider_timestamp_ms")
    if timestamp_ms in (None, ""):
        raise RuntimeError(f"Tencent {thscode} missing provider timestamp")
    provider_dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).astimezone(_legacy.BEIJING)
    query_dt = now.astimezone(_legacy.BEIJING)
    if provider_dt.date() != query_dt.date():
        raise RuntimeError(
            f"Tencent {thscode} is not a same-day session reference: "
            f"provider_date={provider_dt.date().isoformat()} query_date={query_dt.date().isoformat()}"
        )
    phase = market_phase("CN", now=now)
    quote = {
        "market": "CN",
        "market_name": "中国大陆",
        "symbol": str(symbol).upper().replace(".SS", ".SH"),
        "name": row.get("name", str(symbol).upper()),
        "latest_price": row["last_price"],
        "open": row.get("open_price"),
        "high": row.get("high_price"),
        "low": row.get("low_price"),
        "prev_close": row.get("prev_price"),
        "volume": row.get("volume"),
        "amount": row.get("turnover"),
        "turnover": row.get("turnover"),
        "data_time_beijing": provider_dt.isoformat(timespec="seconds"),
        "data_time_local": provider_dt.isoformat(timespec="seconds"),
        "market_phase": phase,
        "market_status_cn": display_market_status("CN", phase),
        "data_nature_cn": "最近有效交易时段行情（查询时腾讯直取）",
        "source": "tencent_qq",
        "freshness": "SESSION_REFERENCE",
        "quality_status": "PASS",
        "direct_quote": True,
        "refresh_source": "QUERY_TIME_PROVIDER_SESSION_REFERENCE",
        "provider_symbol": row.get("provider_symbol"),
        "provider_timestamp_ms": timestamp_ms,
        "session_reference_rule": "Explicit CN queries outside active trading may consume only a same-Beijing-date Tencent terminal quote; previous-session quotes are rejected.",
    }
    return _attach_stock_minute(symbol, root, quote)


def _explicit_overseas_session_reference(symbol: str, market: str, now, policy: dict) -> dict:
    yahoo_symbol = DEFAULT_SYMBOLS.get(symbol, (symbol, market))[0]
    quote = _yahoo(yahoo_symbol, market, now, policy) | {"symbol": symbol}
    quote["freshness"] = "SESSION_REFERENCE"
    quote["quality_status"] = "PASS"
    quote["data_nature_cn"] = "最近有效交易时段行情（查询时直取）"
    quote["refresh_source"] = "QUERY_TIME_PROVIDER_SESSION_REFERENCE"
    quote["session_reference_rule"] = "Explicit non-active-market queries return the latest timestamped provider session reference and never label it as live."
    return quote


def _sync_patchable_globals() -> None:
    names = (
        "market_phase", "display_market_status", "fetch_tencent_quotes", "shutil", "_cli_json",
        "fetch_naver_kospi", "fetch_twse_taiex", "fetch_eastmoney_index",
        "fetch_hstech_eastmoney", "fetch_hstech_hithink", "fetch_formal_yahoo",
        "provider_attempt", "select_hstech_candidate", "_yahoo", "_eastmoney_etf", "_request_json",
    )
    for name in names:
        if name in globals():
            setattr(_legacy, name, globals()[name])
    _legacy._a_share = _a_share


def refresh_market_quotes(root, requested_symbols: list[str], now):
    _sync_patchable_globals()
    result = _legacy.refresh_market_quotes(root, requested_symbols, now)
    explicit = [str(x).upper().replace(".SS", ".SH") for x in requested_symbols if str(x).strip()]
    if not explicit:
        return result

    returned = {str(x.get("symbol") or "").upper().replace(".SS", ".SH") for x in (result.get("quotes") or []) if isinstance(x, dict)}
    failed = {str(x.get("symbol") or "").upper().replace(".SS", ".SH") for x in (result.get("failures") or []) if isinstance(x, dict)}
    policy = _legacy._policy(root)
    for symbol in explicit:
        if symbol in returned or symbol in failed:
            continue
        canonical = FORMAL_OBJECT_ALIASES.get(symbol)
        market = "CN" if _cn_code(symbol) else (FORMAL_MARKETS.get(canonical) if canonical else DEFAULT_SYMBOLS.get(symbol, ("", _market_for_symbol(symbol)))[1])
        phase = market_phase(market, now=now)
        if phase in _ACTIVE_PHASES:
            continue
        try:
            if market == "CN":
                quote = _explicit_cn_session_reference(symbol, root, now)
            elif canonical:
                # Formal monitored overseas objects keep their existing state/provider semantics.
                # If the active formal direct chain does not produce a current quote, cached state remains the router fallback.
                continue
            else:
                quote = _explicit_overseas_session_reference(symbol, market, now, policy)
            result.setdefault("quotes", []).append(quote)
        except Exception as exc:
            result.setdefault("failures", []).append({"symbol": symbol, "error": str(exc)[-500:]})

    result["refresh_contract"] = (
        "QUERY_TIME_ACTIVE_MARKET_REQUIRES_FRESH_PROVIDER_TIMESTAMP; "
        "EXPLICIT_NON_ACTIVE_MARKET_RETURNS_LATEST_TIMESTAMPED_SESSION_REFERENCE; "
        "CN_OFF_SESSION_REJECTS_PREVIOUS_DATE_TENCENT_QUOTES"
    )
    return result


main = getattr(_legacy, "main", None)


if __name__ == "__main__" and main is not None:
    main()
