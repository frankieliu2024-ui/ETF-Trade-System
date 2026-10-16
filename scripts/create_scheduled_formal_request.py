from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from formal_entry_contract import build_identity
except ModuleNotFoundError:  # package import in repository tests
    from scripts.formal_entry_contract import build_identity

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

CONVERSATION_MODE = "MANUAL_CONVERSATION"
MANUAL_MODE = "MANUAL_SCHEDULED_TASK"
AUTOMATIC_MODE = "AUTOMATIC_SCHEDULED_TASK"
SCHEDULED_REVIEW_SOURCE = "CHATGPT_SCHEDULED_REVIEW_ACTOR"
TRIGGER_SOURCES = {
    CONVERSATION_MODE: "CHATGPT_MANUAL_FORMAL_ANALYSIS",
    MANUAL_MODE: "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
    AUTOMATIC_MODE: "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
}


def classify_schedule_invocation(entry_mode: str, scheduled_cron: str) -> str:
    mode = str(entry_mode or "").strip().upper()
    if mode in {CONVERSATION_MODE, MANUAL_MODE}:
        return "RUN"
    if mode != AUTOMATIC_MODE:
        return "TERMINAL"
    return "RUN" if str(scheduled_cron or "").strip() in SLOTS else "SKIP"


def _manual_identity(now: datetime, entry_mode: str) -> tuple[str, str]:
    suffix = secrets.token_hex(4)
    value = now.strftime("%Y%m%dT%H%M%S%z")
    prefix = "conversation" if entry_mode == CONVERSATION_MODE else "manual-scheduled"
    run_id = f"{prefix}-{value}-{suffix}"
    return run_id, run_id


def build_scheduled_request(
    *,
    now: datetime,
    task_id: str,
    task_run_id: str,
    occurrence_id: str,
    entry_mode: str = AUTOMATIC_MODE,
) -> dict:
    mode = str(entry_mode or "").strip().upper()
    if mode not in TRIGGER_SOURCES:
        raise ValueError("unsupported scheduled task entry_mode")
    occurrence = str(occurrence_id or "").strip()
    if not occurrence:
        raise ValueError("occurrence_id is required")
    if not task_id or not task_run_id:
        raise ValueError("task_id and task_run_id are required")
    if mode == AUTOMATIC_MODE and not occurrence.startswith("scheduled_"):
        raise ValueError("automatic occurrence must start with scheduled_")
    if mode in {CONVERSATION_MODE, MANUAL_MODE} and occurrence.startswith("scheduled_"):
        raise ValueError("manual occurrence must not impersonate automatic occurrence")

    requested_at = now.astimezone(BEIJING).isoformat()
    request_id = (
        "fd_" + now.astimezone(BEIJING).strftime("%Y%m%d_%H%M%S")
        + "_scheduled_formal_review_" + secrets.token_hex(4)
    )
    trigger_source = TRIGGER_SOURCES[mode]
    identity = build_identity({
        "request_id": request_id,
        "source": trigger_source,
        "entry_mode": mode,
        "task_id": str(task_id),
        "task_run_id": str(task_run_id),
        "occurrence": occurrence,
        "market_date": now.astimezone(BEIJING).date().isoformat(),
    })
    return {
        "request_id": request_id,
        "parent_request_id": request_id,
        "request_type": "STATE_SYNC_ONLY",
        "formal_fact_type": "FORMAL_SCHEDULED_REVIEW",
        "source": SCHEDULED_REVIEW_SOURCE,
        "trigger_source": trigger_source,
        "entry_mode": mode,
        "interaction_scenario": "FORMAL_DECISION",
        "requested_at_beijing": requested_at,
        "task_id": str(task_id),
        "task_run_id": str(task_run_id),
        "occurrence": identity.occurrence,
        "occurrence_id": identity.occurrence,
        "market_date": now.astimezone(BEIJING).date().isoformat(),
        "triggered_at_beijing": requested_at,
        "presentation_binding": {
            "report_type": "ETF_FORMAL_DECISION",
            "task_id": str(task_id),
            "task_run_id": str(task_run_id),
            "report_id": f"ETF_FORMAL_DECISION:{task_run_id}",
            "occurrence_id": identity.occurrence,
            "market_date": now.astimezone(BEIJING).date().isoformat(),
        },
        "no_trade_authority": True,
        "identity_dedup_key": identity.dedup_key,
    }


def _existing_occurrence(occurrence_id: str) -> str:
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in REQUEST_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("formal_fact_type") == "FORMAL_SCHEDULED_REVIEW"
            and payload.get("occurrence_id") == occurrence_id
        ):
            return str(payload.get("request_id") or "")
    return ""


def main() -> int:
    mode = os.environ.get("ENTRY_MODE", AUTOMATIC_MODE).strip().upper()
    cron = os.environ.get("SCHEDULED_CRON", "").strip()
    decision = classify_schedule_invocation(mode, cron)
    if decision == "SKIP":
        print("FORMAL_SCHEDULE_INGRESS=SKIP")
        return 0
    if decision == "TERMINAL":
        raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_ENTRY_MODE")

    now = datetime.now(BEIJING)
    task_id = os.environ.get("TASK_ID", "").strip() or "ETF正式决策"
    task_run_id = os.environ.get("TASK_RUN_ID", "").strip()
    occurrence = os.environ.get("OCCURRENCE_ID", "").strip()

    if mode in {CONVERSATION_MODE, MANUAL_MODE}:
        if not task_run_id or not occurrence:
            task_run_id, occurrence = _manual_identity(now, mode)
    else:
        if cron not in SLOTS:
            raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_AUTOMATIC_SLOT")
        if not task_run_id or not occurrence:
            raise SystemExit(
                "FORMAL_SCHEDULE_INGRESS=MISSING_AUTOMATIC_RUNTIME_IDENTITY"
            )
        if not occurrence.startswith("scheduled_"):
            raise SystemExit("FORMAL_SCHEDULE_INGRESS=INVALID_AUTOMATIC_OCCURRENCE")

    existing = _existing_occurrence(occurrence)
    if existing:
        print(f"FORMAL_SCHEDULE_INGRESS=EXISTS request_id={existing}")
        return 0

    payload = build_scheduled_request(
        now=now,
        task_id=task_id,
        task_run_id=task_run_id,
        occurrence_id=occurrence,
        entry_mode=mode,
    )
    path = REQUEST_DIR / f"{payload['request_id']}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"FORMAL_SCHEDULE_INGRESS=CREATED request_id={payload['request_id']} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
