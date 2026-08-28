from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from market_quote_router import display_market_status, market_phase
    from multi_source_market import _request_json
    from build_overseas_context import (
        OBJECTS as FORMAL_OVERSEAS_OBJECTS,
        EASTMONEY_DIRECT_FALLBACKS,
        fetch_eastmoney_index,
        fetch_hstech_eastmoney,
        fetch_hstech_hithink,
        fetch_naver_kospi,
        fetch_twse_taiex,
        fetch_yahoo as fetch_formal_yahoo,
        provider_attempt,
        select_hstech_candidate,
    )
except ModuleNotFoundError:
    from scripts.market_quote_router import display_market_status, market_phase
    from scripts.multi_source_market import _request_json
    from scripts.build_overseas_context import (
        OBJECTS as FORMAL_OVERSEAS_OBJECTS,
        EASTMONEY_DIRECT_FALLBACKS,
        fetch_eastmoney_index,
        fetch_hstech_eastmoney,
        fetch_hstech_hithink,
        fetch_naver_kospi,
        fetch_twse_taiex,
        fetch_yahoo as fetch_formal_yahoo,
        provider_attempt,
        select_hstech_candidate,
    )
try:
    from tencent_quote import fetch_tencent_quotes
except ModuleNotFoundError:
    from scripts.tencent_quote import fetch_tencent_quotes

BEIJING = ZoneInfo("Asia/Shanghai")
MARKET_ZONES = {
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "US": "America/New_York",
}
DEFAULT_SYMBOLS = {
    "NDX": ("^NDX", "US"),
    "SOX": ("^SOX", "US"),
    "QQQ": ("QQQ", "US"),
    "SOXX": ("SOXX", "US"),
    "N225": ("^N225", "JP"),
    "KOSPI": ("^KS11", "KR"),
    "TWII": ("^TWII", "TW"),
    "HSTECH": ("HSTECH.HK", "HK"),
}
FORMAL_OBJECT_ALIASES = {
    "NDX": "NDX",
    "^NDX": "NDX",
    "SOX": "SOX",
    "^SOX": "SOX",
    "N225": "N225",
    "^N225": "N225",
    "KOSPI": "KOSPI",
    "^KS11": "KOSPI",
    "TWII": "TWII",
    "^TWII": "TWII",
    "HSTECH": "HSTECH",
    "HSTECH.HK": "HSTECH",
}
FORMAL_MARKETS = {"NDX": "US", "SOX": "US", "N225": "JP", "KOSPI": "KR", "TWII": "TW", "HSTECH": "HK"}


