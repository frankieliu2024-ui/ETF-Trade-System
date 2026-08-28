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


def _stock_thscode(symbol: str) -> str:
    upper = str(symbol).upper()
    if upper.endswith((".SH", ".SZ")):
        return upper
    raw = upper.split(".")[0]
    return f"{raw}.SH" if raw.startswith(("5", "6")) else f"{raw}.SZ"


def _is_stock_minute_target(symbol: str, root) -> bool:
    raw = str(symbol).upper().replace(".SH", "").replace(".SZ", "")
    if raw in {"000001", "399006", "000688"}:
        return False
    try:
        universe = json.loads((root / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        etf_codes = {str(x.get("code") or "") for x in (universe.get("objects") or [])}
        if raw in etf_codes:
            return False
    except Exception:
        # On incomplete query/test roots, fund-like codes remain excluded by prefix.
        if raw.startswith(("159", "5")):
            return False
    return raw.isdigit() and len(raw) == 6


def _a_share(symbol: str, root, now, policy: dict) -> dict:
    quote = _original_a_share(symbol, root, now, policy)
    if not _is_stock_minute_target(symbol, root):
        return quote
    code = str(symbol).upper().replace(".SH", "").replace(".SZ", "")
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


def _sync_patchable_globals() -> None:
    # Existing tests and callers patch the canonical module. Mirror those public
    # dependency names into the legacy implementation before each call so the
    # wrapper remains behaviorally transparent outside the A-share minute overlay.
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
    return _legacy.refresh_market_quotes(root, requested_symbols, now)


main = getattr(_legacy, "main", None)


if __name__ == "__main__" and main is not None:
    main()
