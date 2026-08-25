from __future__ import annotations
import pandas as pd
import akshare as ak
import scripts.run_market_breadth_margin_stage1 as base

_original_fetch_margin = base.fetch_margin

def fetch_margin_with_fallback(trading_dates,start,end):
    try:
        q,cov=_original_fetch_margin(trading_dates,start,end)
        cov['provider_path']='SSE+SZSE official'
        return q,cov
    except Exception as official_error:
        z=ak.stock_margin_account_info().copy()
        z=z.rename(columns={'日期':'date','融资余额':'balance_total','融资买入额':'buy_total'})
        z['date']=pd.to_datetime(z['date'])
        z=z[(z['date']>=pd.Timestamp(start))&(z['date']<=pd.Timestamp(end))]
        m=pd.DataFrame(index=pd.DatetimeIndex([pd.Timestamp(d) for d in trading_dates],name='date')).join(z.set_index('date')[['balance_total','buy_total']])
        m['margin_balance_1d_change']=(m.balance_total/m.balance_total.shift(1)-1)*100
        m['margin_balance_5d_change']=(m.balance_total/m.balance_total.shift(5)-1)*100
        m['margin_buy_5d_vs20']=(m.buy_total.rolling(5,min_periods=5).mean()/m.buy_total.rolling(20,min_periods=20).mean()-1)*100
        q=m[['margin_balance_1d_change','margin_balance_5d_change','margin_buy_5d_vs20']].shift(1)
        q.columns=[c+'_lag1' for c in q.columns]
        n=int(q.notna().all(axis=1).sum())
        cov={'status':'PASS' if n>=100 else 'DEGRADED','rows_complete':n,'provider_path':'Eastmoney aggregate fallback after official SSE failure','official_error':str(official_error)[:240]}
        if n<70:
            raise RuntimeError(f'Margin fallback history insufficient: {n}; official={official_error}')
        return q,cov

base.fetch_margin=fetch_margin_with_fallback
if __name__=='__main__':
    raise SystemExit(base.main())
