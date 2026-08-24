"""Read-only public Yahoo chart adapter for overseas market monitoring.

This adapter only fetches and validates market facts. It never produces a
trade decision and never writes MASTER or account state.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


YAHOO_SYMBOLS = {
    "SOX": "^SOX",
    "SOXX": "SOXX",
    "SOXQ": "SOXQ",
    "SAMSUNG_005930.KS": "005930.KS",
    "SK_HYNIX_000660.KS": "000660.KS",
    "NDX": "^NDX",
    "SPX": "^GSPC",
    "VIX": "^VIX",
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "N225": "^N225",
    "TWII": "^TWII",
    "HSTECH": "^HSTECH",
    "GOLD": "GC=F",
    "DXY": "DX-Y.NYB",
}


def _request_json(
    symbol: str,
    period: str = "5y",
    interval: str = "1d",
    include_prepost: bool = False,
) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    prepost = "true" if include_prepost else "false"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?range={period}&interval={interval}&includePrePost={prepost}&events=history"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.15"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def normalize_chart(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    for index, timestamp in enumerate(timestamps):
        row = {"date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()}
        for field in ("open", "high", "low", "close", "volume"):
            values = quote.get(field) or []
            row[field] = values[index] if index < len(values) else None
        rows.append(row)
    quality = validate_rows(rows)
    return {
        "provider": "yahoo_chart_api",
        "symbol": symbol,
        "timezone": result.get("meta", {}).get("timezone", "unknown"),
        "adjusted": False,
        "rows": len(rows),
        "latest_date": next((row["date"] for row in reversed(rows) if all(row.get(field) is not None for field in ("open", "high", "low", "close", "volume"))), None),
        "quality": quality,
    }


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = [row["date"] for row in rows]
    valid = [row for row in rows if all(row.get(field) is not None for field in ("open", "high", "low", "close", "volume"))]
    bad_ohlc = sum(
        not (row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"])
        for row in valid
    )
    duplicate_dates = len(dates) - len(set(dates))
    return {
        "valid_rows": len(valid),
        "missing_required_rows": len(rows) - len(valid),
        "duplicate_dates": duplicate_dates,
        "bad_ohlc_rows": bad_ohlc,
        "pass": bool(valid and len(valid) == len(rows) and duplicate_dates == 0 and bad_ohlc == 0),
    }


def probe(symbols: dict[str, str] | None = None) -> list[dict[str, Any]]:
    results = []
    for object_id, symbol in (symbols or YAHOO_SYMBOLS).items():
        try:
            results.append({"object": object_id, "success": True, **normalize_chart(_request_json(symbol), symbol)})
        except Exception as error:  # noqa: BLE001 - preserve provider failure as audit fact
            results.append({"object": object_id, "provider": "yahoo_chart_api", "symbol": symbol, "success": False, "error": str(error)})
    return results


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
