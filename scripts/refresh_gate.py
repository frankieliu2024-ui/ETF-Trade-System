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
SCHEDULED_FORMAL_DECISION_INTENT = "SCHEDULED_FORMAL_DECISION"


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


def _query_intent(req: dict) -> str:
    return str(req.get("query_intent") or req.get("request_kind") or "").upper()


def _is_scheduled_formal_decision(req: dict) -> bool:
    return _query_intent(req) == SCHEDULED_FORMAL_DECISION_INTENT


def _requires_wait(req: dict) -> bool:
    if (
        req.get("force_refresh") is True
        or req.get("wait_for_refresh") is True
        or req.get("require_post_request_snapshot") is True
    ):
        return True
    intent = _query_intent(req)
    return bool(intent and intent == _explicit_latest_intent())


def latest_wait_request(preferred_path: Path | None = None) -> tuple[Path | None, dict]:
    # The triggering request is the canonical identity for this run.  Never
    # let a historical request in the shared directory take ownership of a
    # newer execution merely because it also requested a refresh.
    if preferred_path is not None and preferred_path.exists():
        req = load_json(preferred_path)
        if _requires_wait(req):
            return preferred_path, req
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


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_gate(now: datetime | None = None, request_file: str | Path | None = None) -> dict:
    preferred = None
    if request_file:
        candidate = Path(request_file)
        preferred = candidate if candidate.is_absolute() else (ROOT / candidate)
    path, req = latest_wait_request(preferred)
    if not req:
        return {
            "status": "NOT_REQUESTED",
            "formal_analysis_allowed": True,
            "formal_current_pit_analysis_allowed": True,
            "degraded_analysis_allowed": True,
            "formal_decision_persist_allowed": True,
            "fallback_allowed": True,
            "rule": "普通盘中请求允许即时补采失败后回退最近有效快照；用户明确要求最新/当前/现在行情或等待指定节点新数据时，必须先建立显式query-time请求并关闭请求前快照回退。",
        }

    current = load_json(CURRENT)
    health = load_json(RUNTIME_HEALTH)
    requested = parse_time(req.get("requested_at_beijing"))
    target = parse_time(req.get("requested_market_time") or req.get("requested_at_beijing"))
    scheduled_formal_decision = _is_scheduled_formal_decision(req)
    threshold_candidates = [x for x in (requested, target) if x is not None]
    threshold = max(threshold_candidates) if threshold_candidates else None
    captured_text = current.get("data_freshness", {}).get("captured_at_beijing") or current.get("captured_at")
    captured = parse_time(captured_text)

    checked_at = _utc_now(now)
    if scheduled_formal_decision:
        # Scheduled Formal Decision PREWARM is intentionally allowed to acquire
        # a legal post-request fact before the nominal node.  The consumer may
        # persist it only after the nominal node has actually arrived.  This is
        # distinct from EXPLICIT_LATEST, which keeps requested_market_time as a
        # hard lower bound for the returned fact itself.
        post_request_snapshot = requested is not None and captured is not None and captured >= requested
        nominal_node_reached = target is None or checked_at >= target
        ready = bool(post_request_snapshot and nominal_node_reached)
    else:
        post_request_snapshot = requested is not None and captured is not None and captured >= requested
        nominal_node_reached = True
        ready = threshold is not None and captured is not None and captured >= threshold

    finished = parse_time(health.get("finished_at") or health.get("captured_at_beijing"))
    terminal_unavailable_after_request = (
        str(health.get("status") or "").upper() in {"FAILED", "SKIPPED"}
        and requested is not None
        and finished is not None
        and finished >= requested
        and not ready
    )
    status = "READY" if ready else ("FAILED" if terminal_unavailable_after_request else "PENDING")
    explicit_latest = _query_intent(req) == _explicit_latest_intent()
    allow_fallback = req.get("allow_wait_refresh_fallback") is True and not explicit_latest
    # Expose one business-level result independently of the legacy gate status.
    # READY means the request has a legal post-request CURRENT; DEGRADED means
    # an explicitly permitted fallback is usable; NOT_READY means no formal
    # result may be produced yet. This avoids making callers infer business
    # state from PENDING/FAILED or workflow exit codes.
    request_result = (
        "READY"
        if ready
        else ("DEGRADED" if allow_fallback else ("EXPIRED" if terminal_unavailable_after_request else "NOT_READY"))
    )
    request_result_terminal = bool(ready or terminal_unavailable_after_request)
    rule = (
        "SCHEDULED_FORMAL_DECISION允许在名义节点前PREWARM形成请求后的合法CURRENT；只有名义节点实际到达后才允许正式分析/持久化。"
        "PREWARM事实仍须由Formal Decision按现行PIT/freshness/quality/session合同复核；本规则不放宽EXPLICIT_LATEST。"
        if scheduled_formal_decision
        else "EXPLICIT_LATEST或wait_for_refresh请求必须等本次请求之后且不早于requested_market_time的新CURRENT发布后才可称为最新/当前行情；请求前即使仍处普通FRESH窗口，也不得冒充本次最新查询结果。仅非EXPLICIT_LATEST且用户明确授权时可回退旧快照。"
    )
    return {
        "status": status,
        "request_result": request_result,
        "request_result_terminal": request_result_terminal,
        "request_result_reason": (
            "post_request_snapshot_ready"
            if ready
            else (health.get("reason", "") if terminal_unavailable_after_request else "awaiting_post_request_snapshot")
        ),
        # Compatibility: this legacy field means that analysis which depends on
        # the requested fresh/current PIT may proceed. It is not a blanket ban
        # on clearly-labelled degraded reasoning from other still-legal facts.
        "formal_analysis_allowed": ready or allow_fallback,
        "formal_current_pit_analysis_allowed": ready or allow_fallback,
        "degraded_analysis_allowed": True,
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
        "failure_reason": health.get("reason", "") if terminal_unavailable_after_request else "",
        "terminal_unavailable": bool(terminal_unavailable_after_request),
        "post_request_snapshot": bool(post_request_snapshot),
        "nominal_node_reached": bool(nominal_node_reached),
        "rule": rule,
    }


