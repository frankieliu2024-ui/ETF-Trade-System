from pathlib import Path
import importlib.util
import pandas as pd

SRC = Path(__file__).resolve().with_name("backtest_v2214_joint_optimization.py")
spec = importlib.util.spec_from_file_location("joint", SRC)
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)

out = j.OUT
frames = {}
for code in j.CURRENT:
    frames[code] = j.enrich(j.load_excel_ohlcv(j.excel_by_prefix(j.ETF_EXCEL_PREFIX[code])), code)
for code in j.CANDIDATES:
    frames[code] = j.enrich(j.load_candidate(code), code)

periods = {
    "2024H2": (pd.Timestamp("2024-05-30"), pd.Timestamp("2024-12-31")),
    "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
    "2026YTD": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-08-21")),
}

year_rows = []
for code, x in frames.items():
    ev = j.build_events(x, code)
    for period, (a, b) in periods.items():
        z = ev[(ev.date >= a) & (ev.date <= b)]
        s = j.summarize_events(z, code)
        s.update(code=code, period=period)
        year_rows.append(s)
pd.DataFrame(year_rows).to_csv(out / "yearly_event_stability.csv", index=False, encoding="utf-8-sig")

sens_rows = []
for code, x0 in frames.items():
    for volume_mult in [0.9, 1.0, 1.1]:
        x = x0.copy()
        x["p02"] = ((x.close >= x.high60_close - 1e-12) & (x.vol_ratio >= volume_mult) & (x.clv >= .70) & (x.prior5 <= .10))
        x["p06"] = ((x.close.shift(1) < x.ma20.shift(1)) & (x.close >= x.ma20) & (x.vol_ratio >= volume_mult) & (x.clv >= .70))
        x["p09"] = ((x.range10 <= .06) & (x.close >= x.high20_close - 1e-12) & (x.vol_ratio >= 1.2 * volume_mult) & (x.clv >= .75))
        x["signal_raw"] = x[["p02", "p06", "p09", "p07_proxy"]].any(axis=1)
        for gap in [10, 20, 30]:
            old = j.dedup_signal_rows
            j.dedup_signal_rows = lambda data, _gap=20, fixed=gap: old(data, fixed)
            ev = j.build_events(x, code)
            j.dedup_signal_rows = old
            s = j.summarize_events(ev, code)
            s.update(code=code, volume_mult=volume_mult, dedup_gap=gap)
            sens_rows.append(s)
pd.DataFrame(sens_rows).to_csv(out / "parameter_neighborhood.csv", index=False, encoding="utf-8-sig")

# Incremental candidate events by period, using the already constructed formal output.
all_ev = pd.read_csv(out / "all_opportunity_events.csv", parse_dates=["date"])
all_ev["code"] = all_ev["code"].astype(str)
inc_rows = []
base = all_ev[all_ev.code.astype(str).isin(j.CURRENT)]
for code in j.CANDIDATES:
    ce = all_ev[all_ev.code.astype(str) == code].copy()
    for period, (a, b) in periods.items():
        z = ce[(ce.date >= a) & (ce.date <= b)]
        independent = []
        for _, r in z.iterrows():
            near = base[(base.date >= r.date - pd.Timedelta(days=5)) & (base.date <= r.date + pd.Timedelta(days=5))]
            independent.append(near.empty)
        inc_rows.append({"code": code, "period": period, "events": len(z), "independent_events_calendar5": sum(independent),
                         "t10_mean": z.t10.mean() if len(z) else None, "t20_mean": z.t20.mean() if len(z) else None,
                         "mae20_mean": z.mae20.mean() if len(z) else None})
pd.DataFrame(inc_rows).to_csv(out / "candidate_period_incremental.csv", index=False, encoding="utf-8-sig")

# Test which existing regional research position, if any, is the least costly to
# replace when adding the two strongest new factors (innovation drug and gold).
calendar = pd.DatetimeIndex(frames["561980"].loc[
    frames["561980"].date.between(j.COMMON_START, j.END), "date"
])
replace_rows = []
for removed in [None, "159687", "159561", "513520", "513180", "159781"]:
    pool = [c for c in j.CURRENT if c != removed] + ["159992", "518880"]
    _, s = j.cluster_pool(all_ev, pool, calendar)
    s.update(removed=removed or "none", pool_size=len(pool))
    replace_rows.append(s)
pd.DataFrame(replace_rows).to_csv(out / "replacement_sensitivity.csv", index=False, encoding="utf-8-sig")

loo_rows = []
for removed in [None] + j.CURRENT:
    pool = [c for c in j.CURRENT if c != removed]
    _, s = j.cluster_pool(all_ev, pool, calendar)
    s.update(removed=removed or "none", pool_size=len(pool))
    loo_rows.append(s)
pd.DataFrame(loo_rows).to_csv(out / "current_pool_leave_one_out.csv", index=False, encoding="utf-8-sig")

print({"year_rows": len(year_rows), "sensitivity_rows": len(sens_rows), "incremental_rows": len(inc_rows), "replacement_rows": len(replace_rows), "loo_rows": len(loo_rows)})
