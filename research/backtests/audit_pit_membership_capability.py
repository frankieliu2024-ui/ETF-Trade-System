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
ARCHIVE_BASE = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
ANN_API = "https://api.fund.eastmoney.com/f10/JJGG"


def request(url: str, params: dict, attempts: int = 4) -> requests.Response:
    last = None
    for attempt in range(attempts):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {type(last).__name__}: {last}")


def fetch_holdings(code: str, year: int) -> tuple[pd.DataFrame, dict]:
    params = {"type": "jjcc", "code": code, "topline": "200", "year": str(year), "month": "", "rt": "0.0"}
    r = request(ARCHIVE_BASE, params)
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
    return re.sub(r"\s+", " ", x)


def flatten_json(obj, prefix="") -> list[tuple[str, str]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(flatten_json(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(flatten_json(v, f"{prefix}[{i}]"))
    elif obj is not None:
        out.append((prefix, str(obj)))
    return out


def fetch_announcements_api(code: str) -> tuple[list[dict], dict]:
    hits = []
    total_bytes = 0
    pages_ok = 0
    sample_keys = []
    for page in range(1, 8):
        r = request(ANN_API, {"fundcode": code, "pageIndex": str(page), "pageSize": "100", "type": "0"})
        pages_ok += 1
        total_bytes += len(r.content)
        try:
            obj = r.json()
        except Exception as exc:
            raise RuntimeError(f"announcement API non-JSON for {code}: {exc}; body={r.text[:120]}")
        flat = flatten_json(obj)
        if page == 1:
            sample_keys = sorted({k.split(".")[-1].split("[")[0] for k, _ in flat})[:80]
        values = [v for _, v in flat]
        joined = " | ".join(values)
        # Search the flattened response for report titles and nearby 2024-2026 dates.
        for m in re.finditer(r"(?:季度报告|中期报告|半年度报告|年度报告)", joined):
            snippet = joined[max(0, m.start()-220): min(len(joined), m.end()+220)]
            dates = re.findall(r"20(?:24|25|26)[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{2}:\d{2}:\d{2})?", snippet)
            if dates:
                hits.append({"fund_code": code, "page": page, "date_token": dates[0], "snippet": snippet[:420], "source": "api.fund.eastmoney.com/f10/JJGG"})
        # Detect empty result structures without assuming a fixed schema.
        if len(r.content) < 80 or not flat:
            break
    uniq = []
    seen = set()
    for h in hits:
        key = (h["date_token"], h["snippet"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq, {
        "fund_code": code,
        "pages_ok": pages_ok,
        "response_bytes": total_bytes,
        "report_date_candidate_count": len(uniq),
        "sample_json_keys": sample_keys,
        "endpoint": "api.fund.eastmoney.com/f10/JJGG",
    }


def fetch_announcements_legacy(code: str) -> tuple[list[dict], dict]:
    hits = []
    r = request(ARCHIVE_BASE, {"type": "jjgg", "code": code, "page": "1", "per": "100", "rt": "0.0"})
    plain = clean_text(r.text)
    for m in re.finditer(r"(20(?:24|25|26)[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", plain):
        snippet = plain[max(0, m.start()-180): min(len(plain), m.end()+180)]
        if re.search(r"季度报告|中期报告|半年度报告|年度报告|基金报告", snippet):
            hits.append({"fund_code": code, "page": 1, "date_token": m.group(1), "snippet": snippet[:380], "source": "legacy_jjgg"})
    return hits, {
        "fund_code": code,
        "pages_ok": 1,
        "response_bytes": len(r.content),
        "report_date_candidate_count": len(hits),
        "endpoint": "fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjgg",
    }


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

        api_ok = False
        try:
            rows, meta = fetch_announcements_api(code)
            announcement_rows.extend(rows)
            announcement_meta.append(meta)
            api_ok = bool(rows)
            print(json.dumps(meta, ensure_ascii=False), flush=True)
        except Exception as exc:
            errors.append({"stage": "announcements_api", "fund_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]})
        if not api_ok:
            try:
                rows, meta = fetch_announcements_legacy(code)
                announcement_rows.extend(rows)
                announcement_meta.append(meta)
                print(json.dumps(meta, ensure_ascii=False), flush=True)
            except Exception as exc:
                errors.append({"stage": "announcements_legacy", "fund_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]})

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
