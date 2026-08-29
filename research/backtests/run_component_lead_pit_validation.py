#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_market_breadth_margin_stage1 as base
import audit_pit_membership_capability as membership

TARGETS = ["561980", "588000", "515880", "159326"]
CORE_SIGNALS = ["leader_equal_3d_lag1", "leader_breadth_pos_3d_lag1", "leader_minus_etf_3d_lag1"]
HEAD = {"User-Agent": "Mozilla/5.0 ETF-Trade-System research", "Referer": "https://gu.qq.com/"}


def r4(x):
    try:
        x = float(x)
    except Exception:
        return None
    return round(x, 4) if math.isfinite(x) else None


def symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def fetch_price(code: str, start: str, end: str) -> pd.DataFrame:
    sym = symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{sym},day,{start},{end},1400,qfq"}
    r = requests.get(url, params=params, headers=HEAD, timeout=25)
    r.raise_for_status()
    data = ((r.json().get("data") or {}).get(sym) or {})
    rows = data.get("qfqday") or data.get("day") or []
    if not rows:
        raise RuntimeError(f"Tencent kline empty for {code}")
    out = []
    for p in rows:
        if len(p) >= 3:
            out.append({"date": pd.Timestamp(p[0]), "open": pd.to_numeric(p[1], errors="coerce"), "close": pd.to_numeric(p[2], errors="coerce")})
    x = pd.DataFrame(out).dropna().drop_duplicates("date").sort_values("date")
    if len(x) < 60:
        raise RuntimeError(f"Tencent kline insufficient for {code}: {len(x)}")
    return x


def build_membership() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    holding_parts = []
    pub_parts = []
    meta = {"holdings": [], "publications": [], "errors": []}
    for code in TARGETS:
        for year in [2023, 2024, 2025, 2026]:
            try:
                f, m = membership.fetch_holdings(code, year)
                holding_parts.append(f)
                meta["holdings"].append(m)
            except Exception as exc:
                meta["errors"].append({"stage": "holdings", "code": code, "year": year, "error": f"{type(exc).__name__}: {exc}"[:300]})
        try:
            f, m = membership.fetch_report_publications(code)
            pub_parts.append(f)
            meta["publications"].append(m)
        except Exception as exc:
            meta["errors"].append({"stage": "publication", "code": code, "error": f"{type(exc).__name__}: {exc}"[:300]})
    h = pd.concat(holding_parts, ignore_index=True) if holding_parts else pd.DataFrame()
    p = pd.concat(pub_parts, ignore_index=True) if pub_parts else pd.DataFrame()
    if h.empty or p.empty:
        raise RuntimeError("PIT membership inputs unavailable")
    q = h.merge(p[["fund_code", "quarter", "publish_date", "title"]], on=["fund_code", "quarter"], how="left")
    q = q.dropna(subset=["publish_date"])
    return h, q, meta


def current_top5(holdings: pd.DataFrame, code: str) -> list[str]:
    g = holdings[(holdings.fund_code == code) & (holdings["rank"] <= 5)]
    if g.empty:
        return []
    q = sorted(g.quarter.unique())[-1]
    return g[g.quarter == q].sort_values("rank").holding_code.astype(str).tolist()


def pit_top5(mapping: pd.DataFrame, code: str, d: pd.Timestamp) -> tuple[list[str], str | None, pd.Timestamp | None]:
    g = mapping[(mapping.fund_code == code) & (mapping["rank"] <= 5) & (mapping.publish_date < d)]
    if g.empty:
        return [], None, None
    q = g.sort_values("publish_date").quarter.iloc[-1]
    z = g[g.quarter == q].sort_values("rank")
    return z.holding_code.astype(str).tolist(), q, pd.Timestamp(z.publish_date.iloc[0])


def aligned_close(prices: dict[str, pd.DataFrame], code: str, dates: pd.DatetimeIndex) -> pd.Series:
    return prices[code].set_index("date")["close"].reindex(dates).ffill(limit=5)


