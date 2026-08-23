from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


j = load_module("joint", ROOT / "专项回测" / "backtest_v2214_joint_optimization.py")
a = load_module("amount", ROOT / "专项回测" / "hithink_amount_rebacktest.py")
OUT = ROOT / "专项回测" / "outputs" / "hithink_etf_audit_20260823" / "amount_rebacktest"


def events_with_gap(x: pd.DataFrame, code: str, gap: int) -> pd.DataFrame:
    old = j.dedup_signal_rows
    try:
        j.dedup_signal_rows = lambda data, _gap=20: old(data, gap)
        return j.build_events(x, code)
    finally:
        j.dedup_signal_rows = old


def main():
    codes = j.CURRENT + j.CANDIDATES
    frames = {c: a.enrich_amount(a.load(c), c) for c in codes}
    periods = {
        "2024H2": (pd.Timestamp("2024-05-30"), pd.Timestamp("2024-12-31")),
        "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        "2026YTD": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-08-21")),
    }
    yearly = []
    for c, x in frames.items():
        ev = j.build_events(x[x.date <= j.END].reset_index(drop=True), c)
        for period, (start, end) in periods.items():
            s = j.summarize_events(ev[ev.date.between(start, end)], c)
            s.update(code=c, period=period)
            yearly.append(s)
    pd.DataFrame(yearly).to_csv(OUT / "yearly_stability_amount.csv", index=False, encoding="utf-8-sig")

    sens = []
    for c, base in frames.items():
        for mult in [.9, 1.0, 1.1]:
            x = base.copy()
            r = x["amount_ratio"]
            x["p02"] = ((x.close >= x.high60_close - 1e-12) & (r >= mult) & (x.clv >= .70) & (x.prior5 <= .10))
            x["p06"] = ((x.close.shift(1) < x.ma20.shift(1)) & (x.close >= x.ma20) & (r >= mult) & (x.clv >= .70))
            x["p09"] = ((x.range10 <= .06) & (x.close >= x.high20_close - 1e-12) & (r >= 1.2 * mult) & (x.clv >= .75))
            x["signal_raw"] = x[["p02", "p06", "p09", "p07_proxy"]].any(axis=1)
            for gap in [10, 20, 30]:
                ev = events_with_gap(x[x.date <= j.END].reset_index(drop=True), c, gap)
                s = j.summarize_events(ev, c)
                s.update(code=c, amount_mult=mult, dedup_gap=gap)
                sens.append(s)
    pd.DataFrame(sens).to_csv(OUT / "parameter_neighborhood_amount.csv", index=False, encoding="utf-8-sig")

    events = pd.read_csv(OUT / "all_opportunity_events_amount.csv", parse_dates=["date"])
    events["code"] = events["code"].astype(str)
    calendar = pd.DatetimeIndex(sorted(frames["561980"].loc[frames["561980"].date.between(j.COMMON_START, j.END), "date"]))
    replace = []
    for removed in [None, "159687", "159561", "513520", "513180", "159781"]:
        pool = [c for c in j.CURRENT if c != removed] + ["159992", "518880"]
        _, s = j.cluster_pool(events, pool, calendar)
        s.update(removed=removed or "none", pool_size=len(pool))
        replace.append(s)
    pd.DataFrame(replace).to_csv(OUT / "replacement_sensitivity_amount.csv", index=False, encoding="utf-8-sig")

    loo = []
    for removed in [None] + j.CURRENT:
        pool = [c for c in j.CURRENT if c != removed]
        _, s = j.cluster_pool(events, pool, calendar)
        s.update(removed=removed or "none", pool_size=len(pool))
        loo.append(s)
    pd.DataFrame(loo).to_csv(OUT / "current_leave_one_out_amount.csv", index=False, encoding="utf-8-sig")

    alternatives = []
    alt_pools = {f"current8_plus_{c}": j.CURRENT + [c] for c in j.CANDIDATES}
    alt_pools.update({
        "minus159687_plus515880": [c for c in j.CURRENT if c != "159687"] + ["515880"],
        "minus159687_plus515880_159992_518880": [c for c in j.CURRENT if c != "159687"] + ["515880", "159992", "518880"],
        "minus159687_plus159992": [c for c in j.CURRENT if c != "159687"] + ["159992"],
        "minus159687_plus518880": [c for c in j.CURRENT if c != "159687"] + ["518880"],
    })
    for label, pool in alt_pools.items():
        _, s = j.cluster_pool(events, pool, calendar)
        s.update(scenario=label, pool_size=len(pool))
        alternatives.append(s)
    pd.DataFrame(alternatives).to_csv(OUT / "alternative_capacity_amount.csv", index=False, encoding="utf-8-sig")

    # Simple non-parametric uncertainty audit for current8 versus the proposed 9-ETF pool.
    c0, _ = j.cluster_pool(events, j.CURRENT, calendar)
    c9, _ = j.cluster_pool(events, [c for c in j.CURRENT if c != "159687"] + ["159992", "518880"], calendar)
    rng = np.random.default_rng(2214)
    diffs = []
    for _ in range(10000):
        m0 = rng.choice(c0["t20"].dropna().to_numpy(), len(c0), replace=True).mean()
        m9 = rng.choice(c9["t20"].dropna().to_numpy(), len(c9), replace=True).mean()
        diffs.append(m9 - m0)
    diffs = np.asarray(diffs)
    boot = pd.DataFrame([{
        "comparison": "proposed9_minus_current8", "mean_diff": c9.t20.mean() - c0.t20.mean(),
        "ci_2_5": np.quantile(diffs, .025), "ci_97_5": np.quantile(diffs, .975),
        "probability_positive": (diffs > 0).mean(), "current_events": len(c0), "proposed_events": len(c9),
    }])
    boot.to_csv(OUT / "pool_bootstrap_amount.csv", index=False, encoding="utf-8-sig")
    print({"yearly": len(yearly), "sensitivity": len(sens), "bootstrap": boot.iloc[0].to_dict()})


if __name__ == "__main__":
    main()
