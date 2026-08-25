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
except ModuleNotFoundError:
    from scripts.market_quote_router import display_market_status, market_phase
    from scripts.multi_source_market import _request_json

BEIJING = ZoneInfo("Asia/Shanghai")
MARKET_ZONES = {"HK": "Asia/Hong_Kong", "TW": "Asia/Taipei", "JP": "Asia/Tokyo", "KR": "Asia/Seoul", "US": "America/New_York"}
DEFAULT_SYMBOLS = {
    "NDX": ("^NDX", "US"), "SOX": ("^SOX", "US"), "QQQ": ("QQQ", "US"), "SOXX": ("SOXX", "US"),
    "N225": ("^N225", "JP"), "KOSPI": ("^KS11", "KR"), "TWII": ("^TWII", "TW"),
    "HSTECH": ("HSTECH.HK", "HK"),
}


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
    if upper.endswith((".HK", ".HKG")): return "HK"
    if upper.endswith(".TW"): return "TW"
    if upper.endswith((".T", ".JP")): return "JP"
    if upper.endswith(".KS"): return "KR"
    return "US"


def _cn_code(symbol: str) -> bool:
    raw = symbol.upper().replace(".SH", "").replace(".SZ", "")
    return raw.isdigit() and len(raw) == 6


def _quote(symbol: str, market: str, price: dict, now: datetime, policy: dict, source: str) -> dict:
    ts = datetime.fromtimestamp(int(price["timestamp"]), timezone.utc)
    phase = market_phase(market, now=now)
    status = _status(ts, now, policy)
    nature = {
        "REGULAR": "实时交易行情", "PRE_MARKET": "盘前行情（附最近正式收盘基准）",
        "POST_MARKET": "盘后行情（附当日正式收盘）", "MIDDAY_BREAK": "午间休市期间的上午最新有效行情",
        "OPENING_AUCTION": "集合竞价时点行情", "OFF_SESSION": "最近正式收盘行情",
    }.get(phase, "最新有效行情")
    return {
        "market": market, "market_name": {"US": "美国", "HK": "中国香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国"}.get(market, market),
        "symbol": symbol, "name": symbol, "latest_price": price.get("close"),
        "data_time_beijing": ts.astimezone(BEIJING).isoformat(timespec="seconds"),
        "data_time_local": ts.astimezone(ZoneInfo(MARKET_ZONES.get(market, "America/New_York"))).isoformat(timespec="seconds"),
        "market_phase": phase, "market_status_cn": display_market_status(market, phase),
        "data_nature_cn": nature, "source": source, "freshness": status,
        "quality_status": "PASS" if status in {"FRESH", "DEGRADED"} else status,
        "direct_quote": True, "refresh_source": "QUERY_TIME_PROVIDER",
    }


def _yahoo(symbol: str, market: str, now: datetime, policy: dict) -> dict:
    payload = _request_json(symbol, period="1d", interval="5m", include_prepost=True)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    for index in range(len(timestamps) - 1, -1, -1):
        close = (quote.get("close") or [])[index] if index < len(quote.get("close") or []) else None
        if close is not None:
            return _quote(symbol, market, {"timestamp": int(timestamps[index]), "close": close}, now, policy, "yahoo_chart_api")
    raise RuntimeError(f"Yahoo returned no valid bar for {symbol}")


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
        if not obj.get("ok", True): raise RuntimeError(str(obj)[-500:])
        return obj
    finally:
        output.unlink(missing_ok=True)


def _a_share(symbol: str, root: Path, now: datetime, policy: dict) -> dict:
    cli = shutil.which("hithink-finance")
    if not cli: raise RuntimeError("hithink-finance CLI unavailable for query-time A-share refresh")
    code = symbol.upper()
    raw_code = code.split(".")[0]
    thscode = code if "." in code else (f"{code}.SH" if raw_code.startswith(("5", "6")) else f"{code}.SZ")
    if raw_code in {"000001", "399006"}:
        obj = _cli_json(cli, ["index", "snapshot", "--thscodes", thscode], root)
    elif raw_code.startswith("159") or raw_code.startswith(("5", "588")):
        obj = _cli_json(cli, ["fund", "snapshot", "--thscode", thscode], root)
    else:
        obj = _cli_json(cli, ["market", "snapshot", "--thscodes", thscode], root)
    items = obj.get("data", {}).get("item") or []
    item = items[0] if items else {}
    close = item.get("last_price", item.get("close_price"))
    ts = obj.get("data", {}).get("timestamp")
    if close is None or ts in (None, ""): raise RuntimeError(f"A-share provider returned incomplete quote for {thscode}")
    dt = datetime.fromtimestamp(float(ts) / 1000, timezone.utc)
    phase = market_phase("CN", now=now)
    age_status = _status(dt, now, policy)
    return {
        "market": "CN", "market_name": "中国大陆", "symbol": code, "name": item.get("name", code),
        "latest_price": close, "data_time_beijing": dt.astimezone(BEIJING).isoformat(timespec="seconds"),
        "data_time_local": dt.astimezone(BEIJING).isoformat(timespec="seconds"), "market_phase": phase,
        "market_status_cn": display_market_status("CN", phase),
        "data_nature_cn": "实时交易行情" if phase == "REGULAR" else "最近有效行情",
        "source": "hithink-finance", "freshness": age_status,
        "quality_status": "PASS" if age_status in {"FRESH", "DEGRADED"} else age_status,
        "direct_quote": True, "refresh_source": "QUERY_TIME_PROVIDER",
    }


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
        market = "CN" if _cn_code(symbol) else DEFAULT_SYMBOLS.get(symbol, ("", _market_for_symbol(symbol)))[1]
        try:
            if market == "CN":
                quotes.append(_a_share(symbol, root, now, policy))
            else:
                yahoo_symbol = DEFAULT_SYMBOLS.get(symbol, (symbol, market))[0]
                quotes.append(_yahoo(yahoo_symbol, market, now, policy) | {"symbol": symbol})
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)[-500:]})
    return {"quotes": quotes, "failures": failures, "requested_symbols": deduped, "refreshed_at_beijing": now.astimezone(BEIJING).isoformat(timespec="seconds")}
