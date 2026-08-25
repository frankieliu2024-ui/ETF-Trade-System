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


def latest_wait_request() -> tuple[Path | None, dict]:
    candidates = []
    if REQUEST_DIR.exists():
        for path in REQUEST_DIR.glob("*.json"):
            try:
                req = load_json(path)
            except Exception:
                continue
            if req.get("wait_for_refresh") is not True:
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
            "rule": "普通盘中请求允许即时补采失败后回退最近有效快照；用户明确要求等待新数据时关闭自动回退。",
        }

    current = load_json(CURRENT)
    health = load_json(RUNTIME_HEALTH)
    requested = parse_time(req.get("requested_at_beijing"))
    target = parse_time(req.get("requested_market_time") or req.get("requested_at_beijing"))
    threshold = max(x for x in (requested, target) if x is not None)
    captured_text = current.get("data_freshness", {}).get("captured_at_beijing") or current.get("captured_at")
    captured = parse_time(captured_text)
    ready = captured is not None and captured >= threshold

    finished = parse_time(health.get("finished_at") or health.get("captured_at_beijing"))
    failed_after_request = (
        str(health.get("status") or "").upper() == "FAILED"
        and requested is not None
        and finished is not None
        and finished >= requested
        and not ready
    )
    status = "READY" if ready else ("FAILED" if failed_after_request else "PENDING")
    return {
        "status": status,
        "formal_analysis_allowed": ready,
        "formal_decision_persist_allowed": ready,
        "fallback_allowed": False,
        "request_file": str(path.relative_to(ROOT)).replace("\\", "/") if path else "",
        "request_id": str(req.get("request_id") or (path.stem if path else "")),
        "requested_at_beijing": req.get("requested_at_beijing", ""),
        "requested_market_time": req.get("requested_market_time", req.get("requested_at_beijing", "")),
        "current_snapshot_time": captured_text or "",
        "resolved_snapshot_time": captured_text if ready else "",
        "latest_snapshot": current.get("latest_snapshot", ""),
        "failure_reason": health.get("reason", "") if failed_after_request else "",
        "rule": "用户明确要求‘等补采/等指定节点数据后再回复’时，必须等本次请求之后且不早于requested_market_time的新CURRENT发布后，才允许正式风险许可、主候选、金额或卖出判断并持久化；PENDING/FAILED时禁止自动回退旧快照，除非用户随后明确授权回退。",
    }


def guard_request(path: Path) -> int:
    req = load_json(path)
    if not isinstance(req.get("formal_decision"), dict):
        return 0
    if req.get("allow_wait_refresh_fallback") is True:
        return 0
    gate = build_gate()
    if gate.get("formal_decision_persist_allowed", True):
        return 0
    print(json.dumps({"ok": False, "reason": "WAIT_FOR_REFRESH_NOT_READY", "refresh_gate": gate, "request": str(path)}, ensure_ascii=False))
    return 3


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
