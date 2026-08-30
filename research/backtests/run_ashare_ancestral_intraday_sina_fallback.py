#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "research" / "backtests" / "analyze_ashare_ancestral_intraday_history.py"

spec = importlib.util.spec_from_file_location("intraday_base", TARGET)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(base)


def sina_symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def fetch_sina_5m(code: str):
    symbol = sina_symbol(code)
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
    params = {"symbol": symbol, "scale": "5", "ma": "no", "datalen": "1023"}
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={
        "User-Agent": "Mozilla/5.0 ETF-Trade-System research-only",
        "Referer": "https://finance.sina.com.cn/",
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        text = resp.read().decode("utf-8", "replace").strip()
    # Supports either raw JSON array or JSONP-like prefix/suffix.
    m = re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if not m:
        raise RuntimeError("Sina response contains no JSON array")
    arr = json.loads(m.group(1))
    bars = []
    for x in arr:
        day = str(x.get("day") or "")
        if not day:
            continue
        dt = datetime.fromisoformat(day)
        bars.append({
            "dt": dt, "date": dt.date().isoformat(), "time": dt.strftime("%H:%M"),
            "open": base.f(x.get("open")), "close": base.f(x.get("close")),
            "high": base.f(x.get("high")), "low": base.f(x.get("low")),
            "volume": base.f(x.get("volume")), "amount": base.f(x.get("amount")),
        })
    if not bars:
        raise RuntimeError("Sina returned no usable 5m bars")
    return bars


def fetch_with_fallback(code: str):
    errors = []
    try:
        bars = base.fetch_5m(code)
        if bars:
            return bars
    except Exception as exc:
        errors.append("eastmoney=" + repr(exc))
    try:
        return fetch_sina_5m(code)
    except Exception as exc:
        errors.append("sina=" + repr(exc))
    raise RuntimeError("; ".join(errors))


base.fetch_5m = fetch_with_fallback
base.main()
