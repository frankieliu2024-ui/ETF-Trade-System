#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
LABEL_RE = re.compile(r"(20\d{2})年([1-4])季度")


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


def quarter_segments(text: str, fund_code: str) -> pd.DataFrame:
    matches = list(LABEL_RE.finditer(text))
    candidates: dict[str, list[list[str]]] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end]
        codes = re.findall(r">\s*(\d{6})\s*<", segment)
        ordered = []
        seen = set()
        for code in codes:
            if code not in seen:
                seen.add(code)
                ordered.append(code)
        q = f"{m.group(1)}Q{m.group(2)}"
        if ordered:
            candidates.setdefault(q, []).append(ordered)

    rows = []
    for q, variants in sorted(candidates.items()):
        # The response can repeat a quarter label in navigation text. The real
        # holdings section is the variant with the most stock codes.
        chosen = max(variants, key=len)
        for rank, holding_code in enumerate(chosen[:10], start=1):
            rows.append({
                "fund_code": fund_code,
                "quarter": q,
                "rank": rank,
                "holding_code": holding_code,
            })
    return pd.DataFrame(rows, columns=["fund_code", "quarter", "rank", "holding_code"])


def fetch_holdings(code: str, year: int) -> tuple[pd.DataFrame, dict]:
    params = {"type": "jjcc", "code": code, "topline": "200", "year": str(year), "month": "", "rt": "0.0"}
    r = request(ARCHIVE_BASE, params)
    frame = quarter_segments(r.text, code)
    quarters = sorted(frame["quarter"].unique().tolist()) if len(frame) else []
    meta = {
        "fund_code": code,
        "year": year,
        "http_status": r.status_code,
        "response_bytes": len(r.content),
        "quarters": quarters,
        "quarter_count": len(quarters),
        "endpoint": "fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc",
    }
    return frame, meta


def iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_dicts(value)


def title_to_quarter(title: str) -> str | None:
    m = re.search(r"(20\d{2})年第([1-4])季度报告", title)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    # Semiannual/annual reports are useful cross-checks but quarter reports are
    # preferred whenever they exist. Map them only as fallback period labels.
    m = re.search(r"(20\d{2})年(?:中期|半年度)报告", title)
    if m:
        return f"{m.group(1)}Q2"
    m = re.search(r"(20\d{2})年年度报告", title)
    if m:
        return f"{m.group(1)}Q4"
    return None


def fetch_report_publications(code: str) -> tuple[pd.DataFrame, dict]:
    rows = []
    total_bytes = 0
    pages_ok = 0
    sample_keys = []
    for page in range(1, 8):
        r = request(ANN_API, {"fundcode": code, "pageIndex": str(page), "pageSize": "100", "type": "0"})
        pages_ok += 1
        total_bytes += len(r.content)
        obj = r.json()
        if page == 1:
            sample_keys = sorted({str(k) for d in iter_dicts(obj) for k in d.keys()})[:80]
        found_on_page = 0
        for d in iter_dicts(obj):
            title = str(d.get("TITLE") or d.get("ShortTitle") or "")
            publish = d.get("PUBLISHDATE") or d.get("PUBLISHDATEDesc")
            fund = str(d.get("FUNDCODE") or code)
            if fund != code or not title or not publish or "提示性公告" in title:
                continue
            q = title_to_quarter(title)
            if not q:
                continue
            rows.append({
                "fund_code": code,
                "quarter": q,
                "publish_date": pd.to_datetime(str(publish), errors="coerce"),
                "title": title,
                "announcement_id": d.get("ID"),
                "newcategory": d.get("NEWCATEGORY"),
            })
            found_on_page += 1
        if found_on_page == 0 and page >= 3 and len(r.content) < 300:
            break
    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.dropna(subset=["publish_date"])
        # Prefer the explicit quarter report; for duplicate annual/quarter period
        # mappings keep the earliest exact body publication visible to market.
        frame["is_exact_quarter"] = frame["title"].str.contains(r"第[1-4]季度报告", regex=True)
        frame = frame.sort_values(["quarter", "is_exact_quarter", "publish_date"], ascending=[True, False, True])
        frame = frame.drop_duplicates(["fund_code", "quarter"], keep="first")
    meta = {
        "fund_code": code,
        "pages_ok": pages_ok,
        "response_bytes": total_bytes,
        "mapped_report_count": int(len(frame)),
        "sample_json_keys": sample_keys,
        "endpoint": "api.fund.eastmoney.com/f10/JJGG",
    }
    return frame, meta


def topn_set(frame: pd.DataFrame, quarter: str, n: int) -> set[str]:
    x = frame[(frame["quarter"] == quarter) & (frame["rank"] <= n)]
    return set(x["holding_code"].astype(str))


