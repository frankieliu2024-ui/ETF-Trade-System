#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, zipfile
from calendar import monthcalendar, FRIDAY
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO, StringIO
from pathlib import Path
import pandas as pd
import requests
import akshare as ak

PRODUCTS={"IF":"000300","IC":"000905","IM":"000852"}
HEAD={"User-Agent":"Mozilla/5.0 ETF-Trade-System research"}

def third_friday(year:int,month:int)->pd.Timestamp:
    weeks=monthcalendar(year,month); fridays=[w[FRIDAY] for w in weeks if w[FRIDAY]]
    return pd.Timestamp(year=year,month=month,day=fridays[2])

def contract_month(contract:str)->tuple[int,int]:
    m=re.fullmatch(r"(IF|IC|IM)(\d{4})",str(contract).upper().strip())
    if not m: raise ValueError(f"unsupported contract code: {contract}")
    return 2000+int(m.group(2)[:2]),int(m.group(2)[2:])

def month_keys(start:str,end:str):
    a=pd.Timestamp(start); b=pd.Timestamp(end)
    for x in pd.period_range(a,b,freq="M"): yield x.strftime("%Y%m")

def parse_daily_csv(raw:bytes,date:str)->pd.DataFrame:
    text=raw.decode("gb2312",errors="ignore"); x=pd.read_csv(StringIO(text))
    if "合约代码" not in x.columns: return pd.DataFrame()
    x=x[~x["合约代码"].astype(str).isin(["小计","合计"])].copy()
    x=x[~x["合约代码"].astype(str).str.contains("IO|MO|HO",regex=True)].copy()
    if x.shape[1]<11: return pd.DataFrame()
    y=pd.DataFrame({"contract":x.iloc[:,0].astype(str).str.strip(),"volume":x.iloc[:,4],"open_interest":x.iloc[:,6],"close":x.iloc[:,8],"settlement":x.iloc[:,9]})
    y["trade_date"]=pd.Timestamp(date); y["product"]=y["contract"].str.extract(r"^([A-Za-z]+)",expand=False).str.upper()
    return y[y["product"].isin(PRODUCTS)]

def fetch_month(ym:str,a:pd.Timestamp,b:pd.Timestamp):
    url=f"https://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
    for attempt in range(3):
        try:
            r=requests.get(url,headers=HEAD,timeout=30); r.raise_for_status(); out=[]
            with zipfile.ZipFile(BytesIO(r.content)) as z:
                for name in z.namelist():
                    m=re.fullmatch(r"(\d{8})_1\.csv",name.split("/")[-1])
                    if not m: continue
                    d=pd.Timestamp(m.group(1))
                    if d<a or d>b: continue
                    q=parse_daily_csv(z.read(name),m.group(1))
                    if not q.empty: out.append(q)
            return ym,out
        except Exception:
            if attempt==2: raise
    return ym,[]

def fetch_futures(start:str,end:str)->pd.DataFrame:
    parts=[]; a=pd.Timestamp(start); b=pd.Timestamp(end); months=list(month_keys(start,end))
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs={ex.submit(fetch_month,ym,a,b):ym for ym in months}
        for f in as_completed(fs):
            ym,out=f.result(); parts.extend(out); print(f"fetched {ym}: {sum(len(x) for x in out)} rows",flush=True)
    if not parts: raise RuntimeError("no CFFEX contract history returned from monthly archives")
    f=pd.concat(parts,ignore_index=True).drop_duplicates(["trade_date","product","contract"])
    for c in ["close","settlement","volume","open_interest"]: f[c]=pd.to_numeric(f[c],errors="coerce")
    f=f.dropna(subset=["close","settlement","volume","open_interest"])
    all_days=pd.DatetimeIndex(sorted(f["trade_date"].unique())); expiries={}
    for contract in sorted(f["contract"].unique()):
        try:y,m=contract_month(contract)
        except ValueError:continue
        nominal=third_friday(y,m); candidates=all_days[(all_days>=nominal)&(all_days.year==y)&(all_days.month==m)]
        if len(candidates):expiries[contract]=candidates[0]
    f["expiry_date"]=f["contract"].map(expiries)
    missing_expiry=sorted(f.loc[f.expiry_date.isna(),"contract"].unique().tolist())
    if missing_expiry: raise RuntimeError(f"expiry mapping missing for contracts: {missing_expiry[:20]}")
    return f.sort_values(["trade_date","product","contract"])

def fetch_spot(start:str,end:str)->pd.DataFrame:
    rows=[]
    for p,code in PRODUCTS.items():
        x=ak.index_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end).copy().rename(columns={"日期":"trade_date","收盘":"spot_close"})
        if x.empty: raise RuntimeError(f"spot history empty for {p}/{code}")
        x["trade_date"]=pd.to_datetime(x["trade_date"]); x["spot_close"]=pd.to_numeric(x["spot_close"],errors="coerce"); x=x.dropna(subset=["trade_date","spot_close"]); x["product"]=p; rows.append(x[["trade_date","product","spot_close"]])
    return pd.concat(rows,ignore_index=True).drop_duplicates(["trade_date","product"]).sort_values(["trade_date","product"])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start",default="20240101"); ap.add_argument("--end",default="20260828"); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    f=fetch_futures(a.start,a.end); s=fetch_spot(a.start,a.end); f.to_csv(a.out/"cffex_contract_daily.csv",index=False); s.to_csv(a.out/"cffex_spot_daily.csv",index=False)
    q={"mode":"RESEARCH_ONLY_CFFEX_EXPIRY_AWARE_INPUTS","source":"CFFEX monthly history archive via official exchange URL; spot via AKShare Eastmoney index history","start":a.start,"end":a.end,"futures_rows":len(f),"spot_rows":len(s),"contracts":{p:int(f[f.product==p].contract.nunique()) for p in PRODUCTS},"date_range":{p:[f[f.product==p].trade_date.min().date().isoformat(),f[f.product==p].trade_date.max().date().isoformat()] for p in PRODUCTS},"expiry_rule":"published third-Friday rule, shifted to first CFFEX trading day on/after nominal date when needed","production_context_integration":False,"trade_signal":None}
    (a.out/"input_qa.json").write_text(json.dumps(q,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(q,ensure_ascii=False))
if __name__=="__main__": raise SystemExit(main())