def build_signal_panel(etf_panel: pd.DataFrame, holdings: pd.DataFrame, mapping: pd.DataFrame, prices: dict[str, pd.DataFrame], mode: str) -> pd.DataFrame:
    rows = []
    for code in TARGETS:
        e = etf_panel[etf_panel.code == code].sort_values("date").copy()
        if e.empty:
            continue
        dates = pd.DatetimeIndex(e.date)
        stock_series = {c: aligned_close(prices, c, dates) for c in prices}
        static = current_top5(holdings, code)
        for i, d in enumerate(dates):
            if i < 21:
                continue
            if mode == "STATIC":
                leaders, q, pub = static, "CURRENT_STATIC", None
            else:
                leaders, q, pub = pit_top5(mapping, code, d)
            if len(leaders) < 4:
                continue
            r1, r3 = [], []
            for leader in leaders:
                s = stock_series.get(leader)
                if s is None:
                    continue
                vals = [s.iloc[i - 1], s.iloc[i - 2], s.iloc[i - 4]]
                if any(pd.isna(v) or v <= 0 for v in vals):
                    continue
                r1.append(float(vals[0] / vals[1] - 1) * 100)
                r3.append(float(vals[0] / vals[2] - 1) * 100)
            if len(r3) < 4:
                continue
            etf_close = e.close.to_numpy(float)
            etf3 = (etf_close[i - 1] / etf_close[i - 4] - 1) * 100
            rows.append({
                "date": d,
                "code": code,
                "membership_quarter": q,
                "membership_publish_date": pub,
                "leader_count": len(r3),
                "leader_equal_1d_lag1": float(np.mean(r1)),
                "leader_equal_3d_lag1": float(np.mean(r3)),
                "leader_breadth_pos_3d_lag1": float(np.mean(np.array(r3) > 0)),
                "leader_minus_etf_3d_lag1": float(np.mean(r3) - etf3),
            })
    return pd.DataFrame(rows)


def rank(s):
    return s.rank(method="average", pct=True)


def metric(df: pd.DataFrame, sig: str, label: str, controls: list[str], minobs: int = 50) -> dict:
    x = df[[sig, label] + controls].dropna()
    if len(x) < minobs:
        return {"n": int(len(x)), "ic": None}
    q = pd.DataFrame({c: rank(x[c]) for c in [sig, label] + controls}, index=x.index)
    C = np.column_stack([np.ones(len(q))] + [q[c].to_numpy() for c in controls])
    a = q[sig].to_numpy(); b = q[label].to_numpy()
    ba, *_ = np.linalg.lstsq(C, a, rcond=None)
    bb, *_ = np.linalg.lstsq(C, b, rcond=None)
    ra = a - C @ ba; rb = b - C @ bb
    ic = None if np.std(ra) <= 1e-12 or np.std(rb) <= 1e-12 else float(np.corrcoef(ra, rb)[0, 1])
    return {"n": int(len(x)), "ic": r4(ic)}


