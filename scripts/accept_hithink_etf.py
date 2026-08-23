from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from hithink_etf_data import HithinkETFClient


ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "历史成交EXCEL（截止2026-08-21）"
OUT = ROOT / "专项回测" / "outputs" / "hithink_acceptance_20260823"
RAW = OUT / "raw"
NORMALIZED = OUT / "normalized"
NORMALIZED.mkdir(parents=True, exist_ok=True)

ETF_LIST = [
    ("561980", "半导体设备ETF招商", "561980.SH", "09 "),
    ("588000", "科创50ETF华夏", "588000.SH", "10 "),
    ("159781", "科创创业ETF易方达", "159781.SZ", "11 "),
    ("159941", "纳指ETF广发", "159941.SZ", "12 "),
    ("159687", "亚太精选ETF南方", "159687.SZ", "13 "),
    ("159561", "德国ETF嘉实", "159561.SZ", "14 "),
    ("513520", "日经ETF华夏", "513520.SH", "15 "),
    ("513180", "恒生科技ETF华夏", "513180.SH", "16 "),
]


def clean_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace({"--": pd.NA, "-": pd.NA, "": pd.NA}), errors="coerce")


def load_excel(prefix: str) -> pd.DataFrame:
    matches = list(EXCEL.glob(f"{prefix}*.xlsx"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one workbook for prefix {prefix!r}, got {matches}")
    raw = pd.read_excel(matches[0])
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.iloc[:, 0].astype(str).str.slice(0, 10), errors="coerce"),
            "excel_open": clean_numeric(raw.iloc[:, 1]),
            "excel_high": clean_numeric(raw.iloc[:, 2]),
            "excel_low": clean_numeric(raw.iloc[:, 3]),
            "excel_close": clean_numeric(raw.iloc[:, 4]),
            "excel_volume": clean_numeric(raw.iloc[:, 7]) if raw.shape[1] > 7 else pd.NA,
            "excel_amount": clean_numeric(raw.iloc[:, 8]) if raw.shape[1] > 8 else pd.NA,
        }
    )
    return frame.dropna(subset=["date", "excel_close"]).drop_duplicates("date").sort_values("date")


def main() -> None:
    client = HithinkETFClient()
    report: list[dict] = []
    failures: list[dict] = []

    for ticker, expected_name, expected_thscode, excel_prefix in ETF_LIST:
        try:
            raw_dir = RAW / ticker
            symbol = client.search_etf(ticker, raw_dir)
            if symbol["thscode"] != expected_thscode:
                raise RuntimeError(f"thscode mismatch: {symbol['thscode']} != {expected_thscode}")
            snapshot = client.snapshot(expected_thscode, raw_dir)
            history, meta = client.history(
                expected_thscode,
                date(2026, 8, 13),
                date(2026, 8, 23),
                raw_dir,
            )
            history.to_csv(
                NORMALIZED / f"{ticker}_{expected_name}.csv",
                index=False,
                encoding="utf-8-sig",
                date_format="%Y-%m-%d",
            )
            excel = load_excel(excel_prefix)
            overlap = history.merge(excel, on="date", how="inner")
            for column in ["open", "high", "low", "close"]:
                overlap[f"{column}_abs_diff"] = (overlap[column] - overlap[f"excel_{column}"]).abs()
            price_max_abs_diff = float(
                overlap[[f"{column}_abs_diff" for column in ["open", "high", "low", "close"]]].max().max()
            ) if not overlap.empty else None
            volume_ratio = (overlap["volume"] / overlap["excel_volume"]).replace([float("inf"), -float("inf")], pd.NA)
            amount_ratio = (overlap["amount"] / overlap["excel_amount"]).replace([float("inf"), -float("inf")], pd.NA)
            bad_ohlc = int(
                (
                    (history["high"] < history[["open", "close", "low"]].max(axis=1))
                    | (history["low"] > history[["open", "close", "high"]].min(axis=1))
                ).sum()
            )
            report.append(
                {
                    "ticker": ticker,
                    "name": expected_name,
                    "resolved_name": symbol["name"],
                    "thscode": expected_thscode,
                    "asset_type": symbol["asset_type"],
                    "snapshot_last_price": snapshot["last_price"],
                    "snapshot_source_timestamp": snapshot["source_timestamp"],
                    "history_rows": len(history),
                    "history_start": history["date"].min().strftime("%Y-%m-%d"),
                    "history_end": history["date"].max().strftime("%Y-%m-%d"),
                    "overlap_rows": len(overlap),
                    "price_max_abs_diff": price_max_abs_diff,
                    "volume_ratio_median": float(volume_ratio.median()) if volume_ratio.notna().any() else None,
                    "amount_ratio_median": float(amount_ratio.median()) if amount_ratio.notna().any() else None,
                    "duplicate_dates": int(history["date"].duplicated().sum()),
                    "missing_ohlc_cells": int(history[["open", "high", "low", "close"]].isna().sum().sum()),
                    "bad_ohlc_rows": bad_ohlc,
                    "request_count": meta["windows"] + 2,
                    "status": "pass" if len(history) >= 5 and len(overlap) >= 5 and price_max_abs_diff == 0 and bad_ohlc == 0 else "review",
                }
            )
        except Exception as exc:
            failures.append({"ticker": ticker, "name": expected_name, "error": str(exc)})

    summary = {
        "source": "同花顺金融数据API",
        "contract": "ETF行情数据接口使用规范 V1.0",
        "tested_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "success_count": len(report),
        "failure_count": len(failures),
        "pass_count": sum(row["status"] == "pass" for row in report),
        "review_count": sum(row["status"] == "review" for row in report),
        "results": report,
        "failures": failures,
    }
    pd.DataFrame(report).to_csv(OUT / "acceptance_results.csv", index=False, encoding="utf-8-sig")
    (OUT / "acceptance_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "success_count": summary["success_count"],
                "failure_count": summary["failure_count"],
                "pass_count": summary["pass_count"],
                "review_count": summary["review_count"],
                "output": str(OUT),
            },
            ensure_ascii=False,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
