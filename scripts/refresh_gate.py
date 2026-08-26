from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
CURRENT = ROOT / "data" / "state" / "CURRENT.json"
RUNTIME_HEALTH = ROOT / "data" / "state" / "runtime_health.json"
QUERY_CONTEXT = ROOT / "data" / "state" / "query_context.json"
DECISION_CONTEXT = ROOT / "data" / "state" / "decision_context.json"
RUNTIME_POLICY = ROOT / "config" / "runtime_policy.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _explicit_latest_intent() -> str:
    policy = load_json(RUNTIME_POLICY, {})
    preference = policy.get("query_time_refresh_preference") or {}
    return str(preference.get("explicit_latest_query_intent") or "EXPLICIT_LATEST").upper()


def _requires_wait(req: dict) -> bool:
    if req.get("wait_for_refresh") is True or req.get("require_post_request_snapshot") is True:
        return True
    intent = str(req.get("query_intent") or req.get("request_kind") or "").upper()
    return bool(intent and intent == _explicit_latest_intent())


def latest_wait_request() -> tuple[Path | None, dict]:
    candidates = []
    if REQUEST_DIR.exists():
        for path in REQUEST_DIR.glob("*.json"):
            try:
                req = load_json(path)
            except Exception:
                continue
            if not _requires_wait(req):
                continue
            requested = parse_time(req.get("requested_at_beijing"))
            if requested is not None:
                candidates.append((requested, path, req))
    if not candidates:
        return None, {}
    _, path, req = max(candidates, key=lambda x: x[0])
    return path, req


def build_gate() -> dict:
    path, req = latest_wait_request()
    if not req:
        return {
            "status": "NOT_REQUESTED",
            "formal_analysis_allowed": True,
            "formal_decision_persist_allowed": True,
            "fallback_allowed": True,
            "rule": "普通盘中请求允许即时补采失败后回退最近有效快照；用户明确要求最新/当前/现在行情或等待指定节点新数据时，必须先建立显式query-time请求并关闭请求前快照回退。",
        }

    current = load_json(CURRENT)
    health = load_json(RUNTIME_HEALTH)
    requested = parse_time(req.get("requested_at_beijing"))
    target = parse_time(req.get("requested_market_time") or req.get("requested_at_beijing"))
    threshold_candidates = [x for x in (requested, target) if x is not None]
    threshold = max(threshold_candidates) if threshold_candidates else None
    captured_text = current.get("data_freshness", {}).get("captured_at_beijing") or current.get("captured_at")
    captured = parse_time(captured_text)
    ready = threshold is not None and captured is not None and captured >= threshold

    finished = parse_time(health.get("finished_at") or health.get("captured_at_beijing"))
    failed_after_request = (
        str(health.get("status") or "").upper() == "FAILED"
        and requested is not None
        and finished is not None
        and finished >= requested
        and not ready
    )
    status = "READY" if ready else ("FAILED" if failed_after_request else "PENDING")
    explicit_latest = str(req.get("query_intent") or req.get("request_kind") or "").upper() == _explicit_latest_intent()
    allow_fallback = req.get("allow_wait_refresh_fallback") is True and not explicit_latest
    return {
        "status": status,
        "formal_analysis_allowed": ready or allow_fallback,
        "formal_decision_persist_allowed": ready or allow_fallback,
        "fallback_allowed": allow_fallback,
        "query_intent": req.get("query_intent") or req.get("request_kind") or "",
        "request_file": str(path.relative_to(ROOT)).replace("\\", "/") if path else "",
        "request_id": str(req.get("request_id") or (path.stem if path else "")),
        "requested_at_beijing": req.get("requested_at_beijing", ""),
        "requested_market_time": req.get("requested_market_time", req.get("requested_at_beijing", "")),
        "current_snapshot_time": captured_text or "",
        "resolved_snapshot_time": captured_text if ready else "",
        "latest_snapshot": current.get("latest_snapshot", ""),
        "failure_reason": health.get("reason", "") if failed_after_request else "",
        "rule": "EXPLICIT_LATEST或wait_for_refresh请求必须等本次请求之后且不早于requested_market_time的新CURRENT发布后才可称为最新/当前行情；请求前即使仍处普通FRESH窗口，也不得冒充本次最新查询结果。仅非EXPLICIT_LATEST且用户明确授权时可回退旧快照。",
    }


