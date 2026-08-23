from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
POOL_OUT = ROOT / "专项回测" / "outputs" / "v2214_pool_monitor_20260823"
ETF_OUT = POOL_OUT / "etf_history"
RAW = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823" / "raw"
AUDIT = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823"
SHANGHAI = ZoneInfo("Asia/Shanghai")

ETF_LIST = [
    ("515880", "通信ETF国泰", "515880.SH", "第二阶段优先候选"),
    ("159992", "创新药ETF银华", "159992.SZ", "第二阶段优先候选"),
    ("159326", "电网设备ETF华夏", "159326.SZ", "第二阶段优先候选"),
    ("518880", "黄金ETF华安", "518880.SH", "第二阶段优先候选"),
    ("512400", "有色金属ETF南方", "512400.SH", "第二阶段优先候选"),
    ("512880", "证券ETF国泰", "512880.SH", "第二阶段优先候选"),
    ("510880", "红利ETF华泰柏瑞", "510880.SH", "第二阶段次级候选"),
    ("159819", "人工智能ETF易方达", "159819.SZ", "科技工具对照"),
    ("562500", "机器人ETF华夏", "562500.SH", "科技工具对照"),
    ("512170", "医疗ETF华宝", "512170.SH", "医药工具对照"),
    ("159611", "电力ETF广发", "159611.SZ", "电力工具对照"),
    ("515790", "光伏ETF华泰柏瑞", "515790.SH", "新能源工具对照"),
    ("515220", "煤炭ETF国泰", "515220.SH", "资源工具对照"),
    ("159870", "化工ETF鹏华", "159870.SZ", "周期工具对照"),
    ("512660", "军工ETF国泰", "512660.SH", "制造工具对照"),
    ("159928", "消费ETF汇添富", "159928.SZ", "消费工具对照"),
    ("159915", "创业板ETF易方达", "159915.SZ", "宽基工具对照"),
]


def find_cli() -> str:
    found = shutil.which("hithink-finance")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "pnpm" / "hithink-finance.CMD"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("hithink-finance CLI is not on PATH and was not found in the user pnpm directory")


def api_date(series: pd.Series) -> pd.Series:
    return (pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
            .dt.tz_localize(None).dt.normalize())


def normalize(obj: dict) -> pd.DataFrame:
    if not obj.get("ok") or obj.get("meta", {}).get("source") != "remote":
        raise RuntimeError(f"Unsuccessful or non-remote response: {obj.get('meta')}")
    data = obj["data"]
    frame = pd.DataFrame(data.get("item") or [])
    if frame.empty:
        raise RuntimeError("Remote history returned no rows")
    frame["date"] = api_date(frame["date_ms"])
    frame = frame.rename(columns={"open_price": "open", "high_price": "high", "low_price": "low",
                                  "close_price": "close", "turnover": "amount"})
    frame = frame[["date", "open", "close", "high", "low", "volume", "amount"]]
    for c in ["open", "close", "high", "low", "volume", "amount"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    prior = frame["close"].shift(1)
    frame["amplitude_pct"] = (frame["high"] - frame["low"]) / prior * 100
    frame["change_pct"] = (frame["close"] / prior - 1) * 100
    frame["change"] = frame["close"] - prior
    frame["turnover_pct"] = pd.NA
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2021-08-23")
    parser.add_argument("--end", default="2026-08-21")
    args = parser.parse_args()
    start = pd.Timestamp(args.start, tz=SHANGHAI)
    end = pd.Timestamp(args.end, tz=SHANGHAI) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    if end - start > pd.Timedelta(days=365 * 5 + 2):
        raise ValueError("Requested interval exceeds the current five-year CLI contract")
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    cli = find_cli()
    ETF_OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    for code, name, thscode, role in ETF_LIST:
        raw_file = RAW / f"{code}_{thscode.split('.')[-1]}.json"
        completed = subprocess.run(
            [cli, "fund", "history", "--thscode", thscode, "--start-ms", str(start_ms),
             "--end-ms", str(end_ms), "--output", str(raw_file), "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{code} CLI failed: {completed.stderr[-500:]}")
        obj = json.loads(raw_file.read_text(encoding="utf-8"))
        frame = normalize(obj)
        if frame["date"].max() != pd.Timestamp(args.end):
            raise RuntimeError(f"{code} last date {frame['date'].max().date()} != expected {args.end}")
        if frame["date"].duplicated().any() or frame[["open", "high", "low", "close", "volume", "amount"]].isna().any().any():
            raise RuntimeError(f"{code} failed duplicate/missing validation")
        if ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) |
                (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).any():
            raise RuntimeError(f"{code} failed OHLC validation")
        target = ETF_OUT / f"{code}_{name}.csv"
        temp = target.with_suffix(".csv.tmp")
        frame.to_csv(temp, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
        temp.replace(target)
        rows.append({
            "kind": "ETF", "code": code, "name": name, "role": role,
            "source": "同花顺金融数据CLI", "source_url": "hithink-finance fund history",
            "method": "CLI JSON; interval=1d; Asia/Shanghai日期映射", "adjustment": obj["data"].get("adjust"),
            "rows": len(frame), "start_date": frame["date"].min().date().isoformat(),
            "end_date": frame["date"].max().date().isoformat(), "fields": ",".join(frame.columns),
            "duplicate_dates": 0, "missing_ohlc_cells": 0, "missing_volume": 0, "missing_amount": 0,
            "bad_ohlc_rows": 0, "nonpositive_close_rows": int((frame["close"] <= 0).sum()),
            "last_close": frame.iloc[-1]["close"], "last_amount": frame.iloc[-1]["amount"],
            "avg_amount_60d": frame.tail(60)["amount"].mean(),
            "latest_5_dates": ",".join(frame.tail(5)["date"].dt.strftime("%Y-%m-%d")),
            "file": str(target), "thscode": thscode, "asset_type": "fund-etf",
            "request_windows": 1, "status": "pass",
        })
    audit = pd.DataFrame(rows)
    audit.to_csv(AUDIT / "cli_refresh_manifest.csv", index=False, encoding="utf-8-sig")
    existing_path = POOL_OUT / "data_manifest.csv"
    if existing_path.exists():
        existing = pd.read_csv(existing_path)
        existing = existing[existing["kind"] != "ETF"]
        merged = pd.concat([audit, existing], ignore_index=True, sort=False)
    else:
        merged = audit
    merged.to_csv(existing_path, index=False, encoding="utf-8-sig")
    summary = {
        "run_at": datetime.now(SHANGHAI).isoformat(), "source": "hithink-finance CLI remote",
        "success": len(audit), "failure": 0, "start": args.start, "end": args.end,
        "total_rows": int(audit["rows"].sum()), "missing_amount": 0,
        "note": "CLI is the approved long-history path; REST truncation audit is recorded separately.",
    }
    (AUDIT / "cli_refresh_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
