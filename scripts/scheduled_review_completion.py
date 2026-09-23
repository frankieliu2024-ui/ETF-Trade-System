from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from runtime_session_gate import classify_live_snapshot_request
    from process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )
except ModuleNotFoundError:
    from scripts.runtime_session_gate import classify_live_snapshot_request
    from scripts.process_state_sync_request import (
        validate_current_lifecycle_contract,
        validate_managed_position_lifecycle,
    )

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
REQUIRED_FULL_DAY_FIELDS = (
    "market_date",
    "review_scope",
    "review_version",
    "reviewed_at_beijing",
    "risk_permission",
    "opportunity_status",
    "main_candidate",
    "lifecycle",
    "holding_actions",
    "amount_yuan",
    "action",
    "data_time",
    "account_close",
    "capital_efficiency",
    "capital_efficiency_reason",
    "next_unit_capital_use",
    "max_risk",
    "error_reason",
    "information_gap",
    "most_fragile_hypothesis",
    "overseas_overestimate",
    "overseas_a_share_divergence",
    "case_mode",
    "review_boundary",
)


def _safe_id(value: object) -> str:
    value = str(value or "").strip()
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("scheduled review completion requires a safe request identity")
    return value


def validate_full_day_review(review: dict, account: dict | None = None) -> None:
    if not isinstance(review, dict) or not review:
        raise ValueError("completed formal_review payload is required")
    if str(review.get("review_scope") or "").strip().upper() != "FULL_DAY":
        raise ValueError("scheduled full-day completion requires review_scope=FULL_DAY")
    missing = [key for key in REQUIRED_FULL_DAY_FIELDS if key not in review or review[key] in (None, "")]
    if missing:
        raise ValueError("formal_review missing required full-day fields: " + ", ".join(missing))
    data_time = review.get("data_time")
    if not isinstance(data_time, dict) or not str(data_time.get("close_snapshot") or "").strip():
        raise ValueError("formal_review.data_time.close_snapshot is required")
    if not isinstance(review.get("account_close"), dict):
        raise ValueError("formal_review.account_close must be an object")
    if not isinstance(review.get("lifecycle"), dict) or not isinstance(review.get("holding_actions"), dict):
        raise ValueError("formal_review lifecycle and holding_actions must be objects")
    if account is not None:
        lifecycle_error = validate_current_lifecycle_contract(review.get("lifecycle"), account)
        if lifecycle_error:
            raise ValueError(f"invalid formal review lifecycle contract: {lifecycle_error}")
        managed_error = validate_managed_position_lifecycle(
            review.get("holding_actions"), account, "formal_review.managed_positions"
        )
        if managed_error:
            raise ValueError(f"invalid formal review managed-position contract: {managed_error}")


def build_completion_request(
    formal_review: dict,
    request_id: str,
    requested_at_beijing: str,
    market_date: str = "",
    parent_request_id: str = "",
    account: dict | None = None,
) -> dict:
    """Wrap an already-formed full-day Scheduled Review for the existing state-sync owner."""
    validate_full_day_review(formal_review, account)
    review_market_date = str(formal_review.get("market_date") or "").strip()
    effective_market_date = str(market_date or review_market_date).strip()
    if not effective_market_date or effective_market_date != review_market_date:
        raise ValueError("scheduled review completion market_date must match formal_review.market_date")
    payload = {
        "request_id": _safe_id(request_id),
        "request_type": "STATE_SYNC_ONLY",
        "formal_fact_type": "FORMAL_POST_CLOSE_REVIEW",
        "source": "CHATGPT_SCHEDULED_REVIEW_ACTOR",
        "interaction_scenario": "POST_CLOSE_REVIEW",
        "requested_at_beijing": str(requested_at_beijing or "").strip(),
        "market_date": effective_market_date,
        "formal_review": formal_review,
    }
    if parent_request_id:
        payload["parent_request_id"] = _safe_id(parent_request_id)
    if not payload["requested_at_beijing"]:
        raise ValueError("scheduled review completion requires requested_at_beijing")
    if classify_live_snapshot_request(payload) != "STATE_SYNC_ONLY":
        raise ValueError("scheduled review completion must remain STATE_SYNC_ONLY")
    return payload


def write_completion_request(
    review_path: str,
    request_id: str,
    requested_at_beijing: str,
    market_date: str = "",
    parent_request_id: str = "",
    output_path: str = "",
) -> Path:
    source = (ROOT / review_path).resolve()
    if ROOT not in source.parents:
        raise ValueError("review path must stay inside repository root")
    review = json.loads(source.read_text(encoding="utf-8"))
    account_path = ROOT / "data/state/account_fact.json"
    account = json.loads(account_path.read_text(encoding="utf-8")) if account_path.exists() else None
    payload = build_completion_request(
        review, request_id, requested_at_beijing, market_date, parent_request_id, account
    )
    target = (ROOT / output_path).resolve() if output_path else REQUEST_DIR / f"{payload['request_id']}.json"
    if ROOT not in target.parents or target.suffix != ".json":
        raise ValueError("output path must be a repository JSON path")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == serialized:
            return target
        raise FileExistsError("completion request path already contains a different payload")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialized, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-review", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--market-date", default="")
    parser.add_argument("--parent-request-id", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    target = write_completion_request(
        args.formal_review,
        args.request_id,
        args.requested_at_beijing,
        args.market_date,
        args.parent_request_id,
        args.output,
    )
    print(json.dumps({"ok": True, "request": str(target.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
