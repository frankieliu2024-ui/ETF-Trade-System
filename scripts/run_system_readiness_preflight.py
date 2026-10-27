"""Run the read-only ETF system readiness and handoff preflight.

This command is intentionally side-effect free: it reads the current checkout,
runs the isolated three-task pipeline replay, and emits a fresh JSON result.
It does not write production state, dispatch workflows, or call PushPlus.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "ETF_SYSTEM_INDEX.md",
    ".github/workflows/market-snapshot.yml",
    ".github/workflows/decision-notification.yml",
    "scripts/build_query_context.py",
    "scripts/build_e2e_status.py",
    "scripts/formal_request_consumer.py",
    "scripts/scheduled_review_completion.py",
    "scripts/scheduled_review_completion_recovery.py",
    "scripts/process_state_sync_request.py",
    "scripts/notification_center.py",
    "tests/test_isolated_three_task_pipeline_replay.py",
    "scripts/run_scheduled_system_review_actor.py",
)
TASKS = ("ETF系统复核", "ETF交易复盘", "ETF正式决策")


def check_required_files() -> dict[str, object]:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    return {"status": "PASS" if not missing else "FAIL", "missing": missing}


def check_single_notification_owner() -> dict[str, object]:
    workflow_dir = ROOT / ".github" / "workflows"
    senders = []
    for path in workflow_dir.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "PUSHPLUS_TOKEN" in text or "PUSHPLUS_URL" in text:
            senders.append(path.name)
    return {
        "status": "PASS" if senders == ["decision-notification.yml"] else "FAIL",
        "pushplus_workflows": senders,
    }


def check_task_boundaries() -> dict[str, object]:
    config = ROOT / "config" / "maintenance" / "task_entry_scheduling_protocol.json"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    missing = [task for task in TASKS if task not in text]
    return {"status": "PASS" if not missing else "FAIL", "missing_tasks": missing}


def run_replay() -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "unittest",
        "-q",
        "tests.test_isolated_three_task_pipeline_replay",
        "tests.test_three_task_notification_replay_harness",
        "tests.test_scheduled_review_completion_recovery",
        "tests.test_three_task_notification_e2e_fault_injection",
        "tests.test_task_entry_scheduling_governance",
    ]
    env = dict(__import__("os").environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "scripts") + ((":" + existing_pythonpath) if existing_pythonpath else "")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()
    checks = {
        "required_files": check_required_files(),
        "single_notification_owner": check_single_notification_owner(),
        "task_boundaries": check_task_boundaries(),
        "isolated_replay": run_replay(),
    }
    failed = [name for name, result in checks.items() if result["status"] != "PASS"]
    result = {
        "schema_version": "1.0",
        "preflight_type": "ETF_SYSTEM_READINESS_AND_HANDOFF",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "status": "FAIL" if failed else "PASS",
        "failed_checks": failed,
        "checks": checks,
        "production_notification_sent": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
