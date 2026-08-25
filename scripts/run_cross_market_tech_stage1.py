from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import ssl
import time
from collections import defaultdict
from datetime import datetime, time as dtime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY = ROOT / "events/research/daily_features"
SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_PAGE = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_PAGE = "https://www.szse.cn/market/fund/volume/etf/index.html"
BEIJING = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def r4(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_local_panel(start: str, end: str, targets: list[str]) -> pd.DataFrame:
    rows = []
    wanted = set(targets)
    for path in sorted(DAILY.glob("*.json")):
        d = path.stem
        if d < start or d > end:
            continue
        obj = load_json(path)
        for item in obj.get("features") or []:
            code = str(item.get("code") or "")
            if code not in wanted:
                continue
            if item.get("open") is None or item.get("close") is None:
                continue
            rows.append({
                "date": pd.Timestamp(d),
                "code": code,
                "open": float(item["open"]),
                "close": float(item["close"]),
            })
    df = pd.DataFrame(rows).drop_duplicates(["date", "code"]).sort_values(["code", "date"])
    if df.empty:
        raise RuntimeError("No local ETF daily history for target set")
    parts = []
    for code, g in df.groupby("code", sort=True):
        g = g.copy().sort_values("date")
        g["mom5_lag1"] = (g["close"].shift(1) / g["close"].shift(6) - 1.0) * 100.0
        g["mom20_lag1"] = (g["close"].shift(1) / g["close"].shift(21) - 1.0) * 100.0
        for h in (1, 3, 5):
            g[f"fwd_open_to_{h}d_close_pct"] = (g["close"].shift(-(h - 1)) / g["open"] - 1.0) * 100.0
        parts.append(g)
    return pd.concat(parts, ignore_index=True).sort_values(["date", "code"])


def fetch_yahoo_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()) - 10 * 86400
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()) + 10 * 86400
    enc = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
    params = {
        "period1": str(p1), "period2": str(p2), "interval": "1d",
        "events": "history", "includeAdjustedClose": "false"
    }
    headers = {"User-Agent": "Mozilla/5.0 ETF-Trade-System research", "Accept": "application/json"}
    last = None
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
            result = payload["chart"]["result"][0]
            ts = result.get("timestamp") or []
            quote_block = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote_block.get("close") or []
            rows = []
            for i, raw_ts in enumerate(ts):
                close = closes[i] if i < len(closes) else None
                if close is None:
                    continue
                stamp = datetime.fromtimestamp(int(raw_ts), timezone.utc).astimezone(NEW_YORK)
                local_date = stamp.date()
                close_local = datetime.combine(local_date, dtime(16, 0), tzinfo=NEW_YORK)
                rows.append({
                    "us_date": pd.Timestamp(local_date.isoformat()),
                    "session_close_utc": close_local.astimezone(timezone.utc),
                    "close": float(close),
                })
            df = pd.DataFrame(rows).drop_duplicates("us_date").sort_values("us_date")
            if len(df) < 100:
                raise RuntimeError(f"Yahoo history too short for {symbol}: {len(df)}")
            df["ret_1d"] = (df["close"] / df["close"].shift(1) - 1.0) * 100.0
            df["ret_3d"] = (df["close"] / df["close"].shift(3) - 1.0) * 100.0
            return df
        except Exception as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Yahoo history fetch failed for {symbol}: {last}")


def attach_external(local: pd.DataFrame, sox: pd.DataFrame, ndx: pd.DataFrame) -> pd.DataFrame:
    s = sox[["us_date", "session_close_utc", "ret_1d", "ret_3d"]].rename(columns={
        "session_close_utc": "sox_close_utc", "ret_1d": "sox_1d", "ret_3d": "sox_3d"
    })
    n = ndx[["us_date", "session_close_utc", "ret_1d", "ret_3d"]].rename(columns={
        "session_close_utc": "ndx_close_utc", "ret_1d": "ndx_1d", "ret_3d": "ndx_3d"
    })
    us = s.merge(n, on="us_date", how="inner").sort_values("us_date")
    us["completed_utc"] = us[["sox_close_utc", "ndx_close_utc"]].max(axis=1)
    us["us_tech_equal_1d"] = (us["sox_1d"] + us["ndx_1d"]) / 2.0
    us["sox_minus_ndx_1d"] = us["sox_1d"] - us["ndx_1d"]
    us["us_tech_equal_3d"] = (us["sox_3d"] + us["ndx_3d"]) / 2.0
    records = us.to_dict("records")
    completed = [x["completed_utc"] for x in records]
    out = local.copy()
    cols = ["sox_1d", "ndx_1d", "us_tech_equal_1d", "sox_minus_ndx_1d", "us_tech_equal_3d"]
    for c in cols:
        out[c] = np.nan
    out["us_source_date"] = ""
    for idx, row in out.iterrows():
        d = row["date"].date()
        a_open = datetime.combine(d, dtime(9, 30), tzinfo=BEIJING).astimezone(timezone.utc)
        pos = bisect.bisect_left(completed, a_open) - 1
        if pos < 0:
            continue
        rec = records[pos]
        if not rec["completed_utc"] < a_open:
            continue
        out.at[idx, "us_source_date"] = rec["us_date"].date().isoformat()
        for c in cols:
            out.at[idx, c] = rec[c]
    return out


