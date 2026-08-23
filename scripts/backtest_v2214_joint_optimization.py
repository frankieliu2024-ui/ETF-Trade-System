from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "历史成交EXCEL（截止2026-08-21）"
STAGE1 = ROOT / "专项回测" / "outputs" / "v2214_pool_monitor_20260823"
OUT = ROOT / "专项回测" / "outputs" / "v2214_joint_20260823"
OUT.mkdir(parents=True, exist_ok=True)

CURRENT = ["561980", "588000", "159781", "159941", "159687", "159561", "513520", "513180"]
CANDIDATES = ["515880", "159992", "159326", "518880", "512400", "512880", "159928"]
TECH = ["561980", "588000", "159781", "515880"]
NAMES = {
    "561980": "半导体设备ETF", "588000": "科创50ETF", "159781": "科创创业ETF",
    "159941": "纳指ETF", "159687": "亚太精选ETF", "159561": "德国ETF",
    "513520": "日经ETF", "513180": "恒生科技ETF", "515880": "通信ETF",
    "159992": "创新药ETF", "159326": "电网设备ETF", "518880": "黄金ETF",
    "512400": "有色金属ETF", "512880": "证券ETF", "159928": "消费ETF",
}
COMMON_START = pd.Timestamp("2024-05-30")
END = pd.Timestamp("2026-08-21")


