"""Single production owner for Scheduled Review completion handoff.

All three business tasks use this module. It is deliberately not an Actor or
task entry point: it only persists the already-frozen completion, wakes the
canonical notification workflow, and reads back the canonical projections.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urllib_request

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def _credentials() -> tuple[str, str]:
    token = (os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip())
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip() or "frankieliu2024-ui/ETF-Trade-System"
    if not token:
        raise RuntimeError("scheduled review completion owner requires an authorized GitHub write capability")
    if "/" not in repository:
        raise RuntimeError("invalid GitHub repository identity")
    return token, repository


def persist_completion(target: Path) -> tuple[str, dict[str, object]]:
    token, repository = _credentials()
    relative = target.resolve().relative_to(ROOT).as_posix()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/contents/{relative}"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
    body = target.read_bytes()
    try:
        req = urllib_request.Request(endpoint + "?ref=main", headers=headers)
        with urllib_request.urlopen(req, timeout=30) as response:
            remote = json.loads(response.read().decode("utf-8"))
        if base64.b64decode(str(remote.get("content") or "").replace("\\n", "")) != body:
            raise RuntimeError("different completion already exists on main")
        return relative, {"written": False, "read_back": True, "attempts": 1}
    except Exception as exc:
        if getattr(exc, "code", None) != 404:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("completion owner read failed: " + str(exc)) from exc
    payload = json.dumps({"message": "Persist Scheduled Review completion", "content": base64.b64encode(body).decode("ascii"), "branch": "main"}).encode("utf-8")
    with urllib_request.urlopen(urllib_request.Request(endpoint, data=payload, method="PUT", headers=headers), timeout=30) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"completion owner write returned HTTP {response.status}")
    req = urllib_request.Request(endpoint + "?ref=main", headers=headers)
    with urllib_request.urlopen(req, timeout=30) as response:
        verified = json.loads(response.read().decode("utf-8"))
    if base64.b64decode(str(verified.get("content") or "").replace("\\n", "")) != body:
        raise RuntimeError("completion owner readback mismatch")
    return relative, {"written": True, "read_back": True, "attempts": 1}


def dispatch_notification(completion_path: str) -> dict[str, object]:
    token, repository = _credentials()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/decision-notification.yml/dispatches"
    payload = json.dumps({"ref": "main", "inputs": {"scheduled_review_completion_path": completion_path}}).encode("utf-8")
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
    with urllib_request.urlopen(urllib_request.Request(endpoint, data=payload, method="POST", headers=headers), timeout=30) as response:
        if response.status != 204:
            raise RuntimeError(f"notification dispatch returned HTTP {response.status}")
    return {"submitted": True, "completion_path": completion_path, "read_back": False}


def dispatch_notification_with_one_recovery(completion_path: str) -> dict[str, object]:
    """Submit the same completion once, with one bounded same-completion recovery.

    Recovery is not a second notification chain: it reuses the exact same
    completion path and is allowed only after the primary submission returned
    an explicit failure.  An accepted-but-not-read-back submission is kept
    pending and is never duplicated here.
    """
    try:
        primary = dispatch_notification(completion_path)
    except Exception as exc:
        primary = {"submitted": False, "accepted": False, "reason": str(exc), "exception": True}
    if primary.get("submitted") or primary.get("accepted"):
        return {"primary": primary, "recovery": None, "recovery_attempted": False}
    try:
        recovery = dispatch_notification(completion_path)
    except Exception as exc:
        recovery = {"submitted": False, "accepted": False, "reason": str(exc), "exception": True}
    return {
        "primary": primary,
        "recovery": recovery,
        "recovery_attempted": True,
        "same_completion_path": True,
        "recovery_limit": 1,
    }


def readback_projection(target: Path, *, timeout_seconds: int = 60) -> dict[str, object]:
    """Read back event, REPORT, notification-center and provider state.

    The existence of paths is not treated as proof: every result is obtained
    from current main through ``read_main_json`` and carries its own status.
    """
    payload = json.loads(target.read_text(encoding="utf-8"))
    binding = payload.get("presentation_binding") or {}
    task_id = str(payload.get("task_id") or binding.get("task_id") or "")
    occurrence = str(payload.get("occurrence_id") or binding.get("occurrence_id") or payload.get("task_run_id") or "")
    market_date = str(payload.get("market_date") or "")
    report_id = str(binding.get("report_id") or payload.get("report_id") or "")
    if task_id == "ETF正式决策":
        event_path = f"events/reviews/formal_{occurrence}.json"
    elif task_id == "ETF系统复核":
        event_path = f"events/reviews/system_{occurrence}.json"
    else:
        event_path = f"events/reviews/{market_date}.json"
    deadline = time.time() + max(0, timeout_seconds)
    last: dict[str, object] = {}
    while True:
        event = read_main_json(event_path)
        event_status = "READ_BACK" if isinstance(event, dict) else "NOT_READ_BACK"
        actual_report_id = str((event or {}).get("report_id") or report_id)
        report_path = f"requests/report_delivery/{actual_report_id.replace(':', '__')}.json" if actual_report_id else ""
        report = read_main_json(report_path) if report_path else None
        center = read_main_json("data/state/notification_center.json")
        provider = None
        if isinstance(center, dict) and actual_report_id and contains_text(center, actual_report_id):
            provider = "BOUND_RECORD_PRESENT"
        last = {
            "event": {"status": event_status, "path": event_path},
            "report": {"status": "READ_BACK" if isinstance(report, dict) else "NOT_READ_BACK", "path": report_path},
            "notification_center": {"status": "READ_BACK" if isinstance(center, dict) and actual_report_id and contains_text(center, actual_report_id) else "NOT_READ_BACK"},
            "provider": {"status": provider or "NOT_CONFIRMED"},
        }
        if all(item.get("status") == "READ_BACK" for item in (last["event"], last["report"])) and last["notification_center"]["status"] == "READ_BACK":
            last["status"] = "READ_BACK"
            return last
        if time.time() >= deadline:
            last["status"] = "NOT_CONFIRMED"
            return last
        time.sleep(3)


def read_main_json(relative_path: str) -> dict[str, object] | None:
    token, repository = _credentials()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/contents/{relative_path}?ref=main"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        with urllib_request.urlopen(urllib_request.Request(endpoint, headers=headers), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        value = json.loads(base64.b64decode(str(payload.get("content") or "").replace("\\n", "")).decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def contains_text(value: object, needle: str) -> bool:
    if isinstance(value, dict):
        return any(contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(contains_text(item, needle) for item in value)
    return needle in str(value) if value is not None else False


def execute_post_handoff(target: Path, *, completion_kind: str = "SCHEDULED_REVIEW_COMPLETION") -> dict[str, object]:
    """Execute the mandatory post-content handoff with explicit checkpoints.

    This is the only orchestration helper for callers that already froze and
    published FINAL_CONTENT.  It deliberately returns a structured failure
    instead of allowing callers to emit an unqualified "cannot reliably"
    message: every failure is tied to the exact stage that was attempted.
    """
    result: dict[str, object] = {
        "attempted": True,
        "completion_kind": completion_kind,
        "completion_write": {"attempted": False, "status": "NOT_STARTED"},
        "completion_readback": {"attempted": False, "status": "NOT_STARTED"},
        "notification_dispatch": {"attempted": False, "status": "NOT_STARTED"},
        "projection_readback": {"attempted": False, "status": "NOT_STARTED"},
        "ok": False,
    }
    try:
        relative, persistence = persist_completion(target)
        result["completion_write"] = {"attempted": True, "status": "SUCCEEDED", **persistence}
        result["completion_readback"] = {"attempted": True, "status": "SUCCEEDED", "path": relative}
    except Exception as exc:
        result["completion_write"] = {"attempted": True, "status": "FAILED", "reason": str(exc)}
        result["failure_stage"] = "completion_write"
        result["failure_reason"] = str(exc)
        return result

    try:
        dispatch = dispatch_notification_with_one_recovery(relative)
        primary = dispatch.get("primary") or {}
        submitted = bool(primary.get("submitted"))
        if not submitted and dispatch.get("recovery_attempted"):
            submitted = bool((dispatch.get("recovery") or {}).get("submitted"))
        result["notification_dispatch"] = {
            "attempted": True,
            "status": "SUBMITTED" if submitted else "FAILED",
            **dispatch,
        }
        if not submitted:
            result["failure_stage"] = "notification_dispatch"
            result["failure_reason"] = str(dispatch.get("reason") or "notification dispatch was not submitted")
            return result
    except Exception as exc:
        result["notification_dispatch"] = {"attempted": True, "status": "FAILED", "reason": str(exc)}
        result["failure_stage"] = "notification_dispatch"
        result["failure_reason"] = str(exc)
        return result

    try:
        projection = readback_projection(target)
        result["projection_readback"] = {"attempted": True, **projection}
        result["ok"] = projection.get("status") == "READ_BACK"
        if not result["ok"]:
            result["failure_stage"] = "projection_readback"
            result["failure_reason"] = "canonical event/REPORT/notification/provider state was not fully confirmed"
    except Exception as exc:
        result["projection_readback"] = {"attempted": True, "status": "FAILED", "reason": str(exc)}
        result["failure_stage"] = "projection_readback"
        result["failure_reason"] = str(exc)
    return result

