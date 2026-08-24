from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def r4(x):
    try:
        y = float(x)
    except (TypeError, ValueError):
        return None
    return round(y, 4) if math.isfinite(y) else None


def load_prices(start: str, end: str) -> pd.DataFrame:
    rows = []
    for path in sorted((ROOT / "events/research/daily_features").glob("*.json")):
        d = path.stem
        if not (start <= d <= end):
            continue
        payload = load_json(path)
        for item in payload.get("features") or []:
            code, close = str(item.get("code") or ""), item.get("close")
            if code and close is not None:
                rows.append((pd.Timestamp(d), code, float(close)))
    if not rows:
        raise RuntimeError("No ETF history")
    return pd.DataFrame(rows, columns=["date", "code", "close"]).pivot(index="date", columns="code", values="close").sort_index()


def components(corr: pd.DataFrame, threshold: float) -> list[list[str]]:
    cols = list(corr.columns)
    parent = {c: c for c in cols}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            v = corr.loc[a, b]
            if pd.notna(v) and float(v) >= threshold:
                union(a, b)
    out = defaultdict(list)
    for c in cols:
        out[find(c)].append(c)
    return sorted([sorted(v) for v in out.values() if len(v) >= 2], key=lambda z: (-len(z), z))


def normalized_weights(weights) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    w = np.where(np.isfinite(w), np.maximum(w, 0.0), 0.0)
    if w.sum() <= 0:
        raise ValueError("invalid weights")
    return w / w.sum()


def risk_metrics(test: pd.DataFrame, w: np.ndarray, corr: pd.DataFrame, threshold: float) -> dict:
    w = normalized_weights(w)
    r = test.to_numpy(float) @ w
    vol = float(np.std(r, ddof=1) * np.sqrt(252)) if len(r) > 1 else np.nan
    wealth = np.cumprod(1 + r)
    peak = np.maximum.accumulate(wealth)
    mdd = abs(float(np.min(wealth / peak - 1))) if len(r) else np.nan
    q = np.quantile(r, 0.05) if len(r) else np.nan
    tail = r[r <= q]
    cvar = abs(float(np.mean(tail))) if len(tail) else np.nan
    asset_vol = test.std(ddof=1).to_numpy(float) * np.sqrt(252)
    dr = float(np.dot(w, asset_vol) / vol) if vol > 0 else np.nan
    eff_n = 1 / float(np.sum(w*w))
    comps = components(corr, threshold)
    shares = [sum(w[list(test.columns).index(c)] for c in comp) for comp in comps]
    return {
        "annualized_volatility": r4(vol), "max_drawdown": r4(mdd), "cvar_95": r4(cvar),
        "diversification_ratio": r4(dr), "effective_asset_count": r4(eff_n),
        "largest_high_corr_component_weight_share": r4(max(shares) if shares else 0.0),
    }


def release_scores(train: pd.DataFrame, w: np.ndarray, fraction: float) -> dict[str, float]:
    w = normalized_weights(w)
    cov = train.cov().to_numpy(float) * 252
    base_var = float(w @ cov @ w)
    base_vol = math.sqrt(max(base_var, 0.0))
    out = {}
    for i, c in enumerate(train.columns):
        released = min(float(fraction), float(w[i]))
        if released <= 1e-9:
            continue
        w2 = w.copy(); w2[i] -= released
        new_vol = math.sqrt(max(float(w2 @ cov @ w2), 0.0))
        out[c] = (base_vol - new_vol) / released
    return out


def pair_rank_stability(score_maps: list[dict[str, float]]) -> float | None:
    vals = []
    for i, a in enumerate(score_maps):
        for b in score_maps[i+1:]:
            common = sorted(set(a) & set(b))
            if len(common) < 4:
                continue
            x = pd.Series([a[c] for c in common], index=common)
            y = pd.Series([b[c] for c in common], index=common)
            rho = x.corr(y, method="spearman")
            if pd.notna(rho): vals.append(float(rho))
    return float(np.mean(vals)) if vals else None


