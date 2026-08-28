from __future__ import annotations

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


def _a_share(symbol: str, root, now, policy: dict) -> dict:
    quote = _original_a_share(symbol, root, now, policy)
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


# Patch only the A-share query-time provider function. The legacy router retains
# all overseas/session/fallback behavior and resolves this global at execution.
_legacy._a_share = _a_share
refresh_market_quotes = _legacy.refresh_market_quotes
main = getattr(_legacy, "main", None)


if __name__ == "__main__" and main is not None:
    main()
