"""Run the existing production acceptance chain after a mutation.

This is orchestration, not a second checker: consistency, maintenance and E2E
remain the canonical implementations. It deliberately performs no git push or
workflow dispatch, so acceptance cannot depend on a recursive GITHUB_TOKEN
event.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSISTENCY = ROOT / "data" / "state" / "system_consistency.json"
MAINTENANCE = ROOT / "data" / "state" / "maintenance_health.json"
E2E = ROOT / "data" / "state" / "e2e_status.json"


def run(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False)
    return completed.returncode


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical post-write production acceptance.")
    parser.add_argument("--mutation-sha", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()

    # Rebuild derived execution quality before the fresh consistency gate so
    # the gate and all downstream consumers see the same canonical trade facts.
    quality_rc = run([sys.executable, str(ROOT / "scripts" / "build_execution_quality.py")])
    with tempfile.NamedTemporaryFile(prefix="etf-system-consistency-", suffix=".json", delete=False) as handle:
        fresh_report_path = handle.name
    try:
        consistency_rc = run([
            sys.executable,
            str(ROOT / "scripts" / "check_system_consistency.py"),
            "--no-persist",
            "--report-path",
            fresh_report_path,
        ])
        fresh_consistency = read_json(Path(fresh_report_path))
        # Publish the exact final normalized report produced by this invocation
        # into the local acceptance workspace. Maintenance and E2E consume this
        # file; neither reads the pre-existing main snapshot.
        CONSISTENCY.write_text(json.dumps(fresh_consistency, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        try:
            Path(fresh_report_path).unlink()
        except OSError:
            pass
    maintenance_rc = run([sys.executable, str(ROOT / "scripts" / "maintenance_guard.py")])
    e2e_rc = run([sys.executable, str(ROOT / "scripts" / "build_e2e_status.py")])

    consistency = read_json(CONSISTENCY)
    maintenance = read_json(MAINTENANCE)
    e2e = read_json(E2E)
    consistency_ok = consistency.get("status") in {"PASS", "WARNING"} and int(consistency.get("hard_error_count") or 0) == 0
    maintenance_ok = maintenance.get("status") in {"PASS", "WARNING", "DEGRADED"} and maintenance.get("status") != "FAIL"
    e2e_ok = e2e.get("status") in {"READY", "DEGRADED"}
    accepted = quality_rc == 0 and consistency_rc == 0 and maintenance_rc == 0 and e2e_rc == 0 and consistency_ok and maintenance_ok and e2e_ok

    print(json.dumps({
        "acceptance": "PASS" if accepted else "FAIL",
        "quality_rebuild": "PASS" if quality_rc == 0 else "FAIL",
        "mutation_sha": args.mutation_sha,
        "consistency": consistency.get("status"),
        "maintenance": maintenance.get("status"),
        "e2e": e2e.get("status"),
        "persisted_paths": [
            "data/state/system_consistency.json",
            "data/state/maintenance_health.json",
            "data/state/maintenance_diagnostic.json",
            "data/state/e2e_status.json",
        ],
        "recursive_push_required": False,
    }, ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
