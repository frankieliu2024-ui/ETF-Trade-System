#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd
import requests

ETF_CODES = ["561980", "588000", "515880", "159326"]
YEARS = [2024, 2025, 2026]
HEADERS = {
    "User-Agent": "Mozilla/5.0 ETF-Trade-System research capability audit",
    "Referer": "https://fundf10.eastmoney.com/",
}


def fetch_holdings(code: str, year: int) -> tuple[pd.DataFrame, dict]:
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {
        "type": "jjcc",
        "code": code,
        "topline": "200",
        "year": str(year),
        "month": "",
        "rt": "0.0",
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    text = r.text
    # Response is JS assigning HTML to apidata.content. Extract quarter headings and
    # six-digit A-share codes without assuming a stable HTML table layout.
    quarter_labels = re.findall(r"(20\d{2}年(?:1季度|2季度|3季度|4季度|中报|年报))", text)
    codes = re.findall(r">\s*(\d{6})\s*<", text)
    rows = []
    for c in codes:
        rows.append({"fund_code": code, "year": year, "holding_code": c})
    frame = pd.DataFrame(rows).drop_duplicates() if rows else pd.DataFrame(columns=["fund_code", "year", "holding_code"])
    meta = {
        "fund_code": code,
        "year": year,
        "http_status": r.status_code,
        "response_bytes": len(r.content),
        "quarter_labels": sorted(set(quarter_labels)),
        "holding_code_count": int(frame["holding_code"].nunique()) if len(frame) else 0,
        "endpoint": "fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc",
    }
    return frame, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    all_rows = []
    audits = []
    errors = []
    for code in ETF_CODES:
        for year in YEARS:
            try:
                frame, meta = fetch_holdings(code, year)
                all_rows.append(frame)
                audits.append(meta)
                print(json.dumps(meta, ensure_ascii=False), flush=True)
            except Exception as exc:
                err = {"fund_code": code, "year": year, "error": f"{type(exc).__name__}: {exc}"[:500]}
                errors.append(err)
                print(json.dumps(err, ensure_ascii=False), flush=True)

    holdings = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame(columns=["fund_code", "year", "holding_code"])
    holdings.to_csv(args.out / "historical_holdings_endpoint_probe.csv", index=False)

    usable = {
        code: sum(1 for x in audits if x["fund_code"] == code and x["holding_code_count"] > 0)
        for code in ETF_CODES
    }
    payload = {
        "mode": "RESEARCH_ONLY_PIT_MEMBERSHIP_CAPABILITY_AUDIT",
        "production_context_integration": False,
        "trade_signal": None,
        "scope": ETF_CODES,
        "years": YEARS,
        "endpoint_probe": audits,
        "errors": errors,
        "usable_year_counts": usable,
        "membership_snapshot_capability": "AVAILABLE" if all(v >= 2 for v in usable.values()) else "PARTIAL_OR_UNAVAILABLE",
        "pit_disclosure_time_capability": "NOT_YET_VALIDATED",
        "interpretation": "This probe only tests whether historical disclosed holding snapshots are retrievable. It does not establish when each snapshot became public and therefore is not yet a valid PIT membership series.",
    }
    (args.out / "pit_membership_capability.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
