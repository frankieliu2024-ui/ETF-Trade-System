#!/usr/bin/env python3
from __future__ import annotations

import argparse, base64, json, math, sys, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from gmssl import sm3, func
from gmssl.sm4 import CryptSM4, SM4_ENCRYPT

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import run_market_breadth_margin_stage1 as base
try:
    import run_market_breadth_margin_stage1_v2 as margin_v2
    FETCH_MARGIN=margin_v2.fetch_margin_with_fallback
except Exception:
    FETCH_MARGIN=base.fetch_margin

TARGET='561980'
SECRET='d274d06273f96656442b0728316026a1'
ENVELOPE_REQUEST_ID='01ab90a0d4ce45f3bb35b919099d9da1'
HOST='https://static.cmfchina.com'
PCF_PATH='/ws-business-server/fund/getEtfStockList'
REGULAR_EFFECTIVE={'2023-12-18','2024-06-17','2024-12-16','2025-06-16','2025-12-15','2026-06-15'}
HEAD={'User-Agent':'Mozilla/5.0 ETF-Trade-System research','Referer':'https://static.cmfchina.com/web/fundDetail/561980/'}
PRICE_HEAD={'User-Agent':'Mozilla/5.0 ETF-Trade-System research','Referer':'https://gu.qq.com/'}


def r4(x):
    try:x=float(x)
    except Exception:return None
    return round(x,4) if math.isfinite(x) else None

def sm3_hex(b:bytes)->str:return sm3.sm3_hash(func.bytes_to_list(b))
def encrypted_request(payload:dict):
    data=dict(payload); data['siteno']='web'; data['merchantId']=0; data['request_id']=str(uuid.uuid4())
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
    key=sm3_hex(SECRET.encode())[:16].encode('ascii')
    crypt=CryptSM4(); crypt.set_key(key,SM4_ENCRYPT)
    b64=base64.b64encode(crypt.crypt_ecb(raw)).decode()
    sig=sm3_hex(f'secret={SECRET}data={b64}request_id={ENVELOPE_REQUEST_ID}encrypted=true'.encode())
    return {'data':b64,'request_id':ENVELOPE_REQUEST_ID,'encrypted':'true'},{**HEAD,'tk-trans-merchant-key':'thinkive','tk-trans-signature':sig}
def fetch_pcf(date:str)->dict:
    payload={'startDate':date,'productCode':TARGET,'pageNum':1,'pageSize':1000,'isPreview':'0'}
    params,headers=encrypted_request(payload)
    r=requests.get(HOST+PCF_PATH,params=params,headers=headers,timeout=20); r.raise_for_status(); z=r.json()
    data=z.get('data') or {}; rows=data.get('list') or []
    if z.get('code')!=0 or len(rows)<30: raise RuntimeError(f'PCF invalid {date}: code={z.get("code")} rows={len(rows)}')
    norm=[]
    for x in rows:
        code=str(x.get('stockCode') or '')
        if len(code)!=6: continue
        td=pd.to_numeric(x.get('tdAmount'),errors='coerce'); qty=pd.to_numeric(x.get('stockNum'),errors='coerce')
        norm.append({'stockCode':code,'stockName':x.get('stockName'),'stockNum':None if pd.isna(qty) else float(qty),'tdAmount':0.0 if pd.isna(td) else float(td),'cashFlag':x.get('cashFlag'),'annDate':x.get('annDate')})
    if len(norm)<30: raise RuntimeError(f'PCF normalized rows insufficient {date}: {len(norm)}')
    return {'date':date,'total':len(norm),'rows':norm}

def symbol(code):return ('sh' if code.startswith('6') else 'sz')+code
def fetch_price(code,start,end):
    sym=symbol(code); url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'; params={'param':f'{sym},day,{start},{end},1400,qfq'}
    r=requests.get(url,params=params,headers=PRICE_HEAD,timeout=25); r.raise_for_status(); data=((r.json().get('data') or {}).get(sym) or {})
    rows=data.get('qfqday') or data.get('day') or []; out=[]
    for p in rows:
        if len(p)>=3: out.append({'date':pd.Timestamp(p[0]),'close':pd.to_numeric(p[2],errors='coerce')})
    x=pd.DataFrame(out).dropna().drop_duplicates('date').sort_values('date')
    if len(x)<100: raise RuntimeError(f'price insufficient {code}: {len(x)}')
    return x

