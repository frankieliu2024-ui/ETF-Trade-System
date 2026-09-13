from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL_RE = re.compile(r'v_(sh|sz)([0-9]{6})="([^"]*)"')
# Shanghai index 000001.SH is a verified production Tencent symbol: sh000001.
_TENCENT_HISTORY_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _number(value: str):
    if value in {"", "-", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_ms(value: str) -> int:
    if not value:
        raise RuntimeError("Tencent quote missing provider timestamp")
    try:
        dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise RuntimeError(f"Tencent quote invalid provider timestamp: {value}") from exc
    return int(dt.timestamp() * 1000)


def _provider_symbol(thscode: str) -> str:
    code, suffix = str(thscode).upper().split(".", 1)
    if suffix not in {"SH", "SZ"} or not code.isdigit() or len(code) != 6:
        raise RuntimeError(f"Tencent unsupported A-share code: {thscode}")
    return ("sh" if suffix == "SH" else "sz") + code


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _decode_history_payload(raw: str) -> dict:
    text = raw.strip()
    if not text:
        raise RuntimeError("Tencent history returned an empty response")
    if not text.startswith("{") and "=" in text:
        text = text.split("=", 1)[1].strip().rstrip(";")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tencent history returned invalid JSON/JSONP") from exc
    if payload.get("code") not in {0, "0", None}:
        raise RuntimeError(f"Tencent history business failure: code={payload.get('code')}")
    return payload


def _history_page(
    *,
    provider_symbol: str,
    start_date: date,
    end_date: date,
    adjustment: str,
    count: int,
    timeout: int,
) -> list[list]:
    adjust = str(adjustment).lower().strip()
    if adjust not in {"none", "qfq"}:
        raise ValueError("Tencent history adjustment must be 'none' or 'qfq'")
    param = f"{provider_symbol},day,{start_date.isoformat()},{end_date.isoformat()},{count}"
    if adjust == "qfq":
        param += ",qfq"
    url = _TENCENT_HISTORY_ENDPOINT + "?" + urllib.parse.urlencode({"param": param})
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ETF-Trade-System/2.2.31",
            "Referer": "https://gu.qq.com/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if int(response.status) != 200:
            raise RuntimeError(f"Tencent history HTTP status {response.status}")
        raw = response.read().decode("utf-8", "replace")
    payload = _decode_history_payload(raw)
    stock = (payload.get("data") or {}).get(provider_symbol) or {}
    preferred_key = "qfqday" if adjust == "qfq" else "day"
    rows = stock.get(preferred_key)
    if rows is None:
        rows = stock.get("day") or stock.get("qfqday") or []
    if not isinstance(rows, list):
        raise RuntimeError(f"Tencent history malformed row collection for {provider_symbol}")
    return rows


def fetch_tencent_daily_history(
    thscode: str,
    start: date | datetime | str,
    end: date | datetime | str,
    *,
    adjustment: str = "none",
    timeout: int = 10,
    page_size: int = 500,
    window_days: int = 366,
) -> tuple[list[dict], dict]:
    """Read Tencent public daily K-line history without changing production state.

    This is a research/historical-data capability, not a production-provider
    priority change. The public Tencent endpoint is unofficial and unversioned,
    so callers must retain provider identity and validate returned coverage.
    Requests are split into bounded calendar windows so the endpoint's per-call
    bar cap cannot silently truncate a multi-year research interval.
    """
    if page_size < 1 or page_size > 500:
        raise ValueError("Tencent history page_size must be between 1 and 500")
    if window_days < 1 or window_days > 366:
        raise ValueError("Tencent history window_days must be between 1 and 366")
    start_date, end_date = _as_date(start), _as_date(end)
    if end_date < start_date:
        raise ValueError("end must not be earlier than start")
    provider_symbol = _provider_symbol(thscode)
    rows_by_date: dict[str, dict] = {}
    cursor_start = start_date
    pages = 0
    while cursor_start <= end_date:
        cursor_end = min(cursor_start + timedelta(days=window_days - 1), end_date)
        raw_rows = _history_page(
            provider_symbol=provider_symbol,
            start_date=cursor_start,
            end_date=cursor_end,
            adjustment=adjustment,
            count=page_size,
            timeout=timeout,
        )
        pages += 1
        for raw in raw_rows:
            if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                continue
            try:
                market_date = date.fromisoformat(str(raw[0]))
                opening = float(raw[1])
                close = float(raw[2])
                high = float(raw[3])
                low = float(raw[4])
                volume = float(raw[5])
            except (TypeError, ValueError):
                continue
            if market_date < cursor_start or market_date > cursor_end:
                continue
            if low > min(opening, close, high) or high < max(opening, close, low):
                continue
            rows_by_date[market_date.isoformat()] = {
                "date": market_date.isoformat(),
                "open": opening,
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": None,
                "provider": "tencent_qq_history",
                "provider_symbol": provider_symbol,
                "quality_status": "PASS",
            }
        cursor_start = cursor_end + timedelta(days=1)
    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    meta = {
        "provider": "tencent_qq_history",
        "endpoint": _TENCENT_HISTORY_ENDPOINT,
        "provider_symbol": provider_symbol,
        "interval": "1d",
        "adjustment": "前复权 qfq" if adjustment == "qfq" else "腾讯公开K线原始/未复权日线",
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "window_days": window_days,
        "pages": pages,
        "rows": len(rows),
        "retrieved_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "read_only": True,
        "production_provider_priority_unchanged": True,
    }
    return rows, meta


def fetch_tencent_quotes(thscodes: list[str], timeout: int = 10) -> dict[str, dict]:
    """Fetch a batch of Shanghai/Shenzhen quotes from Tencent's public quote endpoint."""
    normalized = [_provider_symbol(thscode) for thscode in thscodes]
    if not normalized:
        return {}
    url = "https://qt.gtimg.cn/q=" + urllib.parse.quote(",".join(normalized), safe=",")
    request = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16", "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if int(response.status) != 200:
            raise RuntimeError(f"Tencent HTTP status {response.status}")
        raw = response.read().decode("gbk", "replace")
    parsed = {f"{market.upper()}{code}": fields.split("~") for market, code, fields in _SYMBOL_RE.findall(raw)}
    result = {}
    for thscode, provider_symbol in zip(thscodes, normalized):
        fields = parsed.get(provider_symbol.upper()) or []
        if len(fields) < 38 or fields[2] != provider_symbol[2:]:
            raise RuntimeError(f"Tencent quote missing or mismatched row for {thscode}")
        suspended = len(fields) > 40 and fields[40].strip().upper() == "S"
        timestamp_ms = _timestamp_ms(fields[30])
        price, prev_close = _number(fields[3]), _number(fields[4])
        if price is None or prev_close is None or price < 0 or prev_close < 0:
            raise RuntimeError(f"Tencent quote missing price fields for {thscode}")
        result[str(thscode).upper()] = {
            "name": fields[1].strip(),
            "provider_symbol": provider_symbol,
            "open_price": None if suspended else _number(fields[5]),
            "high_price": None if suspended else _number(fields[33]),
            "low_price": None if suspended else _number(fields[34]),
            "last_price": price,
            "prev_price": prev_close,
            "provider_price_change_amount": _number(fields[31]),
            "provider_price_change_ratio_pct": _number(fields[32]),
            "price_change_ratio_pct": ((price / prev_close) - 1) * 100 if prev_close else None,
            "change_pct_source": "CALCULATED_FROM_LAST_PREV_CLOSE",
            "provider_field_31_semantics": "price_change_amount",
            "provider_field_32_semantics": "price_change_ratio_pct",
            "volume": 0.0 if suspended else ((_number(fields[6]) * 100) if _number(fields[6]) is not None else None),
            "turnover": 0.0 if suspended else ((_number(fields[37]) * 10000) if _number(fields[37]) is not None else None),
            "provider_timestamp_ms": timestamp_ms,
            "provider_status_code": "S" if suspended else "",
            "trading_status": "SUSPENDED" if suspended else "TRADING",
            "tradable": not suspended,
        }
    return result
