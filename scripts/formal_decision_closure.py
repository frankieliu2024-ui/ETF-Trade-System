"""Read-only completion status for one durable manual Formal Decision request.

This does not create decisions or introduce a state store.  It derives closure
from the existing durable request, canonical FORMAL_DECISION event, and
canonical production-acceptance state.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def formal_decision_closure(root: Path, parent_request_id: str) -> dict:
    request_path = root / "requests/live_snapshot" / f"{parent_request_id}.json"
    if not request_path.is_file():
        return {"status":"REQUEST_NOT_DURABLE","complete":False,"parent_request_id":parent_request_id}
    matches=[]
    decision_dir=root/"events/decisions"
    for path in decision_dir.glob("*.json") if decision_dir.exists() else []:
        try: event=load(path)
        except Exception: continue
        if event.get("event_type")=="FORMAL_DECISION" and str(event.get("parent_request_id") or "")==parent_request_id:
            matches.append((path,event))
    if not matches:
        return {"status":"DECISION_NOT_PERSISTED","complete":False,"parent_request_id":parent_request_id}
    if len(matches)!=1:
        return {"status":"AMBIGUOUS_CANONICAL_DECISION","complete":False,"parent_request_id":parent_request_id,"decision_count":len(matches)}
    path,event=matches[0]
    acceptance_path=root/"data/state/e2e_status.json"
    if not acceptance_path.is_file():
        return {"status":"PERSISTED_ACCEPTANCE_UNPROVEN","complete":False,"parent_request_id":parent_request_id,"decision_id":event.get("decision_id"),"decision_path":str(path.relative_to(root))}
    acceptance=load(acceptance_path)
    # Acceptance is repository-wide derived evidence. Require an explicit PASS/SUCCESS
    # and a recorded commit identity; never infer acceptance from event existence.
    status=str(acceptance.get("status") or acceptance.get("e2e_status") or acceptance.get("production_acceptance") or "").upper()
    accepted_sha=str(acceptance.get("acceptance_sha") or acceptance.get("mutation_sha") or acceptance.get("validated_sha") or "")
    if status not in {"PASS","SUCCESS","READY"} or not accepted_sha:
        return {"status":"PERSISTED_ACCEPTANCE_UNPROVEN","complete":False,"parent_request_id":parent_request_id,"decision_id":event.get("decision_id"),"decision_path":str(path.relative_to(root))}
    return {"status":"COMPLETE","complete":True,"parent_request_id":parent_request_id,"decision_id":event.get("decision_id"),"decision_path":str(path.relative_to(root)),"acceptance_sha":accepted_sha}

def main():
    p=argparse.ArgumentParser(); p.add_argument("parent_request_id"); a=p.parse_args()
    print(json.dumps(formal_decision_closure(ROOT,a.parent_request_id),ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()