def _policy(root: Path) -> dict:
    try:
        return json.loads((root / "config/runtime_policy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500}


def _status(timestamp: datetime, now: datetime, policy: dict) -> str:
    age = max(0, int((now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()))
    fresh = int(policy.get("fresh_max_age_seconds", 900))
    degraded = int(policy.get("degraded_max_age_seconds", 1500))
    return "FRESH" if age <= fresh else ("DEGRADED" if age <= degraded else "STALE")


def _market_for_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if upper.endswith((".HK", ".HKG")):
        return "HK"
    if upper.endswith(".TW"):
        return "TW"
    if upper.endswith((".T", ".JP")):
        return "JP"
    if upper.endswith(".KS"):
        return "KR"
    return "US"


def _cn_code(symbol: str) -> bool:
    raw = symbol.upper().replace(".SH", "").replace(".SZ", "")
    return raw.isdigit() and len(raw) == 6


def _quote(symbol: str, market: str, price: dict, now: datetime, policy: dict, source: str) -> dict:
    ts = datetime.fromtimestamp(int(price["timestamp"]), timezone.utc)
    phase = market_phase(market, now=now)
    status = _status(ts, now, policy)
    nature = {
        "REGULAR": "实时交易行情" if status == "FRESH" else "交易中但数据源延迟的盘中行情",
        "PRE_MARKET": "盘前行情（附最近正式收盘基准）",
        "POST_MARKET": "盘后行情（附当日正式收盘）",
        "MIDDAY_BREAK": "午间休市期间的上午最新有效行情",
        "OPENING_AUCTION": "集合竞价时点行情",
        "OFF_SESSION": "最近正式收盘行情",
    }.get(phase, "最新有效行情")
    return {
        "market": market,
        "market_name": {"US": "美国", "HK": "中国香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国"}.get(market, market),
        "symbol": symbol,
        "name": symbol,
        "latest_price": price.get("close"),
        "open": price.get("open"),
        "high": price.get("high"),
        "low": price.get("low"),
        "prev_close": price.get("prev_close"),
        "volume": price.get("volume"),
        "amount": price.get("amount"),
        "turnover": price.get("amount"),
        "data_time_beijing": ts.astimezone(BEIJING).isoformat(timespec="seconds"),
        "data_time_local": ts.astimezone(ZoneInfo(MARKET_ZONES.get(market, "America/New_York"))).isoformat(timespec="seconds"),
        "market_phase": phase,
        "market_status_cn": display_market_status(market, phase),
        "data_nature_cn": nature,
        "source": source,
        "freshness": status,
        "quality_status": "PASS" if status == "FRESH" else ("DEGRADED" if status == "DEGRADED" else status),
        "direct_quote": True,
        "refresh_source": "QUERY_TIME_PROVIDER",
    }


def _yahoo(symbol: str, market: str, now: datetime, policy: dict) -> dict:
    payload = _request_json(symbol, period="1d", interval="5m", include_prepost=True)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    for index in range(len(timestamps) - 1, -1, -1):
        close = (quote.get("close") or [])[index] if index < len(quote.get("close") or []) else None
        if close is not None:
            return _quote(
                symbol,
                market,
                {
                    "timestamp": int(timestamps[index]),
                    "close": close,
                    "open": (quote.get("open") or [None])[index],
                    "high": (quote.get("high") or [None])[index],
                    "low": (quote.get("low") or [None])[index],
                    "volume": (quote.get("volume") or [None])[index],
                    "prev_close": result.get("meta", {}).get("previousClose") or result.get("meta", {}).get("chartPreviousClose"),
                    "amount": None,
                },
                now,
                policy,
                "yahoo_chart_api",
            )
    raise RuntimeError(f"Yahoo returned no valid bar for {symbol}")


def _formal_overseas_record(object_id: str, now: datetime) -> tuple[dict, dict]:
    generated_utc = now.astimezone(timezone.utc)
    spec = FORMAL_OVERSEAS_OBJECTS[object_id]

    if object_id in {"N225", "KOSPI", "TWII"}:
        secid = EASTMONEY_DIRECT_FALLBACKS[object_id]
        if object_id == "N225":
            chain = [
                (f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),
                (f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),
                ("yahoo_chart_api", lambda: fetch_formal_yahoo(object_id, spec, generated_utc)),
            ]
        elif object_id == "KOSPI":
            chain = [
                ("naver_finance:KOSPI", lambda: fetch_naver_kospi(generated_utc)),
                (f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),
                (f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),
                ("yahoo_chart_api", lambda: fetch_formal_yahoo(object_id, spec, generated_utc)),
            ]
        else:
            chain = [
                ("twse_mis:tse_t00.tw", lambda: fetch_twse_taiex(generated_utc)),
                (f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),
                (f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),
                ("yahoo_chart_api", lambda: fetch_formal_yahoo(object_id, spec, generated_utc)),
            ]
        errors = []
        for provider_id, loader in chain:
            try:
                record = loader()
                summary = provider_attempt(record, provider_id, generated_utc)
                if record.get("quality_status") == "PASS" and summary.get("stable_for_10m_pulse") is True:
                    record["selected_provider_id"] = provider_id
                    return record, summary
                errors.append(f"{provider_id}: freshness={summary.get('freshness_status')} quality={record.get('quality_status')}")
            except Exception as exc:
                errors.append(f"{provider_id}: {str(exc)[-240:]}")
        raise RuntimeError(f"{object_id} query-time direct chain has no fresh usable quote; " + " | ".join(errors))

    if object_id == "HSTECH":
        candidates = []
        errors = []
        chain = [
            ("eastmoney_push2:124.HSTECH", lambda: fetch_hstech_eastmoney(generated_utc)),
            ("yahoo_chart_api:HSTECH.HK", lambda: fetch_formal_yahoo(object_id, spec, generated_utc)),
            ("hithink-finance:HS2083", lambda: fetch_hstech_hithink(generated_utc)),
        ]
        for provider_id, loader in chain:
            try:
                record = loader()
                summary = provider_attempt(record, provider_id, generated_utc)
                candidates.append((record, summary))
            except Exception as exc:
                errors.append(f"{provider_id}: {str(exc)[-240:]}")
        selected = select_hstech_candidate(candidates)
        if selected:
            record, summary, _ = selected
            if record.get("quality_status") == "PASS" and summary.get("stable_for_10m_pulse") is True:
                record["selected_provider_id"] = summary.get("provider")
                return record, summary
        raise RuntimeError("HSTECH query-time direct chain has no fresh usable quote; " + " | ".join(errors))

    record = fetch_formal_yahoo(object_id, spec, generated_utc)
    summary = provider_attempt(record, "yahoo_chart_api", generated_utc)
    if record.get("market_phase_at_generation") == "OPEN" and summary.get("stable_for_10m_pulse") is not True:
        raise RuntimeError(f"{object_id} query-time quote is not fresh: {summary.get('freshness_status')}")
    record["selected_provider_id"] = "yahoo_chart_api"
    return record, summary


def _formal_overseas_quote(requested_symbol: str, object_id: str, now: datetime) -> dict:
    record, summary = _formal_overseas_record(object_id, now)
    latest = record.get("latest") or {}
    market = FORMAL_MARKETS[object_id]
    phase = market_phase(market, now=now)
    freshness = str(summary.get("freshness_status") or "MISSING")
    if phase in {"REGULAR", "OPENING_AUCTION", "PRE_MARKET", "POST_MARKET"} and freshness != "FRESH":
        raise RuntimeError(f"{object_id} active-market immediate refresh rejected non-FRESH provider result: {freshness}")
    return {
        "market": market,
        "market_name": {"US": "美国", "HK": "中国香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国"}[market],
        "symbol": requested_symbol,
        "canonical_object": object_id,
        "name": record.get("name") or requested_symbol,
        "latest_price": latest.get("close"),
        "open": latest.get("open"),
        "high": latest.get("high"),
        "low": latest.get("low"),
        "prev_close": latest.get("previous_close"),
        "volume": latest.get("volume"),
        "amount": latest.get("amount"),
        "turnover": latest.get("amount"),
        "data_time_beijing": latest.get("as_of_beijing"),
        "data_time_local": latest.get("as_of_local"),
        "market_phase": phase,
        "market_status_cn": display_market_status(market, phase),
        "data_nature_cn": "实时交易行情" if freshness == "FRESH" else "最近正式交易时段参考",
        "source": record.get("selected_provider_id") or record.get("provider"),
        "freshness": freshness,
        "quality_status": "PASS" if record.get("quality_status") == "PASS" and freshness == "FRESH" else record.get("quality_status", "DEGRADED"),
        "direct_quote": True,
        "refresh_source": "QUERY_TIME_FORMAL_PROVIDER_CHAIN",
        "provider_timestamp_field": record.get("provider_timestamp_field"),
        "refresh_contract": "ACTIVE_MARKET_REQUIRES_FRESH_PROVIDER_TIMESTAMP",
    }


def _cli_json(cli: str, args: list[str], root: Path) -> dict:
    out_dir = root / "data/market/raw/query_time"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="quote_", suffix=".json", dir=out_dir, delete=False) as handle:
        output = Path(handle.name)
    try:
        cp = subprocess.run([cli, *args, "--output", str(output), "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        if cp.returncode != 0 or not output.exists():
            raise RuntimeError((cp.stderr or cp.stdout or "CLI request failed")[-500:])
        obj = json.loads(output.read_text(encoding="utf-8"))
        if not obj.get("ok", True):
            raise RuntimeError(str(obj)[-500:])
        return obj
    finally:
        output.unlink(missing_ok=True)


def _eastmoney_etf(symbol: str, thscode: str, now: datetime, policy: dict) -> dict:
    code = thscode.split(".")[0]
    secid = f"{'1' if thscode.endswith('.SH') else '0'}.{code}"
    params = urllib.parse.urlencode({"secid": secid, "fltt": "2", "invt": "2", "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86"})
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + params
    request = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = (json.load(response).get("data") or {})
    if str(data.get("f57") or "") != code:
        raise RuntimeError(f"Eastmoney code mismatch for {thscode}: {data.get('f57')}")
    if any(data.get(key) in (None, "", "-") for key in ("f43", "f60", "f86")):
        raise RuntimeError(f"Eastmoney {thscode} missing direct ETF fields")
    raw_ts = float(data["f86"])
    timestamp_ms = int(raw_ts * 1000 if raw_ts < 10_000_000_000 else raw_ts)
    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
    status = _status(timestamp, now, policy)
    if status == "STALE":
        raise RuntimeError(f"Eastmoney {thscode} provider timestamp is stale: {timestamp.isoformat()}")
    volume = data.get("f47")
    amount = data.get("f48")
    open_price = float(data["f46"]) if data.get("f46") not in (None, "", "-") else None
    high_price = float(data["f44"]) if data.get("f44") not in (None, "", "-") else None
    low_price = float(data["f45"]) if data.get("f45") not in (None, "", "-") else None
    no_trade_partial = open_price is None and high_price is None and low_price is None and volume in (None, "", "-") and amount in (None, "", "-")
    return {
        "market": "CN",
        "market_name": "中国大陆",
        "symbol": symbol,
        "name": str(data.get("f58") or symbol),
        "latest_price": float(data["f43"]),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "prev_close": float(data["f60"]),
        "volume": int(float(volume) * 100) if volume not in (None, "", "-") else None,
        "amount": float(amount) if amount not in (None, "", "-") else None,
        "turnover": float(amount) if amount not in (None, "", "-") else None,
        "data_time_beijing": timestamp.astimezone(BEIJING).isoformat(timespec="seconds"),
        "data_time_local": timestamp.astimezone(BEIJING).isoformat(timespec="seconds"),
        "market_phase": market_phase("CN", now=now),
        "market_status_cn": display_market_status("CN", market_phase("CN", now=now)),
        "data_nature_cn": "交易中暂无新成交的最新有效价" if no_trade_partial else ("实时交易行情" if status == "FRESH" else "交易中但数据源延迟的盘中行情"),
        "source": "eastmoney_push2",
        "freshness": status,
        "quality_status": "DEGRADED" if no_trade_partial else ("PASS" if status == "FRESH" else status),
        "direct_quote": True,
        "refresh_source": "QUERY_TIME_PROVIDER",
    }


def _a_share(symbol: str, root: Path, now: datetime, policy: dict) -> dict:
    code = symbol.upper()
    raw_code = code.split(".")[0]
    thscode = code if "." in code else (f"{code}.SH" if raw_code.startswith(("5", "6")) else f"{code}.SZ")
    phase = market_phase("CN", now=now)
    tencent_error = None
    try:
        quote = fetch_tencent_quotes([thscode], timeout=10)[thscode.upper()]
        timestamp = datetime.fromtimestamp(int(quote["provider_timestamp_ms"]) / 1000, timezone.utc)
        status = _status(timestamp, now, policy)
        if status == "STALE":
            raise RuntimeError(f"Tencent {thscode} provider timestamp is stale: {timestamp.isoformat()}")
        no_trade_partial = any(quote.get(key) is None for key in ("open_price", "high_price", "low_price", "volume", "turnover"))
        return {
            "market": "CN",
            "market_name": "中国大陆",
            "symbol": code,
            "name": quote.get("name", code),
            "latest_price": quote["last_price"],
            "open": quote.get("open_price"),
            "high": quote.get("high_price"),
            "low": quote.get("low_price"),
            "prev_close": quote["prev_price"],
            "volume": quote.get("volume"),
            "amount": quote.get("turnover"),
            "turnover": quote.get("turnover"),
            "data_time_beijing": timestamp.astimezone(BEIJING).isoformat(timespec="seconds"),
            "data_time_local": timestamp.astimezone(BEIJING).isoformat(timespec="seconds"),
            "market_phase": phase,
            "market_status_cn": display_market_status("CN", phase),
            "data_nature_cn": "交易中暂无新成交的最新有效价" if no_trade_partial else ("实时交易行情" if status == "FRESH" else "交易中但数据源延迟的盘中行情"),
            "source": "tencent_qq",
            "freshness": status,
            "quality_status": "PASS" if status == "FRESH" else ("DEGRADED" if status == "DEGRADED" else status),
            "direct_quote": True,
            "refresh_source": "QUERY_TIME_PROVIDER",
            "provider_symbol": quote.get("provider_symbol"),
            "provider_timestamp_ms": quote["provider_timestamp_ms"],
        }
    except Exception as error:
        tencent_error = error

    cli = shutil.which("hithink-finance")
    if cli:
        commands = [["index", "snapshot", "--thscodes", thscode]] if raw_code in {"000001", "399006"} else [
            ["fund", "snapshot", "--thscode", thscode],
            ["market", "snapshot", "--thscodes", thscode],
        ]
        for command in commands:
            try:
                obj = _cli_json(cli, command, root)
                items = obj.get("data", {}).get("item") or []
                item = next((candidate for candidate in items if str(candidate.get("thscode") or "") == thscode), items[0] if items else {})
                close = item.get("last_price", item.get("close_price"))
                ts = obj.get("data", {}).get("timestamp")
                if close is None or ts in (None, ""):
                    continue
                dt = datetime.fromtimestamp(float(ts) / 1000, timezone.utc)
                status = _status(dt, now, policy)
                if status == "STALE":
                    continue
                return {
                    "market": "CN",
                    "market_name": "中国大陆",
                    "symbol": code,
                    "name": item.get("name", code),
                    "latest_price": close,
                    "open": item.get("open_price"),
                    "high": item.get("high_price"),
                    "low": item.get("low_price"),
                    "prev_close": item.get("pre_close"),
                    "volume": item.get("volume"),
                    "amount": item.get("amount"),
                    "turnover": item.get("amount"),
                    "data_time_beijing": dt.astimezone(BEIJING).isoformat(timespec="seconds"),
                    "data_time_local": dt.astimezone(BEIJING).isoformat(timespec="seconds"),
                    "market_phase": phase,
                    "market_status_cn": display_market_status("CN", phase),
                    "data_nature_cn": "实时交易行情" if status == "FRESH" else "交易中但数据源延迟的盘中行情",
                    "source": "hithink-finance",
                    "freshness": status,
                    "quality_status": "PASS" if status == "FRESH" else "DEGRADED",
                    "direct_quote": True,
                    "refresh_source": "QUERY_TIME_PROVIDER",
                }
            except Exception:
                continue
    if raw_code.startswith("159") or raw_code.startswith(("5", "588")):
        return _eastmoney_etf(code, thscode, now, policy)
    raise RuntimeError(f"Tencent primary failed for {thscode}: {tencent_error}; no valid A-share fallback")


def refresh_market_quotes(root: Path | str, requested_symbols: list[str], now: datetime) -> dict:
    root = Path(root)
    policy = _policy(root)
    symbols = [str(x).upper() for x in requested_symbols if str(x).strip()]
    if not symbols:
        symbols = list(DEFAULT_SYMBOLS)
        current_path = root / "data/state/CURRENT.json"
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
            snapshot = json.loads((root / str(current.get("latest_snapshot") or "")).read_text(encoding="utf-8"))
            symbols.extend(str(x.get("symbol")) for x in snapshot.get("rows", []) if x.get("symbol"))
        except (OSError, json.JSONDecodeError):
            pass
    deduped = list(dict.fromkeys(symbols))
    quotes, failures = [], []
    for symbol in deduped:
        canonical = FORMAL_OBJECT_ALIASES.get(symbol)
        market = "CN" if _cn_code(symbol) else (FORMAL_MARKETS.get(canonical) if canonical else DEFAULT_SYMBOLS.get(symbol, ("", _market_for_symbol(symbol)))[1])
        try:
            phase = market_phase(market, now=now)
            if phase not in {"REGULAR", "OPENING_AUCTION", "PRE_MARKET", "POST_MARKET"}:
                continue
            if market == "CN":
                quote = _a_share(symbol, root, now, policy)
            elif canonical:
                quote = _formal_overseas_quote(symbol, canonical, now)
            else:
                yahoo_symbol = DEFAULT_SYMBOLS.get(symbol, (symbol, market))[0]
                quote = _yahoo(yahoo_symbol, market, now, policy) | {"symbol": symbol}
            if phase in {"REGULAR", "OPENING_AUCTION"} and quote.get("freshness") != "FRESH":
                raise RuntimeError(f"active-market query-time refresh requires FRESH provider timestamp, got {quote.get('freshness')}")
            quotes.append(quote)
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)[-500:]})
    return {
        "quotes": quotes,
        "failures": failures,
        "requested_symbols": deduped,
        "refreshed_at_beijing": now.astimezone(BEIJING).isoformat(timespec="seconds"),
        "refresh_contract": "QUERY_TIME_FIRST_ACTIVE_MARKET_REQUIRES_FRESH_PROVIDER_TIMESTAMP",
    }
