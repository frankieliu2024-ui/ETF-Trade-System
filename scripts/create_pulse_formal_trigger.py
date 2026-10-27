from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scripts.create_scheduled_formal_request import AUTOMATIC_MODE, build_scheduled_request

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "state" / "decision_trigger.json"
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
BEIJING = timezone(timedelta(hours=8))


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def create_request(*, now: datetime, task_run_id: str, occurrence_id: str) -> dict:
    trigger = _read(STATE_PATH)
    if not bool(trigger.get("requires_formal_reassessment")):
        return {"status": "NO_TRIGGER", "reason": "formal_reassessment_not_required"}
    if str(trigger.get("trigger_type") or "") == "FORMAL_REQUEST":
        return {"status": "NO_TRIGGER", "reason": "formal_request_is_downstream_fact"}
    trigger_key = str(trigger.get("idempotency_key") or "").strip()
    if not trigger_key:
        return {"status": "FAILED", "reason": "trigger_idempotency_key_missing"}

    existing = []
    for candidate in REQUEST_DIR.glob("*.json"):
        payload = _read(candidate)
        if str(payload.get("trigger_idempotency_key") or "").strip() == trigger_key:
            existing.append(candidate)
    if existing:
        return {"status": "ALREADY_CONSUMED", "idempotency_key": trigger_key, "request_path": str(existing[0].relative_to(ROOT))}
    request = build_scheduled_request(
        now=now,
        task_id="ETF正式决策",
        task_run_id=task_run_id,
        occurrence_id=occurrence_id,
        entry_mode=AUTOMATIC_MODE,
    )
    request.update({
        "trigger_origin": "FIXED_MARKET_PULSE",
        "trigger_idempotency_key": trigger_key,
        "trigger_type": str(trigger.get("trigger_type") or "FORMAL_REASSESSMENT"),
        "trigger_evidence_change": str(trigger.get("evidence_change") or ""),
        "trigger_evidence_time": str(trigger.get("evidence_time") or ""),
        "trigger_applicable_object": str(trigger.get("applicable_object") or ""),
        "no_trade_authority": True,
    })
    path = REQUEST_DIR / f"{request['request_id']}.json"
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    trigger["pending_trigger"] = True
    trigger["status"] = "FORMAL_REQUEST_CREATED"
    trigger["request_id"] = request["request_id"]
    trigger["triggered_at"] = request["triggered_at_beijing"]
    trigger["consumer"] = "create_pulse_formal_trigger.py"
    STATE_PATH.write_text(json.dumps(trigger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "CREATED", "request_id": request["request_id"], "request_path": str(path.relative_to(ROOT)), "trigger_idempotency_key": trigger_key}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-run-id", required=True)
    parser.add_argument("--occurrence-id", required=True)
    args = parser.parse_args()
    now = datetime.now(BEIJING)
    result = create_request(now=now, task_run_id=args.task_run_id, occurrence_id=args.occurrence_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("status") == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
