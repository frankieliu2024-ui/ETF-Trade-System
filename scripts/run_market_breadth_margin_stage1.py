from __future__ import annotations
import argparse,json,math,os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import akshare as ak
try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc
ROOT=Path(os.environ.get('ETF_SYSTEM_ROOT',Path(__file__).resolve().parents[1])).resolve()
DAILY=ROOT/'events/research/daily_features'
HEAD={'User-Agent':'Mozilla/5.0 ETF-Trade-System research','Referer':'https://quote.eastmoney.com/ztb/'}

def loadj(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def r4(x):
    try:x=float(x)
    except Exception:return None
    return round(x,4) if math.isfinite(x) else None

def local_panel(start,end,targets):
    rows=[];wanted=set(targets)
    for p in sorted(DAILY.glob('*.json')):
        d=p.stem
        if d<start or d>end:continue
        for x in loadj(p).get('features') or []:
            c=str(x.get('code') or '')
            if c in wanted and x.get('open') is not None and x.get('close') is not None:
                rows.append({'date':pd.Timestamp(d),'code':c,'open':float(x['open']),'close':float(x['close'])})
    df=pd.DataFrame(rows).drop_duplicates(['date','code']).sort_values(['code','date'])
    if df.empty:raise RuntimeError('No ETF history')
    out=[]
    for c,g in df.groupby('code'):
        g=g.copy().sort_values('date')
        g['mom5_lag1']=(g.close.shift(1)/g.close.shift(6)-1)*100
        g['mom20_lag1']=(g.close.shift(1)/g.close.shift(21)-1)*100
        for h in (1,3,5):g[f'fwd_{h}d']=(g.close.shift(-(h-1))/g.open-1)*100
        out.append(g)
    return pd.concat(out,ignore_index=True)

def pool_count(endpoint,date):
    params={'ut':'7eea3edcaed734bea9cbfc24409ed989','dpt':'wz.ztzt','Pageindex':'0','pagesize':'10000','date':date.replace('-','')}
    try:
        r=requests.get(endpoint,params=params,headers=HEAD,timeout=12);r.raise_for_status();z=r.json();data=z.get('data')
        if data is None:return None
        pool=data.get('pool')
        return None if pool is None else int(len(pool))
    except Exception:return None

def breadth_probe(cfg):
    eps={'up':'https://push2ex.eastmoney.com/getTopicZTPool','down':'https://push2ex.eastmoney.com/getTopicDTPool','broken':'https://push2ex.eastmoney.com/getTopicZBPool'}
    details=[];ok=0
    for d in cfg['modules']['breadth']['historical_probe_dates']:
        vals={k:pool_count(u,d) for k,u in eps.items()};success=all(v is not None for v in vals.values());ok+=int(success);details.append({'date':d,**vals,'success':success})
    return ok>=int(cfg['modules']['breadth']['minimum_probe_successes']),details,eps

def fetch_breadth(trading_dates,cfg):
    available,probe,eps=breadth_probe(cfg)
    if not available:return pd.DataFrame(index=trading_dates),{'status':'INSUFFICIENT_HISTORY','probe':probe}
    def one(d):
        ds=pd.Timestamp(d).strftime('%Y-%m-%d');vals={k:pool_count(u,ds) for k,u in eps.items()};return pd.Timestamp(d),vals
    rows=[]
    with ThreadPoolExecutor(max_workers=10) as ex:
        fs=[ex.submit(one,d) for d in trading_dates]
        for f in as_completed(fs):
            d,v=f.result()
            if all(x is not None for x in v.values()):rows.append({'date':d,**v})
    x=pd.DataFrame(rows).set_index('date').sort_index().reindex(trading_dates)
    if x.empty:return x,{'status':'INSUFFICIENT_HISTORY','probe':probe,'rows':0}
    x['limit_net_strength']=(x['up']-x['down'])/(x['up']+x['down']+1)*100
    x['seal_quality']=(x['up']-x['broken'])/(x['up']+x['broken']+1)*100
    x['limit_up_5d_change']=x['up']-x['up'].shift(5)
    x=x[['limit_net_strength','seal_quality','limit_up_5d_change']].shift(1)
    x.columns=[c+'_lag1' for c in x.columns]
    return x,{'status':'PASS','probe':probe,'rows':int(x.notna().any(axis=1).sum())}

def fetch_margin(trading_dates,start,end):
    sse=ak.stock_margin_sse(start_date=start.replace('-',''),end_date=end.replace('-',''))
    sse=sse.rename(columns={'信用交易日期':'date','融资余额':'balance','融资买入额':'buy'})
    sse['date']=pd.to_datetime(sse['date']);sse=sse[['date','balance','buy']].dropna().groupby('date',as_index=False).sum()
    dates=[pd.Timestamp(d) for d in trading_dates]
    def sz(d):
        ds=d.strftime('%Y%m%d')
        try:
            z=ak.stock_margin_szse(date=ds)
            if z.empty:return d,None,None
            return d,float(pd.to_numeric(z['融资余额'],errors='coerce').sum()),float(pd.to_numeric(z['融资买入额'],errors='coerce').sum())
        except Exception:return d,None,None
    rs=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs=[ex.submit(sz,d) for d in dates]
        for f in as_completed(fs):
            d,b,u=f.result()
            if b is not None and u is not None:rs.append({'date':d,'balance_sz':b,'buy_sz':u})
    szdf=pd.DataFrame(rs)
    if szdf.empty:raise RuntimeError('SZSE margin history unavailable')
    m=pd.DataFrame(index=pd.DatetimeIndex(dates,name='date')).join(sse.set_index('date')).join(szdf.set_index('date'))
    m['balance_total']=m['balance']+m['balance_sz'];m['buy_total']=m['buy']+m['buy_sz']
    m['margin_balance_1d_change']=(m.balance_total/m.balance_total.shift(1)-1)*100
    m['margin_balance_5d_change']=(m.balance_total/m.balance_total.shift(5)-1)*100
    m['margin_buy_5d_vs20']=(m.buy_total.rolling(5,min_periods=5).mean()/m.buy_total.rolling(20,min_periods=20).mean()-1)*100
    q=m[['margin_balance_1d_change','margin_balance_5d_change','margin_buy_5d_vs20']].shift(1)
    q.columns=[c+'_lag1' for c in q.columns]
    cov={'status':'PASS' if q.notna().all(axis=1).sum()>=100 else 'DEGRADED','rows_complete':int(q.notna().all(axis=1).sum()),'sse_rows':int(len(sse)),'szse_rows':int(len(szdf))}
    return q,cov

def rank(s):return s.rank(method='average',pct=True)
def metric(df,sig,label,controls,minobs):
    x=df[[sig,label]+controls].dropna()
    if len(x)<minobs:return {'n':int(len(x)),'partial_rank_ic':None,'top_bottom_spread_pct_points':None}
    q=pd.DataFrame({c:rank(x[c]) for c in [sig,label]+controls},index=x.index)
    C=np.column_stack([np.ones(len(q))]+[q[c].to_numpy() for c in controls]);a=q[sig].to_numpy();b=q[label].to_numpy()
    ba,*_=np.linalg.lstsq(C,a,rcond=None);bb,*_=np.linalg.lstsq(C,b,rcond=None);ra=a-C@ba;rb=b-C@bb
    ic=None if np.std(ra)<=1e-12 or np.std(rb)<=1e-12 else float(np.corrcoef(ra,rb)[0,1])
    q1,q2=np.quantile(ra,[1/3,2/3]);y=x[label].to_numpy(float);sp=float(y[ra>=q2].mean()-y[ra<=q1].mean())
    return {'n':int(len(x)),'partial_rank_ic':r4(ic),'top_bottom_spread_pct_points':r4(sp)}
def evaluate(panel,cfg,folds,signals):
    controls=cfg['controls'];v=cfg['validation'];fr=[]
    for f in folds:
        z=panel[panel.date.between(pd.Timestamp(f['test_start']),pd.Timestamp(f['test_end']))]
        for t in cfg['targets']:
            e=z[z.code==t['code']]
            for h in cfg['horizons_trading_days']:
                fr.append({'fold':f['name'],'code':t['code'],'horizon_trading_days':h,'metrics':{s:metric(e,s,f'fwd_{h}d',controls,int(v['minimum_observations_per_fold'])) for s in signals}})
    out={};passing=[]
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
    targets=[x['code'] for x in cfg['targets']];p=local_panel(req['data_start'],req['data_end'],targets);td=pd.DatetimeIndex(sorted(p.date.unique()))
    breadth,bcov=fetch_breadth(td,cfg);margin,mcov=fetch_margin(td,req['data_start'],req['data_end']);fac=breadth.join(margin,how='outer')
    p=p.merge(fac,left_on='date',right_index=True,how='left');signals=list(cfg['modules']['margin']['signals'])
    if bcov['status']=='PASS':signals=list(cfg['modules']['breadth']['signals'])+signals
    res=evaluate(p,cfg,req['folds'],signals);passing=res['passing_signals'];bset=set(cfg['modules']['breadth']['signals']);mset=set(cfg['modules']['margin']['signals'])
    passing_by_module={'breadth':[s for s in passing if s in bset],'margin':[s for s in passing if s in mset]}
    if passing:interp='PROMISING_INCREMENT_REQUIRES_SHARE_FLOW_CONTROL'
    elif bcov['status']!='PASS':interp='NO_STABLE_MARGIN_INCREMENT_BREADTH_HISTORY_INSUFFICIENT'
    else:interp='NO_STABLE_INCREMENT_STAGE1'
    payload={'schema_version':'1.0','generated_at':now_utc(),'mode':cfg['mode'],'objective':cfg['objective'],'targets':cfg['targets'],'point_in_time':cfg['point_in_time'],'module_coverage':{'breadth':bcov,'margin':mcov},'validation':res,'passing_signals':passing,'passing_by_module':passing_by_module,'research_interpretation':interp,'share_flow_control_required':bool(passing),'decision_eligible':False,'trade_signal':None,'trial_confirm':None,'portfolio_target':None,'master_override':False,'production_context_integration':False,'research_boundary':cfg['research_boundary']}
    atomic_json_write(ROOT/req['output'],payload);status={'generated_at':payload['generated_at'],'status':'PASS' if mcov['status']=='PASS' else 'DEGRADED','research_interpretation':interp,'breadth_status':bcov['status'],'margin_status':mcov['status'],'passing_signals':passing,'passing_by_module':passing_by_module,'decision_eligible':False,'trade_signal':None,'master_override':False};atomic_json_write(ROOT/req['status_output'],status);print(json.dumps(status,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
