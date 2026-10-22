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
    request_path = root / args.request_path
    request = {}
    if request_path.exists():
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            request = {}
    supplied = request.get("account_fact") if isinstance(request, dict) else None
    account_path = root / "data/state/account_fact.json"
    account = {}
    if account_path.exists():
        try:
            account = json.loads(account_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            account = {}

    # A Formal request is not itself an account-sync request.  Record that
    # explicitly so a successful Formal job cannot be mistaken for a
    # successful broker-fact readback.
    if not isinstance(supplied, dict):
        data["checked_at"] = now
        data["account_sync"] = {
            "request_id": args.request_id,
            "request_path": args.request_path,
            "owner": "scripts/process_state_sync_request.py",
            "workflow_run_id": args.run_id,
            "status": "NOT_APPLICABLE",
            "failure_class": "NO_ACCOUNT_SYNC_REQUEST",
            "readback_status": "NOT_APPLICABLE",
            "canonical_fact_persisted": False,
            "recorded_at_beijing": now,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(data["account_sync"], ensure_ascii=False))
        return 0

    def same(left, right):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        return left == right

    def converged():
        if outcome != "success" or not isinstance(supplied, dict):
            return False
        for key in ("updated_at", "total_asset", "stock_market_value", "cash"):
            if key in supplied and not same(supplied.get(key), account.get(key)):
                return False
        supplied_positions = supplied.get("positions")
        account_positions = account.get("positions")
        if not isinstance(supplied_positions, list) or not isinstance(account_positions, list):
            return False
        by_code = {str(item.get("code") or ""): item for item in account_positions if isinstance(item, dict)}
        for item in supplied_positions:
            if not isinstance(item, dict) or not str(item.get("code") or ""):
                return False
            target = by_code.get(str(item.get("code")))
            if target is None:
                return False
            for key, value in item.items():
                if not same(value, target.get(key)):
                    return False
        return True

    success = converged()
    failure_class = "" if success else ("OWNER_REJECTED" if outcome != "success" else "ACCOUNT_FACT_READBACK_MISMATCH")
    data["checked_at"] = now
    data["account_sync"] = {
        "request_id": args.request_id,
        "request_path": args.request_path,
        "owner": "scripts/process_state_sync_request.py",
        "workflow_run_id": args.run_id,
        "status": "PASS" if success else "OWNER_REJECTED",
        "failure_class": failure_class,
        "readback_status": "CONFIRMED" if success else "FAILED",
        "canonical_fact_persisted": success,
        "recorded_at_beijing": now,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["account_sync"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