def _formal_decision_matches_requested_refresh(req: dict, gate: dict) -> tuple[bool, str]:
    decision = req.get("formal_decision")
    if not isinstance(decision, dict):
        return True, "NO_FORMAL_DECISION"
    if not _requires_wait(req):
        return True, "NO_EXPLICIT_REFRESH_REQUIREMENT"

    requested = parse_time(req.get("requested_at_beijing"))
    target = parse_time(req.get("requested_market_time") or req.get("requested_at_beijing"))
    decision_as_of = parse_time(decision.get("data_as_of_beijing"))
    resolved = parse_time(gate.get("resolved_snapshot_time"))
    if target is None:
        return False, "REQUESTED_MARKET_TIME_INVALID"
    if decision_as_of is None:
        return False, "FORMAL_DECISION_DATA_TIME_MISSING"
    alignment_floor = requested if _is_scheduled_formal_decision(req) else target
    if alignment_floor is None:
        return False, "REQUESTED_AT_INVALID"
    if decision_as_of < alignment_floor:
        return False, "FORMAL_DECISION_PREDATES_REQUESTED_REFRESH"
    if resolved is not None and decision_as_of > resolved:
        return False, "FORMAL_DECISION_DATA_TIME_AFTER_RESOLVED_SNAPSHOT"
    return True, "FORMAL_DECISION_REFRESH_ALIGNED"


def _manual_completion_request_bound_pit_ready(req: dict) -> bool:
    """Let canonical completion pass the legacy CURRENT gate only when the
    existing parent-bound query-time PIT contract is already action-qualified.

    The canonical decision writer independently revalidates the same boundary;
    this guard only prevents the older CURRENT-only precheck from rejecting a
    closure that query-time providers have already resolved.
    """
    if str(req.get("source") or "").upper() != "CHATGPT_MANUAL_FORMAL_COMPLETION":
        return False
    parent_id = str(req.get("parent_request_id") or "").strip()
    if not parent_id or any(part in parent_id for part in ("/", "\\\\", "..")):
        return False
    parent_path = REQUEST_DIR / f"{parent_id}.json"
    if not parent_path.exists() or not QUERY_CONTEXT.exists():
        return False
    parent = load_json(parent_path)
    if str(parent.get("request_id") or "").strip() != parent_id:
        return False
    packet = (load_json(QUERY_CONTEXT).get("decision_fact_pack") or {})
    trigger = packet.get("trigger") or {}
    action = packet.get("formal_action_readiness") or {}
    market_quote = packet.get("market_quote") or {}
    freshness = market_quote.get("decision_freshness") or {}
    return bool(
        str(trigger.get("request_id") or "").strip() == parent_id
        and action.get("ready") is True
        and str(action.get("status") or "").upper() == "READY"
        and action.get("request_scoped_pit_resolved") is True
        and freshness.get("request_time")
        and freshness.get("resolved_post_request") is True
        and str(market_quote.get("mode") or "").upper() in {"QUERY_TIME_IMMEDIATE_REFRESH", "REUSED_REQUEST_BOUND_FACTS"}
    )


def guard_request(path: Path) -> int:
    req = load_json(path)
    if str(req.get("request_type") or "").upper() == "EMERGENCY_EXTERNAL_MARKET_EVIDENCE":
        return 0
    if not isinstance(req.get("formal_decision"), dict):
        return 0
    if req.get("allow_wait_refresh_fallback") is True and _query_intent(req) != _explicit_latest_intent():
        return 0
    gate = build_gate()
    if not gate.get("formal_decision_persist_allowed", True):
        if _manual_completion_request_bound_pit_ready(req):
            return 0
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
    gate = build_gate(request_file=os.environ.get("TRIGGERING_REQUEST_FILE") or None)
    for path in (QUERY_CONTEXT, DECISION_CONTEXT):
        if not path.exists():
            continue
        obj = load_json(path)
        obj["refresh_gate"] = gate
        # Keep the legacy flag for compatibility, but publish the action-scoped
        # meaning explicitly so Chat/consumers do not interpret a pending
        # EXPLICIT_LATEST refresh as "produce no analysis at all".
        obj["formal_analysis_allowed"] = bool(gate.get("formal_analysis_allowed", True))
        obj["formal_current_pit_analysis_allowed"] = bool(
            gate.get("formal_current_pit_analysis_allowed", gate.get("formal_analysis_allowed", True))
        )
        obj["degraded_analysis_allowed"] = bool(gate.get("degraded_analysis_allowed", True))
        if path == QUERY_CONTEXT:
            read_plan = obj.setdefault("decision_read_plan", {})
            if gate.get("status") in {"PENDING", "FAILED"} and not gate.get("fallback_allowed", True):
                read_plan["mode"] = "REQUEST_EXPIRED" if gate.get("request_result") == "EXPIRED" else "WAIT_FOR_REFRESH"
                read_plan["refresh_gate"] = gate
                read_plan["acquisition_priority"] = (
                    ["REQUEST_TERMINAL_NO_POST_REQUEST_SNAPSHOT", "NO_FORMAL_DECISION"]
                    if gate.get("request_result") == "EXPIRED"
                    else ["WAIT_FOR_REQUESTED_REFRESH", "EXPLICIT_FAILURE_NO_FALLBACK"]
                )
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