def model_factories():
    from skfolio.optimization import EqualWeighted, InverseVolatility, RiskBudgeting
    return {
        "EqualWeighted": lambda: EqualWeighted(),
        "InverseVolatility": lambda: InverseVolatility(),
        "RiskBudgeting": lambda: RiskBudgeting(),
    }


def fit_models(train: pd.DataFrame, names: list[str]) -> dict[str, np.ndarray]:
    fac = model_factories(); out = {}
    for name in names:
        m = fac[name](); m.fit(train); out[name] = normalized_weights(m.weights_)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("request_path"); args = ap.parse_args()
    req = load_json(ROOT / args.request_path); cfg = load_json(ROOT / req["config_path"])
    data = cfg["data"]; grid = cfg["robustness_grid"]; val = cfg["validation"]
    prices = load_prices(data["start_date"], data["end_date"])
    returns = prices.pct_change(fill_method=None)
    dates = list(returns.index); min_assets = int(data["minimum_assets_per_fold"])
    test_days, step_days = int(grid["test_days"]), int(grid["step_days"])
    thresholds = [float(x) for x in grid["correlation_thresholds"]]
    fraction = float(cfg["marginal_capital"]["release_fraction_of_portfolio"])
    names = list(cfg["models"]); failures = []; cells = []
    release_by_date_model = defaultdict(list)

    for train_days in [int(x) for x in grid["train_days"]]:
        start_idx = train_days
        while start_idx + test_days <= len(dates):
            tr_dates = dates[start_idx-train_days:start_idx]; te_dates = dates[start_idx:start_idx+test_days]
            tr0, te0 = returns.loc[tr_dates], returns.loc[te_dates]
            eligible = [c for c in returns.columns if tr0[c].notna().mean() >= .95 and te0[c].notna().mean() >= .95]
            if len(eligible) >= min_assets:
                tr = tr0[eligible].dropna(); te = te0[eligible].dropna()
                if len(tr) >= int(train_days*.85) and len(te) >= int(test_days*.85):
                    try:
                        weights = fit_models(tr, names); corr = tr.corr()
                        rel = {name: release_scores(tr, w, fraction) for name, w in weights.items()}
                        for threshold in thresholds:
                            models = {name: {"metrics": risk_metrics(te, w, corr, threshold)} for name, w in weights.items()}
                            comps = components(corr, threshold)
                            cells.append({
                                "train_days": train_days, "threshold": threshold,
                                "train_end": str(tr_dates[-1].date()), "test_start": str(te_dates[0].date()), "test_end": str(te_dates[-1].date()),
                                "asset_count": len(eligible), "high_corr_components": comps, "models": models,
                            })
                            for name in names:
                                release_by_date_model[(str(te_dates[0].date()), name)].append(rel[name])
                    except Exception as exc:
                        failures.append({"train_days": train_days, "test_start": str(te_dates[0].date()), "error": f"{type(exc).__name__}: {exc}"})
            start_idx += step_days

    baseline = val["baseline"]
    parameter_summary = {}
    methods_summary = {}
    for name in names:
        if name == baseline: continue
        per_param = {}
        all_rows = []
        for td in [int(x) for x in grid["train_days"]]:
            for th in thresholds:
                rows = [c for c in cells if c["train_days"]==td and c["threshold"]==th and name in c["models"]]
                wins = defaultdict(int); diffs = defaultdict(list)
                for c in rows:
                    m, b = c["models"][name]["metrics"], c["models"][baseline]["metrics"]
                    for k in ["annualized_volatility","max_drawdown","cvar_95","largest_high_corr_component_weight_share","diversification_ratio"]:
                        mv,bv=m[k],b[k]
                        if mv is None or bv is None: continue
                        diffs[k].append(mv-bv)
                        lower = k != "diversification_ratio"
                        if (lower and mv < bv) or ((not lower) and mv > bv): wins[k]+=1
                ps = {k:{"mean_difference":r4(np.mean(v)) if v else None,"win_rate":r4(wins[k]/len(v)) if v else None} for k,v in diffs.items()}
                key=f"train{td}_corr{th:.2f}"; per_param[key]={"folds":len(rows),"vs_equal":ps}
                if rows: all_rows.extend(rows)
        parameter_summary[name]=per_param
        unique = {(c["train_days"],c["threshold"],c["test_start"]):c for c in all_rows}.values()
        methods_summary[name]={"parameter_cells":len(per_param)}

    # Aggregate parameter-cell pass rates.
    for name in [n for n in names if n != baseline]:
        passes=0; total=0
        for ps in parameter_summary[name].values():
            if ps["folds"] == 0: continue
            total += 1; v=ps["vs_equal"]
            rates=[(v.get(k) or {}).get("win_rate") or 0 for k in ["annualized_volatility","cvar_95","largest_high_corr_component_weight_share"]]
            if sum(x >= float(val["minimum_risk_metric_win_rate"]) for x in rates) >= 2: passes += 1
        methods_summary[name].update({"passing_parameter_cells":passes,"parameter_cell_pass_rate":r4(passes/total) if total else None})

    # Release rank stability across parameterizations sharing the same future window.
    stability = {}
    for name in names:
        vals=[]; top_counts=defaultdict(int); n_maps=0
        for (d,m), maps in release_by_date_model.items():
            if m != name: continue
            s=pair_rank_stability(maps)
            if s is not None: vals.append(s)
            for mp in maps:
                if mp:
                    top_counts[max(mp,key=mp.get)] += 1; n_maps += 1
        stability[name]={
            "mean_pairwise_spearman":r4(np.mean(vals)) if vals else None,
            "top_release_asset_frequency":{k:r4(v/n_maps) for k,v in sorted(top_counts.items(), key=lambda z:-z[1])},
            "comparison_windows":len(vals)
        }

    # Canonical leave-one-ETF-out: 120d / corr 0.70; compare candidate model vs equal on same reduced universe.
    loo = {"InverseVolatility":{"wins":0,"tests":0},"RiskBudgeting":{"wins":0,"tests":0}}
    train_days=120; threshold=.70; start_idx=train_days
    while start_idx + test_days <= len(dates):
        tr_dates=dates[start_idx-train_days:start_idx]; te_dates=dates[start_idx:start_idx+test_days]
        tr0,te0=returns.loc[tr_dates],returns.loc[te_dates]
        eligible=[c for c in returns.columns if tr0[c].notna().mean()>=.95 and te0[c].notna().mean()>=.95]
        if len(eligible)>=min_assets:
            for omitted in eligible:
                subset=[c for c in eligible if c!=omitted]
                if len(subset)<min_assets-1: continue
                tr=tr0[subset].dropna(); te=te0[subset].dropna()
                try:
                    ws=fit_models(tr,[baseline,"InverseVolatility","RiskBudgeting"]); corr=tr.corr()
                    b=risk_metrics(te,ws[baseline],corr,threshold)["annualized_volatility"]
                    for name in ["InverseVolatility","RiskBudgeting"]:
                        mv=risk_metrics(te,ws[name],corr,threshold)["annualized_volatility"]
                        if mv is not None and b is not None:
                            loo[name]["tests"]+=1; loo[name]["wins"]+=int(mv<b)
                except Exception as exc:
                    failures.append({"phase":"leave_one_out","omitted":omitted,"test_start":str(te_dates[0].date()),"error":f"{type(exc).__name__}: {exc}"})
        start_idx += step_days
    for name,x in loo.items(): x["volatility_win_rate_vs_equal"]=r4(x["wins"]/x["tests"]) if x["tests"] else None

    # Current account mapping using research data only through configured end date.
    account_mapping={"status":"DISABLED"}
    if cfg.get("current_account_mapping",{}).get("enabled"):
        acct=load_json(ROOT / cfg["current_account_mapping"]["account_fact_path"])
        holdings={p["code"]:float(p["market_value"]) for p in acct.get("positions",[]) if p.get("asset_type")=="ETF" and p.get("code") in returns.columns}
        cols=[c for c in holdings if returns[c].loc[:pd.Timestamp(data["end_date"])].notna().sum()>=120]
        if len(cols)>=2:
            tr=returns.loc[:pd.Timestamp(data["end_date"]),cols].tail(120).dropna()
            w=np.array([holdings[c] for c in cols],float); w=normalized_weights(w); corr=tr.corr(); comps=components(corr,.70); rel=release_scores(tr,w,fraction)
            comp_shares=[]
            for comp in comps: comp_shares.append({"component":comp,"account_weight_share":r4(sum(w[cols.index(c)] for c in comp))})
            account_mapping={
                "status":"STALE_RESEARCH_MAPPING","account_fact_updated_at":acct.get("updated_at"),"risk_data_end":data["end_date"],
                "etf_holdings":{c:r4(w[cols.index(c)]) for c in cols},"high_corr_components":comp_shares,
                "risk_side_release_efficiency":{c:r4(v) for c,v in sorted(rel.items(), key=lambda z:-z[1])},
                "warning":cfg["current_account_mapping"]["warning"]
            }

    candidates=[]
    for name in ["InverseVolatility","RiskBudgeting"]:
        cell_rate=methods_summary[name].get("parameter_cell_pass_rate") or 0
        rank_stab=stability[name].get("mean_pairwise_spearman") or 0
        loo_rate=loo[name].get("volatility_win_rate_vs_equal") or 0
        if cell_rate>=.67 and rank_stab>=float(val["minimum_release_rank_stability"]) and loo_rate>=.60:
            candidates.append(name)
    interpretation="PROMISING_FOR_RESEARCH_INTEGRATION" if candidates and not failures else ("PROMISING_WITH_LIMITATIONS" if candidates else "NO_STABLE_INCREMENT_STAGE2")
    payload={
        "schema_version":"1.0","generated_at":now_utc(),"mode":"SKFOLIO_STAGE2_ROBUSTNESS_VALIDATION",
        "upstream_commit":cfg["upstream"]["commit"],"parameter_cell_count":len(cells),"failure_count":len(failures),"failures":failures,
        "methods_summary":methods_summary,"parameter_summary":parameter_summary,"release_rank_stability":stability,
        "leave_one_etf_out":loo,"current_account_mapping":account_mapping,"research_candidates":candidates,"research_interpretation":interpretation,
        "decision_eligible":False,"trade_signal":None,"portfolio_target":None,"trial_confirm":None,"master_override":False,
        "historical_decision_prohibited":True,
        "interpretation_boundary":"Stage2只提供共同风险与风险侧边际资本信息；不得把风险侧释放排序解释为收益侧卖出顺序，不得直接生成交易动作。"
    }
    out=ROOT/req["output_path"]; out.parent.mkdir(parents=True,exist_ok=True); atomic_json_write(out,payload)
    status={"schema_version":"1.0","generated_at":payload["generated_at"],"mode":"SKFOLIO_RESEARCH_STAGE2","status":"PASS" if not failures else "DEGRADED","request_path":args.request_path,"upstream_commit":cfg["upstream"]["commit"],"parameter_cell_count":len(cells),"failure_count":len(failures),"research_interpretation":interpretation,"research_candidates":candidates,"decision_eligible":False,"trade_signal":None,"boundary":payload["interpretation_boundary"]}
    sp=ROOT/req["status_path"]; sp.parent.mkdir(parents=True,exist_ok=True); atomic_json_write(sp,status)
    print(json.dumps({"parameter_cells":len(cells),"failures":len(failures),"candidates":candidates,"interpretation":interpretation},ensure_ascii=False))
    return 0 if cells and not failures else 1

if __name__ == "__main__": raise SystemExit(main())