def _formal_decision_matches_requested_refresh(req: dict, gate: dict) -> tuple[bool, str]:
    decision = req.get("formal_decision")
    if not isinstance(decision, dict):
        return True, "NO_FORMAL_DECISION"
    if not _requires_wait(req):
        return True, "NO_EXPLICIT_REFRESH_REQUIREMENT"

    target = parse_time(req.get("requested_market_time") or req.get("requested_at_beijing"))
    decision_as_of = parse_time(decision.get("data_as_of_beijing"))
    resolved = parse_time(gate.get("resolved_snapshot_time"))
    if target is None:
        return False, "REQUESTED_MARKET_TIME_INVALID"
    if decision_as_of is None:
        return False, "FORMAL_DECISION_DATA_TIME_MISSING"
    if decision_as_of < target:
        return False, "FORMAL_DECISION_PREDATES_REQUESTED_REFRESH"
    if resolved is not None and decision_as_of > resolved:
        return False, "FORMAL_DECISION_DATA_TIME_AFTER_RESOLVED_SNAPSHOT"
    return True, "FORMAL_DECISION_REFRESH_ALIGNED"


def guard_request(path: Path) -> int:
    req = load_json(path)
    if not isinstance(req.get("formal_decision"), dict):
        return 0
    if req.get("allow_wait_refresh_fallback") is True and str(req.get("query_intent") or "").upper() != _explicit_latest_intent():
        return 0
    gate = build_gate()
    if not gate.get("formal_decision_persist_allowed", True):
        print(json.dumps({"ok": False, "reason": "WAIT_FOR_REFRESH_NOT_READY", "refresh_gate": gate, "request": str(path)}, ensure_ascii=False))
        return 3
    aligned, reason = _formal_decision_matches_requested_refresh(req, gate)
    if not aligned:
        print(json.dumps({
            "ok": False,
            "reason": reason,
            "refresh_gate": gate,
            "decision_data_as_of_beijing": (req.get("formal_decision") or {}).get("data_as_of_beijing", ""),
            "requested_market_time": req.get("requested_market_time") or req.get("requested_at_beijing") or "",
            "request": str(path),
        }, ensure_ascii=False))
        return 4
    return 0


def annotate_contexts() -> None:
    gate = build_gate()
    for path in (QUERY_CONTEXT, DECISION_CONTEXT):
        if not path.exists():
            continue
        obj = load_json(path)
        obj["refresh_gate"] = gate
        obj["formal_analysis_allowed"] = bool(gate.get("formal_analysis_allowed", True))
        if path == QUERY_CONTEXT:
            read_plan = obj.setdefault("decision_read_plan", {})
            if gate.get("status") in {"PENDING", "FAILED"} and not gate.get("fallback_allowed", True):
                read_plan["mode"] = "WAIT_FOR_REFRESH"
                read_plan["refresh_gate"] = gate
                read_plan["acquisition_priority"] = ["WAIT_FOR_REQUESTED_REFRESH", "EXPLICIT_FAILURE_NO_FALLBACK"]
            else:
                read_plan["refresh_gate"] = gate
        atomic_write(path, obj)
    print(json.dumps({"ok": True, "refresh_gate": gate}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--guard-request")
    group.add_argument("--annotate-contexts", action="store_true")
    args = parser.parse_args()
    if args.guard_request:
        return guard_request((ROOT / args.guard_request).resolve())
    annotate_contexts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
