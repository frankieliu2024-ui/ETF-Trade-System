from __future__ import annotations

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://fuyao.aicubes.cn"
SHANGHAI = ZoneInfo("Asia/Shanghai")
OUT_DIR = ROOT / "research" / "base_stock_screen"


def to_thscode(code: str) -> str:
    code = code.strip()
    if code.startswith(("60", "68", "51", "56", "58")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "15")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    raise ValueError(f"cannot infer exchange for {code}")


def request(path: str, params: dict[str, object]) -> dict:
    key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("HITHINK_FINANCE_API_KEY is not configured")
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-api-key": key, "Accept": "application/json", "User-Agent": "ETF-Trade-System/base-stock-screen-v1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"hithink code={payload.get('code')}: {payload.get('message')}; request_id={payload.get('request_id')}")
    return payload


def fetch_history(code: str, start_date: str, end_date: str) -> tuple[pd.DataFrame, list[str]]:
    thscode = to_thscode(code)
    start = datetime.fromisoformat(start_date).replace(tzinfo=SHANGHAI)
    end = datetime.fromisoformat(end_date).replace(tzinfo=SHANGHAI) + timedelta(days=1) - timedelta(milliseconds=1)
    cursor = start
    rows: list[dict] = []
    req_ids: list[str] = []
    while cursor <= end:
        window_end = min(cursor + timedelta(days=364, hours=23, minutes=59, seconds=59), end)
        payload = request("/api/a-share/prices/historical", {
            "thscode": thscode,
            "interval": "1d",
            "start": int(cursor.timestamp() * 1000),
            "end": int(window_end.timestamp() * 1000),
            "adjust": "none",
        })
        rows.extend(payload.get("data", {}).get("item") or [])
        if payload.get("request_id"):
            req_ids.append(payload["request_id"])
        cursor = window_end + timedelta(milliseconds=1)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"no history returned for {code}")
    frame["date"] = pd.to_datetime(frame["date_ms"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    frame = frame.rename(columns={"open_price":"open","high_price":"high","low_price":"low","close_price":"close","turnover":"amount"})
    frame = frame[["date","open","high","low","close","volume","amount"]].sort_values("date").drop_duplicates("date")
    for c in ["open","high","low","close","volume","amount"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    if frame[["open","high","low","close","volume","amount"]].isna().any().any():
        raise RuntimeError(f"missing market fields for {code}")
    return frame.reset_index(drop=True), req_ids


def metrics(frame: pd.DataFrame) -> dict:
    close = frame["close"]
    ret = close.pct_change().dropna()
    wealth = close / close.iloc[0]
    drawdown = wealth / wealth.cummax() - 1.0
    downside = ret[ret < 0]
    return {
        "rows": int(len(frame)),
        "first_date": frame.iloc[0]["date"].date().isoformat(),
        "last_date": frame.iloc[-1]["date"].date().isoformat(),
        "first_close": float(close.iloc[0]),
        "last_close": float(close.iloc[-1]),
        "return_pct": float((close.iloc[-1] / close.iloc[0] - 1) * 100),
        "ann_vol_pct": float(ret.std(ddof=1) * math.sqrt(242) * 100),
        "downside_vol_pct": float(downside.std(ddof=1) * math.sqrt(242) * 100) if len(downside) > 1 else None,
        "max_drawdown_pct": float(drawdown.min() * 100),
        "worst_day_pct": float(ret.min() * 100),
        "positive_day_pct": float((ret > 0).mean() * 100),
        "avg_amount": float(frame["amount"].mean()),
        "median_amount": float(frame["amount"].median()),
    }


def rank_group(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    n = max(len(df) - 1, 1)
    # Base-stock objective: stability dominates; liquidity is a guardrail; return only breaks ties.
    vol_rank = df["ann_vol_pct"].rank(method="average", ascending=True)
    dd_rank = df["max_drawdown_pct"].abs().rank(method="average", ascending=True)
    liq_rank = df["avg_amount"].rank(method="average", ascending=False)
    ret_rank = df["return_pct"].rank(method="average", ascending=False)
    score = 100 * (0.45*(len(df)-vol_rank)/n + 0.35*(len(df)-dd_rank)/n + 0.15*(len(df)-liq_rank)/n + 0.05*(len(df)-ret_rank)/n)
    for i, value in enumerate(score):
        rows[i]["stability_score"] = round(float(value), 2)
    rows.sort(key=lambda x: x["stability_score"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["market_rank"] = i


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_file")
    args = parser.parse_args()
    request_path = ROOT / args.request_file
    req = json.loads(request_path.read_text(encoding="utf-8"))
    request_id = req["request_id"]
    start_date = req["start_date"]
    end_date = req["end_date"]
    records: list[dict] = []
    for item in req["stocks"]:
        code = str(item["code"])
        frame, request_ids = fetch_history(code, start_date, end_date)
        rec = {
            "code": code,
            "name": item.get("name", ""),
            "market": to_thscode(code).split(".")[-1],
            "role": item.get("role", "candidate"),
            "thscode": to_thscode(code),
            **metrics(frame),
            "provider_request_ids": request_ids,
        }
        records.append(rec)
    for market in sorted(set(r["market"] for r in records)):
        group = [r for r in records if r["market"] == market]
        rank_group(group)
        by_code = {r["code"]: r for r in group}
        for i, rec in enumerate(records):
            if rec["code"] in by_code:
                records[i] = by_code[rec["code"]]
    records.sort(key=lambda x: (x["market"], x.get("market_rank", 999)))
    output = {
        "ok": True,
        "request_id": request_id,
        "provider": "hithink-finance",
        "processed_at": datetime.now(SHANGHAI).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "ranking_method": "45% low annualized volatility + 35% low absolute max drawdown + 15% liquidity + 5% period return; ranked separately by exchange",
        "records": records,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{request_id}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(records).to_csv(OUT_DIR / f"{request_id}.csv", index=False, encoding="utf-8-sig")
    print(json.dumps({"ok": True, "request_id": request_id, "count": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
