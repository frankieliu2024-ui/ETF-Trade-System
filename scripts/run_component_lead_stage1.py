from __future__ import annotations
import argparse,json,math,os,time
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
import numpy as np
import pandas as pd
import requests
try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc
ROOT=Path(os.environ.get('ETF_SYSTEM_ROOT',Path(__file__).resolve().parents[1])).resolve()
DAILY=ROOT/'events/research/daily_features'

def loadj(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def r4(x):
    try: x=float(x)
    except Exception:return None
    return round(x,4) if math.isfinite(x) else None

def local_panel(start,end,targets):
    rows=[]; wanted=set(targets)
    for p in sorted(DAILY.glob('*.json')):
        d=p.stem
        if d<start or d>end: continue
        for x in loadj(p).get('features') or []:
            c=str(x.get('code') or '')
            if c in wanted and x.get('open') is not None and x.get('close') is not None:
                rows.append({'date':pd.Timestamp(d),'code':c,'open':float(x['open']),'close':float(x['close'])})
    df=pd.DataFrame(rows).drop_duplicates(['date','code']).sort_values(['code','date'])
    if df.empty: raise RuntimeError('No ETF history')
    out=[]
    for c,g in df.groupby('code'):
        g=g.copy().sort_values('date')
        g['mom5_lag1']=(g.close.shift(1)/g.close.shift(6)-1)*100
        g['mom20_lag1']=(g.close.shift(1)/g.close.shift(21)-1)*100
        g['etf_3d_lag1']=(g.close.shift(1)/g.close.shift(4)-1)*100
        for h in (1,3,5): g[f'fwd_{h}d']=(g.close.shift(-(h-1))/g.open-1)*100
        out.append(g)
    return pd.concat(out,ignore_index=True)

def yahoo(symbol,start,end):
    p1=int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())-10*86400
    p2=int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())+10*86400
    url=f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol,safe="")}'
    params={'period1':p1,'period2':p2,'interval':'1d','events':'history','includeAdjustedClose':'false'}
    last=None
    for k in range(4):
        try:
            z=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0 ETF-Trade-System research'},timeout=25);z.raise_for_status();r=z.json()['chart']['result'][0]
            ts=r.get('timestamp') or [];cl=((r.get('indicators',{}).get('quote') or [{}])[0].get('close') or [])
            rows=[]
            for i,t in enumerate(ts):
                if i<len(cl) and cl[i] is not None: rows.append({'date':pd.Timestamp(datetime.fromtimestamp(int(t),timezone.utc).date()),'close':float(cl[i])})
            df=pd.DataFrame(rows).drop_duplicates('date').sort_values('date')
            if len(df)<100: raise RuntimeError(f'short history {len(df)}')
            return df
        except Exception as e: last=e; time.sleep(k+1)
    raise RuntimeError(f'{symbol}: {last}')

def covered_mean(df,min_count):
    m=df.mean(axis=1,skipna=True)
    return m.where(df.notna().sum(axis=1)>=min_count)

def attach_leaders(panel,cfg,start,end):
    syms=sorted({s for t in cfg['targets'] for s in t['leaders']}); hist={s:yahoo(s,start,end) for s in syms}
    parts=[]; coverage={}
    for t in cfg['targets']:
        code=t['code'];g=panel[panel.code==code].copy().sort_values('date').set_index('date')
        series=[]
        for s in t['leaders']:
            x=hist[s].set_index('date').close.reindex(g.index)
            ret1=(x/x.shift(1)-1)*100; ret3=(x/x.shift(3)-1)*100
            series.append((s,ret1,ret3))
        r1=pd.concat([x[1] for x in series],axis=1);r3=pd.concat([x[2] for x in series],axis=1);minc=max(3,len(series)-1)
        g['leader_equal_1d_lag1']=covered_mean(r1,minc).shift(1)
        g['leader_equal_3d_lag1']=covered_mean(r3,minc).shift(1)
        denom=r3.notna().sum(axis=1);breadth=(r3.gt(0).sum(axis=1)/denom.replace(0,np.nan)*100).where(denom>=minc)
        g['leader_breadth_pos_3d_lag1']=breadth.shift(1)
        g['leader_minus_etf_3d_lag1']=g['leader_equal_3d_lag1']-g['etf_3d_lag1']
        coverage[code]={'leaders':t['leaders'],'rows_with_3d_basket':int(g['leader_equal_3d_lag1'].notna().sum())}
        parts.append(g.reset_index())
    return pd.concat(parts,ignore_index=True),coverage

def rank(s): return s.rank(method='average',pct=True)
def metric(df,sig,label,controls,minobs):
    x=df[[sig,label]+controls].dropna()
    if len(x)<minobs:return {'n':int(len(x)),'partial_rank_ic':None,'top_bottom_spread_pct_points':None}
    q=pd.DataFrame({c:rank(x[c]) for c in [sig,label]+controls},index=x.index)
    C=np.column_stack([np.ones(len(q))]+[q[c].to_numpy() for c in controls]);a=q[sig].to_numpy();b=q[label].to_numpy()
    ba,*_=np.linalg.lstsq(C,a,rcond=None);bb,*_=np.linalg.lstsq(C,b,rcond=None);ra=a-C@ba;rb=b-C@bb
    ic=None if np.std(ra)<=1e-12 or np.std(rb)<=1e-12 else float(np.corrcoef(ra,rb)[0,1])
    q1,q2=np.quantile(ra,[1/3,2/3]);y=x[label].to_numpy(float);sp=float(y[ra>=q2].mean()-y[ra<=q1].mean())
    return {'n':int(len(x)),'partial_rank_ic':r4(ic),'top_bottom_spread_pct_points':r4(sp)}
