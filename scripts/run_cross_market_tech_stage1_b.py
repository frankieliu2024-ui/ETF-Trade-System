from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

import run_cross_market_tech_stage1 as core

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
A_RESULT = ROOT / "research/backtests/cross_market_tech_stage1_a_validation.json"
OUT = ROOT / "research/backtests/cross_market_tech_stage1_validation.json"
STATUS = ROOT / "data/state/cross_market_tech_research_status.json"


def fetch_one_sse_date(d: str, wanted: set[str]) -> list[tuple[tuple[str, str], float]]:
    headers = {"Referer": core.SSE_PAGE, "User-Agent": "Mozilla/5.0 ETF-Trade-System research", "Accept": "application/json,text/javascript,*/*;q=0.01", "X-Requested-With": "XMLHttpRequest"}
    params = {"isPagination":"true","pageHelp.pageSize":"2000","pageHelp.pageNo":"1","pageHelp.beginPage":"1","pageHelp.cacheSize":"1","pageHelp.endPage":"1","sqlId":core.SSE_SQL,"STAT_DATE":d,"_":str(int(time.time()*1000))}
    last = None
    for attempt in range(3):
        try:
            r = requests.get(core.SSE_URL, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            out=[]
            for row in data.get("result") or (data.get("pageHelp") or {}).get("data") or []:
                c=str(row.get("SEC_CODE") or "").strip(); v=str(row.get("TOT_VOL") or "").replace(",","").strip()
                if c in wanted and v:
                    out.append(((d,c),float(v)))
            return out
        except Exception as exc:
            last=exc; time.sleep(0.4*(attempt+1))
    raise RuntimeError(f"SSE share fetch failed {d}: {last}")


def fast_sse_shares(dates: list[str], wanted: set[str]) -> dict[tuple[str,str],float]:
    out={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch_one_sse_date,d,wanted):d for d in dates}
        for fut in as_completed(futures):
            for key,val in fut.result(): out[key]=val
    return out


def attach_share_control_fast(panel: pd.DataFrame, target_meta: list[dict]):
    dates=sorted(panel["date"].dt.strftime("%Y-%m-%d").unique().tolist())
    sh={x["code"] for x in target_meta if x["code"] in {"561980","588000"}}
    sz={x["code"] for x in target_meta if x["code"] not in sh}
    shares={}
    if sh: shares.update(fast_sse_shares(dates,sh))
    if sz: shares.update(core.fetch_szse_shares(dates,sz))
    parts=[]
    for code,g in panel.groupby("code",sort=True):
        g=g.copy().sort_values("date")
        g["shares_10k"]=[shares.get((d.strftime("%Y-%m-%d"),code)) for d in g["date"]]
        g["reverse_share_change_5d_lag1"]=-((g["shares_10k"].shift(1)/g["shares_10k"].shift(6)-1.0)*100.0)
        parts.append(g)
    return pd.concat(parts,ignore_index=True), {"share_fact_rows":len(shares),"SSE_targets":len(sh),"SZSE_targets":len(sz)}


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("request_path"); args=ap.parse_args()
    req=core.load_json(ROOT/args.request_path); cfg=core.load_json(ROOT/req["config_path"])
    a=core.load_json(A_RESULT)
    passing=list(a.get("stage_a_passing_signals") or [])
    if not passing:
        raise RuntimeError("Stage A did not pass; Stage B must not run")
    target_codes=[x["code"] for x in cfg["targets"]]
    local=core.load_local_panel(req["data_start"],req["data_end"],target_codes)
    sox=core.fetch_yahoo_history(cfg["external_objects"]["SOX"]["symbol"],req["data_start"],req["data_end"])
    ndx=core.fetch_yahoo_history(cfg["external_objects"]["NDX"]["symbol"],req["data_start"],req["data_end"])
    panel=core.attach_external(local,sox,ndx)
    panel_b,coverage=attach_share_control_fast(panel,cfg["targets"])
    stage_b=core.evaluate(panel_b,cfg,req["folds"],list(cfg["stage_b_controls"]),passing)
    final=stage_b["passing_signals"]
    interpretation="PROMISING_CROSS_MARKET_CONDITIONAL_INCREMENT" if final else "NO_INCREMENT_BEYOND_VALIDATED_SHARE_FLOW_CONTROLS"
    payload={
      "schema_version":"1.1","generated_at":now_utc(),"mode":cfg["mode"],"objective":cfg["objective"],
      "targets":cfg["targets"],"external_objects":cfg["external_objects"],"entry_rule":cfg["entry_rule"],"point_in_time_rule":cfg["point_in_time_rule"],
      "stage_a_reference":"research/backtests/cross_market_tech_stage1_a_validation.json","stage_a_passing_signals":passing,
      "stage_b":stage_b,"share_coverage":coverage,"research_interpretation":interpretation,"passing_signals":final,
      "decision_eligible":False,"trade_signal":None,"trial_confirm":None,"portfolio_target":None,"master_override":False,
      "historical_decision_prohibited":True,"production_context_integration":False,
      "interpretation_boundary":"Cross-market Stage B is research evidence only. It cannot directly generate risk permission, opportunity status, amount, holding reduction, exit, portfolio targets or a composite capital-efficiency score."
    }
    atomic_json_write(OUT,payload)
    status={"generated_at":payload["generated_at"],"status":"PASS","research_interpretation":interpretation,"stage_a_passing_signals":passing,"passing_signals":final,"share_coverage":coverage,"decision_eligible":False,"trade_signal":None,"master_override":False}
    atomic_json_write(STATUS,status); print(json.dumps(status,ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
