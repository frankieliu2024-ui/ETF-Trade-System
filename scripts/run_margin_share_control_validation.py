from __future__ import annotations
import argparse,json,os,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
import requests
import run_market_breadth_margin_stage1_v2 as marginv2
import run_cross_market_tech_stage1 as sharecore
try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT=Path(os.environ.get('ETF_SYSTEM_ROOT',Path(__file__).resolve().parents[1])).resolve()
base=marginv2.base
STAGE1=ROOT/'research/backtests/market_breadth_margin_stage1_validation.json'
OUT=ROOT/'research/backtests/margin_share_control_validation.json'
STATUS=ROOT/'data/state/margin_share_control_status.json'
EXPECTED=['margin_balance_5d_change_lag1','margin_buy_5d_vs20_lag1']
SH_CODES={'561980','588000','515880'}

def fetch_one_sse_date(d,wanted):
    headers={'Referer':sharecore.SSE_PAGE,'User-Agent':'Mozilla/5.0 ETF-Trade-System research','Accept':'application/json,text/javascript,*/*;q=0.01','X-Requested-With':'XMLHttpRequest'}
    params={'isPagination':'true','pageHelp.pageSize':'2000','pageHelp.pageNo':'1','pageHelp.beginPage':'1','pageHelp.cacheSize':'1','pageHelp.endPage':'1','sqlId':sharecore.SSE_SQL,'STAT_DATE':d,'_':str(int(time.time()*1000))}
    last=None
    for attempt in range(3):
        try:
            r=requests.get(sharecore.SSE_URL,params=params,headers=headers,timeout=15);r.raise_for_status();z=r.json();out=[]
            for row in z.get('result') or (z.get('pageHelp') or {}).get('data') or []:
                c=str(row.get('SEC_CODE') or '').strip();v=str(row.get('TOT_VOL') or '').replace(',','').strip()
                if c in wanted and v:out.append(((d,c),float(v)))
            return out,None
        except Exception as exc:
            last=exc;time.sleep(0.4*(attempt+1))
    return [],f'{d}: {type(last).__name__}: {last}'

def fetch_sse(dates,wanted):
    out={};errors=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs=[ex.submit(fetch_one_sse_date,d,wanted) for d in dates]
        for f in as_completed(fs):
            rows,err=f.result()
            for k,v in rows:out[k]=v
            if err:errors.append(err)
    return out,errors

def attach_share(panel,targets):
    dates=sorted(panel.date.dt.strftime('%Y-%m-%d').unique().tolist())
    sh={x['code'] for x in targets if x['code'] in SH_CODES};sz={x['code'] for x in targets if x['code'] not in sh}
    shares={};errors=[]
    if sh:
        a,e=fetch_sse(dates,sh);shares.update(a);errors.extend(e)
    if sz:
        try:shares.update(sharecore.fetch_szse_shares(dates,sz))
        except Exception as exc:errors.append(f'SZSE: {type(exc).__name__}: {exc}')
    parts=[];coverage={}
    for code,g in panel.groupby('code',sort=True):
        g=g.copy().sort_values('date');g['shares_10k']=[shares.get((d.strftime('%Y-%m-%d'),code)) for d in g.date]
        g['reverse_share_change_5d_lag1']=-((g.shares_10k.shift(1)/g.shares_10k.shift(6)-1)*100)
        coverage[code]={'share_rows':int(g.shares_10k.notna().sum()),'control_rows':int(g.reverse_share_change_5d_lag1.notna().sum())}
        parts.append(g)
    return pd.concat(parts,ignore_index=True),{'share_fact_rows':len(shares),'SSE_targets':sorted(sh),'SZSE_targets':sorted(sz),'errors':errors[:20],'by_etf':coverage}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('request_path');a=ap.parse_args();req=base.loadj(ROOT/a.request_path);cfg=base.loadj(ROOT/req['config_path']);stage1=base.loadj(STAGE1)
    passed=list(stage1.get('passing_by_module',{}).get('margin') or stage1.get('passing_signals') or [])
    signals=[s for s in EXPECTED if s in passed]
    if signals!=EXPECTED:raise RuntimeError(f'Expected Stage1 margin passers missing: {signals}')
    targets=[x['code'] for x in cfg['targets']];panel=base.local_panel(req['data_start'],req['data_end'],targets);td=pd.DatetimeIndex(sorted(panel.date.unique()))
    margin,mcov=base.fetch_margin(td,req['data_start'],req['data_end']);panel=panel.merge(margin,left_on='date',right_index=True,how='left')
    panel,cov=attach_share(panel,cfg['targets'])
    cfg2=json.loads(json.dumps(cfg));cfg2['controls']=['mom5_lag1','mom20_lag1','reverse_share_change_5d_lag1']
    res=base.evaluate(panel,cfg2,req['folds'],signals);final=res['passing_signals']
    interp='PROMISING_MARGIN_INCREMENT_BEYOND_SHARE_FLOW' if final else 'NO_MARGIN_INCREMENT_BEYOND_VALIDATED_SHARE_FLOW_CONTROLS'
    payload={'schema_version':'1.0','generated_at':now_utc(),'mode':'RESEARCH_ONLY_MARGIN_SHARE_FLOW_FINAL_VALIDATION','objective':'Test whether Stage1-passing margin-financing signals retain stable incremental information after ETF own momentum and validated reverse 5d ETF share-flow control.','targets':cfg['targets'],'stage1_reference':'research/backtests/market_breadth_margin_stage1_validation.json','stage1_passing_signals':signals,'controls':cfg2['controls'],'point_in_time':{'margin':'T margin facts usable from T+1','etf_share_flow':'T ETF share facts usable from T+1','local':'momentum closes no later than D-1; forward return starts ETF open D'},'margin_coverage':mcov,'share_coverage':cov,'validation':res,'passing_signals':final,'research_interpretation':interp,'decision_eligible':False,'trade_signal':None,'trial_confirm':None,'portfolio_target':None,'master_override':False,'production_context_integration':False,'conversion_review_required':bool(final),'research_boundary':'Final statistical independence test only. A passer still requires formal research-conversion review before any current decision-context use; no direct trade action.'}
    atomic_json_write(OUT,payload);status={'generated_at':payload['generated_at'],'status':'PASS','research_interpretation':interp,'stage1_passing_signals':signals,'passing_signals':final,'share_coverage':cov,'decision_eligible':False,'trade_signal':None,'master_override':False,'conversion_review_required':bool(final)};atomic_json_write(STATUS,status);print(json.dumps(status,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
