"""Single production owner for Scheduled Review completion handoff.

All three business tasks use this module. It is deliberately not an Actor or
task entry point: it only persists the already-frozen completion, wakes the
canonical notification workflow, and reads back the canonical projections.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def _credentials() -> tuple[str, str]:
    token = (os.environ.get("ETF_GITHUB_PAT", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip())
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip() or "frankieliu2024-ui/ETF-Trade-System"
    if not token:
        raise RuntimeError("scheduled review completion owner requires an authorized GitHub write capability")
    if "/" not in repository:
        raise RuntimeError("invalid GitHub repository identity")
    return token, repository


def persist_completion(target: Path) -> tuple[str, dict[str, object]]:
    payload = json.loads(target.read_text(encoding="utf-8"))
    # The completion writer is the single authoritative handoff boundary.
    # Hashes are produced by the frozen-completion builder.  The writer must
    # The single writer owns the final completion envelope.  A missing hash is
    # completed here from the already-frozen body; an existing hash that drifts
    # is rejected because it would indicate body mutation.
    final_content = payload.get("final_content")
    if isinstance(final_content, str):
        computed_hash = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
        existing_hash = str(payload.get("content_hash") or "").strip().lower()
        if existing_hash and existing_hash != computed_hash:
            raise RuntimeError("completion content_hash is missing or does not match final_content")
        binding_value = payload.get("presentation_binding")
        if isinstance(binding_value, dict):
            binding_hash = str(binding_value.get("content_hash") or "").strip().lower()
            if binding_hash and binding_hash != computed_hash:
                raise RuntimeError("presentation_binding content_hash is missing or does not match final_content")
            binding_value["content_hash"] = computed_hash
        if "final_content_hash" in payload:
            final_hash = str(payload.get("final_content_hash") or "").strip().lower()
            if final_hash and final_hash != computed_hash:
                raise RuntimeError("final_content_hash does not match final_content")
            payload["final_content_hash"] = computed_hash
        payload["content_hash"] = computed_hash
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding = payload.get("presentation_binding") if isinstance(payload.get("presentation_binding"), dict) else {}
    report_type = str(payload.get("report_type") or binding.get("report_type") or "").strip().upper()
    # Optional runtime metadata must not block a frozen Scheduled Review handoff.\n    # Derive only deterministic identity from the canonical request; business正文\n    # and task semantics remain unchanged.\n    if report_type in {"ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"}:\n        request_id = str(payload.get("request_id") or "").strip()\n        task_name = str(payload.get("task_name") or "").strip()\n        if request_id:\n            payload.setdefault("task_run_id", request_id)\n            payload.setdefault("occurrence_id", request_id)\n            payload.setdefault("report_id", f"{task_name or report_type}:{request_id}")\n        binding = payload.setdefault("presentation_binding", {})\n        if isinstance(binding, dict):\n            binding.setdefault("task_id", task_name)\n            binding.setdefault("task_run_id", payload.get("task_run_id"))\n            binding.setdefault("occurrence_id", payload.get("occurrence_id"))\n            binding.setdefault("report_id", payload.get("report_id"))\n            binding.setdefault("report_type", report_type)\n        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")\n        binding = payload.get("presentation_binding") if isinstance(payload.get("presentation_binding"), dict) else {}\n    if report_type == "ETF_FORMAL_DECISION":
        try:
            from scheduled_review_completion import validate_formal_runtime_completion_contract
        except ModuleNotFoundError:
            from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract
        validate_formal_runtime_completion_contract(payload, root=ROOT)
    token, repository = _credentials()
    relative = target.resolve().relative_to(ROOT).as_posix()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/contents/{relative}"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
    body = target.read_bytes()
    # Write first.  A pre-read is not a production prerequisite: it can race
    # with the notification consumer and was the source of false handoff
    # failures.  If GitHub reports an existing path, read it only to establish
    # idempotency or a path conflict.
    payload = json.dumps({"message": "Persist Scheduled Review completion", "content": base64.b64encode(body).decode("ascii"), "branch": "main"}).encode("utf-8")
    try:
        with urllib_request.urlopen(urllib_request.Request(endpoint, data=payload, method="PUT", headers=headers), timeout=30) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"completion owner write returned HTTP {response.status}")
        written = True
    except Exception as exc:
        if getattr(exc, "code", None) not in (409, 422):
            raise RuntimeError("completion owner write failed: " + str(exc)) from exc
        req = urllib_request.Request(endpoint + "?ref=main", headers=headers)
        with urllib_request.urlopen(req, timeout=30) as response:
            remote = json.loads(response.read().decode("utf-8"))
        if base64.b64decode(str(remote.get("content") or "").replace("\\n", "")) != body:
            raise RuntimeError("different completion already exists on main")
        written = False
    req = urllib_request.Request(endpoint + "?ref=main", headers=headers)
    with urllib_request.urlopen(req, timeout=30) as response:
        verified = json.loads(response.read().decode("utf-8"))
    if base64.b64decode(str(verified.get("content") or "").replace("\\n", "")) != body:
        raise RuntimeError("completion owner readback mismatch")
    return relative, {"written": written, "read_back": True, "attempts": 1}


def dispatch_notification(completion_path: str, *, force_resend: bool = False) -> dict[str, object]:
    token, repository = _credentials()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/decision-notification.yml/dispatches"
    inputs = {
        "scheduled_review_completion_path": completion_path,
        "force_resend": "true" if force_resend else "false",
    }
    payload = json.dumps({"ref": "main", "inputs": inputs}).encode("utf-8")
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        with urllib_request.urlopen(urllib_request.Request(endpoint, data=payload, method="POST", headers=headers), timeout=30) as response:
            if response.status != 204:
                return {
                    "submitted": False,
                    "accepted": False,
                    "explicit_failure": True,
                    "status": response.status,
                    "completion_path": completion_path,
                }
    except HTTPError as exc:
        # An HTTP rejection is explicit: GitHub confirmed that this dispatch
        # was not accepted, so the bounded same-completion recovery is safe.
        return {
            "submitted": False,
            "accepted": False,
            "explicit_failure": True,
            "status": exc.code,
            "reason": str(exc),
            "completion_path": completion_path,
        }
    # A 204 only means GitHub accepted the API request.  Confirm that Actions
    # actually created the workflow run; otherwise the caller cannot tell a
    # silently dropped wake from a successful handoff.
    run_created = wait_for_notification_run(completion_path)
    return {"submitted": True, "accepted": True, "workflow_run_created": run_created, "completion_path": completion_path, "read_back": False}


def wait_for_notification_run(completion_path: str, *, timeout_seconds: int = 15) -> bool:
    """Confirm a workflow_dispatch run exists for this exact completion path."""
    token, repository = _credentials()
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/decision-notification.yml/runs?event=workflow_dispatch&branch=main&per_page=20"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    needle = str(completion_path).strip()
    deadline = time.time() + max(0, timeout_seconds)
    while True:
        try:
            with urllib_request.urlopen(urllib_request.Request(endpoint, headers=headers), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for run in payload.get("workflow_runs") or []:
                name = str(run.get("display_title") or run.get("name") or "")
                if needle in name:
                    return True
        except Exception:
            # Dispatch acceptance remains useful evidence; failure to inspect
            # runs must not create a blind duplicate here.
            return False
        if time.time() >= deadline:
            return False
        time.sleep(3)


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
        # A timeout/connection error does not prove GitHub rejected the
        # dispatch. Retrying would create a second notification chain.
        primary = {"submitted": False, "accepted": False, "explicit_failure": False, "reason": str(exc), "exception": True}
        return {"primary": primary, "recovery": None, "recovery_attempted": False, "same_completion_path": True}
    if (primary.get("submitted") or primary.get("accepted")) and primary.get("workflow_run_created") is not False:
        return {"primary": primary, "recovery": None, "recovery_attempted": False}
    # GitHub can return 204 while failing to materialize the Actions run.  A
    # single same-completion recovery makes this self-healing; notification
    # dedupe still prevents a second user-visible message.
    if primary.get("submitted") and primary.get("workflow_run_created") is False:
        try:
            recovery = dispatch_notification(completion_path, force_resend=True)
        except Exception as exc:
            recovery = {"submitted": False, "accepted": False, "reason": str(exc), "exception": True}
        return {"primary": primary, "recovery": recovery, "recovery_attempted": True, "same_completion_path": True, "recovery_limit": 1}
    if not primary.get("explicit_failure"):
        return {"primary": primary, "recovery": None, "recovery_attempted": False, "same_completion_path": True}
    try:
        recovery = dispatch_notification(completion_path, force_resend=True)
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
        center_bound = isinstance(center, dict) and actual_report_id and contains_text(center, actual_report_id)
        provider = None
        if center_bound and isinstance(center, dict):
            # A notification-center record is not provider delivery evidence.
            # Only an explicit provider result may be reported as such.
            provider = _provider_status(center, actual_report_id)
        last = {
            "event": {"status": event_status, "path": event_path},
            "report": {"status": "READ_BACK" if isinstance(report, dict) else "NOT_READ_BACK", "path": report_path},
            "notification_center": {"status": "READ_BACK" if center_bound else "NOT_READ_BACK"},
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


def _provider_status(center: dict[str, object], report_id: str) -> str | None:
    """Return only an explicit provider status for this report, never binding presence."""
    def walk(value: object) -> str | None:
        if isinstance(value, dict):
            if report_id in {str(value.get("report_id") or ""), str(value.get("reportId") or "")}:
                for key in ("provider_status", "provider_delivery_status", "pushplus_status"):
                    if value.get(key):
                        return str(value[key])
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None
    return walk(center)


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
        "business_notification": {"status": "NOT_STARTED"},
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
            result["business_notification"] = {"status": "FAILED"}
            result["failure_stage"] = "notification_dispatch"
            result["failure_reason"] = str(dispatch.get("reason") or "notification dispatch was not submitted")
            return result
        result["business_notification"] = {"status": "SUBMITTED", "completion_path": relative}
        # Notification submission is the business handoff boundary.  Audit
        # projections are deliberately reported after it and cannot rewrite a
        # successful business notification as a notification failure.
        result["ok"] = True
    except Exception as exc:
        result["notification_dispatch"] = {"attempted": True, "status": "FAILED", "reason": str(exc)}
        result["failure_stage"] = "notification_dispatch"
        result["failure_reason"] = str(exc)
        return result

    try:
        projection = readback_projection(target)
        result["projection_readback"] = {"attempted": True, **projection}
        result["projection_readback"]["audit_status"] = "COMPLETE" if projection.get("status") == "READ_BACK" else "INCOMPLETE"
        if projection.get("status") != "READ_BACK":
            result["failure_stage"] = "projection_readback"
            result["failure_reason"] = "audit projection was not fully confirmed; business notification status is preserved"
    except Exception as exc:
        result["projection_readback"] = {"attempted": True, "status": "FAILED", "reason": str(exc)}
        result["failure_stage"] = "projection_readback"
        result["failure_reason"] = str(exc)
    return result