def evaluate(panel,cfg,folds):
    signals=cfg['candidate_signals'];controls=cfg['controls'];minobs=int(cfg['validation']['minimum_observations_per_fold']);fr=[]
    for f in folds:
        z=panel[panel.date.between(pd.Timestamp(f['test_start']),pd.Timestamp(f['test_end']))]
        for t in cfg['targets']:
            e=z[z.code==t['code']]
            for h in cfg['horizons_trading_days']:
                fr.append({'fold':f['name'],'code':t['code'],'horizon_trading_days':h,'metrics':{s:metric(e,s,f'fwd_{h}d',controls,minobs) for s in signals}})
    out={};passing=[];v=cfg['validation']
    for s in signals:
        hs={};qc=0
        for h in cfg['horizons_trading_days']:
            cells=[];by=defaultdict(list)
            for r in fr:
                if r['horizon_trading_days']==h and r['metrics'][s]['partial_rank_ic'] is not None:
                    m={'code':r['code'],'fold':r['fold'],**r['metrics'][s]};cells.append(m);by[r['code']].append(m)
            stable=[];es={}
            for c,rs in sorted(by.items()):
                vals=[x['partial_rank_ic'] for x in rs];pos=sum(x>0 for x in vals);mi=float(np.mean(vals));sp=[x['top_bottom_spread_pct_points'] for x in rs if x['top_bottom_spread_pct_points'] is not None]
                if pos>=v['required_positive_folds_per_etf'] and mi>0:stable.append(c)
                es[c]={'fold_count':len(vals),'positive_folds':pos,'mean_partial_rank_ic':r4(mi),'mean_top_bottom_spread_pct_points':r4(np.mean(sp)) if sp else None}
            vals=[x['partial_rank_ic'] for x in cells];sp=[x['top_bottom_spread_pct_points'] for x in cells if x['top_bottom_spread_pct_points'] is not None];mi=float(np.mean(vals)) if vals else float('nan');ms=float(np.mean(sp)) if sp else float('nan');pc=sum(x>0 for x in vals)
            ok=len(stable)>=v['minimum_stable_etfs_per_horizon'] and pc>=v['minimum_positive_cells_per_horizon'] and math.isfinite(mi) and mi>v['minimum_partial_rank_ic'] and (not v.get('require_positive_top_bottom_spread',True) or (math.isfinite(ms) and ms>0))
            if ok:qc+=1
            hs[str(h)]={'cell_count':len(vals),'positive_cells':pc,'mean_partial_rank_ic':r4(mi),'mean_top_bottom_spread_pct_points':r4(ms),'stable_etfs':stable,'etf_summary':es,'qualifies':bool(ok)}
        passed=qc>=v['required_qualifying_horizons'];out[s]={'qualifying_horizon_count':qc,'stage_pass':passed,'horizons':hs}
        if passed:passing.append(s)
    return {'controls':controls,'fold_results':fr,'signal_summary':out,'passing_signals':passing}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('request_path');a=ap.parse_args();req=loadj(ROOT/a.request_path);cfg=loadj(ROOT/req['config_path'])
    p=local_panel(req['data_start'],req['data_end'],[x['code'] for x in cfg['targets']]);p,cov=attach_leaders(p,cfg,req['data_start'],req['data_end']);res=evaluate(p,cfg,req['folds']);passed=res['passing_signals']
    interpretation='PROMISING_STATIC_BASKET_INCREMENT_REQUIRES_HISTORICAL_MEMBERSHIP_VALIDATION' if passed else 'NO_STABLE_INCREMENT_STAGE1'
    payload={'schema_version':'1.0','generated_at':now_utc(),'mode':cfg['mode'],'objective':cfg['objective'],'targets':cfg['targets'],'point_in_time_rule':'For ETF trading date D, all leader signals use closes no later than D-1; forward returns start at ETF open on D.','survivorship_bias_warning':cfg['method_note'],'coverage':cov,'validation':res,'research_interpretation':interpretation,'passing_signals':passed,'decision_eligible':False,'trade_signal':None,'trial_confirm':None,'portfolio_target':None,'master_override':False,'production_context_integration':False,'historical_membership_validation_required':bool(passed),'share_flow_control_required':bool(passed)}
    atomic_json_write(ROOT/req['output'],payload);status={'generated_at':payload['generated_at'],'status':'PASS','research_interpretation':interpretation,'passing_signals':passed,'target_count':len(cfg['targets']),'decision_eligible':False,'trade_signal':None,'master_override':False};atomic_json_write(ROOT/req['status_output'],status);print(json.dumps(status,ensure_ascii=False));return 0
if __name__=='__main__': raise SystemExit(main())
