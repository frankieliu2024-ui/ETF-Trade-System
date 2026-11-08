#!/usr/bin/env python3
"""Research-only Hyperliquid/A-share minute lead study.

This module deliberately does not write production state or generate trades.
It downloads PIT-bounded minute candles, records provider limitations, aligns
the two markets, and reports forward returns for exploratory research.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

HL_URL = "https://api.hyperliquid.xyz/info"
CN_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

def post(payload: dict):
    req = Request(HL_URL, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)

def get(url: str):
    with urlopen(Request(url, headers={"User-Agent":"ETF-Trade-System research-only"}), timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def hl_candles(coin: str, start_ms: int, end_ms: int) -> dict:
    payload = {"type":"candleSnapshot","req":{"coin":coin,"interval":"1m","startTime":start_ms,"endTime":end_ms}}
    try:
        rows = post(payload)
        return {"status":"PASS" if rows else "EMPTY","coin":coin,"rows":rows if isinstance(rows,list) else [],"provider_as_of":None}
    except Exception as exc:
        return {"status":"FAILED","coin":coin,"rows":[],"error":f"{type(exc).__name__}: {exc}"}

def tencent_minutes(symbol: str) -> dict:
    try:
        raw = get(CN_URL + "?code=" + symbol + "&r=" + str(time.time()))
        return {"status":"RAW_RECEIVED","symbol":symbol,"raw":raw}
    except Exception as exc:
        return {"status":"FAILED","symbol":symbol,"raw":"","error":f"{type(exc).__name__}: {exc}"}

def run(coin: str, ashare: str, start_ms: int, end_ms: int) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    hl = hl_candles(coin, start_ms, end_ms)
    cn = tencent_minutes(ashare)
    return {
        "schema_version":"research-hl-ashare-minute-v1",
        "mode":"RESEARCH_ONLY_EXPLORATORY",
        "read_only":True,
        "started_at_utc":started,
        "finished_at_utc":datetime.now(timezone.utc).isoformat(),
        "mapping":{"hyperliquid":coin,"a_share":ashare},
        "pit_window":{"start_ms":start_ms,"end_ms":end_ms},
        "hyperliquid":hl,
        "a_share_minute":cn,
        "research_status":"READY_FOR_ALIGNMENT" if hl["status"]=="PASS" and cn["status"]=="RAW_RECEIVED" else "DATA_COVERAGE_INSUFFICIENT",
        "decision_boundary":"No production state, formal decision, trading permission, amount or order is generated.",
        "next_analysis":["minute lead/lag cross-correlation","forward 5/15/30/60 minute returns","lead/lag reversal and divergence","provider timestamp and PIT audit"]
    }

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--coin",default="xyz:CXMT")
    p.add_argument("--ashare",default="sh688249")
    p.add_argument("--start-ms",type=int,required=True)
    p.add_argument("--end-ms",type=int,required=True)
    p.add_argument("--output")
    a=p.parse_args()
    result=run(a.coin,a.ashare,a.start_ms,a.end_ms)
    data=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output:
        with open(a.output,"w",encoding="utf-8") as f: f.write(data+"\n")
    print(data)