def rank_pct(series: pd.Series) -> pd.Series:
    return series.rank(method="average", pct=True)


def partial_rank_metrics(df: pd.DataFrame, signal: str, label: str, controls: list[str], min_obs: int) -> dict:
    needed = [signal, label] + controls
    x = df[needed].dropna().copy()
    if len(x) < min_obs:
        return {"n": int(len(x)), "partial_rank_ic": None, "top_bottom_spread_pct_points": None}
    ranked = pd.DataFrame({c: rank_pct(x[c]) for c in needed}, index=x.index)
    C = np.column_stack([np.ones(len(ranked))] + [ranked[c].to_numpy() for c in controls])
    sx = ranked[signal].to_numpy()
    sy = ranked[label].to_numpy()
    bx, *_ = np.linalg.lstsq(C, sx, rcond=None)
    by, *_ = np.linalg.lstsq(C, sy, rcond=None)
    rx = sx - C @ bx
    ry = sy - C @ by
    if np.std(rx) <= 1e-12 or np.std(ry) <= 1e-12:
        ic = None
    else:
        ic = float(np.corrcoef(rx, ry)[0, 1])
    q1, q2 = np.quantile(rx, [1/3, 2/3])
    raw_y = x[label].to_numpy(dtype=float)
    top = raw_y[rx >= q2]
    bottom = raw_y[rx <= q1]
    spread = float(np.mean(top) - np.mean(bottom)) if len(top) and len(bottom) else None
    return {"n": int(len(x)), "partial_rank_ic": r4(ic), "top_bottom_spread_pct_points": r4(spread)}


def evaluate(panel: pd.DataFrame, cfg: dict, folds: list[dict], controls: list[str], signals: list[str]) -> dict:
    min_obs = int(cfg["validation"]["minimum_observations_per_fold"])
    fold_results = []
    for fold in folds:
        sub = panel[panel["date"].between(pd.Timestamp(fold["test_start"]), pd.Timestamp(fold["test_end"]))]
        for code in [x["code"] for x in cfg["targets"]]:
            etf = sub[sub["code"] == code]
            for h in cfg["horizons_trading_days"]:
                label = f"fwd_open_to_{int(h)}d_close_pct"
                metrics = {s: partial_rank_metrics(etf, s, label, controls, min_obs) for s in signals}
                fold_results.append({"fold": fold["name"], "code": code, "horizon_trading_days": int(h), "metrics": metrics})

    summary = {}
    passing = []
    min_stable = int(cfg["validation"]["minimum_stable_etfs_per_horizon"])
    min_positive_cells = int(cfg["validation"]["minimum_positive_cells_per_horizon"])
    min_ic = float(cfg["validation"]["minimum_partial_rank_ic"])
    required_h = int(cfg["validation"]["required_qualifying_horizons"])
    positive_folds_req = int(cfg["validation"]["required_positive_folds_per_etf"])
    require_spread = bool(cfg["validation"].get("require_positive_top_bottom_spread", True))

    for sig in signals:
        horizons = {}
        qualifying_count = 0
        for h in cfg["horizons_trading_days"]:
            cells = []
            by_etf = defaultdict(list)
            for fr in fold_results:
                if fr["horizon_trading_days"] != int(h):
                    continue
                m = fr["metrics"][sig]
                if m["partial_rank_ic"] is None:
                    continue
                rec = {"code": fr["code"], "fold": fr["fold"], **m}
                cells.append(rec)
                by_etf[fr["code"]].append(rec)
            stable_etfs = []
            etf_summary = {}
            for code, rows in sorted(by_etf.items()):
                vals = [float(x["partial_rank_ic"]) for x in rows]
                spreads = [float(x["top_bottom_spread_pct_points"]) for x in rows if x["top_bottom_spread_pct_points"] is not None]
                pos = sum(v > 0 for v in vals)
                mean_ic = float(np.mean(vals)) if vals else float("nan")
                if pos >= positive_folds_req and mean_ic > 0:
                    stable_etfs.append(code)
                etf_summary[code] = {
                    "fold_count": len(vals), "positive_folds": pos,
                    "mean_partial_rank_ic": r4(mean_ic),
                    "mean_top_bottom_spread_pct_points": r4(np.mean(spreads)) if spreads else None,
                }
            vals = [float(x["partial_rank_ic"]) for x in cells]
            spreads = [float(x["top_bottom_spread_pct_points"]) for x in cells if x["top_bottom_spread_pct_points"] is not None]
            mean_ic = float(np.mean(vals)) if vals else float("nan")
            mean_spread = float(np.mean(spreads)) if spreads else float("nan")
            positive_cells = sum(v > 0 for v in vals)
            qualifies = (
                len(stable_etfs) >= min_stable
                and positive_cells >= min_positive_cells
                and math.isfinite(mean_ic) and mean_ic > min_ic
                and (not require_spread or (math.isfinite(mean_spread) and mean_spread > 0))
            )
            if qualifies:
                qualifying_count += 1
            horizons[str(h)] = {
                "cell_count": len(vals), "positive_cells": positive_cells,
                "mean_partial_rank_ic": r4(mean_ic),
                "mean_top_bottom_spread_pct_points": r4(mean_spread),
                "stable_etfs": stable_etfs, "etf_summary": etf_summary,
                "qualifies": bool(qualifies),
            }
        passed = qualifying_count >= required_h
        summary[sig] = {"qualifying_horizon_count": qualifying_count, "stage_pass": passed, "horizons": horizons}
        if passed:
            passing.append(sig)
    return {"controls": controls, "fold_results": fold_results, "signal_summary": summary, "passing_signals": passing}


