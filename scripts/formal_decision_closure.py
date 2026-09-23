"""Read-only completion status for one durable manual Formal Decision request.

Formal Decision closure belongs to the canonical Decision Fact itself.  It does
not wait for Dashboard, account, E2E, notification, research, or other derived
projections to converge.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def _canonical_decision_readback_valid(event: dict, parent_request_id: str) -> bool:
    """Post-write acceptance for the Decision Fact, not for downstream projections."""
    decision = event.get("formal_decision")
    decision_id = str(event.get("decision_id") or "").strip()
    pit_status = str(event.get("point_in_time_status") or "").strip()
    return bool(
        event.get("event_type") == "FORMAL_DECISION"
        and str(event.get("parent_request_id") or "").strip() == parent_request_id
        and str(event.get("request_id") or "").strip()
        and decision_id
        and str(event.get("fingerprint") or "").strip()
        and isinstance(decision, dict)
        and str(decision.get("decision_id") or "").strip() == decision_id
        and (
            pit_status in {
                "CONSUMED_SNAPSHOT_VALIDATED",
                "POINT_IN_TIME_SNAPSHOT_TWO_CLOCK_VALIDATED",
                "REQUEST_BOUND_QUERY_TIME_PIT_VALIDATED",
            }
            or event.get("request_bound_query_time_pit") is True
        )
    )

def formal_decision_closure(root: Path, parent_request_id: str) -> dict:
    request_path = root / "requests/live_snapshot" / f"{parent_request_id}.json"
    if not request_path.is_file():
        return {"status":"REQUEST_NOT_DURABLE","complete":False,"parent_request_id":parent_request_id}
    try:
        request=load(request_path)
    except Exception:
        return {"status":"REQUEST_NOT_DURABLE","complete":False,"parent_request_id":parent_request_id}
    if str(request.get("request_id") or "").strip() != parent_request_id:
        return {"status":"REQUEST_IDENTITY_INVALID","complete":False,"parent_request_id":parent_request_id}
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
    base={"parent_request_id":parent_request_id,"decision_id":event.get("decision_id"),"decision_path":str(path.relative_to(root))}
    if not _canonical_decision_readback_valid(event,parent_request_id):
        return {"status":"DECISION_POST_WRITE_ACCEPTANCE_FAILED","complete":False,**base}
    return {"status":"COMPLETE","complete":True,"post_write_acceptance":"CANONICAL_DECISION_FACT_READBACK_VALIDATED",**base}

def main():
    p=argparse.ArgumentParser(); p.add_argument("parent_request_id"); a=p.parse_args()
    print(json.dumps(formal_decision_closure(ROOT,a.parent_request_id),ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()
