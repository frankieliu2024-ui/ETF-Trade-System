from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from runtime_session_gate import classify_live_snapshot_request
except ModuleNotFoundError:
    from scripts.runtime_session_gate import classify_live_snapshot_request


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "live_snapshot"


def _safe_identity(value: object) -> str:
    text = str(value or "").strip()
    if not text or any(part in text for part in ("/", "\\", "..")):
        raise ValueError("manual formal completion requires a safe request identity")
    return text


def build_completion_request(source_request: dict, formal_decision: dict) -> dict:
    """Build the second-stage STATE_SYNC_ONLY request for an already-formed Chat decision.

    This helper never infers a decision from query/decision context.  It only
    transports a completed formal decision to the existing state-sync owner.
    """
    if not isinstance(source_request, dict) or not source_request:
        raise ValueError("source request is required")
    if not isinstance(formal_decision, dict) or not formal_decision:
        raise ValueError("completed formal_decision payload is required")

    request_id = _safe_identity(source_request.get("request_id"))
    scenario = str(source_request.get("interaction_scenario") or "").strip()
    if not scenario:
        raise ValueError("source request is missing interaction_scenario")

    completed = {
        "request_id": f"{request_id}__formal_completion",
        "request_type": "STATE_SYNC_ONLY",
        "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
        "interaction_scenario": scenario,
        "parent_request_id": request_id,
        "requested_at_beijing": source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "market_date": source_request.get("market_date"),
        "persistence_available_at_beijing": source_request.get("persistence_available_at_beijing") or source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "consumed_snapshot": source_request.get("consumed_snapshot") or source_request.get("consumed_snapshot_path") or formal_decision.get("consumed_snapshot") or formal_decision.get("consumed_snapshot_path"),
        "formal_decision": formal_decision,
    }
    completed = {key: value for key, value in completed.items() if value not in (None, "")}
    if classify_live_snapshot_request(completed) != "STATE_SYNC_ONLY":
        raise ValueError("manual formal completion must remain STATE_SYNC_ONLY")
    return completed


def write_completion_request(source_request_path: str, decision_path: str, output_path: str = "") -> Path:
    source_path = (ROOT / source_request_path).resolve()
    decision_file = (ROOT / decision_path).resolve()
    if ROOT not in source_path.parents or ROOT not in decision_file.parents:
        raise ValueError("paths must stay inside repository root")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    payload = build_completion_request(source, decision)
    target = (ROOT / output_path).resolve() if output_path else REQUEST_DIR / f"{payload['request_id']}.json"
    if ROOT not in target.parents or target.suffix != ".json":
        raise ValueError("output path must be a repository JSON path")
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == serialized:
        return target
    target.write_text(serialized, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-request", required=True)
    parser.add_argument("--formal-decision", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    target = write_completion_request(args.source_request, args.formal_decision, args.output)
    print(json.dumps({"ok": True, "request": str(target.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
