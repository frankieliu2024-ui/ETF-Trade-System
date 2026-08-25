from __future__ import annotations

import json, math, os, ssl, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen
from http.cookiejar import CookieJar

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY = ROOT / "events/research/daily_features"
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"
OUT = ROOT / "data/state/etf_share_flow_evidence.json"
VALIDATION = ROOT / "research/backtests/etf_share_flow_increment_poc.json"
SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_PAGE = "https://www.sse.com.cn/market/funddata/volumn/etfvolumn/"
SSE_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_PAGE = "https://www.szse.cn/market/fund/volume/etf/index.html"


def load(path, default=None):
    if not path.exists(): return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))

def r4(x):
    try: x=float(x)
    except Exception: return None
    return round(x,4) if math.isfinite(x) else None

def pct_rank(values):
    pairs=sorted((v,i) for i,v in enumerate(values)); out=[0.0]*len(values); p=0
    while p<len(pairs):
        q=p
        while q+1<len(pairs) and pairs[q+1][0]==pairs[p][0]: q+=1
        rank=((p+1)+(q+1))/2/len(pairs)
        for k in range(p,q+1): out[pairs[k][1]]=rank
        p=q+1
    return out

def complete_daily_files():
    rows=[]
    for p in DAILY.glob("*.json"):
        x=load(p,{})
        phase=str(x.get("market_phase", ""))
        if x.get("quality_status") == "PASS" and ("POST_CLOSE" in phase or phase in {"CLOSED","CLOSE"}):
            rows.append((str(x.get("market_date") or p.stem), p, x))
    return sorted(rows, key=lambda z:z[0])

def get_url(url, headers, params=None, opener=None, verify=True):
    if params: url += ("&" if "?" in url else "?") + urlencode(params)
    req=Request(url, headers=headers)
    if opener: return opener.open(req, timeout=25).read().decode("utf-8")
    ctx=None if verify else ssl._create_unverified_context()
    return urlopen(req, timeout=25, context=ctx).read().decode("utf-8")

def fetch_sse(dates, wanted):
    headers={"Referer":SSE_PAGE,"User-Agent":"Mozilla/5.0","Accept":"application/json,text/javascript,*/*;q=0.01","X-Requested-With":"XMLHttpRequest"}
    opener=build_opener(HTTPCookieProcessor(CookieJar()))
    try: get_url(SSE_PAGE, headers, opener=opener)
    except Exception: pass
    out={}
    for d in dates:
        params={"isPagination":"true","pageHelp.pageSize":"2000","pageHelp.pageNo":"1","pageHelp.beginPage":"1","pageHelp.cacheSize":"1","pageHelp.endPage":"1","sqlId":SSE_SQL,"STAT_DATE":d,"_":str(int(time.time()*1000))}
        data=json.loads(get_url(SSE_URL,headers,params,opener=opener))
        for x in data.get("result") or (data.get("pageHelp") or {}).get("data") or []:
            c=str(x.get("SEC_CODE") or "").strip()
            if c in wanted and x.get("TOT_VOL") not in (None,""):
                out[(d,c)]=float(str(x["TOT_VOL"]).replace(",",""))
        time.sleep(0.4)
    return out

def fetch_szse(dates, wanted):
    headers={"Referer":SZSE_PAGE,"User-Agent":"Mozilla/5.0","Accept":"application/json,text/javascript,*/*;q=0.01","X-Requested-With":"XMLHttpRequest"}
    start,end=min(dates),max(dates); out={}
    for c in sorted(wanted):
        params={"SHOWTYPE":"JSON","CATALOGID":"scsj_fund_jjgm","TABKEY":"tab1","jjlb":"ETF","txtDm":c,"txtStart":start,"txtEnd":end,"tab1PAGENO":"1","random":str(time.time())}
        data=json.loads(get_url(SZSE_URL,headers,params,verify=False)); block=data[0] if isinstance(data,list) and data else {}
        for x in block.get("data") or []:
            d=str(x.get("size_date") or "").strip(); v=str(x.get("current_size") or "").replace(",","").strip()
            if d in dates and v: out[(d,c)]=float(v)
        time.sleep(0.2)
    return out

