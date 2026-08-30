#!/usr/bin/env python3
"""Build the PIT-safe ETF cross-sectional matrix for Issue #102.

Sources are limited to existing ETF daily research facts in this repository and
official SSE/SZSE ETF share-history endpoints. No QuantMind/QuantDB data is used.
The output is research-only and must not be written into production state.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/research/quantmind_qlib_wfa_stage1.json"
DAILY_DIR = ROOT / "events/research/daily_features"
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"

SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_REFERER = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REFERER = "https://www.szse.cn/market/fund/volume/etf/index.html"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_price_panel(start: str, end: str) -> pd.DataFrame:
    rows = []
    for path in sorted(DAILY_DIR.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        payload = load_json(path)
        if payload.get("quality_status") not in {None, "PASS"}:
            continue
        for x in payload.get("features") or []:
            rows.append({
                "date": pd.Timestamp(d),
                "code": str(x.get("code") or ""),
                "close": x.get("close"),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No ETF daily research facts in requested range")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"]).drop_duplicates(["date", "code"]).sort_values(["date", "code"])


def http_session(referer: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/javascript,*/*;q=0.01",
    })
    return s


def fetch_sse_by_dates(dates: list[pd.Timestamp], wanted: set[str]) -> list[dict]:
    s = http_session(SSE_REFERER)
    out = []
    for i, ts in enumerate(dates):
        params = {
            "isPagination": "true", "pageHelp.pageSize": "2000", "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
            "sqlId": SSE_SQL, "STAT_DATE": ts.strftime("%Y-%m-%d"),
        }
        last = None
        for attempt in range(3):
            try:
                r = s.get(SSE_URL, params=params, timeout=20)
                r.raise_for_status(); payload = r.json(); last = None
                rows = payload.get("result") or (payload.get("pageHelp") or {}).get("data") or []
                for x in rows:
                    code = str(x.get("SEC_CODE") or "").strip()
                    if code not in wanted:
                        continue
                    value = str(x.get("TOT_VOL") or "").replace(",", "").strip()
                    if value:
                        out.append({"date": ts, "code": code, "shares_10k": float(value), "share_source": "SSE"})
                break
            except Exception as exc:
                last = exc
                time.sleep(0.7 * (attempt + 1))
        if last is not None:
            raise RuntimeError(f"SSE share-history fetch failed for {ts.date()}: {last}")
        if i % 50 == 0:
            time.sleep(0.15)
    return out


def date_windows(start: pd.Timestamp, end: pd.Timestamp, span_days: int = 170):
    cur = start
    while cur <= end:
        stop = min(cur + pd.Timedelta(days=span_days - 1), end)
        yield cur, stop
        cur = stop + pd.Timedelta(days=1)


def fetch_szse_code(code: str, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    s = http_session(SZSE_REFERER)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    out = []
    for ws, we in date_windows(start, end):
        page = 1
        while True:
            params = {
                "SHOWTYPE": "JSON", "CATALOGID": "scsj_fund_jjgm", "TABKEY": "tab1", "jjlb": "ETF",
                "txtDm": code, "txtStart": ws.strftime("%Y-%m-%d"), "txtEnd": we.strftime("%Y-%m-%d"),
                "tab1PAGENO": str(page), "random": str(time.time()),
            }
            last = None
            for attempt in range(3):
                try:
                    r = s.get(SZSE_URL, params=params, timeout=20, verify=False)
                    r.raise_for_status(); payload = r.json(); last = None; break
                except Exception as exc:
                    last = exc; time.sleep(0.7 * (attempt + 1))
            if last is not None:
                raise RuntimeError(f"SZSE share-history fetch failed for {code} {ws.date()}..{we.date()}: {last}")
            block = payload[0] if isinstance(payload, list) and payload else {}
            if block.get("error"):
                raise RuntimeError(f"SZSE API error for {code}: {block.get('error')}")
            for x in block.get("data") or []:
                value = str(x.get("current_size") or "").replace(",", "").strip()
                d = str(x.get("size_date") or "").strip()
                if value and d:
                    out.append({"date": pd.Timestamp(d), "code": code, "shares_10k": float(value), "share_source": "SZSE"})
            pages = int((block.get("metadata") or {}).get("pagecount") or 1)
            if page >= pages:
                break
            page += 1; time.sleep(0.2)
        time.sleep(0.2)
    return out


def fetch_share_panel(price: pd.DataFrame, universe: dict) -> pd.DataFrame:
    meta = {str(x["code"]): x for x in universe.get("objects") or []}
    sh = {c for c, x in meta.items() if str(x.get("thscode", "")).endswith(".SH")}
    sz = {c for c, x in meta.items() if str(x.get("thscode", "")).endswith(".SZ")}
    dates = sorted(price["date"].drop_duplicates().tolist())
    rows = fetch_sse_by_dates(dates, sh)
    start, end = min(dates), max(dates)
    for code in sorted(sz):
        rows.extend(fetch_szse_code(code, start, end))
    shares = pd.DataFrame(rows)
    if shares.empty:
        raise RuntimeError("Official ETF share-history returned no usable rows")
    return shares.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"])


def pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(method="average", pct=True)


def build_matrix(price: pd.DataFrame, shares: pd.DataFrame, horizons: list[int], min_n: int) -> pd.DataFrame:
    panel = price.merge(shares, on=["date", "code"], how="left").sort_values(["code", "date"])
    parts = []
    for code, g in panel.groupby("code", sort=True):
        g = g.copy().sort_values("date")
        close = g["close"].astype(float)
        sh = g["shares_10k"].astype(float)
        for n in [1, 5, 10, 20]:
            g[f"ret_{n}d_pct"] = (close / close.shift(n) - 1.0) * 100.0
        g["vol_20d_pct"] = g["ret_1d_pct"].rolling(20, min_periods=20).std()
        for n in [1, 3, 5]:
            raw = (sh / sh.shift(n) - 1.0) * 100.0
            g[f"neg_share_change_{n}d_pct_lag1"] = -raw.shift(1)
        for h in horizons:
            g[f"forward_{h}d_pct"] = (close.shift(-h) / close - 1.0) * 100.0
            g[f"label_date_{h}d"] = g["date"].shift(-h)
        parts.append(g)
    x = pd.concat(parts, ignore_index=True)

    mapping = {
        "neg_share_change_1d_pct_lag1": "rank_neg_share_change_1d",
        "neg_share_change_3d_pct_lag1": "rank_neg_share_change_3d",
        "neg_share_change_5d_pct_lag1": "rank_neg_share_change_5d",
        "ret_1d_pct": "rank_ret_1d", "ret_5d_pct": "rank_ret_5d",
        "ret_10d_pct": "rank_ret_10d", "ret_20d_pct": "rank_ret_20d",
        "vol_20d_pct": "rank_vol_20d",
    }
    ranked = []
    for _, g in x.groupby("date", sort=True):
        g = g.copy()
        for raw, name in mapping.items():
            valid = g[raw].dropna()
            if len(valid) >= min_n and valid.nunique() >= 2:
                g.loc[valid.index, name] = pct_rank(valid)
        g["momentum_ensemble"] = (g["rank_ret_5d"] + g["rank_ret_20d"]) / 2.0
        g["share_momentum_ensemble"] = (
            g["rank_neg_share_change_5d"] + g["rank_ret_5d"] + g["rank_ret_20d"]
        ) / 3.0
        ranked.append(g)
    return pd.concat(ranked, ignore_index=True).sort_values(["date", "code"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", required=True)
    args = p.parse_args()
    cfg = load_json(Path(args.config))
    price = load_price_panel(cfg["data_start"], cfg["data_end"])
    shares = fetch_share_panel(price, load_json(UNIVERSE))
    matrix = build_matrix(price, shares, [int(x) for x in cfg["horizons_trading_days"]], int(cfg["validation"]["minimum_cross_section_size"]))
    keep = ["date", "code", *cfg["feature_columns"], *cfg["baseline_scores"]]
    for h in cfg["horizons_trading_days"]:
        keep.extend([f"forward_{h}d_pct", f"label_date_{h}d"])
    out = matrix[keep].copy()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    coverage = {
        "rows": int(len(out)), "dates": int(out["date"].nunique()), "codes": int(out["code"].nunique()),
        "date_min": str(out["date"].min().date()), "date_max": str(out["date"].max().date()),
        "complete_feature_rows": int(out[cfg["feature_columns"]].dropna().shape[0]),
    }
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
