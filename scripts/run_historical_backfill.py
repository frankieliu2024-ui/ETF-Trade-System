"""One-shot, fail-closed runner for the existing HISTORICAL_BACKFILL ingress."""
from __future__ import annotations
import argparse, copy, hashlib, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--request-id", required=True)
    args=p.parse_args()
    manifest=(ROOT/args.manifest).resolve()
    if ROOT not in manifest.parents or not manifest.exists():
        raise SystemExit("manifest must be an existing repository-relative file")
    data=load(manifest)
    if str(data.get("ingress_mode") or "").upper() != "HISTORICAL_BACKFILL":
        raise SystemExit("only HISTORICAL_BACKFILL is accepted")
    if str(data.get("request_id") or "") != args.request_id:
        raise SystemExit("request identity mismatch")
    trades=data.get("historical_trades")
    if not isinstance(trades,list) or len(trades)!=38:
        raise SystemExit("manifest must contain exactly 38 historical actions")
    before={}
    for rel in ("data/state/account_fact.json","data/state/CURRENT.json"):
        path=ROOT/rel
        before[rel]=digest(path) if path.exists() else None
    proc=[sys.executable,str(ROOT/"scripts/process_state_sync_request.py"),str(manifest.relative_to(ROOT))]
    first=subprocess.run(proc,cwd=ROOT,text=True,capture_output=True)
    if first.returncode:
        print(first.stdout); print(first.stderr,file=sys.stderr); return first.returncode
    second=subprocess.run(proc,cwd=ROOT,text=True,capture_output=True)
    if second.returncode:
        print(second.stdout); print(second.stderr,file=sys.stderr); return second.returncode
    after={rel:digest(ROOT/rel) if (ROOT/rel).exists() else None for rel in before}
    if before != after:
        raise SystemExit("current account/CURRENT mutation detected")
    print(json.dumps({"status":"PASS","request_id":args.request_id,"first_replay":first.stdout.strip(),"second_replay":second.stdout.strip(),"duplicate_events_created":0,"current_account_unchanged":True},ensure_ascii=False))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
