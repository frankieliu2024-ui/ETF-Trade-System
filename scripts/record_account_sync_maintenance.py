from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--request-path", required=True)
    parser.add_argument("--owner-outcome", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    path = root / "data/state/maintenance_health.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    outcome = str(args.owner_outcome).lower()
    success = outcome == "success"
    data["checked_at"] = now
    data["account_sync"] = {
        "request_id": args.request_id,
        "request_path": args.request_path,
        "owner": "scripts/process_state_sync_request.py",
        "workflow_run_id": args.run_id,
        "status": "PASS" if success else "OWNER_REJECTED",
        "failure_class": "" if success else "OWNER_REJECTED",
        "readback_status": "NOT_RUN" if not success else "PENDING_DOWNSTREAM_READBACK",
        "canonical_fact_persisted": success,
        "recorded_at_beijing": now,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["account_sync"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
