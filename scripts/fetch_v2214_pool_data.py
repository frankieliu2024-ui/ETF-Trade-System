from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from hithink_etf_data import HithinkETFClient


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "专项回测" / "outputs" / "v2214_pool_monitor_20260823"
ETF_OUT = OUT / "etf_history"
INDEX_OUT = OUT / "index_history"
ETF_OUT.mkdir(parents=True, exist_ok=True)
INDEX_OUT.mkdir(parents=True, exist_ok=True)
HITHINK_RAW = OUT / "raw" / "hithink"

ETF_LIST = [
    ("515880", "通信ETF国泰", "1.515880", "第二阶段优先候选"),
    ("159992", "创新药ETF银华", "0.159992", "第二阶段优先候选"),
    ("159326", "电网设备ETF华夏", "0.159326", "第二阶段优先候选"),
    ("518880", "黄金ETF华安", "1.518880", "第二阶段优先候选"),
    ("512400", "有色金属ETF南方", "1.512400", "第二阶段优先候选"),
    ("512880", "证券ETF国泰", "1.512880", "第二阶段优先候选"),
    ("510880", "红利ETF华泰柏瑞", "1.510880", "第二阶段次级候选"),
    ("159819", "人工智能ETF易方达", "0.159819", "科技工具对照"),
    ("562500", "机器人ETF华夏", "1.562500", "科技工具对照"),
    ("512170", "医疗ETF华宝", "1.512170", "医药工具对照"),
    ("159611", "电力ETF广发", "0.159611", "电力工具对照"),
    ("515790", "光伏ETF华泰柏瑞", "1.515790", "新能源工具对照"),
    ("515220", "煤炭ETF国泰", "1.515220", "资源工具对照"),
    ("159870", "化工ETF鹏华", "0.159870", "周期工具对照"),
    ("512660", "军工ETF国泰", "1.512660", "制造工具对照"),
    ("159928", "消费ETF汇添富", "0.159928", "消费工具对照"),
    ("159915", "创业板ETF易方达", "0.159915", "宽基工具对照"),
]

INDEX_LIST = [
    ("931160", "中证全指通信设备指数", "2.931160", "通信ETF跟踪指数"),
    ("931152", "中证创新药产业指数", "2.931152", "创新药ETF跟踪指数"),
    ("931994", "中证电网设备主题指数", "2.931994", "电网设备ETF跟踪指数"),
    ("000819", "中证申万有色金属指数", "1.000819", "有色ETF跟踪指数"),
    ("399975", "中证全指证券公司指数", "0.399975", "证券ETF跟踪指数"),
    ("000015", "上证红利指数", "1.000015", "红利ETF跟踪指数"),
    ("H30590", "中证机器人指数", "2.H30590", "机器人ETF跟踪指数"),
]

YAHOO_LIST = [
    ("GC=F", "COMEX黄金期货", "黄金ETF/商品避险", "新增监测候选"),
    ("HG=F", "COMEX铜期货", "有色金属/全球制造周期", "新增监测候选"),
    ("^NBI", "纳斯达克生物科技指数", "创新药/全球生物科技风险偏好", "新增监测候选"),
    ("DX-Y.NYB", "美元指数", "黄金及全球流动性背景", "条件调用候选"),
]

FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
COLS = ["date", "open", "close", "high", "low", "volume", "amount", "amplitude_pct", "change_pct", "change", "turnover_pct"]


def get_json(url: str, attempts: int = 1) -> dict:
    err = None
    for attempt in range(attempts):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                "Referer": "https://quote.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # source instability is logged after bounded retries
            err = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}: {err}")


def audit_and_save(df: pd.DataFrame, code: str, name: str, role: str, kind: str,
                   source: str, source_url: str, method: str, adjustment: str) -> dict:
    for col in COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[COLS]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in COLS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    outfile = (ETF_OUT if kind == "ETF" else INDEX_OUT) / f"{code}_{name}.csv"
    df.to_csv(outfile, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")

    dup_dates = int(df["date"].duplicated().sum())
    missing_ohlc = int(df[["open", "high", "low", "close"]].isna().sum().sum())
    missing_volume = int(df["volume"].isna().sum())
    missing_amount = int(df["amount"].isna().sum())
    bad_ohlc = int(((df["high"] < df[["open", "close", "low"]].max(axis=1)) | (df["low"] > df[["open", "close", "high"]].min(axis=1))).sum())
    nonpositive_close = int((df["close"] <= 0).sum())
    recent = df.tail(min(60, len(df)))
    return {
        "kind": kind,
        "code": code,
        "name": name,
        "role": role,
        "source": source,
        "source_url": source_url,
        "method": method,
        "adjustment": adjustment,
        "rows": len(df),
        "start_date": df["date"].min().strftime("%Y-%m-%d"),
        "end_date": df["date"].max().strftime("%Y-%m-%d"),
        "fields": ",".join(COLS),
        "duplicate_dates": dup_dates,
        "missing_ohlc_cells": missing_ohlc,
        "missing_volume": missing_volume,
        "missing_amount": missing_amount,
        "bad_ohlc_rows": bad_ohlc,
        "nonpositive_close_rows": nonpositive_close,
        "last_close": float(df.iloc[-1]["close"]),
        "last_amount": float(df.iloc[-1]["amount"]) if pd.notna(df.iloc[-1]["amount"]) else None,
        "avg_amount_60d": float(recent["amount"].mean()) if recent["amount"].notna().any() else None,
        "latest_5_dates": ",".join(df.tail(5)["date"].dt.strftime("%Y-%m-%d").tolist()),
        "file": str(outfile),
    }


def fetch_eastmoney(code: str, name: str, secid: str, role: str, kind: str) -> dict:
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2={FIELDS2}"
        "&klt=101&fqt=0&beg=0&end=20260821&lmt=1000000"
    )
    obj = get_json(url)
    data = obj.get("data") or {}
    klines = data.get("klines") or []
    rows = [x.split(",") for x in klines]
    df = pd.DataFrame(rows, columns=COLS)
    if df.empty:
        raise RuntimeError(f"No history returned for {code} {name}")
    return audit_and_save(df, code, name, role, kind, "东方财富公开K线接口", url,
                          "HTTPS JSON, 日线klt=101", "不复权 fqt=0")


