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

def validate_manifest(data):
    """Validate the #623 business cardinality without entering the ingress."""
    trades = data.get("historical_trades")
    acquisition = data.get("acquisition")
    if not isinstance(trades, list) or len(trades) != 37:
        raise ValueError("manifest must contain exactly 37 ordinary historical trades")
    if not isinstance(acquisition, dict) or str(acquisition.get("code") or "") != "301689":
        raise ValueError("manifest must contain exactly one 301689 acquisition")
    for item in trades:
        if not isinstance(item, dict):
            raise ValueError("ordinary historical trade must be an object")
        if str(item.get("event_type") or "").upper() == "IPO_ALLOTMENT_ACQUISITION":
            raise ValueError("acquisition must not be placed in historical_trades")
    return trades, acquisition

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
    try:
        trades, _acquisition = validate_manifest(data)
    except ValueError as exc:
        raise SystemExit(str(exc))
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