def summarize(panel: pd.DataFrame, mode: str) -> dict:
    controls = ["mom5_lag1", "mom20_lag1"]
    out = {}
    for code in TARGETS:
        e = panel[panel.code == code].sort_values("date")
        if e.empty:
            out[code] = {"status": "NO_PIT_COVERAGE", "signals": {}}
            continue
        mid = e.date.min() + (e.date.max() - e.date.min()) / 2
        sigs = {}
        for sig in CORE_SIGNALS:
            full = []
            early = []
            late = []
            annual = {}
            for h in (1, 3, 5):
                full.append(metric(e, sig, f"fwd_{h}d", controls))
                early.append(metric(e[e.date <= mid], sig, f"fwd_{h}d", controls, minobs=30))
                late.append(metric(e[e.date > mid], sig, f"fwd_{h}d", controls, minobs=30))
            for y in sorted(e.date.dt.year.unique()):
                vals = []
                for h in (1, 3, 5):
                    m = metric(e[e.date.dt.year == y], sig, f"fwd_{h}d", controls, minobs=30)
                    if m["ic"] is not None:
                        vals.append(m["ic"])
                annual[str(y)] = r4(np.median(vals)) if vals else None
            fv = [m["ic"] for m in full if m["ic"] is not None]
            ev = [m["ic"] for m in early if m["ic"] is not None]
            lv = [m["ic"] for m in late if m["ic"] is not None]
            median_ic = float(np.median(fv)) if fv else np.nan
            sign_consistency = float(np.mean(np.array(fv) > 0)) if fv else np.nan
            em = float(np.median(ev)) if ev else np.nan
            lm = float(np.median(lv)) if lv else np.nan
            positive_years = sum(v is not None and v > 0 for v in annual.values())
            available_years = sum(v is not None for v in annual.values())
            supported = bool(len(fv) == 3 and median_ic > 0.03 and sign_consistency >= 2 / 3 and em > 0 and lm > 0 and available_years >= 2 and positive_years >= 2)
            sigs[sig] = {
                "full_by_horizon": full,
                "median_partial_rank_ic": r4(median_ic),
                "sign_consistency": r4(sign_consistency),
                "early_median_ic": r4(em),
                "late_median_ic": r4(lm),
                "annual_median_ic": annual,
                "positive_years": positive_years,
                "available_years": available_years,
                "supported": supported,
            }
        out[code] = {
            "status": "READY",
            "start": e.date.min().date().isoformat(),
            "end": e.date.max().date().isoformat(),
            "rows": int(len(e)),
            "supported_signals": [s for s, v in sigs.items() if v["supported"]],
            "signals": sigs,
        }
    supported_objects = [c for c, v in out.items() if v.get("supported_signals")]
    if len(supported_objects) >= 2 and any(c in supported_objects for c in ["561980", "588000"]):
        grade = "PASS_PIT_COMPONENT_LEAD"
    elif supported_objects:
        grade = "PARTIAL_SUPPORT"
    else:
        grade = "NO_STABLE_INCREMENT"
    return {
        "selection_mode": mode,
        "controls": controls,
        "objects": out,
        "supported_objects": supported_objects,
        "research_interpretation": grade,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-08-28")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    holdings, mapping, membership_meta = build_membership()
    etf = base.local_panel(args.start, args.end, TARGETS)

    all_leaders = sorted(set(holdings.loc[holdings["rank"] <= 5, "holding_code"].astype(str)))
    prices = {}
    price_errors = []
    price_start = "2023-10-01"
    price_end = pd.Timestamp(args.end).strftime("%Y-%m-%d")
    for code in all_leaders:
        try:
            prices[code] = fetch_price(code, price_start, price_end)
        except Exception as exc:
            price_errors.append({"code": code, "error": f"{type(exc).__name__}: {exc}"[:300]})

    static_sig = build_signal_panel(etf, holdings, mapping, prices, "STATIC")
    pit_sig = build_signal_panel(etf, holdings, mapping, prices, "PIT")
    static_panel = etf.merge(static_sig, on=["date", "code"], how="inner")
    pit_panel = etf.merge(pit_sig, on=["date", "code"], how="inner")

    static_result = summarize(static_panel, "STATIC_CURRENT_TOP5")
    pit_result = summarize(pit_panel, "PIT_LAST_PUBLISHED_QUARTER_TOP5")

    payload = {
        "schema_version": "1.1",
        "mode": "RESEARCH_ONLY_COMPONENT_LEAD_PIT_MEMBERSHIP_VALIDATION",
        "objective": "Test whether component-lead increment survives replacing the fixed 2026Q2 top-five basket with the latest actually published quarterly top-five holdings available at each historical date.",
        "point_in_time": "For ETF trading date D, membership uses only a quarter report with publish_date < D; leader closes use no later than D-1; forward ETF return starts at D open.",
        "controls": ["mom5_lag1", "mom20_lag1"],
        "control_note": "Primary PIT-vs-static test intentionally matches original Stage1 controls. Margin is deferred to a secondary check only if PIT support survives, so the constituent-bias question is not blocked by an unrelated slow margin-history path.",
        "pre_registered_support_rule": "Per ETF/signal: median partial rank IC across 1/3/5d > 0.03, >=2/3 horizons positive, early and late medians positive, and >=2 positive annual medians. Overall PASS requires >=2 supported ETFs and at least one of 561980/588000; one supported ETF => PARTIAL; none => NO_STABLE_INCREMENT.",
        "membership_meta": membership_meta,
        "price_source": "Tencent IFZQ qfq daily kline",
        "price_errors": price_errors,
        "static": static_result,
        "pit": pit_result,
        "research_interpretation": pit_result["research_interpretation"],
        "decision_eligible": False,
        "production_context_integration": False,
        "trade_signal": None,
        "master_override": False,
    }
    (args.out / "component_lead_pit_validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    static_sig.to_csv(args.out / "component_lead_static_signal_panel.csv", index=False)
    pit_sig.to_csv(args.out / "component_lead_pit_signal_panel.csv", index=False)
    print(json.dumps({"research_interpretation": payload["research_interpretation"], "pit_supported_objects": pit_result["supported_objects"], "static_supported_objects": static_result["supported_objects"], "price_errors": len(price_errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
