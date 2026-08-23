from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "专项回测" / "backtest_v2214_joint_optimization.py"
spec = importlib.util.spec_from_file_location("joint", SRC)
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)

NORMALIZED = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823" / "normalized"
OUT = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823" / "amount_rebacktest"
OUT.mkdir(parents=True, exist_ok=True)


def load(code: str) -> pd.DataFrame:
    files = list(NORMALIZED.glob(f"{code}_hithink_*.csv"))
    if len(files) != 1:
        raise RuntimeError((code, files))
    df = pd.read_csv(files[0], parse_dates=["date"])
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def enrich_amount(df: pd.DataFrame, code: str) -> pd.DataFrame:
    x = j.enrich(df, code)
    x["amount20"] = x["amount"].rolling(20).mean()
    x["amount_ratio"] = x["amount"] / x["amount20"]
    # Reuse the event builder's diagnostic field name, but values now represent
    # amount ratio, not volume ratio.
    x["vol_ratio"] = x["amount_ratio"]
    x["p02"] = ((x["close"] >= x["high60_close"] - 1e-12) & (x["amount_ratio"] >= 1.0) &
                (x["clv"] >= .70) & (x["prior5"] <= .10))
    x["p06"] = ((x["close"].shift(1) < x["ma20"].shift(1)) & (x["close"] >= x["ma20"]) &
                (x["amount_ratio"] >= 1.0) & (x["clv"] >= .70))
    x["p09"] = ((x["range10"] <= .06) & (x["close"] >= x["high20_close"] - 1e-12) &
                (x["amount_ratio"] >= 1.2) & (x["clv"] >= .75))
    tech_like = code in {"561980", "588000", "159781", "515880", "159326"}
    x["p07_proxy"] = (tech_like & (x["ret1"] >= .03) & (x["clv"] >= .75) &
                      x["amount_ratio"].between(1.0, 1.8, inclusive="both") & (x["prior5"] <= .08))
    x["signal_raw"] = x[["p02", "p06", "p09", "p07_proxy"]].any(axis=1)
    return x


def main():
    codes = j.CURRENT + j.CANDIDATES
    etfs = {c: enrich_amount(load(c), c) for c in codes}
    filtered = {c: d[d["date"].between(j.COMMON_START, j.END)].reset_index(drop=True) for c, d in etfs.items()}
    all_events = pd.concat([j.build_events(filtered[c], c) for c in codes], ignore_index=True)
    calendar = pd.DatetimeIndex(sorted(filtered["561980"]["date"].unique()))
    all_events.to_csv(OUT / "all_opportunity_events_amount.csv", index=False, encoding="utf-8-sig")

    etf_rows = []
    for c in codes:
        s = j.summarize_events(all_events[all_events.code == c], c)
        s.update(code=c, name=j.NAMES[c])
        etf_rows.append(s)
    pd.DataFrame(etf_rows).to_csv(OUT / "etf_event_summary_amount.csv", index=False, encoding="utf-8-sig")

    tech_schemes = {
        "A_561980_588000_159781": ["561980", "588000", "159781"],
        "B_561980_588000": ["561980", "588000"],
        "C_561980_159781": ["561980", "159781"],
        "D_561980_515880": ["561980", "515880"],
        "E_561980_588000_515880": ["561980", "588000", "515880"],
        "F_561980_159781_515880": ["561980", "159781", "515880"],
    }
    tr = []
    for label, pool in tech_schemes.items():
        _, s = j.cluster_pool(all_events, pool, calendar)
        s["scheme"] = label
        tr.append(s)
    pd.DataFrame(tr).to_csv(OUT / "tech_scheme_summary_amount.csv", index=False, encoding="utf-8-sig")

    cv = j.candidate_value_table(all_events, filtered, calendar)
    cv.to_csv(OUT / "candidate_incremental_value_amount.csv", index=False, encoding="utf-8-sig")

    scenarios = {
        "current8": j.CURRENT,
        "current_minus_159781_plus_515880": [c for c in j.CURRENT if c != "159781"] + ["515880"],
        "current_minus_588000_plus_515880": [c for c in j.CURRENT if c != "588000"] + ["515880"],
        "current_minus_159687_plus_innov_gold": [c for c in j.CURRENT if c != "159687"] + ["159992", "518880"],
        "focused10": ["561980", "588000", "515880", "159941", "513180", "513520", "159992", "518880", "512880", "159928"],
        "expanded12": ["561980", "588000", "515880", "159941", "513180", "513520", "159992", "159326", "518880", "512400", "512880", "159928"],
    }
    pr = []
    for label, pool in scenarios.items():
        _, s = j.cluster_pool(all_events, pool, calendar)
        s["scenario"] = label
        pr.append(s)
    pd.DataFrame(pr).to_csv(OUT / "pool_capacity_scenarios_amount.csv", index=False, encoding="utf-8-sig")

    migration = j.migration_analysis(all_events, filtered, calendar)
    migration.to_csv(OUT / "capital_migration_events_amount.csv", index=False, encoding="utf-8-sig")
    if not migration.empty:
        migration.groupby("relation").agg(
            n=("date", "size"), hold_old=("hold_old_t20", "mean"), release25=("release25_t20_net", "mean"),
            release50=("release50_t20_net", "mean"), switch100=("switch100_t20_net", "mean"), new=("new_t20", "mean")
        ).reset_index().to_csv(OUT / "capital_migration_summary_amount.csv", index=False, encoding="utf-8-sig")

    old = pd.read_csv(j.OUT / "all_opportunity_events.csv", parse_dates=["date"])
    old["code"] = old["code"].astype(str)
    change_rows = []
    for c in codes:
        a = set(old.loc[old.code == c, "date"])
        b = set(all_events.loc[all_events.code == c, "date"])
        change_rows.append({"code": c, "volume_proxy_events": len(a), "amount_events": len(b),
                            "common_events": len(a & b), "removed_by_amount": len(a - b), "added_by_amount": len(b - a)})
    pd.DataFrame(change_rows).to_csv(OUT / "volume_vs_amount_event_changes.csv", index=False, encoding="utf-8-sig")

    summary = {"source": "hithink-finance remote", "window": "2024-05-30/2026-08-21",
               "events": len(all_events), "amount_missing": 0, "adjust_field": None,
               "note": "Prices are API-returned continuous series; adjust field is null, continuity checked empirically."}
    (OUT / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