def get_url(url, headers, params=None, opener=None, verify=True):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    req = Request(url, headers=headers)
    if opener:
        return opener.open(req, timeout=25).read().decode("utf-8")
    ctx = None if verify else ssl._create_unverified_context()
    return urlopen(req, timeout=25, context=ctx).read().decode("utf-8")


def fetch_sse_shares(dates: list[str], wanted: set[str]) -> dict[tuple[str, str], float]:
    headers = {"Referer": SSE_PAGE, "User-Agent": "Mozilla/5.0", "Accept": "application/json,text/javascript,*/*;q=0.01", "X-Requested-With": "XMLHttpRequest"}
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        get_url(SSE_PAGE, headers, opener=opener)
    except Exception:
        pass
    out = {}
    for i, d in enumerate(dates):
        params = {"isPagination":"true","pageHelp.pageSize":"2000","pageHelp.pageNo":"1","pageHelp.beginPage":"1","pageHelp.cacheSize":"1","pageHelp.endPage":"1","sqlId":SSE_SQL,"STAT_DATE":d,"_":str(int(time.time()*1000))}
        last = None
        for attempt in range(5):
            try:
                data = json.loads(get_url(SSE_URL, headers, params, opener=opener))
                for row in data.get("result") or (data.get("pageHelp") or {}).get("data") or []:
                    c = str(row.get("SEC_CODE") or "").strip()
                    v = str(row.get("TOT_VOL") or "").replace(",", "").strip()
                    if c in wanted and v:
                        out[(d, c)] = float(v)
                last = None
                break
            except Exception as exc:
                last = exc
                time.sleep(1.0 + attempt)
        if last is not None:
            raise RuntimeError(f"SSE share fetch failed {d}: {last}")
        if i % 40 == 0:
            time.sleep(0.4)
        else:
            time.sleep(0.18)
    return out