def build_stability(holdings: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    summary = {}
    for code in ETF_CODES:
        h = holdings[holdings["fund_code"] == code]
        quarters = sorted(h["quarter"].unique().tolist())
        for prev_q, curr_q in zip(quarters, quarters[1:]):
            for n in (5, 10):
                a, b = topn_set(h, prev_q, n), topn_set(h, curr_q, n)
                if not a or not b:
                    continue
                inter = len(a & b)
                union = len(a | b)
                rows.append({
                    "fund_code": code,
                    "from_quarter": prev_q,
                    "to_quarter": curr_q,
                    "top_n": n,
                    "overlap_count": inter,
                    "overlap_rate_prev": inter / len(a),
                    "jaccard": inter / union if union else None,
                })
        x = pd.DataFrame([r for r in rows if r["fund_code"] == code and r["top_n"] == 5])
        summary[code] = {
            "quarters": quarters,
            "quarter_count": len(quarters),
            "top5_mean_overlap_rate": round(float(x["overlap_rate_prev"].mean()), 4) if len(x) else None,
            "top5_min_overlap_rate": round(float(x["overlap_rate_prev"].min()), 4) if len(x) else None,
            "top5_mean_jaccard": round(float(x["jaccard"].mean()), 4) if len(x) else None,
        }
    return pd.DataFrame(rows), summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    holding_parts = []
    holding_meta = []
    publication_parts = []
    publication_meta = []
    errors = []

    for code in ETF_CODES:
        for year in YEARS:
            try:
                frame, meta = fetch_holdings(code, year)
                holding_parts.append(frame)
                holding_meta.append(meta)
                print(json.dumps(meta, ensure_ascii=False), flush=True)
            except Exception as exc:
                errors.append({"stage": "holdings", "fund_code": code, "year": year, "error": f"{type(exc).__name__}: {exc}"[:500]})
        try:
            frame, meta = fetch_report_publications(code)
            publication_parts.append(frame)
            publication_meta.append(meta)
            print(json.dumps(meta, ensure_ascii=False), flush=True)
        except Exception as exc:
            errors.append({"stage": "publication", "fund_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]})

    holdings = pd.concat(holding_parts, ignore_index=True) if holding_parts else pd.DataFrame(columns=["fund_code", "quarter", "rank", "holding_code"])
    publications = pd.concat(publication_parts, ignore_index=True) if publication_parts else pd.DataFrame(columns=["fund_code", "quarter", "publish_date", "title"])
    stability, stability_summary = build_stability(holdings)

    mapping = holdings[["fund_code", "quarter"]].drop_duplicates().merge(
        publications[["fund_code", "quarter", "publish_date", "title"]],
        on=["fund_code", "quarter"], how="left",
    )
    mapping["pit_mapping_ready"] = mapping["publish_date"].notna()

    holdings.to_csv(args.out / "quarterly_top_holdings.csv", index=False)
    publications.to_csv(args.out / "quarter_report_publications.csv", index=False)
    mapping.to_csv(args.out / "quarter_to_publication_mapping.csv", index=False)
    stability.to_csv(args.out / "top_holding_stability.csv", index=False)

    ready_counts = mapping.groupby("fund_code")["pit_mapping_ready"].agg(["sum", "count"]).to_dict("index") if len(mapping) else {}
    payload = {
        "mode": "RESEARCH_ONLY_PIT_MEMBERSHIP_CAPABILITY_AND_STABILITY_AUDIT",
        "production_context_integration": False,
        "trade_signal": None,
        "scope": ETF_CODES,
        "years": YEARS,
        "holdings_endpoint_probe": holding_meta,
        "publication_endpoint_probe": publication_meta,
        "errors": errors,
        "pit_mapping_counts": ready_counts,
        "top_holding_stability": stability_summary,
        "membership_snapshot_capability": "AVAILABLE" if all(v.get("quarter_count", 0) >= 6 for v in stability_summary.values()) else "PARTIAL_OR_UNAVAILABLE",
        "pit_disclosure_time_capability": "AVAILABLE" if ready_counts and all(v["sum"] == v["count"] for v in ready_counts.values()) else "PARTIAL_OR_UNAVAILABLE",
        "interpretation": "Index ETFs are expected to have relatively stable holdings between index rebalances. This audit therefore quantifies top-5/top-10 quarter-to-quarter overlap before deciding whether full daily PIT membership reconstruction materially changes Stage1. Static-basket bias is treated as an empirical question, not assumed large. Any quarter snapshot may enter research only after its actual report publication date.",
    }
    (args.out / "pit_membership_capability.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
