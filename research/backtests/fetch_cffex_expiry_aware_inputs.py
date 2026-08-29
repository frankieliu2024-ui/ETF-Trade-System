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
    for x in pd.period_range(pd.Timestamp(start),pd.Timestamp(end),freq="M"): yield x.strftime("%Y%m")

def parse_daily_csv(raw:bytes,date:str)->pd.DataFrame:
    x=pd.read_csv(StringIO(raw.decode("gb2312",errors="ignore")))
    if "合约代码" not in x.columns or x.shape[1]<11:return pd.DataFrame()
    x=x[~x["合约代码"].astype(str).isin(["小计","合计"])].copy(); x=x[~x["合约代码"].astype(str).str.contains("IO|MO|HO",regex=True)].copy()
    y=pd.DataFrame({"contract":x.iloc[:,0].astype(str).str.strip(),"volume":x.iloc[:,4],"open_interest":x.iloc[:,6],"close":x.iloc[:,8],"settlement":x.iloc[:,9]})
    y["trade_date"]=pd.Timestamp(date); y["product"]=y["contract"].str.extract(r"^([A-Za-z]+)",expand=False).str.upper(); return y[y["product"].isin(PRODUCTS)]

def fetch_month(ym:str,a:pd.Timestamp,b:pd.Timestamp):
    url=f"http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
    last=None
    for attempt in range(2):
        try:
            r=requests.get(url,headers=HEAD,timeout=12); r.raise_for_status(); out=[]
            with zipfile.ZipFile(BytesIO(r.content)) as z:
                for name in z.namelist():
                    m=re.fullmatch(r"(\d{8})_1\.csv",name.split("/")[-1])
                    if not m:continue
                    d=pd.Timestamp(m.group(1))
                    if d<a or d>b:continue
                    q=parse_daily_csv(z.read(name),m.group(1))
                    if not q.empty:out.append(q)
            return ym,out
        except Exception as e:last=e
    raise RuntimeError(f"CFFEX archive fetch failed {ym}: {type(last).__name__}: {last}")

def expiry_for_contract(contract:str, observed_days:pd.DatetimeIndex, sample_end:pd.Timestamp)->tuple[pd.Timestamp,str]:
    y,m=contract_month(contract); nominal=third_friday(y,m)
    # For an expiry already observable inside the research sample, use the
    # actual CFFEX trading calendar in the downloaded archive to apply the
    # published holiday/non-trading-day forward-shift rule.
    if nominal<=sample_end:
        candidates=observed_days[(observed_days>=nominal)&(observed_days.year==y)&(observed_days.month==m)]
        if len(candidates):
            actual=candidates[0]
            return actual,"OBSERVED_RULE_DATE" if actual==nominal else "OBSERVED_RULE_SHIFT"
        raise RuntimeError(f"cannot resolve historical expiry trading day for {contract} from observed CFFEX calendar")
    # CFFEX archives can contain far-dated listed contracts whose expiry lies
    # after the research end date. Their nominal third-Friday expiry is already
    # a point-in-time contract rule fact; do not falsely require a future
    # trading day to exist inside the historical sample.
    return nominal,"RULE_NOMINAL_AFTER_SAMPLE"

def fetch_futures(start:str,end:str)->pd.DataFrame:
    parts=[]; a=pd.Timestamp(start); b=pd.Timestamp(end); months=list(month_keys(start,end))
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs={ex.submit(fetch_month,ym,a,b):ym for ym in months}
        for f in as_completed(fs):
            ym,out=f.result(); parts.extend(out); print(f"fetched {ym}: {sum(len(x) for x in out)} rows",flush=True)
    if not parts:raise RuntimeError("no CFFEX contract history returned from monthly archives")
    f=pd.concat(parts,ignore_index=True).drop_duplicates(["trade_date","product","contract"])
    for c in ["close","settlement","volume","open_interest"]:f[c]=pd.to_numeric(f[c],errors="coerce")
    f=f.dropna(subset=["close","settlement","volume","open_interest"]); observed_days=pd.DatetimeIndex(sorted(f["trade_date"].unique()))
    mapping={}
    for contract in sorted(f["contract"].unique()): mapping[contract]=expiry_for_contract(contract,observed_days,b)
    f["expiry_date"]=f["contract"].map(lambda c:mapping[c][0]); f["expiry_date_source"]=f["contract"].map(lambda c:mapping[c][1])
    if (f["expiry_date"]<f["trade_date"]).any():
        bad=f.loc[f["expiry_date"]<f["trade_date"],["trade_date","contract","expiry_date"]].head(20).to_dict("records")
        raise RuntimeError(f"post-expiry futures rows detected: {bad}")
    return f.sort_values(["trade_date","product","contract"])

def fetch_spot(start:str,end:str)->pd.DataFrame:
    rows=[]
    for p,code in PRODUCTS.items():
        x=ak.index_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end).copy().rename(columns={"日期":"trade_date","收盘":"spot_close"})
        if x.empty:raise RuntimeError(f"spot history empty for {p}/{code}")
        x["trade_date"]=pd.to_datetime(x["trade_date"]); x["spot_close"]=pd.to_numeric(x["spot_close"],errors="coerce"); x=x.dropna(subset=["trade_date","spot_close"]); x["product"]=p; rows.append(x[["trade_date","product","spot_close"]])
    return pd.concat(rows,ignore_index=True).drop_duplicates(["trade_date","product"]).sort_values(["trade_date","product"])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start",default="20240101"); ap.add_argument("--end",default="20260828"); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    f=fetch_futures(a.start,a.end); s=fetch_spot(a.start,a.end); f.to_csv(a.out/"cffex_contract_daily.csv",index=False); s.to_csv(a.out/"cffex_spot_daily.csv",index=False)
    q={"mode":"RESEARCH_ONLY_CFFEX_EXPIRY_AWARE_INPUTS","source":"CFFEX official monthly history archive; spot via AKShare Eastmoney index history","start":a.start,"end":a.end,"futures_rows":len(f),"spot_rows":len(s),"contracts":{p:int(f[f.product==p].contract.nunique()) for p in PRODUCTS},"date_range":{p:[f[f.product==p].trade_date.min().date().isoformat(),f[f.product==p].trade_date.max().date().isoformat()] for p in PRODUCTS},"expiry_date_source_counts":{k:int(v) for k,v in f.drop_duplicates("contract")["expiry_date_source"].value_counts().to_dict().items()},"expiry_rule":"published third-Friday rule; historical expiries use observed CFFEX trading-day shift when required; contracts expiring after sample end keep nominal rule date without future-data lookahead","production_context_integration":False,"trade_signal":None}
    (a.out/"input_qa.json").write_text(json.dumps(q,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(q,ensure_ascii=False))
if __name__=="__main__":raise SystemExit(main())
