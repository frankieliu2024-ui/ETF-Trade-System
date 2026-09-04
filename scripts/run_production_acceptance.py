"""Run canonical post-write acceptance without destroying last-known state."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONSISTENCY=ROOT/"data/state/system_consistency.json"; MAINTENANCE=ROOT/"data/state/maintenance_health.json"; E2E=ROOT/"data/state/e2e_status.json"
def run(command): return subprocess.run(command,cwd=ROOT,env=os.environ.copy(),check=False).returncode
def read_json(path):
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return {}
    return value if isinstance(value,dict) else {}
def is_complete_consistency_report(value):
    if not isinstance(value,dict) or value.get("status") not in {"PASS","WARNING","FAIL"}: return False
    for key in ("hard_error_count","warning_count"):
        if isinstance(value.get(key),bool) or not isinstance(value.get(key),int) or value[key]<0: return False
    return all(isinstance(value.get(key),list) for key in ("checks","errors","warnings"))
def persist_consistency_report_if_valid(report,target=CONSISTENCY):
    if not is_complete_consistency_report(report): return False
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8"); return True
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--mutation-sha",default=os.environ.get("GITHUB_SHA","")); args=parser.parse_args()
    quality_rc=run([sys.executable,str(ROOT/"scripts/build_execution_quality.py")])
    state_rc=run([sys.executable,str(ROOT/"scripts/build_state_context.py")])
    query_rc=run([sys.executable,str(ROOT/"scripts/build_query_context.py")])
    with tempfile.NamedTemporaryFile(prefix="etf-system-consistency-",suffix=".json",delete=False) as handle: fresh_path=Path(handle.name)
    report_error=""
    try:
        consistency_rc=run([sys.executable,str(ROOT/"scripts/check_system_consistency.py"),"--no-persist","--report-path",str(fresh_path)])
        fresh=read_json(fresh_path)
        if not is_complete_consistency_report(fresh): report_error="checker did not produce a complete report; canonical state preserved"
        else: persist_consistency_report_if_valid(fresh,CONSISTENCY)
    finally:
        try: fresh_path.unlink()
        except OSError: pass
    if report_error: maintenance_rc=e2e_rc=1
    else:
        maintenance_rc=run([sys.executable,str(ROOT/"scripts/maintenance_guard.py")]); e2e_rc=run([sys.executable,str(ROOT/"scripts/build_e2e_status.py")])
    consistency=read_json(CONSISTENCY); maintenance=read_json(MAINTENANCE); e2e=read_json(E2E)
    consistency_ok=is_complete_consistency_report(consistency) and consistency.get("status") in {"PASS","WARNING"} and consistency.get("hard_error_count")==0
    maintenance_ok=maintenance.get("status") in {"PASS","WARNING","DEGRADED"}; e2e_ok=e2e.get("status") in {"READY","DEGRADED"}
    accepted=all(x==0 for x in (quality_rc,state_rc,query_rc,consistency_rc,maintenance_rc,e2e_rc)) and consistency_ok and maintenance_ok and e2e_ok and not report_error
    print(json.dumps({"acceptance":"PASS" if accepted else "FAIL","quality_rebuild":quality_rc==0,"state_context":state_rc==0,"query_context":query_rc==0,"consistency_report_valid":not report_error,"consistency":consistency.get("status"),"maintenance":maintenance.get("status"),"e2e":e2e.get("status"),"report_error":report_error,"mutation_sha":args.mutation_sha,"recursive_push_required":False},ensure_ascii=False))
    return 0 if accepted else 1
if __name__=="__main__": raise SystemExit(main())
