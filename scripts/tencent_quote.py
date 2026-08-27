from __future__ import annotations

import re
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL_RE = re.compile(r'v_(sh|sz)([0-9]{6})="([^"]*)"')
# Shanghai index 000001.SH is a verified production Tencent symbol: sh000001.


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


def fetch_tencent_quotes(thscodes: list[str], timeout: int = 10) -> dict[str, dict]:
    """Fetch a batch of Shanghai/Shenzhen quotes from Tencent's public quote endpoint."""
    normalized = []
    for thscode in thscodes:
        code, suffix = str(thscode).upper().split(".", 1)
        if suffix not in {"SH", "SZ"} or not code.isdigit():
            raise RuntimeError(f"Tencent unsupported A-share code: {thscode}")
        normalized.append(("sh" if suffix == "SH" else "sz") + code)
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
        suspended = (
            len(fields) > 42
            and fields[42].strip().upper() == "S"
            and len(fields) > 32
            and bool(re.fullmatch(r"[0-9]{14}", fields[32] or ""))
        )
        timestamp_field = fields[32] if suspended else fields[30]
        timestamp_ms = _timestamp_ms(timestamp_field)
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
            # Tencent field 31 is the absolute price change; field 32 is the
            # percentage change. Compute the percentage from the verified last
            # and previous close so a provider semantic mismatch cannot enter
            # downstream contexts as a 100x scale error.
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
