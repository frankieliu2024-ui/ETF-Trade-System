#!/usr/bin/env python3
from __future__ import annotations

import argparse, base64, json, uuid
from pathlib import Path
import requests
from gmssl import sm3, func
from gmssl.sm4 import CryptSM4, SM4_ENCRYPT

SECRET="d274d06273f96656442b0728316026a1"
ENVELOPE_REQUEST_ID="01ab90a0d4ce45f3bb35b919099d9da1"
HOSTS=["https://static.cmfchina.com","https://www.cmfchina.com","https://cmfchina.com"]
DATES=["2023-12-15","2023-12-18","2024-06-14","2024-06-17","2024-12-13","2024-12-16","2025-06-13","2025-06-16","2025-12-12","2025-12-15","2026-06-12","2026-06-15","2026-08-28"]
HEAD={"User-Agent":"Mozilla/5.0 ETF-Trade-System research","Referer":"https://static.cmfchina.com/web/fundDetail/561980/"}


def sm3_hex(b:bytes)->str:
    return sm3.sm3_hash(func.bytes_to_list(b))

def encrypt_payload(payload:dict)->tuple[dict,dict]:
    data=dict(payload)
    data["siteno"]="web"; data["merchantId"]=0; data["request_id"]=str(uuid.uuid4())
    raw=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    key=sm3_hex(SECRET.encode("utf-8"))[:16].encode("ascii")
    crypt=CryptSM4(); crypt.set_key(key,SM4_ENCRYPT)
    enc=crypt.crypt_ecb(raw)
    b64=base64.b64encode(enc).decode("ascii")
    sig_text=f"secret={SECRET}data={b64}request_id={ENVELOPE_REQUEST_ID}encrypted=true"
    signature=sm3_hex(sig_text.encode("utf-8"))
    params={"data":b64,"request_id":ENVELOPE_REQUEST_ID,"encrypted":"true"}
    headers={**HEAD,"tk-trans-merchant-key":"thinkive","tk-trans-signature":signature}
    return params,headers

def call(host:str,path:str,payload:dict)->dict:
    params,headers=encrypt_payload(payload)
    url=host+path
    r=requests.get(url,params=params,headers=headers,timeout=20)
    out={"host":host,"path":path,"payload":payload,"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content),"body_prefix":r.text[:1000]}
    try: out["json"]=r.json()
    except Exception: pass
    return out

def meaningful(obj)->bool:
    if not isinstance(obj,dict): return False
    def walk(x):
        if isinstance(x,dict):
            return any(walk(v) for v in x.values())
        if isinstance(x,list): return len(x)>0 and any(walk(v) for v in x)
        return x not in (None,"",0,False,"0","false","False")
    return walk(obj)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    rows=[]
    # First verify current/default call, then historical date pairs.
    payloads=[("/ws-business-server/fund/getEtfSgshqd",{"tradeDate":"","productCode":"561980","isPreview":"0"})]
    for d in DATES:
        payloads.append(("/ws-business-server/fund/getEtfSgshqd",{"tradeDate":d,"productCode":"561980","isPreview":"0"}))
        payloads.append(("/ws-business-server/fund/getEtfStockList",{"startDate":d,"productCode":"561980","pageNum":1,"pageSize":1000,"isPreview":"0"}))
    # Try static host first; only expand hosts if static doesn't produce a meaningful current result.
    current_ok=False
    for host in HOSTS:
        host_rows=[]
        for idx,(path,payload) in enumerate(payloads):
            try:
                x=call(host,path,payload); rows.append(x); host_rows.append(x)
                if idx==0 and meaningful(x.get("json")): current_ok=True
            except Exception as exc:
                rows.append({"host":host,"path":path,"payload":payload,"error":f"{type(exc).__name__}: {exc}"[:400]})
        if current_ok: break
    good=[]
    for x in rows:
        j=x.get("json")
        if meaningful(j): good.append(x)
    (a.out/"pcf_api_probe.json").write_text(json.dumps({"mode":"RESEARCH_ONLY_561980_PCF_API_PROBE","production_context_integration":False,"trade_signal":None,"results":rows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (a.out/"pcf_api_meaningful.json").write_text(json.dumps(good,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"result_count":len(rows),"meaningful_count":len(good),"meaningful_summaries":[{"host":x.get("host"),"path":x.get("path"),"payload":x.get("payload"),"status":x.get("status"),"body_prefix":x.get("body_prefix","")[:250]} for x in good[:20]]},ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
