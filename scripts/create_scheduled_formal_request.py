from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from formal_entry_contract import build_identity

ROOT = Path(__file__).resolve().parents[1]
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
BEIJING = timezone(timedelta(hours=8))

# GitHub Actions cron is UTC; these are the only four automatic Beijing nodes.
SLOTS = {
    "0 2 * * 1-5": (10, 0),
    "0 3 * * 1-5": (11, 0),
    "30 5 * * 1-5": (13, 30),
    "30 6 * * 1-5": (14, 30),
}


def classify_schedule_invocation(entry_mode: str, scheduled_cron: str) -> str:
    mode = str(entry_mode or "").strip().upper()
    if mode == "MANUAL_SCHEDULED_TASK":
        return "RUN"
    if mode != "AUTOMATIC_SCHEDULED_TASK":
        return "TERMINAL"
    return "RUN" if str(scheduled_cron or "").strip() in SLOTS else "SKIP"


def build_scheduled_request(
    *,
    now: datetime,
    task_id: str,
    task_run_id: str,
    occurrence_id: str,
) -> dict:
    occurrence = str(occurrence_id or "").strip()
    if not occurrence:
        raise ValueError("occurrence_id is required")
    if not task_id or not task_run_id:
        raise ValueError("task_id and task_run_id are required")
    market_date = now.date().isoformat()
    payload = {
        "request_id": (
            "fd_" + now.strftime("%Y%m%d_%H%M%S") + "_"
            + "github_scheduled_formal_analysis"
        ),
        "requested_at_beijing": now.isoformat(),
        "request_type": "MARKET_QUOTE_REFRESH",
        "source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        "entry_mode": "AUTOMATIC_SCHEDULED_TASK",
        "task_id": str(task_id),
        "task_run_id": str(task_run_id),
        "occurrence": occurrence,
        "occurrence_id": occurrence,
        "market_date": market_date,
        "triggered_at_beijing": now.isoformat(),
        "intent": {"query_intent": "FORMAL_INTRADAY_ANALYSIS"},
        "query_intent": "FORMAL_INTRADAY_ANALYSIS",
        "force_refresh": True,
        "require_post_request_snapshot": True,
        "reuse_inflight_refresh": True,
        "duplicate_equivalent_refresh_forbidden": True,
        "ingress_owner": "GITHUB_SCHEDULED_FORMAL_DECISION",
    }
    identity = build_identity(payload)
    payload["identity_dedup_key"] = identity.dedup_key
    return payload


def _existing_occurrence(occurrence_id: str) -> str:
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in REQUEST_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("source") == "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
            and payload.get("occurrence_id") == occurrence_id
        ):
            return str(payload.get("request_id") or "")
    return ""


def main() -> int:
    cron = os.environ.get("SCHEDULED_CRON", "").strip()
    mode = os.environ.get("ENTRY_MODE", "AUTOMATIC_SCHEDULED_TASK").strip()
    decision = classify_schedule_invocation(mode, cron)
    if decision == "SKIP":
        print("FORMAL_SCHEDULE_INGRESS=SKIP")
        return 0
    if decision == "TERMINAL":
        raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_ENTRY_MODE")

    now = datetime.now(BEIJING)
    slot = SLOTS.get(cron)
    if slot is None:
        raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_AUTOMATIC_SLOT")
    hour, minute = slot
    # Automatic task identity is supplied by the workflow runtime. Never
    # synthesize task/occurrence identity when the runtime did not provide it.
    task_id = os.environ.get("TASK_ID", "").strip()
    task_run_id = os.environ.get("TASK_RUN_ID", "").strip()
    occurrence = os.environ.get("OCCURRENCE_ID", "").strip()
    if not task_id or not task_run_id or not occurrence:
        raise SystemExit(
            "FORMAL_SCHEDULE_INGRESS=MISSING_AUTOMATIC_RUNTIME_IDENTITY"
        )
    if not occurrence.startswith("scheduled_"):
        raise SystemExit(
            "FORMAL_SCHEDULE_INGRESS=INVALID_AUTOMATIC_OCCURRENCE"
        )
    existing = _existing_occurrence(occurrence)
    if existing:
        print(f"FORMAL_SCHEDULE_INGRESS=EXISTS request_id={existing}")
        return 0

    payload = build_scheduled_request(
        now=now,
        task_id=task_id,
        task_run_id=task_run_id,
        occurrence_id=occurrence,
    )
    path = REQUEST_DIR / f"{payload['request_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FORMAL_SCHEDULE_INGRESS=CREATED request_id={payload['request_id']} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