def build(root=ROOT):
    validation=load(VALIDATION,{})
    files=complete_daily_files()
    base={"schema_version":"1.0","generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"),"read_only":True,"decision_eligible":False,"trade_signal":None,"trial_confirm":None,"portfolio_target":None,"master_override":False,"capital_efficiency_score":None}
    if validation.get("research_interpretation") != "PROMISING_CONDITIONAL_INCREMENT":
        return {**base,"status":"BLOCKED","use_in_current_decision":False,"reason":"conditional increment validation not eligible"}
    if len(files)<21:
        return {**base,"status":"DEGRADED","use_in_current_decision":False,"reason":"insufficient complete daily history"}
    d=files[-1][0]
    old=load(OUT,{})
    if old.get("price_as_of_market_date")==d and old.get("status")=="READY": return old
    dates=[x[0] for x in files]
    prev_date, old_share_date=dates[-2],dates[-7]
    needed_price_dates=[dates[-1],dates[-6],dates[-21]]
    price={}
    for dd,_,x in files:
        if dd not in needed_price_dates: continue
        for f in x.get("features") or []:
            if f.get("close") is not None: price[(dd,str(f.get("code")))]=float(f["close"])
    uni=load(UNIVERSE,{}); meta={str(x["code"]):x for x in uni.get("objects") or []}
    sh={c for c,x in meta.items() if str(x.get("thscode","")).endswith(".SH")}; sz=set(meta)-sh
    try:
        shares={}; shares.update(fetch_sse([old_share_date,prev_date],sh)); shares.update(fetch_szse([old_share_date,prev_date],sz))
    except Exception as exc:
        return {**base,"status":"DEGRADED","use_in_current_decision":False,"price_as_of_market_date":d,"reason":f"official share fetch failed: {type(exc).__name__}: {exc}"}
    rows=[]
    for c,x in meta.items():
        p0,p5,p20=price.get((dates[-1],c)),price.get((dates[-6],c)),price.get((dates[-21],c))
        s0,s5=shares.get((prev_date,c)),shares.get((old_share_date,c))
        if None in (p0,p5,p20,s0,s5) or not p5 or not p20 or not s5: continue
        rows.append({"code":c,"name":x.get("name",c),"share_change_5d_pct_lag1":(s0/s5-1)*100,"reverse_share_change_5d_pct_lag1":-(s0/s5-1)*100,"ret_5d_pct":(p0/p5-1)*100,"ret_20d_pct":(p0/p20-1)*100})
    if len(rows)<7:
        return {**base,"status":"DEGRADED","use_in_current_decision":False,"price_as_of_market_date":d,"share_fact_latest_date":prev_date,"coverage":len(rows),"reason":"cross-section below 7"}
    sr=pct_rank([x["reverse_share_change_5d_pct_lag1"] for x in rows]); r5=pct_rank([x["ret_5d_pct"] for x in rows]); r20=pct_rank([x["ret_20d_pct"] for x in rows])
    for i,x in enumerate(rows):
        x.update({"reverse_share_rank_pct":r4(sr[i]),"ret5_rank_pct":r4(r5[i]),"ret20_rank_pct":r4(r20[i]),"conditional_increment_rank_value":r4((sr[i]+r5[i]+r20[i])/3)})
        for k in ("share_change_5d_pct_lag1","reverse_share_change_5d_pct_lag1","ret_5d_pct","ret_20d_pct"): x[k]=r4(x[k])
    rows.sort(key=lambda x:x["conditional_increment_rank_value"], reverse=True)
    for i,x in enumerate(rows,1): x["conditional_increment_rank"]=i; x["display_name"]=f"{x['name']}（{x['code']}）"
    return {**base,"status":"READY","use_in_current_decision":True,"price_as_of_market_date":d,"share_fact_latest_date":prev_date,"share_fact_base_date":old_share_date,"availability_rule":"T日份额只从T+1交易日起使用；本证据使用最近完整收盘价格，盘中不改写验证口径。","coverage":len(rows),"validated_reference":{"result":"PROMISING_CONDITIONAL_INCREMENT","candidate":"reverse 5d share change + 5d/20d momentum equal-rank ensemble","validation_file":"research/backtests/etf_share_flow_increment_poc.json"},"items":rows,"interpretation_rule":"仅作为ETF横截面机会比较的增强证据；排名不是资本效率排名，不能单独产生风险许可、机会状态、Trial/Confirm、金额、降低风险或退出。"}

if __name__=="__main__":
    out=build(); OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"status":out.get("status"),"coverage":out.get("coverage"),"use_in_current_decision":out.get("use_in_current_decision")},ensure_ascii=False))