def rank(s):return s.rank(method='average',pct=True)
def metric(df,sig,label,controls,minobs=50):
    x=df[[sig,label]+controls].dropna()
    if len(x)<minobs:return {'n':int(len(x)),'ic':None}
    q=pd.DataFrame({c:rank(x[c]) for c in [sig,label]+controls},index=x.index)
    C=np.column_stack([np.ones(len(q))]+[q[c].to_numpy() for c in controls]); a=q[sig].to_numpy(); b=q[label].to_numpy()
    ba,*_=np.linalg.lstsq(C,a,rcond=None); bb,*_=np.linalg.lstsq(C,b,rcond=None); ra=a-C@ba; rb=b-C@bb
    ic=None if np.std(ra)<=1e-12 or np.std(rb)<=1e-12 else float(np.corrcoef(ra,rb)[0,1])
    return {'n':int(len(x)),'ic':r4(ic)}
def evaluate(panel,controls):
    sig='leader_minus_etf_3d_lag1'; full=[]; early=[]; late=[]; annual={}
    mid=panel.date.min()+(panel.date.max()-panel.date.min())/2
    for h in (1,3,5):
        full.append(metric(panel,sig,f'fwd_{h}d',controls,50)); early.append(metric(panel[panel.date<=mid],sig,f'fwd_{h}d',controls,30)); late.append(metric(panel[panel.date>mid],sig,f'fwd_{h}d',controls,30))
    for y in sorted(panel.date.dt.year.unique()):
        vals=[]
        for h in (1,3,5):
            m=metric(panel[panel.date.dt.year==y],sig,f'fwd_{h}d',controls,30)
            if m['ic'] is not None: vals.append(m['ic'])
        annual[str(y)]=r4(np.median(vals)) if vals else None
    fv=[m['ic'] for m in full if m['ic'] is not None]; ev=[m['ic'] for m in early if m['ic'] is not None]; lv=[m['ic'] for m in late if m['ic'] is not None]
    med=float(np.median(fv)) if fv else np.nan; em=float(np.median(ev)) if ev else np.nan; lm=float(np.median(lv)) if lv else np.nan
    py=sum(v is not None and v>0 for v in annual.values())
    passed=bool(len(fv)==3 and med>0.03 and em>0 and lm>0 and py>=2)
    return {'controls':controls,'full_by_horizon':full,'median_partial_rank_ic':r4(med),'early_median_ic':r4(em),'late_median_ic':r4(lm),'annual_median_ic':annual,'positive_years':py,'supported':passed}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--start',default='2024-01-01'); ap.add_argument('--end',default='2026-08-28'); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    etf=base.local_panel(a.start,a.end,[TARGET]).sort_values('date'); dates=[pd.Timestamp(d) for d in sorted(etf.date.unique())]
    fetch_dates=sorted(set([d.strftime('%Y-%m-%d') for d in dates]+['2023-12-18']))
    pcfs={}; errors=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        fut={ex.submit(fetch_pcf,d):d for d in fetch_dates}
        for f in as_completed(fut):
            d=fut[f]
            try:pcfs[d]=f.result()
            except Exception as exc:errors.append({'date':d,'error':f'{type(exc).__name__}: {exc}'[:300]})
    coverage=len(pcfs)/len(fetch_dates)
    if coverage<0.98 or any(d not in pcfs for d in REGULAR_EFFECTIVE):
        payload={'mode':'RESEARCH_ONLY_561980_INDEX_REBALANCE_PIT','research_interpretation':'TERMINATED_INSUFFICIENT_INDEX_MEMBERSHIP','pcf_coverage':coverage,'pcf_errors':errors,'decision_eligible':False,'production_context_integration':False,'trade_signal':None}
        (a.out/'561980_index_rebalance_pit_validation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False)); return 0
    # Exact component-set change points from daily official PCF plus mandatory regular review effective dates.
    change=[]; prev=None
    for d in fetch_dates:
        if d not in pcfs: continue
        rows=pcfs[d]['rows']; comp=tuple(sorted(x['stockCode'] for x in rows)); forced=d in REGULAR_EFFECTIVE
        if comp!=prev or forced:
            ranked=sorted(rows,key=lambda x:(x['tdAmount'],x['stockCode']),reverse=True)
            top5=[x['stockCode'] for x in ranked[:5]]
            change.append({'effective_date':d,'reason':'REGULAR_REVIEW' if forced else ('INITIAL' if prev is None else 'OBSERVED_COMPONENT_CHANGE'),'component_count':len(comp),'component_hash':str(hash(comp)),'top5':top5,'top5_detail':[{'code':x['stockCode'],'name':x['stockName'],'tdAmount':x['tdAmount'],'stockNum':x['stockNum']} for x in ranked[:5]]})
        prev=comp
    # Build top5 assignment for each ETF trading day.
    cp=pd.DataFrame(change); cp['effective_date']=pd.to_datetime(cp.effective_date)
    all_leaders=sorted({c for x in change for c in x['top5']}); prices={}; price_errors=[]
    for c in all_leaders:
        try:prices[c]=fetch_price(c,'2023-12-01',a.end)
        except Exception as exc:price_errors.append({'code':c,'error':f'{type(exc).__name__}: {exc}'[:300]})
    rows=[]; e=etf.reset_index(drop=True); edates=pd.DatetimeIndex(e.date)
    aligned={c:prices[c].set_index('date')['close'].reindex(edates).ffill(limit=5) for c in prices}
    for i,d in enumerate(edates):
        if i<21:continue
        valid=cp[cp.effective_date<=d]
        if valid.empty:continue
        top5=valid.iloc[-1].top5
        if any(c not in aligned for c in top5):continue
        rs=[]
        for c in top5:
            s=aligned[c]; v0,v3=s.iloc[i-1],s.iloc[i-4]
            if pd.isna(v0) or pd.isna(v3) or v3<=0:break
            rs.append((v0/v3-1)*100)
        if len(rs)!=5:continue
        etf3=(float(e.close.iloc[i-1])/float(e.close.iloc[i-4])-1)*100
        rows.append({'date':d,'code':TARGET,'top5':','.join(top5),'leader_equal_3d_lag1':float(np.mean(rs)),'leader_minus_etf_3d_lag1':float(np.mean(rs)-etf3)})
    sig=pd.DataFrame(rows); panel=e.merge(sig,on=['date','code'],how='inner')
    margin_cov={'status':'UNAVAILABLE'}
    try:
        m,margin_cov=FETCH_MARGIN(pd.DatetimeIndex(sorted(e.date.unique())),a.start,a.end); panel=panel.merge(m,left_on='date',right_index=True,how='left')
    except Exception as exc:margin_cov={'status':'UNAVAILABLE','error':f'{type(exc).__name__}: {exc}'[:300]}
    base_controls=['mom5_lag1','mom20_lag1']; base_result=evaluate(panel,base_controls)
    margin_controls=base_controls+['margin_balance_5d_change_lag1']
    margin_result=evaluate(panel,margin_controls) if 'margin_balance_5d_change_lag1' in panel and panel.margin_balance_5d_change_lag1.notna().sum()>=100 else {'controls':margin_controls,'supported':False,'status':'INSUFFICIENT_MARGIN_CONTROL'}
    grade='PASS_561980_INDEX_REBALANCE_PIT' if base_result['supported'] and margin_result.get('supported') else ('PARTIAL_SUPPORT' if base_result['median_partial_rank_ic'] is not None and base_result['median_partial_rank_ic']>0 else 'NO_STABLE_INCREMENT')
    # Detect non-regular observed component changes for audit; these are exact PCF dates, not guessed temporary-adjustment dates.
    temporary=[x for x in change if x['reason']=='OBSERVED_COMPONENT_CHANGE']
    payload={'schema_version':'1.0','mode':'RESEARCH_ONLY_561980_INDEX_REBALANCE_PIT_VALIDATION','objective':'Retest the sole #61 561980 three-day component-vs-ETF lead using exact official daily PCF membership, forcing regular CSI 931865 review effective dates and capturing any other observed component changes.','point_in_time':'PCF membership for D is public before D trading; signal uses constituent closes no later than D-1; ETF forward return starts at D open.','top5_definition':'At each component change or regular review effective date, rank the official PCF basket by tdAmount (creation-unit reference value) and hold that top5 until the next detected component change/review.','pre_registered_gate':'median partial rank IC > 0.03, early and late medians > 0, at least two positive annual medians, and the same criteria after adding margin_balance_5d_change_lag1 control.','pcf_source':HOST+PCF_PATH,'pcf_coverage':r4(coverage),'pcf_dates_requested':len(fetch_dates),'pcf_dates_ok':len(pcfs),'pcf_errors':errors,'regular_effective_dates':sorted(REGULAR_EFFECTIVE),'change_points':change,'observed_nonregular_component_changes':temporary,'leader_codes':all_leaders,'price_source':'Tencent IFZQ qfq daily kline','price_errors':price_errors,'panel_rows':int(len(panel)),'base_control_result':base_result,'margin_control_coverage':margin_cov,'margin_control_result':margin_result,'research_interpretation':grade,'decision_eligible':False,'production_context_integration':False,'can_generate_decision_independently':False,'trade_signal':None}
    (a.out/'561980_index_rebalance_pit_validation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cp.to_csv(a.out/'561980_pcf_change_points.csv',index=False); panel.to_csv(a.out/'561980_index_rebalance_signal_panel.csv',index=False)
    print(json.dumps({'research_interpretation':grade,'pcf_coverage':payload['pcf_coverage'],'change_point_count':len(change),'nonregular_change_count':len(temporary),'leader_codes':all_leaders,'panel_rows':len(panel),'base':base_result,'margin':margin_result},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
