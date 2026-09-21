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


def _safe_id(value: object) -> str:
    value = str(value or "").strip()
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("manual completion requires a safe request identity")
    return value


def build_completion_request(source_request: dict, formal_decision: dict, consumed_snapshot: str, completion_request_id: str = "") -> dict:
    """Transport an already-formed manual conclusion through the existing state-sync request path."""
    if not isinstance(source_request, dict) or not source_request:
        raise ValueError("source manual request is required")
    source = str(source_request.get("source") or "").strip().upper()
    intent = str(source_request.get("intent") or source_request.get("query_intent") or "").strip().upper()
    manual_sources = {"CHATGPT_MANUAL_FORMAL_ANALYSIS", "CHATGPT_USER_CONTINUE"}
    formal_intents = {"EXPLICIT_LATEST", "FORMAL_INTRADAY_ANALYSIS"}
    if source not in manual_sources or intent not in formal_intents:
        raise ValueError("source request is not a manual formal analysis")
    if not isinstance(formal_decision, dict) or not formal_decision:
        raise ValueError("completed formal_decision payload is required")
    parent_id = _safe_id(source_request.get("request_id"))
    snapshot = str(consumed_snapshot or source_request.get("consumed_snapshot") or formal_decision.get("consumed_snapshot") or "").strip()
    if not snapshot:
        raise ValueError("manual completion requires an explicit consumed PIT snapshot")
    envelope_id = _safe_id(completion_request_id or f"{parent_id}__formal_completion")
    if envelope_id == parent_id:
        raise ValueError("completion envelope identity must differ from its parent request")
    scenario = str(source_request.get("interaction_scenario") or "").strip()
    if not scenario:
        raise ValueError("source request is missing interaction_scenario")
    payload = {
        "request_id": envelope_id,
        "parent_request_id": parent_id,
        "request_type": "STATE_SYNC_ONLY",
        "source": "CHATGPT_MANUAL_FORMAL_COMPLETION",
        "interaction_scenario": scenario,
        "requested_at_beijing": source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "market_date": source_request.get("market_date"),
        "persistence_available_at_beijing": source_request.get("persistence_available_at_beijing") or source_request.get("requested_at_beijing") or source_request.get("request_time_beijing"),
        "consumed_snapshot": snapshot,
        "formal_decision": formal_decision,
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    if classify_live_snapshot_request(payload) != "STATE_SYNC_ONLY":
        raise ValueError("manual completion must remain STATE_SYNC_ONLY")
    return payload


def write_completion_request(source_request_path: str, decision_path: str, consumed_snapshot: str, output_path: str = "", completion_request_id: str = "") -> Path:
    source_path = (ROOT / source_request_path).resolve()
    decision_file = (ROOT / decision_path).resolve()
    if ROOT not in source_path.parents or ROOT not in decision_file.parents:
        raise ValueError("input paths must stay inside repository root")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    payload = build_completion_request(source, decision, consumed_snapshot, completion_request_id)
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
    parser.add_argument("--source-request", required=True)
    parser.add_argument("--formal-decision", required=True)
    parser.add_argument("--consumed-snapshot", required=True)
    parser.add_argument("--completion-request-id", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    target = write_completion_request(args.source_request, args.formal_decision, args.consumed_snapshot, args.output, args.completion_request_id)
    print(json.dumps({"ok": True, "request": str(target.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
