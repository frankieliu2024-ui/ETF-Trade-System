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

    # A Formal/System Review request is not an account-sync request.  It
    # must never overwrite the last broker account-sync receipt: that receipt
    # belongs to the account-sync owner and is the only evidence used by
    # downstream freshness/readback consumers.  Keep the non-account request
    # visible in a separate maintenance field without changing account_sync.
    if not isinstance(supplied, dict):
        data["checked_at"] = now
        data["last_non_account_maintenance"] = {
            "request_id": args.request_id,
            "request_path": args.request_path,
            "owner": "scripts/process_state_sync_request.py",
            "workflow_run_id": args.run_id,
            "status": "NOT_APPLICABLE",
            "failure_class": "NO_ACCOUNT_SYNC_REQUEST",
            "recorded_at_beijing": now,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(data["last_non_account_maintenance"], ensure_ascii=False))
        return 0

    def same(left, right):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        return left == right

    def position_value_matches(supplied_item, account_item, key, value):
        # The canonical owner normalizes convertible bonds to asset_type=OTHER
        # and preserves security_type=CONVERTIBLE_BOND. Treat that representation
        # as equivalent to the broker-ingress alias without weakening any other
        # position-field comparison.
        if key == "asset_type":
            supplied_type = str(value or "").upper()
            account_type = str(account_item.get("asset_type") or "").upper()
            account_security_type = str(account_item.get("security_type") or "").upper()
            if supplied_type == "CONVERTIBLE_BOND":
                return account_type == "CONVERTIBLE_BOND" or (
                    account_type == "OTHER" and account_security_type == "CONVERTIBLE_BOND"
                )
        return same(value, account_item.get(key))

    def converged():
        if outcome != "success" or not isinstance(supplied, dict):
            return False
        # The ingress timestamp records when the screenshot was captured;
        # the canonical account fact may use the workflow receipt timestamp for
        # updated_at. Validate the immutable screenshot timestamp separately and
        # do not reject a successful projection merely because those clocks differ.
        for key in ("total_asset", "stock_market_value", "cash"):
            if key in supplied and not same(supplied.get(key), account.get(key)):
                return False
        # The canonical owner may normalize the receipt timestamp. It is
        # provenance metadata, not the code-keyed account identity contract.
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
            # Security code is the identity key. Quantity availability is\n            # the machine fact contract; broker names and labels are display-only.\n            for key in ("quantity", "available_quantity"):\n                if key in item and not position_value_matches(item, target, key, item[key]):\n                    return False
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
    # A confirmed account-fact update invalidates any older reconciliation
    # projection. Do not preserve a stale overall PASS while only refreshing
    # the account_sync receipt; downstream maintenance must rebuild all
    # projections before health can return to PASS.
    if success:
        prior_account_updated_at = str(data.get("account_fact_updated_at") or "").strip()
        current_account_updated_at = str(account.get("updated_at") or "").strip()
        if current_account_updated_at and current_account_updated_at != prior_account_updated_at:
            data["account_fact_updated_at"] = current_account_updated_at
            equity_path = root / "data/state/etf_strategy_equity.json"
            dashboard_path = root / "ETF当前状态_DASHBOARD.md"
            equity = {}
            try:
                equity = json.loads(equity_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                equity = {}
            replay_cutoff = str(equity.get("replay_cutoff") or (equity.get("summary") or {}).get("as_of_transaction_date") or "")
            account_date = current_account_updated_at[:10]
            dashboard_text = ""
            try:
                dashboard_text = dashboard_path.read_text(encoding="utf-8")
            except OSError:
                dashboard_text = ""
            projections_fresh = (
                replay_cutoff >= account_date
                and current_account_updated_at in dashboard_text
                and str((equity.get("summary") or {}).get("coverage_status") or "").upper()
                not in {"", "DEGRADED_PENDING_LATEST_VALUATION"}
            )
            if projections_fresh:
                data["status"] = "PASS"
                data["failure_class"] = ""
                data["projection_status"] = "REBUILT_AND_READ_BACK"
                data["reconciliation_stale"] = False
                data["reconciliation_stale_reason"] = ""
                data["projection_required"] = []
                data["projections_rebuilt_at"] = now
            else:
                data["status"] = "MAINTENANCE_INCOMPLETE"
                data["failure_class"] = "PROJECTION_INCOMPLETE"
                data["projection_status"] = "ACCOUNT_FACT_UPDATED_REQUIRES_REBUILD"
                data["reconciliation_stale"] = True
                data["reconciliation_stale_reason"] = (
                    "account_fact changed; projection rebuild or same-fact Dashboard readback was not confirmed"
                )
                data["projection_required"] = [
                    "strategy_equity",
                    "risk",
                    "lifecycle",
                    "dashboard",
                    "reconciliation",
                ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["account_sync"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
