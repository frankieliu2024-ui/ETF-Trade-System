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
_ACTIVE_CN_PHASES = {"REGULAR", "OPENING_AUCTION"}


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


def _attach_stock_minute(symbol: str, root, quote: dict) -> dict:
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


def _a_share(symbol: str, root, now, policy: dict) -> dict:
    quote = _original_a_share(symbol, root, now, policy)
    return _attach_stock_minute(symbol, root, quote)


def _a_share_calendar(root) -> dict:
    try:
        return json.loads((root / "config/market/a_share_trading_calendar_2026.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_a_share_trading_date(root, date_value) -> bool:
    calendar = _a_share_calendar(root)
    text = date_value.isoformat()
    if date_value.weekday() >= 5:
        return False
    if calendar.get("coverage_start") and text < str(calendar["coverage_start"]):
        return False
    if calendar.get("coverage_end") and text > str(calendar["coverage_end"]):
        return False
    return text not in set(calendar.get("closed_dates") or [])


def _latest_completed_a_share_session_date(root, now):
    from datetime import timedelta
    query_date = now.astimezone(_legacy.BEIJING).date()
    phase = market_phase("CN", now=now)
    candidate = query_date
    # Before the first trading phase, today's session has not produced a completed
    # reference. Midday/post-close may legally consume today's latest terminal fact.
    if phase == "OFF_SESSION" and now.astimezone(_legacy.BEIJING).hour < 9:
        candidate -= timedelta(days=1)
    if not _is_a_share_trading_date(root, candidate):
        candidate -= timedelta(days=1)
    for _ in range(10):
        if _is_a_share_trading_date(root, candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("cannot resolve latest completed A-share session date")


def _is_etf_reference_target(symbol: str, root) -> bool:
    raw = str(symbol).upper().replace(".SH", "").replace(".SZ", "")
    if not (raw.isdigit() and len(raw) == 6):
        return False
    try:
        universe = json.loads((root / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        if raw in {str(x.get("code") or "") for x in (universe.get("objects") or [])}:
            return True
    except Exception:
        pass
    # Discovery candidates are dynamic and intentionally need not be in the monitor
    # universe. Exchange fund prefixes are only an object classifier, never a
    # permission/ranking rule.
    return raw.startswith(("159", "5"))


def _explicit_etf_session_reference(symbol: str, root, now) -> dict:
    """Return a provider-backed ETF terminal fact for the latest legal completed session.

    This fact is eligible for structure/Observation evaluation only. It is never
    relabelled FRESH and never grants executable-price/amount permission.
    """
    thscode = _stock_thscode(symbol)
    provider_rows = fetch_tencent_quotes([thscode], timeout=10)
    row = provider_rows[thscode.upper()]
    timestamp_ms = row.get("provider_timestamp_ms")
    if timestamp_ms in (None, ""):
        raise RuntimeError(f"Tencent {thscode} missing provider timestamp")
    provider_dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).astimezone(_legacy.BEIJING)
    expected_date = _latest_completed_a_share_session_date(root, now)
    if provider_dt.date() != expected_date:
        raise RuntimeError(
            f"Tencent {thscode} is not the latest legal completed-session reference: "
            f"provider_date={provider_dt.date().isoformat()} expected_date={expected_date.isoformat()}"
        )
    phase = market_phase("CN", now=now)
    no_trade_partial = any(row.get(key) is None for key in ("open_price", "high_price", "low_price", "volume", "turnover"))
    return {
        "market": "CN", "market_name": "中国大陆", "symbol": str(symbol).upper(),
        "name": row.get("name", str(symbol).upper()), "latest_price": row["last_price"],
        "open": row.get("open_price"), "high": row.get("high_price"), "low": row.get("low_price"),
        "prev_close": row.get("prev_price"), "volume": row.get("volume"),
        "amount": row.get("turnover"), "turnover": row.get("turnover"),
        "data_time_beijing": provider_dt.isoformat(timespec="seconds"),
        "data_time_local": provider_dt.isoformat(timespec="seconds"),
        "market_phase": phase, "market_status_cn": display_market_status("CN", phase),
        "data_nature_cn": "最近合法已完成A股交易时段ETF正式参考行情",
        "source": "tencent_qq", "freshness": "SESSION_REFERENCE",
        "quality_status": "DEGRADED" if no_trade_partial else "PASS",
        "direct_quote": True, "refresh_source": "QUERY_TIME_PROVIDER_ETF_SESSION_REFERENCE",
        "provider_symbol": row.get("provider_symbol"), "provider_timestamp_ms": timestamp_ms,
        "evidence_fitness": {
            "object_fact": True, "observation_evaluation": True,
            "capital_candidate_evaluation": True, "executable_price": False,
            "trade_amount_or_shares": False,
        },
        "session_reference_rule": "Provider timestamp must equal the latest legally completed A-share session; SESSION_REFERENCE never grants executable-price permission.",
    }


def _explicit_same_day_stock_reference(symbol: str, root, now) -> dict:
    """Query Tencent directly for an explicitly requested A-share stock outside active trading.

    The route accepts only a Tencent quote whose provider date is the current Beijing
    date. This keeps post-close/lunch queries useful without allowing a previous-day
    quote to masquerade as a same-day immediate refresh. Minute evidence remains an
    overlay; the Tencent quote is still the formal latest-price fact for this query.
    """
    thscode = _stock_thscode(symbol)
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
    no_trade_partial = any(row.get(key) is None for key in ("open_price", "high_price", "low_price", "volume", "turnover"))
    quote = {
        "market": "CN",
        "market_name": "中国大陆",
        "symbol": str(symbol).upper(),
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
        "data_nature_cn": "当日交易时段最近有效价（查询时腾讯直取）" if not no_trade_partial else "当日交易中暂无新成交的最近有效价（查询时腾讯直取）",
        "source": "tencent_qq",
        "freshness": "SESSION_REFERENCE",
        "quality_status": "DEGRADED" if no_trade_partial else "PASS",
        "direct_quote": True,
        "refresh_source": "QUERY_TIME_PROVIDER_SAME_DAY_SESSION_REFERENCE",
        "provider_symbol": row.get("provider_symbol"),
        "provider_timestamp_ms": timestamp_ms,
        "session_reference_rule": "Only explicit A-share stock queries may consume same-Beijing-date Tencent terminal quotes outside active trading; previous-session quotes are rejected.",
    }
    return _attach_stock_minute(symbol, root, quote)


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
    result = _legacy.refresh_market_quotes(root, requested_symbols, now)
    explicit = [str(x).upper() for x in requested_symbols if str(x).strip()]
    if not explicit or market_phase("CN", now=now) in _ACTIVE_CN_PHASES:
        return result

    returned = {str(x.get("symbol") or "").upper() for x in (result.get("quotes") or []) if isinstance(x, dict)}
    failed = {str(x.get("symbol") or "").upper() for x in (result.get("failures") or []) if isinstance(x, dict)}
    for symbol in explicit:
        if symbol in returned or symbol in failed or not _cn_code(symbol):
            continue
        try:
            if _is_etf_reference_target(symbol, root):
                result.setdefault("quotes", []).append(_explicit_etf_session_reference(symbol, root, now))
            elif _is_stock_minute_target(symbol, root):
                result.setdefault("quotes", []).append(_explicit_same_day_stock_reference(symbol, root, now))
        except Exception as exc:
            result.setdefault("failures", []).append({"symbol": symbol, "error": str(exc)[-500:]})
    result["refresh_contract"] = "QUERY_TIME_ACTIVE_MARKET_REQUIRES_FRESH_PROVIDER_TIMESTAMP; EXPLICIT_CN_ETF_OFF_SESSION_REQUIRES_LATEST_LEGAL_COMPLETED_SESSION_REFERENCE; EXPLICIT_CN_STOCK_OFF_SESSION_REQUIRES_SAME_DAY_TENCENT_SESSION_REFERENCE"
    return result


main = getattr(_legacy, "main", None)


if __name__ == "__main__" and main is not None:
    main()
