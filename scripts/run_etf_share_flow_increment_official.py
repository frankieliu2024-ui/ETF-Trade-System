from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_etf_share_flow_increment_poc as poc


def robust_sse_fetch(dates, wanted):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": poc.core.SSE_REFERER,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    session = requests.Session()
    session.headers.update(headers)
    try:
        session.get(poc.core.SSE_REFERER, timeout=20)
    except Exception:
        pass
    time.sleep(2.0)
    out = []
    for i, ts in enumerate(dates):
        d = pd.Timestamp(ts).strftime("%Y-%m-%d")
        params = {
            "isPagination": "true",
            "pageHelp.pageSize": "2000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": poc.core.SSE_SQL,
            "STAT_DATE": d,
            "_": str(int(time.time() * 1000)),
        }
        last = None
        for attempt in range(5):
            try:
                response = session.get(poc.core.SSE_URL, params=params, timeout=25)
                if response.status_code == 403:
                    last = RuntimeError("HTTP 403")
                    time.sleep(2.0 + attempt * 2.0 + random.random())
                    try:
                        session.get(poc.core.SSE_REFERER, timeout=20)
                    except Exception:
                        pass
                    continue
                response.raise_for_status()
                data = response.json()
                rows = data.get("result") or (data.get("pageHelp") or {}).get("data") or []
                for item in rows:
                    code = str(item.get("SEC_CODE") or "").strip()
                    if code not in wanted:
                        continue
                    value = str(item.get("TOT_VOL") or "").replace(",", "").strip()
                    if value:
                        out.append({"date": pd.Timestamp(ts), "code": code, "shares_10k": float(value), "source": "SSE"})
                last = None
                break
            except Exception as exc:
                last = exc
                time.sleep(1.0 + attempt * 1.5 + random.random())
        if last is not None:
            raise RuntimeError(f"SSE official fetch failed {d}: {last}")
        if i and i % 40 == 0:
            time.sleep(0.8 + random.random())
    return out


poc.core.fetch_sse_by_dates = robust_sse_fetch

if __name__ == "__main__":
    raise SystemExit(poc.main())
