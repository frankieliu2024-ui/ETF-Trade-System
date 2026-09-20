"""Read-only historical Discovery shadow for Issue #687.

This runner never mutates canonical state. It reconstructs only what current-main
contracts can prove from repository-retained PIT evidence. Missing historical
broad-spot fields remain explicit rather than being filled from future data.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.formal_etf_opportunity_discovery import (
    _bounded_prefilter, _broad_return_median, _information_classes, _candidate,
    load_discovery_history_snapshot,
)

def read_json(path: Path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return {}
    return value

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--market-date", required=True)
    p.add_argument("--output", required=True)
    a=p.parse_args()
    date=a.market_date
    if date != "2026-09-18":
        raise SystemExit("bounded shadow currently admits only the evidenced 2026-09-18 PIT")

    query=read_json(ROOT/"data/state/query_context.json")
    history=load_discovery_history_snapshot(ROOT,date)
    low=read_json(ROOT/"data/state/low_cost_alpha_evidence.json")
    snapshot_paths=sorted((ROOT/"data/market/snapshots").glob(f"{date}_*.json"))
    snapshot=read_json(snapshot_paths[-1]) if snapshot_paths else {}

    # Canonical repo does not retain the 1621-row Eastmoney broad spot payload.
    # Therefore exact current-algorithm queue reconstruction is impossible.
    # Build a bounded evidence matrix from retained same-date formal rows and
    # the node-local history snapshot, without synthesizing missing f24/f25/f10.
    formal_rows={}
    for row in snapshot.get("rows") or []:
        code=str(row.get("code") or row.get("symbol") or "").replace(".SH","").replace(".SZ","")
        if code:
            formal_rows[code]=row
    low_items={}
    for section in ("selling_exhaustion","c2_participation_structure"):
        for row in (low.get(section) or {}).get("items") or []:
            if isinstance(row,dict) and row.get("code"):
                low_items[str(row["code"])]=row

    focus=["515880","513350","159666","516670"]
    matrix=[]
    for code in focus:
        qitem=next((x for x in ((query.get("formal_etf_discovery") or {}).get("candidates") or []) if str(x.get("code"))==code),{})
        f=formal_rows.get(code,{})
        l=low_items.get(code,{})
        h=history.get(code) or []
        matrix.append({
            "code":code,
            "formal_same_date_return_pct": l.get("close_return_pct", f.get("change_pct")),
            "volume_ratio_vs_prior20d": l.get("volume_ratio_vs_prior20d"),
            "retained_discovery_candidate": bool(qitem),
            "retained_candidate_information_classes": qitem.get("information_classes") or [],
            "retained_history_bars": len(h),
            "exact_current_information_classes":"UNRECOVERABLE_WITHOUT_1621_BROAD_SPOT",
            "exact_current_queue_position":"UNRECOVERABLE_WITHOUT_1621_BROAD_SPOT",
            "current_candidate_replay":"INCONCLUSIVE_WITHOUT_ORIGINAL_BROAD_SPOT_FIELDS",
        })

    result={
        "issue":687,
        "mode":"READ_ONLY_HISTORICAL_DISCOVERY_SHADOW",
        "market_date":date,
        "pit_rule":"NO_FUTURE_DATA; REPOSITORY_RETAINED_EVIDENCE_ONLY",
        "canonical_broad_spot_retained":False,
        "exact_1621_replay":"UNRECOVERABLE",
        "reason":"current algorithm requires original broad cross-section fields/median/class allocation; canonical repository retains derived result and bounded histories, not the full 1621-row spot payload",
        "retained_query_discovery":{
            "generated_at_beijing":(query.get("formal_etf_discovery") or {}).get("generated_at_beijing"),
            "broad_universe_count":(query.get("formal_etf_discovery") or {}).get("broad_universe_count"),
            "history_prefilter_count":(query.get("formal_etf_discovery") or {}).get("history_prefilter_count"),
            "history_attempted_count":(query.get("formal_etf_discovery") or {}).get("history_attempted_count"),
            "history_succeeded_count":(query.get("formal_etf_discovery") or {}).get("history_succeeded_count"),
            "history_failure_count":(query.get("formal_etf_discovery") or {}).get("history_failure_count"),
            "candidate_codes":[str(x.get("code")) for x in ((query.get("formal_etf_discovery") or {}).get("candidates") or [])],
        },
        "retained_history_object_count":len(history),
        "focus_matrix":matrix,
        "acceptance":{
            "current_failure_domain_repairs_testable_from_retained_evidence":True,
            "exact_historical_candidate_set_proven":False,
            "historical_master_action_proven":False,
            "production_mutation_justified_by_shadow":False,
        },
        "next_gate":"ACTIVE_MARKET_CURRENT_BROAD_CROSS_SECTION_E2E",
    }
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":
    raise SystemExit(main())
