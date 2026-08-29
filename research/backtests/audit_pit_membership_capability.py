#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

ETF_CODES = ["561980", "588000", "515880", "159326"]
YEARS = [2024, 2025, 2026]
HEADERS = {
    "User-Agent": "Mozilla/5.0 ETF-Trade-System research capability audit",
    "Referer": "https://fundf10.eastmoney.com/",
}
BASE = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"


def get(params: dict, attempts: int = 4) -> requests.Response:
    last = None
    for attempt in range(attempts):
        try:
            r = requests.get(BASE, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {type(last).__name__}: {last}")


def fetch_holdings(code: str, year: int) -> tuple[pd.DataFrame, dict]:
    params = {
        "type": "jjcc",
        "code": code,
        "topline": "200",
        "year": str(year),
        "month": "",
        "rt": "0.0",
    }
    r = get(params)
    text = r.text
    quarter_labels = re.findall(r"(20\d{2}年(?:1季度|2季度|3季度|4季度|中报|年报))", text)
    codes = re.findall(r">\s*(\d{6})\s*<", text)
    rows = [{"fund_code": code, "year": year, "holding_code": c} for c in codes]
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


def clean_text(raw: str) -> str:
    x = html.unescape(raw)
    x = re.sub(r"<script.*?</script>", " ", x, flags=re.I | re.S)
    x = re.sub(r"<style.*?</style>", " ", x, flags=re.I | re.S)
    x = re.sub(r"<[^>]+>", " ", x)
    x = x.replace("\\/", "/").replace("\\\"", '"')
    return re.sub(r"\s+", " ", x)


def fetch_announcements(code: str) -> tuple[list[dict], dict]:
    hits: list[dict] = []
    total_bytes = 0
    pages_ok = 0
    for page in range(1, 9):
        r = get({"type": "jjgg", "code": code, "page": str(page), "per": "100", "rt": "0.0"})
        pages_ok += 1
        total_bytes += len(r.content)
        plain = clean_text(r.text)
        # Capture report-related text around a visible disclosure date. This is a
        # capability audit: exact report-to-holdings mapping is validated later.
        for m in re.finditer(r"(20(?:24|25|26)[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", plain):
            left = max(0, m.start() - 180)
            right = min(len(plain), m.end() + 180)
            snippet = plain[left:right]
            if re.search(r"季度报告|中期报告|半年度报告|年度报告|基金报告", snippet):
                hits.append({"fund_code": code, "page": page, "date_token": m.group(1), "snippet": snippet[:380]})
        # Empty or repeated tail pages usually become very small; no need to hammer endpoint.
        if len(r.content) < 500:
            break
    # Deduplicate exact repeated snippets caused by pagination artifacts.
    uniq = []
    seen = set()
    for h in hits:
        key = (h["date_token"], h["snippet"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    meta = {
        "fund_code": code,
        "pages_ok": pages_ok,
        "response_bytes": total_bytes,
        "report_date_candidate_count": len(uniq),
        "endpoint": "fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjgg",
    }
    return uniq, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    all_rows = []
    audits = []
    announcement_rows = []
    announcement_meta = []
    errors = []

    for code in ETF_CODES:
        for year in YEARS:
            try:
                frame, meta = fetch_holdings(code, year)
                all_rows.append(frame)
                audits.append(meta)
                print(json.dumps(meta, ensure_ascii=False), flush=True)
            except Exception as exc:
                err = {"stage": "holdings", "fund_code": code, "year": year, "error": f"{type(exc).__name__}: {exc}"[:500]}
                errors.append(err)
                print(json.dumps(err, ensure_ascii=False), flush=True)

        try:
            rows, meta = fetch_announcements(code)
            announcement_rows.extend(rows)
            announcement_meta.append(meta)
            print(json.dumps(meta, ensure_ascii=False), flush=True)
        except Exception as exc:
            err = {"stage": "announcements", "fund_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]}
            errors.append(err)
            print(json.dumps(err, ensure_ascii=False), flush=True)

    holdings = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame(columns=["fund_code", "year", "holding_code"])
    holdings.to_csv(args.out / "historical_holdings_endpoint_probe.csv", index=False)
    pd.DataFrame(announcement_rows).to_csv(args.out / "report_disclosure_candidate_probe.csv", index=False)

    usable = {code: sum(1 for x in audits if x["fund_code"] == code and x["holding_code_count"] > 0) for code in ETF_CODES}
    announcement_usable = {code: sum(1 for x in announcement_rows if x["fund_code"] == code) for code in ETF_CODES}

    payload = {
        "mode": "RESEARCH_ONLY_PIT_MEMBERSHIP_CAPABILITY_AUDIT",
        "production_context_integration": False,
        "trade_signal": None,
        "scope": ETF_CODES,
        "years": YEARS,
        "holdings_endpoint_probe": audits,
        "announcement_endpoint_probe": announcement_meta,
        "errors": errors,
        "usable_year_counts": usable,
        "report_date_candidate_counts": announcement_usable,
        "membership_snapshot_capability": "AVAILABLE" if all(v >= 2 for v in usable.values()) else "PARTIAL_OR_UNAVAILABLE",
        "pit_disclosure_time_capability": "CANDIDATES_AVAILABLE_REQUIRES_MAPPING_VALIDATION" if all(v > 0 for v in announcement_usable.values()) else "PARTIAL_OR_UNAVAILABLE",
        "interpretation": "Historical disclosed holding snapshots and report-announcement date candidates are audited separately. A valid PIT series still requires exact report-period-to-publication-date mapping and must never use a snapshot before its publication date.",
    }
    (args.out / "pit_membership_capability.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
