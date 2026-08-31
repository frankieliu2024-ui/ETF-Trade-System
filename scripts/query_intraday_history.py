from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

try:
    from multi_source_market import YAHOO_SYMBOLS, _request_json
except ModuleNotFoundError:
    from scripts.multi_source_market import YAHOO_SYMBOLS, _request_json

BEIJING = ZoneInfo("Asia/Shanghai")
OBJECT_TIMEZONES = {
    "N225": "Asia/Tokyo",
    "KOSPI": "Asia/Seoul",
    "TWII": "Asia/Taipei",
    "HSTECH": "Asia/Hong_Kong",
    "NDX": "America/New_York",
    "SOX": "America/New_York",
}


def _parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def extract_intraday_rows(
    payload: dict[str, Any],
    *,
    timezone_name: str,
    market_date: str,
    start_beijing: str,
    end_beijing: str,
) -> list[dict[str, Any]]:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    market_zone = ZoneInfo(timezone_name)
    start_clock = _parse_clock(start_beijing)
    end_clock = _parse_clock(end_beijing)
    rows: list[dict[str, Any]] = []

    for idx, raw_ts in enumerate(timestamps):
        dt_utc = datetime.fromtimestamp(int(raw_ts), timezone.utc)
        dt_bj = dt_utc.astimezone(BEIJING)
        dt_local = dt_utc.astimezone(market_zone)
        if dt_bj.date().isoformat() != market_date:
            continue
        if not (start_clock <= dt_bj.time().replace(tzinfo=None) <= end_clock):
            continue
        row: dict[str, Any] = {
            "timestamp": int(raw_ts),
            "as_of_beijing": dt_bj.isoformat(timespec="seconds"),
            "as_of_local": dt_local.isoformat(timespec="seconds"),
        }
        complete = True
        for field in ("open", "high", "low", "close", "volume"):
            values = quote.get(field) or []
            value = values[idx] if idx < len(values) else None
            row[field] = value
            if field != "volume" and value is None:
                complete = False
        if complete:
            rows.append(row)
    return rows


def summarize_window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "MISSING", "rows": 0}
    first = rows[0]
    last = rows[-1]
    first_close = float(first["close"])
    last_close = float(last["close"])
    return {
        "status": "PASS",
        "rows": len(rows),
        "start_as_of_beijing": first["as_of_beijing"],
        "end_as_of_beijing": last["as_of_beijing"],
        "start_close": first_close,
        "end_close": last_close,
        "change_pct": ((last_close / first_close) - 1.0) * 100.0 if first_close else None,
        "window_high": max(float(x["high"]) for x in rows),
        "window_low": min(float(x["low"]) for x in rows),
    }


def query_object(object_id: str, market_date: str, start_beijing: str, end_beijing: str) -> dict[str, Any]:
    symbol = YAHOO_SYMBOLS.get(object_id)
    timezone_name = OBJECT_TIMEZONES.get(object_id)
    if not symbol or not timezone_name:
        raise ValueError(f"unsupported object: {object_id}")
    payload = _request_json(symbol, period="5d", interval="5m")
    rows = extract_intraday_rows(
        payload,
        timezone_name=timezone_name,
        market_date=market_date,
        start_beijing=start_beijing,
        end_beijing=end_beijing,
    )
    return {
        "object": object_id,
        "symbol": symbol,
        "provider": "yahoo_chart_api",
        "interval": "5m",
        "market_date": market_date,
        "window_beijing": {"start": start_beijing, "end": end_beijing},
        "purpose": "OBJECTIVE_HISTORICAL_INTRADAY_FACT_RECOVERY",
        "point_in_time_boundary": "可恢复客观历史价格路径；不得据此伪造当时系统已形成的正式判断。",
        "summary": summarize_window(rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Query objective historical intraday bars for cross-market path comparison.")
    parser.add_argument("--objects", default="N225,KOSPI")
    parser.add_argument("--date", required=True, dest="market_date")
    parser.add_argument("--start-beijing", default="11:30")
    parser.add_argument("--end-beijing", default="13:00")
    args = parser.parse_args()

    results = []
    for object_id in [x.strip().upper() for x in args.objects.split(",") if x.strip()]:
        try:
            results.append(query_object(object_id, args.market_date, args.start_beijing, args.end_beijing))
        except Exception as exc:  # noqa: BLE001 - preserve provider failure as data fact
            results.append({"object": object_id, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