def fetch_szse_shares(dates: list[str], wanted: set[str]) -> dict[tuple[str, str], float]:
    headers = {"Referer": SZSE_PAGE, "User-Agent": "Mozilla/5.0", "Accept": "application/json,text/javascript,*/*;q=0.01", "X-Requested-With": "XMLHttpRequest"}
    start, end = min(dates), max(dates)
    out = {}
    for c in sorted(wanted):
        cur = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        while cur <= end_ts:
            window_end = min(cur + pd.Timedelta(days=169), end_ts)
            page = 1
            while True:
                params = {"SHOWTYPE":"JSON","CATALOGID":"scsj_fund_jjgm","TABKEY":"tab1","jjlb":"ETF","txtDm":c,"txtStart":cur.strftime("%Y-%m-%d"),"txtEnd":window_end.strftime("%Y-%m-%d"),"tab1PAGENO":str(page),"random":str(time.time())}
                last = None
                for attempt in range(5):
                    try:
                        data = json.loads(get_url(SZSE_URL, headers, params, verify=False))
                        last = None
                        break
                    except Exception as exc:
                        last = exc
                        time.sleep(1.0 + attempt)
                if last is not None:
                    raise RuntimeError(f"SZSE share fetch failed {c} {cur.date()}..{window_end.date()}: {last}")
                block = data[0] if isinstance(data, list) and data else {}
                for row in block.get("data") or []:
                    d = str(row.get("size_date") or "").strip()
                    v = str(row.get("current_size") or "").replace(",", "").strip()
                    if d and v:
                        out[(d, c)] = float(v)
                pages = int((block.get("metadata") or {}).get("pagecount") or 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(0.2)
            cur = window_end + pd.Timedelta(days=1)
            time.sleep(0.2)
    return out


def attach_share_control(panel: pd.DataFrame, target_meta: list[dict]) -> tuple[pd.DataFrame, dict]:
    dates = sorted(panel["date"].dt.strftime("%Y-%m-%d").unique().tolist())
    sh = {x["code"] for x in target_meta if x["code"] in {"561980", "588000"}}
    sz = {x["code"] for x in target_meta if x["code"] not in sh}
    shares = {}
    if sh:
        shares.update(fetch_sse_shares(dates, sh))
    if sz:
        shares.update(fetch_szse_shares(dates, sz))
    out_parts = []
    for code, g in panel.groupby("code", sort=True):
        g = g.copy().sort_values("date")
        g["shares_10k"] = [shares.get((d.strftime("%Y-%m-%d"), code)) for d in g["date"]]
        g["reverse_share_change_5d_lag1"] = -((g["shares_10k"].shift(1) / g["shares_10k"].shift(6) - 1.0) * 100.0)
        out_parts.append(g)
    return pd.concat(out_parts, ignore_index=True), {"share_fact_rows": len(shares), "SSE_targets": len(sh), "SZSE_targets": len(sz)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request_path")
    args = ap.parse_args()
    req = load_json(ROOT / args.request_path)
    cfg = load_json(ROOT / req["config_path"])
    target_codes = [x["code"] for x in cfg["targets"]]
    local = load_local_panel(req["data_start"], req["data_end"], target_codes)
    sox = fetch_yahoo_history(cfg["external_objects"]["SOX"]["symbol"], req["data_start"], req["data_end"])
    ndx = fetch_yahoo_history(cfg["external_objects"]["NDX"]["symbol"], req["data_start"], req["data_end"])
    panel = attach_external(local, sox, ndx)
    signals = list(cfg["candidate_signals"])

    stage_a = evaluate(panel, cfg, req["folds"], list(cfg["stage_a_controls"]), signals)
    stage_b = None
    share_coverage = None
    data_status = "PASS"
    if stage_a["passing_signals"]:
        try:
            panel_b, share_coverage = attach_share_control(panel, cfg["targets"])
            stage_b = evaluate(panel_b, cfg, req["folds"], list(cfg["stage_b_controls"]), stage_a["passing_signals"])
        except Exception as exc:
            data_status = "DEGRADED"
            stage_b = {"error": f"{type(exc).__name__}: {exc}", "passing_signals": []}

    if not stage_a["passing_signals"]:
        interpretation = "NO_STABLE_INCREMENT_BEYOND_LOCAL_MOMENTUM"
        final_passing = []
    elif data_status != "PASS":
        interpretation = "STAGE_A_PROMISING_STAGE_B_DATA_DEGRADED"
        final_passing = []
    elif stage_b and stage_b.get("passing_signals"):
        interpretation = "PROMISING_CROSS_MARKET_CONDITIONAL_INCREMENT"
        final_passing = stage_b["passing_signals"]
    else:
        interpretation = "NO_INCREMENT_BEYOND_VALIDATED_SHARE_FLOW_CONTROLS"
        final_passing = []

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": cfg["mode"],
        "objective": cfg["objective"],
        "targets": cfg["targets"],
        "external_objects": cfg["external_objects"],
        "entry_rule": cfg["entry_rule"],
        "point_in_time_rule": cfg["point_in_time_rule"],
        "local_rows": int(len(local)),
        "external_rows": {"SOX": int(len(sox)), "NDX": int(len(ndx))},
        "stage_a": stage_a,
        "stage_b": stage_b,
        "share_coverage": share_coverage,
        "research_interpretation": interpretation,
        "passing_signals": final_passing,
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
        "historical_decision_prohibited": True,
        "production_context_integration": False,
        "method_boundary": cfg["method_note"],
        "interpretation_boundary": "Cross-market statistics are research evidence only. They cannot directly generate risk permission, opportunity status, amount, holding reduction, exit, portfolio targets or a composite capital-efficiency score."
    }
    out = ROOT / req["output"]
    status_path = ROOT / req["status_output"]
    atomic_json_write(out, payload)
    status = {
        "generated_at": payload["generated_at"],
        "status": data_status,
        "research_interpretation": interpretation,
        "stage_a_passing_signals": stage_a["passing_signals"],
        "passing_signals": final_passing,
        "target_count": len(target_codes),
        "decision_eligible": False,
        "trade_signal": None,
        "master_override": False
    }
    atomic_json_write(status_path, status)
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
