from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time
from pathlib import Path
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_etf_share_flow_stage1 as core


def fetch_one(ts, wanted):
    d = pd.Timestamp(ts).strftime('%Y-%m-%d')
    params = {
        'isPagination':'true','pageHelp.pageSize':'2000','pageHelp.pageNo':'1',
        'pageHelp.beginPage':'1','pageHelp.cacheSize':'1','pageHelp.endPage':'1',
        'sqlId':core.SSE_SQL,'STAT_DATE':d,
    }
    headers={'Referer':core.SSE_REFERER,'User-Agent':'Mozilla/5.0','Accept':'application/json,text/javascript,*/*;q=0.01'}
    last=None
    for attempt in range(3):
        try:
            r=requests.get(core.SSE_URL,params=params,headers=headers,timeout=20)
            r.raise_for_status(); data=r.json(); rows=data.get('result') or (data.get('pageHelp') or {}).get('data') or []
            out=[]
            for x in rows:
                code=str(x.get('SEC_CODE') or '').strip()
                if code not in wanted: continue
                val=str(x.get('TOT_VOL') or '').replace(',','').strip()
                if val: out.append({'date':pd.Timestamp(ts),'code':code,'shares_10k':float(val),'source':'SSE'})
            return out
        except Exception as exc:
            last=exc; time.sleep(0.5*(attempt+1))
    raise RuntimeError(f'SSE fetch failed {d}: {last}')


def parallel_fetch(dates, wanted):
    out=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs=[ex.submit(fetch_one,ts,wanted) for ts in dates]
        for fut in as_completed(futs): out.extend(fut.result())
    return out


core.fetch_sse_by_dates = parallel_fetch

if __name__ == '__main__':
    raise SystemExit(core.main())
