from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "market_data"
STATE_PATH = ROOT / "data" / "state" / "full_snapshot_recovery.json"


def run(script: str, *args: str) -> None:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    env = os.environ.copy()
    # Full-universe recovery deliberately stays below the configured provider
    # concurrency ceiling to reduce burst-rate 429 failures.
    env["HITHINK_MAX_WORKERS"] = "1"
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(payload: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def latest_request() -> tuple[Path, dict] | None:
    files = sorted(REQUEST_DIR.glob("full_snapshot_*.json"))
    if not files:
        return None
    path = files[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    latest = latest_request()
    if latest is None:
        print(json.dumps({"ok": True, "skipped": True, "reason": "no_full_snapshot_request"}, ensure_ascii=False))
        return 0

    path, request = latest
    request_id = str(request.get("request_id") or path.stem)
    state = load_state()
    if state.get("last_request_id") == request_id and state.get("status") == "PASS":
        print(json.dumps({"ok": True, "skipped": True, "reason": "already_processed", "request_id": request_id}, ensure_ascii=False))
        return 0

    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        run("check_system_consistency.py")
        run("cloud_runner_snapshot.py", "--node", "scheduled")
        run("build_market_delta.py")
        run("build_overseas_context.py")
        run("build_stock_context.py")
        run("build_account_stock_market.py")
        run("build_state_context.py")
        run("build_query_context.py")
        run("build_post_market_review.py")
        run("build_chatgpt_task_probe.py")
    except Exception as exc:
        save_state({
            "status": "FAILED",
            "last_request_id": request_id,
            "request_file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "started_at": started,
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "error": f"{type(exc).__name__}: {exc}"[-2000:],
        })
        raise

    current_path = ROOT / "data" / "state" / "CURRENT.json"
    current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else {}
    save_state({
        "status": "PASS",
        "last_request_id": request_id,
        "request_file": str(path.relative_to(ROOT)).replace("\\", "/"),
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "market_date": current.get("market_date", ""),
        "latest_valid_node": current.get("latest_valid_node", ""),
        "captured_at": current.get("captured_at", ""),
        "latest_snapshot": current.get("latest_snapshot", ""),
    })
    print(json.dumps({"ok": True, "request_id": request_id, "current": current}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
