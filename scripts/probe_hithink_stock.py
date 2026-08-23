from __future__ import annotations

"""Generic on-demand Hithink A-share quote and daily-bar probe."""

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_URL = "https://fuyao.aicubes.cn"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def to_thscode(code: str) -> str:
    value = code.strip().upper()
    if "." in value:
        return value
    if value.startswith(("60", "68", "51", "56", "58")):
        return f"{value}.SH"
    if value.startswith(("00", "30", "15")):
        return f"{value}.SZ"
    if value.startswith(("4", "8", "92")):
        return f"{value}.BJ"
    raise ValueError(f"cannot infer A-share exchange for {code}")


def request(path: str, params: dict[str, object]) -> dict:
    api_key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("HITHINK_FINANCE_API_KEY is not configured")
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "X-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "ETF-Trade-System/stock-probe-v1",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(
            f"hithink code={payload.get('code')}: {payload.get('message')}; "
            f"request_id={payload.get('request_id')}"
        )
    return payload


def day_window(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def historical(thscode: str, day: date, adjust: str) -> dict:
    start, end = day_window(day)
    payload = request(
        "/api/a-share/prices/historical",
        {
            "thscode": thscode,
            "interval": "1d",
            "start": start,
            "end": end,
            "adjust": adjust,
        },
    )
    items = payload.get("data", {}).get("item") or []
    target_ms = int(datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).timestamp() * 1000)
    exact = [item for item in items if item.get("date_ms") == target_ms]
    if len(exact) != 1:
        raise RuntimeError(f"expected exactly one bar for {day.isoformat()}, got {len(exact)}")
    bar = exact[0]
    return {
        "ok": True,
        "provider": "hithink-finance",
        "endpoint": "/api/a-share/prices/historical",
        "thscode": thscode,
        "date": day.isoformat(),
        "adjust": adjust,
        "open": bar.get("open_price"),
        "high": bar.get("high_price"),
        "low": bar.get("low_price"),
        "close": bar.get("close_price"),
        "volume": bar.get("volume"),
        "amount": bar.get("turnover"),
        "provider_timestamp_ms": payload.get("data", {}).get("timestamp"),
        "request_id": payload.get("request_id"),
    }


def snapshot(thscode: str) -> dict:
    payload = request("/api/a-share/prices/snapshot", {"thscodes": thscode})
    items = payload.get("data", {}).get("item") or []
    exact = [item for item in items if item.get("thscode") == thscode]
    if len(exact) != 1:
        raise RuntimeError(f"expected exactly one snapshot for {thscode}, got {len(exact)}")
    item = exact[0]
    return {
        "ok": True,
        "provider": "hithink-finance",
        "endpoint": "/api/a-share/prices/snapshot",
        "thscode": thscode,
        "open": item.get("open_price"),
        "high": item.get("high_price"),
        "low": item.get("low_price"),
        "last": item.get("last_price"),
        "prev_close": item.get("prev_price"),
        "change_pct": item.get("price_change_ratio_pct"),
        "volume": item.get("volume"),
        "amount": item.get("turnover"),
        "request_id": payload.get("request_id"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="On-demand Hithink A-share market-data probe")
    parser.add_argument("code", help="A-share ticker or thscode, e.g. 688825 or 688825.SH")
    parser.add_argument("--date", help="YYYY-MM-DD; when supplied, fetch one historical daily bar")
    parser.add_argument("--adjust", choices=["none", "forward", "backward"], default="none")
    parser.add_argument("--output", help="optional JSON output path")
    args = parser.parse_args()

    thscode = to_thscode(args.code)
    if args.date:
        result = historical(thscode, date.fromisoformat(args.date), args.adjust)
    else:
        result = snapshot(thscode)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
