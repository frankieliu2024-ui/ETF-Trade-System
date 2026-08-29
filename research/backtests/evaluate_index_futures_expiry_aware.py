#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import run_market_breadth_margin_stage1 as base
try:
    import run_market_breadth_margin_stage1_v2 as margin_v2
    FETCH_MARGIN=margin_v2.fetch_margin_with_fallback
except Exception:
    FETCH_MARGIN=base.fetch_margin

TARGETS=['561980','588000','159781','159992','515880','159326']
CORE={
 'IF_basis_change_5d':'basis_close_pct_change_5d',
 'IC_basis_change_5d':'basis_close_pct_change_5d',
 'IC_oi_change_1d':'open_interest_change_1d',
 'IM_basis_change_5d':'basis_close_pct_change_5d',
}

def rank(s): return s.rank(method='average',pct=True)
def r4(x):
    try:x=float(x)
    except Exception:return None
    return round(x,4) if math.isfinite(x) else None

def metric(df,sig,label,controls,minobs=60):
    x=df[[sig,label]+controls].dropna()
    if len(x)<minobs:return None
    q=pd.DataFrame({c:rank(x[c]) for c in [sig,label]+controls},index=x.index)
    C=np.column_stack([np.ones(len(q))]+[q[c].to_numpy() for c in controls])
    a=q[sig].to_numpy(); b=q[label].to_numpy()
    ba,*_=np.linalg.lstsq(C,a,rcond=None); bb,*_=np.linalg.lstsq(C,b,rcond=None)
    ra=a-C@ba; rb=b-C@bb
    if np.std(ra)<=1e-12 or np.std(rb)<=1e-12:return None
    return float(np.corrcoef(ra,rb)[0,1])

def load_etf(start,end):
    p=base.local_panel(start,end,TARGETS)
    return p.sort_values(['code','date']).reset_index(drop=True)

def add_margin(p,start,end):
    td=pd.DatetimeIndex(sorted(p.date.unique()))
    try:
        m,cov=FETCH_MARGIN(td,start,end)
        p=p.merge(m,left_on='date',right_index=True,how='left')
        return p,cov
    except Exception as e:
        return p,{'status':'UNAVAILABLE','error':f'{type(e).__name__}: {e}'[:300]}

def signal_frame(path:Path,product:str,column:str)->pd.DataFrame:
    s=pd.read_csv(path,parse_dates=['trade_date'])
    s=s[s['product'].eq(product)].sort_values('trade_date')[['trade_date',column,'days_to_expiry','roll_flag']].copy()
    s=s.rename(columns={column:'signal'})
    # T close fact enters only from next ETF trading date.
    s['signal']=s['signal'].shift(1); s['signal_source_date']=s['trade_date'].shift(1)
    return s

def summarize_mode(p,sel_path,mode,margin_cov):
    out={}; controls=['mom5_lag1','mom20_lag1']
    if 'margin_balance_5d_change_lag1' in p and p['margin_balance_5d_change_lag1'].notna().sum()>=100: controls.append('margin_balance_5d_change_lag1')
    years=sorted(p.date.dt.year.unique())
    for name,col in CORE.items():
        product=name.split('_')[0]; s=signal_frame(sel_path,product,col)
        z=p.merge(s,left_on='date',right_on='trade_date',how='left').drop(columns=['trade_date'])
        cells=[]
        for code in TARGETS:
            e=z[z.code==code]
            for h in (1,3,5):
                ic=metric(e,'signal',f'fwd_{h}d',controls)
                if ic is not None: cells.append({'code':code,'horizon':h,'ic':ic})
        vals=[c['ic'] for c in cells]
        n=len(vals); overall=float(np.median(vals)) if vals else np.nan; sign=float(np.mean(np.array(vals)>0)) if vals else np.nan
        mid=z.date.min()+(z.date.max()-z.date.min())/2
        half=[]
        for label,mask in [('early',z.date<=mid),('late',z.date>mid)]:
            hv=[]
            for code in TARGETS:
                e=z[mask & z.code.eq(code)]
                for h in (1,3,5):
                    ic=metric(e,'signal',f'fwd_{h}d',controls,minobs=30)
                    if ic is not None: hv.append(ic)
            half.append((label,float(np.median(hv)) if hv else np.nan))
        annual={}
        for y in years:
            av=[]
            for code in TARGETS:
                e=z[(z.date.dt.year==y)&z.code.eq(code)]
                for h in (1,3,5):
                    ic=metric(e,'signal',f'fwd_{h}d',controls,minobs=30)
                    if ic is not None: av.append(ic)
            annual[str(y)]=r4(np.median(av)) if av else None
        early=dict(half)['early']; late=dict(half)['late']; positive_years=sum(v is not None and v>0 for v in annual.values())
        supported=bool(n>=9 and overall>0.03 and sign>=2/3 and early>0 and late>0 and positive_years>=2)
        out[name]={'tests':n,'median_partial_rank_ic':r4(overall),'sign_consistency':r4(sign),'early_median_ic':r4(early),'late_median_ic':r4(late),'annual_median_ic':annual,'positive_years':positive_years,'supported':supported}
    return {'selection_mode':mode,'controls':controls,'margin_coverage':margin_cov,'signals':out}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--start',default='2024-01-01'); ap.add_argument('--end',default='2026-08-28'); a=ap.parse_args()
    p=load_etf(a.start,a.end); p,mcov=add_margin(p,a.start,a.end)
    near=summarize_mode(p,a.input_dir/'index_futures_expiry_aware_near.csv','NEAR',mcov)
    mainc=summarize_mode(p,a.input_dir/'index_futures_expiry_aware_main.csv','MAIN_PIT',mcov)
    support={k:any(x['signals'][k]['supported'] for x in (near,mainc)) for k in CORE}
    count=sum(support.values()); im_ok=support['IM_basis_change_5d']
    if count>=3 and im_ok: grade='PASS_EXPIRY_AWARE_ROBUST'
    elif count>=1: grade='PARTIAL_SUPPORT'
    else: grade='NO_STABLE_INCREMENT'
    payload={'schema_version':'1.0','mode':'RESEARCH_ONLY_INDEX_FUTURES_EXPIRY_AWARE_FINAL_VALIDATION','point_in_time':'T futures/spot/OI facts shifted to next ETF observation','pre_result_operational_rule':'signal support requires median partial rank IC > 0.03, sign consistency >= 2/3, positive early and late medians, and >=2 positive annual medians; PASS requires >=3 of 4 original signals including IM basis 5d','near':near,'main_pit':mainc,'cross_mode_support':support,'supported_count':count,'research_interpretation':grade,'decision_eligible':False,'production_context_integration':False,'trade_signal':None}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'research_interpretation':grade,'support':support},ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
