#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from calendar import monthcalendar, FRIDAY
from pathlib import Path
import pandas as pd
import akshare as ak

PRODUCTS={"IF":"000300","IC":"000905","IM":"000852"}

def third_friday(year:int,month:int)->pd.Timestamp:
    weeks=monthcalendar(year,month)
    fridays=[w[FRIDAY] for w in weeks if w[FRIDAY]]
    return pd.Timestamp(year=year,month=month,day=fridays[2])

def contract_month(contract:str)->tuple[int,int]:
    m=re.fullmatch(r"(?:CFFEX)?(IF|IC|IM)(\d{4})",str(contract).upper().replace(".",""))
    if not m: raise ValueError(f"unsupported contract code: {contract}")
    yy=int(m.group(2)[:2]); mm=int(m.group(2)[2:]); return 2000+yy,mm

def fetch_futures(start:str,end:str)->pd.DataFrame:
    parts=[]
    for year in range(int(start[:4]),int(end[:4])+1):
        s=max(start,f"{year}0101"); e=min(end,f"{year}1231")
        if s>e: continue
        x=ak.get_futures_daily(start_date=s,end_date=e,market="CFFEX").copy()
        if x.empty: continue
        x=x.rename(columns={"symbol":"contract","date":"trade_date","settle":"settlement","variety":"product"})
        x["product"]=x["product"].astype(str).str.upper().str.strip()
        x=x[x["product"].isin(PRODUCTS)].copy()
        parts.append(x[["trade_date","product","contract","close","settlement","volume","open_interest"]])
    if not parts: raise RuntimeError("no CFFEX futures history returned")
    f=pd.concat(parts,ignore_index=True).drop_duplicates(["trade_date","product","contract"])
    f["trade_date"]=pd.to_datetime(f["trade_date"])
    for c in ["close","settlement","volume","open_interest"]: f[c]=pd.to_numeric(f[c],errors="coerce")
    f=f.dropna(subset=["close","settlement","volume","open_interest"])
    all_days=pd.DatetimeIndex(sorted(f["trade_date"].unique()))
    expiries={}
    for contract in sorted(f["contract"].astype(str).unique()):
        try: y,m=contract_month(contract)
        except ValueError: continue
        nominal=third_friday(y,m)
        candidates=all_days[all_days>=nominal]
        candidates=candidates[(candidates.year==y)&(candidates.month==m)]
        if len(candidates): expiries[contract]=candidates[0]
    f["expiry_date"]=f["contract"].map(expiries)
    f=f.dropna(subset=["expiry_date"])
    return f.sort_values(["trade_date","product","contract"])

def fetch_spot(start:str,end:str)->pd.DataFrame:
    rows=[]
    for p,code in PRODUCTS.items():
        x=ak.index_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end).copy()
        x=x.rename(columns={"日期":"trade_date","收盘":"spot_close"})
        if x.empty: raise RuntimeError(f"spot history empty for {p}/{code}")
        x["trade_date"]=pd.to_datetime(x["trade_date"]); x["spot_close"]=pd.to_numeric(x["spot_close"],errors="coerce")
        x=x.dropna(subset=["trade_date","spot_close"]); x["product"]=p
        rows.append(x[["trade_date","product","spot_close"]])
    return pd.concat(rows,ignore_index=True).drop_duplicates(["trade_date","product"]).sort_values(["trade_date","product"])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start",default="20240101"); ap.add_argument("--end",default="20260828"); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    f=fetch_futures(a.start,a.end); s=fetch_spot(a.start,a.end)
    f.to_csv(a.out/"cffex_contract_daily.csv",index=False); s.to_csv(a.out/"cffex_spot_daily.csv",index=False)
    q={"mode":"RESEARCH_ONLY_CFFEX_EXPIRY_AWARE_INPUTS","start":a.start,"end":a.end,"futures_rows":len(f),"spot_rows":len(s),"contracts":{p:int(f[f.product==p].contract.nunique()) for p in PRODUCTS},"date_range":{p:[f[f.product==p].trade_date.min().date().isoformat(),f[f.product==p].trade_date.max().date().isoformat()] for p in PRODUCTS},"expiry_rule":"third Friday of contract month; if non-trading, first CFFEX trading day after it within month, per published contract rule","production_context_integration":False,"trade_signal":None}
    (a.out/"input_qa.json").write_text(json.dumps(q,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(q,ensure_ascii=False))
if __name__=="__main__": raise SystemExit(main())