def excel_by_prefix(prefix: str) -> Path:
    matches = list(EXCEL.glob(f"{prefix}*.xlsx"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Excel for {prefix}, got {matches}")
    return matches[0]


ETF_EXCEL_PREFIX = {
    "561980": "09 ", "588000": "10 ", "159781": "11 ", "159941": "12 ",
    "159687": "13 ", "159561": "14 ", "513520": "15 ", "513180": "16 ",
}
INDEX_EXCEL_PREFIX = {
    "NDX": "01 ", "SOXQ": "02 ", "KS11": "03 ", "TWII": "04 ",
    "N225": "05 ", "HSTECH": "06 ", "SSE": "07 ", "CYB": "08 ",
}


def clean_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace({"--": np.nan, "-": np.nan, "": np.nan}), errors="coerce")


def load_excel_ohlcv(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path)
    if raw.shape[1] < 5:
        raise ValueError(f"Too few columns: {path}")
    df = pd.DataFrame({
        "date": pd.to_datetime(raw.iloc[:, 0].astype(str).str.slice(0, 10), errors="coerce"),
        "open": clean_numeric(raw.iloc[:, 1]),
        "high": clean_numeric(raw.iloc[:, 2]),
        "low": clean_numeric(raw.iloc[:, 3]),
        "close": clean_numeric(raw.iloc[:, 4]),
        "volume": clean_numeric(raw.iloc[:, 7]) if raw.shape[1] > 7 else np.nan,
        "amount": clean_numeric(raw.iloc[:, 8]) if raw.shape[1] > 8 else np.nan,
    })
    return df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def load_candidate(code: str) -> pd.DataFrame:
    matches = list((STAGE1 / "etf_history").glob(f"{code}_*.csv"))
    if len(matches) != 1:
        raise RuntimeError(f"Candidate file missing/ambiguous {code}: {matches}")
    df = pd.read_csv(matches[0])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def load_external_csv(pattern: str) -> pd.DataFrame:
    matches = list((STAGE1 / "index_history").glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"External file missing/ambiguous {pattern}: {matches}")
    df = pd.read_csv(matches[0])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def enrich(df: pd.DataFrame, code: str) -> pd.DataFrame:
    x = df.copy().sort_values("date").reset_index(drop=True)
    x["code"] = code
    x["ret1"] = x["close"].pct_change()
    x["ret3"] = x["close"].pct_change(3)
    x["ret5"] = x["close"].pct_change(5)
    x["ret10"] = x["close"].pct_change(10)
    x["ret20"] = x["close"].pct_change(20)
    x["ma20"] = x["close"].rolling(20).mean()
    x["vol20"] = x["volume"].rolling(20).mean()
    x["vol_ratio"] = x["volume"] / x["vol20"]
    rng = x["high"] - x["low"]
    x["clv"] = np.where(rng > 0, (x["close"] - x["low"]) / rng, np.nan)
    x["high20_close"] = x["close"].rolling(20).max()
    x["high60_close"] = x["close"].rolling(60).max()
    x["range10"] = x["high"].shift(1).rolling(10).max() / x["low"].shift(1).rolling(10).min() - 1
    x["prior5"] = x["close"].shift(1) / x["close"].shift(6) - 1
    x["p02"] = ((x["close"] >= x["high60_close"] - 1e-12) & (x["vol_ratio"] >= 1.0) & (x["clv"] >= 0.70) & (x["prior5"] <= 0.10))
    x["p06"] = ((x["close"].shift(1) < x["ma20"].shift(1)) & (x["close"] >= x["ma20"]) & (x["vol_ratio"] >= 1.0) & (x["clv"] >= 0.70))
    x["p09"] = ((x["range10"] <= 0.06) & (x["close"] >= x["high20_close"] - 1e-12) & (x["vol_ratio"] >= 1.2) & (x["clv"] >= 0.75))
    # P07-like technology proxy is research-only for 515880/159326; it does not extend MASTER scope.
    tech_like = code in {"561980", "588000", "159781", "515880", "159326"}
    x["p07_proxy"] = tech_like & (x["ret1"] >= 0.03) & (x["clv"] >= 0.75) & x["vol_ratio"].between(1.0, 1.8, inclusive="both") & (x["prior5"] <= 0.08)
    x["signal_raw"] = x[["p02", "p06", "p09", "p07_proxy"]].any(axis=1)
    return x


def dedup_signal_rows(x: pd.DataFrame, gap: int = 20) -> pd.DataFrame:
    idx = np.flatnonzero(x["signal_raw"].fillna(False).to_numpy())
    keep = []
    last = -10**9
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    return x.iloc[keep].copy()


def build_events(x: pd.DataFrame, code: str) -> pd.DataFrame:
    rows = []
    sig = dedup_signal_rows(x, 20)
    for i in sig.index:
        if i + 20 >= len(x):
            continue
        entry = float(x.at[i, "close"])
        rec = {
            "code": code, "name": NAMES.get(code, code), "date": x.at[i, "date"], "entry": entry,
            "signal_types": "+".join(k.upper() for k in ["p02", "p06", "p09", "p07_proxy"] if bool(x.at[i, k])),
            "vol_ratio": float(x.at[i, "vol_ratio"]) if pd.notna(x.at[i, "vol_ratio"]) else np.nan,
            "clv": float(x.at[i, "clv"]) if pd.notna(x.at[i, "clv"]) else np.nan,
            "ret20_at_signal": float(x.at[i, "ret20"]) if pd.notna(x.at[i, "ret20"]) else np.nan,
        }
        for h in [3, 5, 10, 20]:
            rec[f"t{h}"] = float(x.at[i + h, "close"] / entry - 1)
            path = x.iloc[i + 1:i + h + 1]
            rec[f"mfe{h}"] = float(path["high"].max() / entry - 1)
            rec[f"mae{h}"] = float(path["low"].min() / entry - 1)
        rec["trial_success_proxy"] = bool(rec["t3"] > 0 and rec["mfe3"] > abs(rec["mae3"]))
        rec["confirm_quality_proxy"] = bool(rec["trial_success_proxy"] and rec["t10"] > 0)
        occ = 20
        for j in range(i + 1, min(i + 21, len(x))):
            if pd.notna(x.at[j, "ma20"]) and x.at[j, "close"] < x.at[j, "ma20"]:
                occ = j - i
                break
        rec["occupation_days"] = occ
        if i + 1 < len(x):
            rec["wait1_price_change"] = float(x.at[i + 1, "close"] / entry - 1)
            rec["wait1_t10"] = float(x.at[min(i + 11, len(x)-1), "close"] / x.at[i + 1, "close"] - 1)
        rows.append(rec)
    return pd.DataFrame(rows)


def summarize_events(ev: pd.DataFrame, label: str) -> dict:
    if ev.empty:
        return {"label": label, "events": 0}
    gains = ev["t20"].clip(lower=0)
    cutoff = ev["t20"].quantile(0.9)
    right_tail = ev.loc[ev["t20"] >= cutoff, "t20"].clip(lower=0).sum()
    pos_sum = gains.sum()
    return {
        "label": label, "events": len(ev),
        "t5_mean": ev["t5"].mean(), "t5_median": ev["t5"].median(),
        "t10_mean": ev["t10"].mean(), "t10_median": ev["t10"].median(),
        "t20_mean": ev["t20"].mean(), "t20_median": ev["t20"].median(),
        "mfe20_mean": ev["mfe20"].mean(), "mae20_mean": ev["mae20"].mean(),
        "trial_success_rate": ev["trial_success_proxy"].mean(),
        "confirm_quality_rate": ev["confirm_quality_proxy"].mean(),
        "right_tail_share_positive": right_tail / pos_sum if pos_sum > 0 else np.nan,
        "occupation_days_mean": ev["occupation_days"].mean(),
        "occupation_days_total": ev["occupation_days"].sum(),
    }


def add_calendar_index(ev: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    x = ev.copy()
    loc = pd.Series(np.arange(len(calendar)), index=calendar)
    x["cal_idx"] = x["date"].map(loc)
    return x.dropna(subset=["cal_idx"]).assign(cal_idx=lambda d: d["cal_idx"].astype(int))


def cluster_pool(events: pd.DataFrame, codes: list[str], calendar: pd.DatetimeIndex, gap: int = 3) -> tuple[pd.DataFrame, dict]:
    ev = add_calendar_index(events[events["code"].isin(codes)].copy(), calendar).sort_values(["cal_idx", "code"])
    if ev.empty:
        return pd.DataFrame(), {"pool": "+".join(codes), "etf_count": len(codes), "events": 0}
    cluster_ids = []
    cid = 0
    last = None
    for i in ev["cal_idx"]:
        if last is None or i - last > gap:
            cid += 1
        cluster_ids.append(cid)
        last = i
    ev["cluster"] = cluster_ids
    agg_cols = [
        "t5", "t10", "t20", "mfe20", "mae20", "occupation_days",
        "trial_success_proxy", "confirm_quality_proxy",
    ]
    cl = ev.groupby("cluster").agg(
        date=("date", "min"), members=("code", lambda s: "+".join(sorted(set(s)))),
        event_count=("code", "size"), **{c: (c, "mean") for c in agg_cols}
    ).reset_index()
    s = summarize_events(cl.rename(columns={}), "+".join(codes))
    s.update({
        "pool": "+".join(codes), "etf_count": len(codes), "events": len(ev),
        "unique_opportunities": len(cl), "duplicate_events": len(ev) - len(cl),
        "duplicate_rate": (len(ev) - len(cl)) / len(ev),
        "reviews_per_unique": len(ev) / len(cl),
        "cluster_occupation_days_total": cl["occupation_days"].sum(),
    })
    return cl, s


def independent_candidate_events(candidate_ev: pd.DataFrame, base_ev: pd.DataFrame, calendar: pd.DatetimeIndex, gap: int = 3) -> pd.DataFrame:
    c = add_calendar_index(candidate_ev, calendar)
    b = add_calendar_index(base_ev, calendar)
    base_idx = b["cal_idx"].to_numpy()
    c["independent"] = c["cal_idx"].apply(lambda i: not np.any(np.abs(base_idx - i) <= gap))
    return c


def align_index_feature(event_dates: pd.Series, idx: pd.DataFrame, strict_prior: bool) -> pd.DataFrame:
    z = idx[["date", "close"]].dropna().sort_values("date").copy()
    z["idx_ret3"] = z["close"].pct_change(3)
    z["idx_ret5"] = z["close"].pct_change(5)
    e = pd.DataFrame({"event_date": pd.to_datetime(event_dates)}).sort_values("event_date")
    if strict_prior:
        z["available_date"] = z["date"] + pd.Timedelta(days=1)
        return pd.merge_asof(e, z[["available_date", "date", "idx_ret3", "idx_ret5"]].sort_values("available_date"), left_on="event_date", right_on="available_date", direction="backward").drop(columns=["available_date"])
    return pd.merge_asof(e, z[["date", "idx_ret3", "idx_ret5"]], left_on="event_date", right_on="date", direction="backward")


def monitor_pair_stats(ev: pd.DataFrame, idx: pd.DataFrame, target: str, monitor: str, strict_prior: bool, threshold: float = -0.02) -> dict:
    t = ev[ev["code"] == target].sort_values("date").copy()
    if t.empty:
        return {"target": target, "monitor": monitor, "events": 0}
    a = align_index_feature(t["date"], idx, strict_prior)
    t = t.reset_index(drop=True)
    t["idx_ret3"] = a["idx_ret3"]
    t["warning"] = t["idx_ret3"] <= threshold
    w = t[t["warning"]]
    nw = t[~t["warning"]]
    return {
        "target": target, "monitor": monitor, "strict_prior": strict_prior, "threshold": threshold,
        "events": len(t), "warnings": len(w), "warning_rate": len(w) / len(t),
        "warn_t10_mean": w["t10"].mean(), "nonwarn_t10_mean": nw["t10"].mean(),
        "warn_mae10_mean": w["mae10"].mean(), "nonwarn_mae10_mean": nw["mae10"].mean(),
        "warn_mfe10_mean": w["mfe10"].mean(),
        "false_warning_rate": ((w["t10"] > 0) | (w["mfe10"] > 0.03)).mean() if len(w) else np.nan,
        "true_warning_rate": ((w["t10"] < 0) & (w["mae10"] < -0.03)).mean() if len(w) else np.nan,
        "wait1_price_change_mean": w["wait1_price_change"].mean(),
        "wait1_t10_mean": w["wait1_t10"].mean(),
        "immediate_t10_mean_same": w["t10"].mean(),
    }


def scheme_delay_stats(ev: pd.DataFrame, target: str, monitors: list[tuple[str, pd.DataFrame, bool]], threshold: float = -0.02) -> dict:
    t = ev[ev["code"] == target].sort_values("date").reset_index(drop=True).copy()
    if t.empty:
        return {"target": target, "events": 0}
    warnings = np.zeros(len(t), dtype=bool)
    for _, idx, prior in monitors:
        a = align_index_feature(t["date"], idx, prior)
        warnings |= (a["idx_ret3"].to_numpy() <= threshold)
    w = t[warnings]
    return {
        "target": target, "events": len(t), "delayed": len(w), "delay_rate": len(w)/len(t),
        "immediate_t10": t["t10"].mean(),
        "scheme_t10": (t.loc[~warnings, "t10"].sum() + w["wait1_t10"].sum()) / len(t),
        "price_advantage_loss_on_delays": w["wait1_price_change"].mean(),
        "false_delay_rate": ((w["t10"] > 0) | (w["mfe10"] > 0.03)).mean() if len(w) else np.nan,
    }


def candidate_value_table(all_events: pd.DataFrame, etfs: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex) -> pd.DataFrame:
    base = all_events[all_events["code"].isin(CURRENT)]
    tech_events = all_events[all_events["code"].isin(["561980", "588000", "159781"])]
    rows = []
    for code in CANDIDATES:
        ev = all_events[all_events["code"] == code]
        indep = independent_candidate_events(ev, base, calendar)
        tech_indep = independent_candidate_events(ev, tech_events, calendar)
        tech_weak = 0
        for _, r in indep[indep["independent"]].iterrows():
            vals = []
            for tc in ["561980", "588000", "159781"]:
                d = etfs[tc]
                m = d[d["date"] <= r["date"]]
                if not m.empty:
                    vals.append(m.iloc[-1]["ret20"])
            if vals and np.nanmean(vals) <= 0:
                tech_weak += 1
        s = summarize_events(ev, code)
        s.update({
            "code": code, "name": NAMES[code],
            "independent_vs_current": int(indep["independent"].sum()) if not indep.empty else 0,
            "independent_rate": indep["independent"].mean() if not indep.empty else np.nan,
            "independent_vs_tech": int(tech_indep["independent"].sum()) if not tech_indep.empty else 0,
            "independent_when_tech_weak": tech_weak,
        })
        if not indep.empty and indep["independent"].any():
            ie = indep[indep["independent"]]
            s["independent_t10_mean"] = ie["t10"].mean()
            s["independent_t20_mean"] = ie["t20"].mean()
            s["independent_mae20_mean"] = ie["mae20"].mean()
        rows.append(s)
    return pd.DataFrame(rows)


def gold_analysis(gold: pd.DataFrame, ev: pd.DataFrame) -> dict:
    g = gold.reset_index(drop=True)
    ge = ev[ev["code"] == "518880"].copy()
    exits = []
    for _, r in ge.iterrows():
        i = int(g.index[g["date"] == r["date"]][0])
        exit_i = min(i + 20, len(g) - 1)
        for j in range(i + 1, min(i + 21, len(g))):
            if pd.notna(g.at[j, "ma20"]) and g.at[j, "close"] < g.at[j, "ma20"]:
                exit_i = j
                break
        path = g.iloc[i:exit_i + 1]
        peak = path["high"].max()
        exits.append({
            "date": r["date"], "exit_date": g.at[exit_i, "date"], "holding_days": exit_i-i,
            "swing_return": g.at[exit_i, "close"] / g.at[i, "close"] - 1,
            "peak_return": peak / g.at[i, "close"] - 1,
            "peak_giveback": g.at[exit_i, "close"] / peak - 1,
        })
    ex = pd.DataFrame(exits)
    # Ex-post large 20-day rises are only a missed-opportunity audit, never a signal definition.
    g["fwd20"] = g["close"].shift(-20) / g["close"] - 1
    large_idx = []
    last = -999
    for i in np.flatnonzero((g["fwd20"] >= 0.05).fillna(False).to_numpy()):
        if i - last >= 20:
            large_idx.append(i); last = i
    sig_idx = set(g.index[g["date"].isin(ge["date"])])
    captured = sum(any(j in sig_idx for j in range(max(0, i-5), i+1)) for i in large_idx)
    return {
        "events": len(ge), "independent_event_metrics": summarize_events(ge, "gold"),
        "swing_return_mean": ex["swing_return"].mean() if not ex.empty else np.nan,
        "swing_positive_rate": (ex["swing_return"] > 0).mean() if not ex.empty else np.nan,
        "holding_days_mean": ex["holding_days"].mean() if not ex.empty else np.nan,
        "peak_giveback_mean": ex["peak_giveback"].mean() if not ex.empty else np.nan,
        "large_20d_rises": len(large_idx), "captured_with_prior_5d_signal": captured,
        "capture_rate": captured/len(large_idx) if large_idx else np.nan,
        "buy_hold_total_return": g.iloc[-1]["close"]/g.iloc[0]["close"]-1,
    }, ex


def cross_pair_stats(a: pd.DataFrame, b: pd.DataFrame, eva: pd.DataFrame, evb: pd.DataFrame) -> dict:
    z = a[["date", "ret1"]].merge(b[["date", "ret1"]], on="date", suffixes=("_a", "_b")).dropna()
    common_down = ((z["ret1_a"] < 0) & (z["ret1_b"] < 0)).mean()
    common_up = ((z["ret1_a"] > 0) & (z["ret1_b"] > 0)).mean()
    cal = pd.DatetimeIndex(sorted(z["date"].unique()))
    ia = add_calendar_index(eva, cal)
    ib = add_calendar_index(evb, cal)
    overlap = sum(np.any(np.abs(ib["cal_idx"].to_numpy()-i)<=3) for i in ia["cal_idx"]) if len(ib) else 0
    return {
        "return_correlation": z["ret1_a"].corr(z["ret1_b"]),
        "common_up_probability": common_up, "common_down_probability": common_down,
        "a_events": len(ia), "b_events": len(ib), "a_event_overlap_with_b": overlap,
        "a_event_overlap_rate": overlap/len(ia) if len(ia) else np.nan,
    }


def migration_analysis(events: pd.DataFrame, etfs: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex) -> pd.DataFrame:
    base_ev = events[events["code"].isin(CURRENT)]
    rows = []
    for code in CANDIDATES:
        cev = independent_candidate_events(events[events["code"] == code], base_ev, calendar)
        for _, e in cev[cev["independent"]].iterrows():
            weak = []
            for old in CURRENT:
                d = etfs[old]
                m = d[d["date"] <= e["date"]]
                if m.empty: continue
                r = m.iloc[-1]
                if pd.notna(r["ma20"]) and pd.notna(r["ret20"]) and r["close"] < r["ma20"] and r["ret20"] < 0:
                    weak.append((old, float(r["ret20"])))
            if not weak: continue
            old = min(weak, key=lambda q: q[1])[0]
            od = etfs[old].reset_index(drop=True)
            om = od.index[od["date"] == e["date"]]
            if not len(om) or om[0]+20 >= len(od): continue
            oi = int(om[0])
            old_t20 = float(od.at[oi+20, "close"]/od.at[oi, "close"]-1)
            new_t20 = float(e["t20"])
            cost = 0.001
            rows.append({
                "date": e["date"], "new_code": code, "old_code": old,
                "relation": "tech_related" if code in {"515880", "159326"} and old in {"561980", "588000", "159781"} else "cross_factor",
                "hold_old_t20": old_t20,
                "release25_t20_net": 0.75*old_t20+0.25*new_t20-cost*0.25,
                "release50_t20_net": 0.50*old_t20+0.50*new_t20-cost*0.50,
                "switch100_t20_net": new_t20-cost,
                "new_t20": new_t20,
            })
    return pd.DataFrame(rows)


def main() -> None:
    audit = []
    etfs: dict[str, pd.DataFrame] = {}
    for code in CURRENT:
        raw = load_excel_ohlcv(excel_by_prefix(ETF_EXCEL_PREFIX[code]))
        etfs[code] = enrich(raw, code)
        audit.append({"kind":"ETF","code":code,"source":"formal_excel","rows":len(raw),"start":raw.date.min(),"end":raw.date.max(),"missing_ohlc":int(raw[["open","high","low","close"]].isna().sum().sum()),"duplicate_dates":int(raw.date.duplicated().sum()),"adjustment":"source workbook price; not independently relabelled"})
    for code in CANDIDATES:
        raw = load_candidate(code)
        etfs[code] = enrich(raw, code)
        audit.append({"kind":"ETF","code":code,"source":"stage1_public_csv","rows":len(raw),"start":raw.date.min(),"end":raw.date.max(),"missing_ohlc":int(raw[["open","high","low","close"]].isna().sum().sum()),"duplicate_dates":int(raw.date.duplicated().sum()),"adjustment":"public source did not label adjustment; treated as raw"})

    indexes: dict[str, pd.DataFrame] = {}
    for code, prefix in INDEX_EXCEL_PREFIX.items():
        indexes[code] = enrich(load_excel_ohlcv(excel_by_prefix(prefix)), code)
    indexes["NBI"] = enrich(load_external_csv("IDX_NBI_*.csv"), "NBI")
    indexes["GC"] = enrich(load_external_csv("GC_F_*.csv"), "GC")
    indexes["HG"] = enrich(load_external_csv("HG_F_*.csv"), "HG")

    # Common window prevents late-inception ETFs from receiving an easier market sample.
    filtered = {c: d[(d.date >= COMMON_START) & (d.date <= END)].reset_index(drop=True) for c,d in etfs.items()}
    all_events = pd.concat([build_events(d, c) for c,d in filtered.items()], ignore_index=True)
    calendar = pd.DatetimeIndex(sorted(indexes["SSE"].loc[(indexes["SSE"].date>=COMMON_START)&(indexes["SSE"].date<=END),"date"].unique()))

    pd.DataFrame(audit).to_csv(OUT/"data_audit.csv", index=False, encoding="utf-8-sig")
    all_events.to_csv(OUT/"all_opportunity_events.csv", index=False, encoding="utf-8-sig")
    summaries = pd.DataFrame([summarize_events(all_events[all_events.code==c], c) | {"code":c,"name":NAMES[c]} for c in CURRENT+CANDIDATES])
    summaries.to_csv(OUT/"etf_event_summary.csv", index=False, encoding="utf-8-sig")

    tech_schemes = {
        "A_561980_588000_159781": ["561980","588000","159781"],
        "B_561980_588000": ["561980","588000"],
        "C_561980_159781": ["561980","159781"],
        "D_561980_515880": ["561980","515880"],
        "E_561980_588000_515880": ["561980","588000","515880"],
        "F_561980_159781_515880": ["561980","159781","515880"],
    }
    tech_rows=[]
    for label,codes in tech_schemes.items():
        cl,s=cluster_pool(all_events,codes,calendar); s["scheme"]=label; tech_rows.append(s)
        cl.to_csv(OUT/f"tech_clusters_{label}.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(tech_rows).to_csv(OUT/"tech_scheme_summary.csv",index=False,encoding="utf-8-sig")

    cv = candidate_value_table(all_events, filtered, calendar)
    cv.to_csv(OUT/"candidate_incremental_value.csv",index=False,encoding="utf-8-sig")

    gold_summary,gold_exits = gold_analysis(filtered["518880"],all_events)
    gold_exits.to_csv(OUT/"gold_swing_exits.csv",index=False,encoding="utf-8-sig")
    (OUT/"gold_summary.json").write_text(json.dumps(gold_summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")

    cross = cross_pair_stats(filtered["515880"],filtered["159326"],all_events[all_events.code=="515880"],all_events[all_events.code=="159326"])
    (OUT/"communication_grid_cross.json").write_text(
        json.dumps(cross, ensure_ascii=False, indent=2,
                   default=lambda x: x.item() if hasattr(x, "item") else str(x)),
        encoding="utf-8",
    )

    monitor_pairs = [
        (t,m,p) for t in ["561980","588000","159781","515880","159326"] for m,p in [("NDX",True),("SOXQ",True),("KS11",False),("TWII",False),("CYB",False)]
    ] + [("159941","NDX",True),("159687","KS11",False),("159687","TWII",False),("159687","N225",False),("513520","N225",False),("513180","HSTECH",True),("159992","NBI",True),("518880","GC",True),("512400","HG",True),("512880","SSE",False),("159928","SSE",False)]
    mon=[]
    for threshold in [-0.01,-0.02,-0.03]:
        for t,m,p in monitor_pairs:
            mon.append(monitor_pair_stats(all_events,indexes[m],t,m,p,threshold))
    pd.DataFrame(mon).to_csv(OUT/"monitor_incremental_tests.csv",index=False,encoding="utf-8-sig")

    current_map={
        "561980":[("NDX",indexes["NDX"],True),("SOXQ",indexes["SOXQ"],True),("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False)],
        "588000":[("NDX",indexes["NDX"],True),("SOXQ",indexes["SOXQ"],True),("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False)],
        "159781":[("NDX",indexes["NDX"],True),("SOXQ",indexes["SOXQ"],True),("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False)],
        "515880":[("NDX",indexes["NDX"],True),("SOXQ",indexes["SOXQ"],True),("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False)],
        "159326":[("NDX",indexes["NDX"],True),("SOXQ",indexes["SOXQ"],True),("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False)],
        "159941":[("NDX",indexes["NDX"],True)], "159687":[("KS11",indexes["KS11"],False),("TWII",indexes["TWII"],False),("N225",indexes["N225"],False)],
        "513520":[("N225",indexes["N225"],False)], "513180":[("HSTECH",indexes["HSTECH"],True)],
    }
    optimized_map={
        "561980":[("SOXQ",indexes["SOXQ"],True)], "588000":[("CYB",indexes["CYB"],False)], "159781":[("CYB",indexes["CYB"],False)],
        "515880":[("SOXQ",indexes["SOXQ"],True)], "159326":[("CYB",indexes["CYB"],False)],
        "159941":[("NDX",indexes["NDX"],True)], "159687":[("N225",indexes["N225"],False)],
        "513520":[("N225",indexes["N225"],False)], "513180":[("HSTECH",indexes["HSTECH"],True)],
    }
    scheme=[]
    for t in current_map:
        a=scheme_delay_stats(all_events,t,[], -0.02); a["scheme"]="A_self_only"; scheme.append(a)
        b=scheme_delay_stats(all_events,t,current_map[t], -0.02); b["scheme"]="B_current_monitor_veto_stress"; scheme.append(b)
        c=scheme_delay_stats(all_events,t,optimized_map[t], -0.02); c["scheme"]="C_optimized_direct_monitor"; scheme.append(c)
    pd.DataFrame(scheme).to_csv(OUT/"monitor_scheme_counterfactual.csv",index=False,encoding="utf-8-sig")

    migration=migration_analysis(all_events,filtered,calendar)
    migration.to_csv(OUT/"capital_migration_events.csv",index=False,encoding="utf-8-sig")
    if not migration.empty:
        migration.groupby("relation").agg(n=("date","size"),hold_old=("hold_old_t20","mean"),release25=("release25_t20_net","mean"),release50=("release50_t20_net","mean"),switch100=("switch100_t20_net","mean"),new=("new_t20","mean")).reset_index().to_csv(OUT/"capital_migration_summary.csv",index=False,encoding="utf-8-sig")

    # Pool capacity scenarios are all pre-declared; no ex-post greedy ranking is used.
    scenarios={
        "current8":CURRENT,
        "current_minus_159781_plus_515880":[c for c in CURRENT if c!="159781"]+["515880"],
        "current_minus_588000_plus_515880":[c for c in CURRENT if c!="588000"]+["515880"],
        "current_minus_159687_plus_innov_gold":[c for c in CURRENT if c!="159687"]+["159992","518880"],
        "focused10":["561980","588000","515880","159941","513180","513520","159992","518880","512880","159928"],
        "expanded12":["561980","588000","515880","159941","513180","513520","159992","159326","518880","512400","512880","159928"],
    }
    prows=[]
    for label,codes in scenarios.items():
        _,s=cluster_pool(all_events,codes,calendar);s["scenario"]=label;prows.append(s)
    pd.DataFrame(prows).to_csv(OUT/"pool_capacity_scenarios.csv",index=False,encoding="utf-8-sig")

    summary={"common_start":str(COMMON_START.date()),"end":str(END.date()),"current":CURRENT,"candidates":CANDIDATES,"events":len(all_events),"hithink_online_verified":False,"hithink_reason":"CLI installed but API credential not configured"}
    (OUT/"run_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__":
    main()
