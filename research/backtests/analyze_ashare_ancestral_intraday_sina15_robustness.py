#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'research'/'backtests'/'analyze_ashare_ancestral_intraday_sina15.py'
OUT=ROOT/'research'/'reports'/'generated'/'section6_revalidation'
spec=importlib.util.spec_from_file_location('s15',SRC)
s15=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(s15)

KEYS=["high_gap_fast_pullback","high_gap_hold_continue","low_gap_v_recovery","low_gap_continue_weak","high_gap_fill","high_gap_no_fill",
      "early_strong_pm_weak","early_strong_pm_reaccelerate","v_recovery_intraday","spike_reversal_no_reclaim","high_zone_consolidation",
      "tail_rally","tail_selloff","breakout_hold_60m","breakout_fail_60m","relative_strength_expand","relative_strength_fade"]


def mean(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None


def sign(x):
    return 1 if x is not None and x>0 else -1 if x is not None and x<0 else 0


def main():
    by_code_daily={}; intr=[]; errors={}
    for code,name in s15.universe():
        try:
            bars=s15.fetch_15m(code); bd=defaultdict(list)
            for x in bars: bd[x['date']].append(x)
            daily=s15.derive_daily(code,name,bd); by_code_daily[code]=daily
            dm={r['date']:r for r in daily}; ph=s15.prior20_high(daily)
            rs=s15.sigmod.intraday_features(code,name,bd,dm,ph); s15.inject_breakout_60(rs,bd,ph); intr.extend(rs)
        except Exception as e: errors[code]=repr(e)
    panel=s15.mod.enrich_future(by_code_daily); pmap={(r['date'],r['code']):r for r in panel}
    by_date=defaultdict(list)
    for r in intr: by_date[r['date']].append(r)
    for d,xs in by_date.items():
        a=[x['r1000_pct'] for x in xs if x.get('r1000_pct') is not None]; b=[x['close_ret_pct'] for x in xs if x.get('close_ret_pct') is not None]
        med10=statistics.median(a) if a else None; medc=statistics.median(b) if b else None
        for x in xs:
            if med10 is None or medc is None or x.get('r1000_pct') is None or x.get('close_ret_pct') is None:
                x['signals']['relative_strength_expand']=False; x['signals']['relative_strength_fade']=False
            else:
                delta=(x['close_ret_pct']-medc)-(x['r1000_pct']-med10)
                x['signals']['relative_strength_expand']=delta>=1.0; x['signals']['relative_strength_fade']=delta<=-1.0
    sample_dates=sorted(set(r['date'] for r in intr)); split=sample_dates[len(sample_dates)//2] if sample_dates else None
    out={}
    for key in KEYS:
        sel=[r for r in intr if r['signals'].get(key) and (r['date'],r['code']) in pmap]
        vals=[]; by_d=defaultdict(list); by_c=defaultdict(list); half1=[]; half2=[]
        for r in sel:
            q=pmap[(r['date'],r['code'])]['future'].get(5)
            if not q or q.get('relative_to_pool_median_pct_points') is None: continue
            v=q['relative_to_pool_median_pct_points']; vals.append(v); by_d[r['date']].append(v); by_c[r['code']].append(v)
            (half1 if split and r['date']<split else half2).append(v)
        counts=defaultdict(int)
        for r in sel: counts[r['code']]+=1
        top_share=max(counts.values())/len(sel) if sel and counts else None
        pooled=mean(vals); date_mean=mean([mean(v) for v in by_d.values()]); h1=mean(half1); h2=mean(half2)
        same_direction=(sign(pooled)!=0 and sign(pooled)==sign(date_mean)==sign(h1)==sign(h2))
        out[key]={"event_count":len(sel),"evaluable_t5":len(vals),"date_count":len(by_d),"code_count":len(by_c),"top_code_share":round(top_share,4) if top_share is not None else None,
                  "t5_relative_pooled_mean":round(pooled,4) if pooled is not None else None,"t5_relative_date_level_mean":round(date_mean,4) if date_mean is not None else None,
                  "t5_relative_first_half_mean":round(h1,4) if h1 is not None else None,"t5_relative_second_half_mean":round(h2,4) if h2 is not None else None,
                  "same_direction_pooled_date_halves":same_direction,
                  "by_code_t5_relative_mean":{c:round(mean(v),4) for c,v in sorted(by_c.items())}}
    pairs={
      "high_gap_fill_minus_no_fill": ("high_gap_fill","high_gap_no_fill"),
      "early_strong_weak_minus_reaccelerate": ("early_strong_pm_weak","early_strong_pm_reaccelerate"),
      "low_gap_v_minus_continue_weak": ("low_gap_v_recovery","low_gap_continue_weak"),
      "tail_rally_minus_selloff": ("tail_rally","tail_selloff"),
      "breakout_hold_minus_fail": ("breakout_hold_60m","breakout_fail_60m"),
      "relative_expand_minus_fade": ("relative_strength_expand","relative_strength_fade")}
    pair_results={}
    for label,(a,b) in pairs.items():
        av=out[a]['t5_relative_date_level_mean']; bv=out[b]['t5_relative_date_level_mean']
        pair_results[label]=None if av is None or bv is None else round(av-bv,4)
    payload={"schema_version":"1.0","mode":"RESEARCH_ONLY_ASHARE_ANCESTRAL_INTRADAY_FINAL_ROBUSTNESS","sample_date_count":len(sample_dates),"split_date":split,
             "errors":errors,"candidates":out,"paired_date_level_t5_differences_pp":pair_results,
             "decision_boundary":"Robustness only; no formal trading authority or rule conversion."}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'ashare_ancestral_intraday_sina15_robustness.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# A股祖训：15分钟历史最终稳健性检查','',f'样本交易日={len(sample_dates)}；前后半分界={split}。日期级均值用于降低同日多ETF伪样本。','',
           '|候选|事件|日期|对象|最大对象占比|T+5池化|T+5日期级|前半|后半|方向四项一致|','|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for k in KEYS:
        z=out[k]
        lines.append(f"|{k}|{z['event_count']}|{z['date_count']}|{z['code_count']}|{z['top_code_share']}|{z['t5_relative_pooled_mean']}|{z['t5_relative_date_level_mean']}|{z['t5_relative_first_half_mean']}|{z['t5_relative_second_half_mean']}|{z['same_direction_pooled_date_halves']}|")
    lines += ['', '## 成对背景差（日期级T+5相对收益，pp）','']+[f'- {k}: {v}' for k,v in pair_results.items()]
    lines += ['','## 边界','','- 不做阈值优化；只检查伪样本、时间分段和对象集中度。','- 前后半方向不一致的候选不作为稳定祖训。','- 即使方向一致，也仍需结合经济含义、与既有正式证据去重后才能考虑8.1转化。']
    (OUT/'ashare_ancestral_intraday_sina15_robustness.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({"ok":True,"sample_dates":len(sample_dates),"split":split,"errors":errors,"stable":{k:v['same_direction_pooled_date_halves'] for k,v in out.items()}},ensure_ascii=False))

if __name__=='__main__': main()