def fetch_sina(code: str, name: str, secid: str, role: str, kind: str) -> dict:
    market = "sh" if secid.startswith("1.") else "sz"
    symbol = market + code
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
        f"CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=1023"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    match = re.search(r"var _data=\((\[.*\])\)\s*;?", raw, re.S)
    if not match:
        raise RuntimeError(f"Cannot parse Sina response for {code}")
    records = json.loads(match.group(1))
    df = pd.DataFrame(records).rename(columns={"day": "date"})
    return audit_and_save(df, code, name, role, kind, "新浪财经公开K线接口（东方财富失败后的备用源）", url,
                          "HTTPS JSONP, 240分钟聚合为交易日", "接口未标示复权，按原始价格使用")


def fetch_hithink(client: HithinkETFClient, code: str, name: str, role: str) -> dict:
    symbol = client.search_etf(code, HITHINK_RAW / code)
    history, meta = client.history(
        symbol["thscode"],
        pd.Timestamp("2021-01-01").date(),
        pd.Timestamp("2026-08-21").date(),
        HITHINK_RAW / code,
    )
    if history.empty:
        raise RuntimeError(f"No hithink history returned for {code} {name}")
    row = audit_and_save(
        history,
        code,
        name,
        role,
        "ETF",
        "同花顺金融数据API",
        "https://fuyao.aicubes.cn/api/fund/market/historical",
        "HTTPS JSON, interval=1d, 最多5年窗口自动拆分",
        meta["adjustment"],
    )
    row["thscode"] = symbol["thscode"]
    row["asset_type"] = symbol["asset_type"]
    row["request_windows"] = meta["windows"]
    return row


def fetch_yahoo(symbol: str, name: str, serves: str, role: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded}?period1=0&period2=1787616000&interval=1d&events=history"
    )
    obj = get_json(url, attempts=3)
    result = obj["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    timestamps = result["timestamp"]
    df = pd.DataFrame({
        "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("America/New_York").date,
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
    })
    row = audit_and_save(df, symbol.replace("^", "IDX_").replace("=", "_"), name,
                         f"{role}；服务={serves}", "Commodity/OverseasIndex",
                         "Yahoo Finance Chart公开接口", url, "HTTPS JSON, 1d", "原始未复权OHLC")
    row["symbol"] = symbol
    return row


def main() -> None:
    manifest = []
    failures = []
    use_hithink = "--hithink-primary" in sys.argv
    hithink_client = HithinkETFClient() if use_hithink else None
    if "--yahoo-only" in sys.argv:
        for symbol, name, serves, role in YAHOO_LIST:
            try:
                manifest.append(fetch_yahoo(symbol, name, serves, role))
            except Exception as exc:
                failures.append({"kind": "Commodity/OverseasIndex", "code": symbol, "name": name, "error": str(exc)})
        pd.DataFrame(manifest).to_csv(OUT / "monitor_candidate_manifest.csv", index=False, encoding="utf-8-sig")
        (OUT / "monitor_candidate_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "monitor_fetch_failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"success={len(manifest)} failure={len(failures)}")
        return
    for kind, items in (("ETF", ETF_LIST), ("Index", INDEX_LIST)):
        for code, name, secid, role in items:
            try:
                try:
                    if kind == "ETF" and hithink_client is not None:
                        manifest.append(fetch_hithink(hithink_client, code, name, role))
                    else:
                        manifest.append(fetch_eastmoney(code, name, secid, role, kind))
                except Exception as primary_exc:
                    try:
                        row = fetch_eastmoney(code, name, secid, role, kind)
                    except Exception:
                        row = fetch_sina(code, name, secid, role, kind)
                    if kind == "ETF" and use_hithink:
                        row["hithink_primary_error"] = str(primary_exc)
                    manifest.append(row)
            except Exception as exc:
                failures.append({"kind": kind, "code": code, "name": name, "error": str(exc)})
            time.sleep(1.3)
    pd.DataFrame(manifest).to_csv(OUT / "data_manifest.csv", index=False, encoding="utf-8-sig")
    (OUT / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "fetch_failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"success={len(manifest)} failure={len(failures)}")
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
