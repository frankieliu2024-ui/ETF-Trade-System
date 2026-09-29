"""Run canonical post-write acceptance without destroying last-known state."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile
from check_production_mutation_protocol import candidate_change_acceptance_allows_global_failure
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONSISTENCY=ROOT/"data/state/system_consistency.json"; MAINTENANCE=ROOT/"data/state/maintenance_health.json"; E2E=ROOT/"data/state/e2e_status.json"
def run(command, env_overrides=None):
    env=os.environ.copy()
    if env_overrides: env.update(env_overrides)
    return subprocess.run(command,cwd=ROOT,env=env,check=False).returncode
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
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); return True
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--mutation-sha",default=os.environ.get("GITHUB_SHA","")); parser.add_argument("--result-path",default=""); parser.add_argument("--scope-path",default=""); args=parser.parse_args()
    # build_state_context is the canonical acceptance rebuild for execution_quality too;
    # do not invoke the same deterministic builder twice on the Fast Path.
    state_rc=run([sys.executable,str(ROOT/"scripts/build_state_context.py")])
    quality_rc=state_rc
    query_rc=run([sys.executable,str(ROOT/"scripts/build_query_context.py")])
    with tempfile.NamedTemporaryFile(prefix="etf-system-consistency-",suffix=".json",delete=False) as handle: fresh_path=Path(handle.name)
    report_error=""
    try:
        consistency_rc=run([sys.executable,str(ROOT/"scripts/check_system_consistency.py"),"--no-persist","--report-path",str(fresh_path)])
        fresh=read_json(fresh_path)
        if not is_complete_consistency_report(fresh): report_error="checker did not produce a complete report; canonical state preserved"
        # Keep the validated report ephemeral until maintenance/E2E consume it.
    finally:
        pass
    if report_error: maintenance_rc=e2e_rc=1
    else:
        maintenance_rc=run([sys.executable,str(ROOT/"scripts/maintenance_guard.py")], {"ETF_CONSISTENCY_REPORT_PATH": str(fresh_path)})
        e2e_rc=run([sys.executable,str(ROOT/"scripts/build_e2e_status.py")])
        # Persist only after both downstream consumers have seen the same
        # validated report. Invalid reports never reach this path.
        persist_consistency_report_if_valid(fresh, CONSISTENCY)
    consistency=read_json(CONSISTENCY); maintenance=read_json(MAINTENANCE); e2e=read_json(E2E)
    consistency_ok=is_complete_consistency_report(consistency) and consistency.get("status") in {"PASS","WARNING"} and consistency.get("hard_error_count")==0
    scope=read_json(Path(args.scope_path)) if args.scope_path else {}
    changed_files=[str(x) for x in (scope.get("paths") or [])]
    failed_checks=[x for x in (consistency.get("checks") or []) if isinstance(x,dict) and x.get("status")=="FAIL"]
    unrelated_global_failures_allowed=bool(changed_files) and candidate_change_acceptance_allows_global_failure(changed_files=changed_files,failed_checks=failed_checks)
    change_consistency_ok=consistency_ok or unrelated_global_failures_allowed
    maintenance_ok=maintenance.get("status") in {"PASS","WARNING","DEGRADED"}
    maintenance_only_global_consistency=(
        maintenance.get("status")=="FAIL"
        and maintenance.get("system_consistency_status")=="FAIL"
        and (maintenance.get("reconciliation") or {}).get("status")=="PASS"
        and change_consistency_ok
    )
    change_maintenance_ok=maintenance_ok or maintenance_only_global_consistency
    e2e_ok=e2e.get("status") in {"READY","DEGRADED"}
    accepted=all(x==0 for x in (quality_rc,state_rc,query_rc,e2e_rc)) and change_consistency_ok and change_maintenance_ok and e2e_ok and not report_error
    result={"acceptance":"PASS" if accepted else "FAIL","global_health":"PASS" if consistency_ok and maintenance_ok and e2e.get("status")=="READY" else "FAIL","change_specific_acceptance":"PASS" if accepted else "FAIL","unrelated_global_failures_allowed":unrelated_global_failures_allowed,"changed_files":changed_files,"failed_checks":[str(x.get("name") or "") for x in failed_checks],"quality_rebuild":quality_rc==0,"state_context":state_rc==0,"query_context":query_rc==0,"return_codes":{"quality_rebuild":quality_rc,"state_context":state_rc,"query_context":query_rc,"consistency":consistency_rc,"maintenance":maintenance_rc,"e2e":e2e_rc},"consistency_report_valid":not report_error,"consistency":consistency.get("status"),"maintenance":maintenance.get("status"),"e2e":e2e.get("status"),"report_error":report_error,"mutation_sha":args.mutation_sha,"recursive_push_required":False}
    print(json.dumps(result,ensure_ascii=False))
    if args.result_path:
        result_path=Path(args.result_path); result_path.parent.mkdir(parents=True,exist_ok=True); result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    try: fresh_path.unlink()
    except OSError: pass
    return 0 if accepted else 1
if __name__=="__main__": raise SystemExit(main())
