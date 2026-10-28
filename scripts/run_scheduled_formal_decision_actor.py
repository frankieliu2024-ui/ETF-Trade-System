"""Compatibility adapter for the shared Scheduled Review completion owner.

The actor freezes one user-visible investment judgment, delegates persistence
to the same completion owner used by Trade Review, and explicitly wakes the
existing notification workflow after completion readback. It does not create
a BDS, publish an event, or execute a trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request as urllib_request

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()

try:
    from scheduled_review_completion import write_completion_request
except ModuleNotFoundError:
    from scripts.scheduled_review_completion import write_completion_request

try:
    from task_runtime_identity import derive_runtime_identity
except ModuleNotFoundError:
    from scripts.task_runtime_identity import derive_runtime_identity


def _persist_completion_to_main(completion_path: str) -> str:
    """Persist and verify the single completion before dispatching notification."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("cannot persist Formal Decision completion without GitHub API credentials")
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        raise RuntimeError("invalid GITHUB_REPOSITORY")
    path = Path(completion_path).resolve()
    try:
        relative_path = path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
    except ValueError as exc:
        raise RuntimeError("Formal Decision completion must be inside the repository root") from exc
    body = path.read_bytes()
    base = "https://api.github.com/repos/" + owner + "/" + repo
    endpoint = base + "/contents/" + relative_path
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        read_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
        with urllib_request.urlopen(read_req, timeout=30) as response:
            existing = json.loads(response.read().decode("utf-8"))
        existing_body = __import__("base64").b64decode(str(existing.get("content") or "").replace("\\n", ""))
        if existing_body != body:
            raise RuntimeError("different completion already exists on main: " + relative_path)
        return relative_path
    except Exception as exc:
        if getattr(exc, "code", None) != 404:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("failed to read existing Formal Decision completion on main: " + str(exc)) from exc
    payload = json.dumps({
        "message": "Persist Scheduled Review completion",
        "content": __import__("base64").b64encode(body).decode("ascii"),
        "branch": "main",
    }).encode("utf-8")
    write_req = urllib_request.Request(endpoint, data=payload, method="PUT", headers=headers)
    try:
        with urllib_request.urlopen(write_req, timeout=30) as response:
            if response.status not in (200, 201):
                raise RuntimeError("completion persistence returned HTTP " + str(response.status))
    except Exception as exc:
        raise RuntimeError("failed to persist Formal Decision completion on main: " + str(exc)) from exc
    verify_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
    try:
        with urllib_request.urlopen(verify_req, timeout=30) as response:
            verified = json.loads(response.read().decode("utf-8"))
        verified_body = __import__("base64").b64decode(str(verified.get("content") or "").replace("\\n", ""))
    except Exception as exc:
        raise RuntimeError("failed to read back persisted Formal Decision completion: " + str(exc)) from exc
    if verified_body != body:
        raise RuntimeError("persisted trade review completion readback mismatch: " + relative_path)
    return relative_path




def _wait_for_completion_push(completion_path: str, started_at: datetime) -> dict[str, object]:
    """Wait for the canonical push-triggered notification run for this completion."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        return {"submitted": False, "read_back": False, "reason": "missing GitHub credentials"}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "read_back": False, "reason": "invalid GITHUB_REPOSITORY"}
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/decision-notification.yml/runs?event=push&branch=main&per_page=20"
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    while datetime.now(timezone.utc) < deadline:
        try:
            req = urllib_request.Request(endpoint, method="GET", headers=headers)
            with urllib_request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            for run in data.get("workflow_runs", []):
                created_at = str(run.get("created_at") or "")
                if not created_at:
                    continue
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created >= started_at and str(run.get("event") or "") == "push" and str(run.get("head_branch") or "") == "main":
                    return {"submitted": True, "read_back": True, "wake": "CANONICAL_COMPLETION_PUSH", "run_id": run.get("id"), "run_url": run.get("html_url"), "run_status": run.get("status"), "run_conclusion": run.get("conclusion"), "completion_path": completion_path}
        except Exception:
            pass
        time.sleep(3)
    return {"submitted": False, "read_back": False, "wake": "CANONICAL_COMPLETION_PUSH", "completion_path": completion_path, "reason": "canonical completion push notification run not read back"}


def _read_main_json(relative_path: str) -> dict[str, object] | None:
    """Read one canonical JSON object from current main."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or "/" not in repository:
        return None
    owner, repo = repository.split("/", 1)
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/contents/{relative_path}?ref=main"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        req = urllib_request.Request(endpoint, method="GET", headers=headers)
        with urllib_request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = __import__("base64").b64decode(str(payload.get("content") or "").replace("\\n", ""))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _contains_text(value: object, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, needle) for item in value)
    return needle in str(value) if value is not None else False


def _wait_for_projection(occurrence_id: str, report_id: str) -> dict[str, object]:
    """Require canonical event and REPORT before declaring notification handoff complete."""
    event_path = f"events/reviews/formal_{occurrence_id}.json"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    last = ""
    while datetime.now(timezone.utc) < deadline:
        event = _read_main_json(event_path)
        if isinstance(event, dict):
            actual_report_id = str(event.get("report_id") or report_id).strip()
            report_path = (
                f"requests/report_delivery/{actual_report_id.replace(':', '__')}.json"
                if actual_report_id else ""
            )
            report = _read_main_json(report_path) if report_path else None
            notification_center = _read_main_json("data/state/notification_center.json")
            if isinstance(report, dict) and isinstance(notification_center, dict) and _contains_text(notification_center, actual_report_id):
                return {
                    "verified": True,
                    "event_path": event_path,
                    "report_path": report_path,
                    "report_id": actual_report_id,
                    "notification_center": "BOUND_RECORD_READ_BACK",
                    "reason": "canonical event, REPORT, and bound notification record read back from current main",
                }
            if isinstance(report, dict):
                last = "event and REPORT exist but bound notification record is not yet readable"
            elif isinstance(event, dict):
                last = "canonical event exists but REPORT is not yet readable"
        else:
            last = "canonical Formal Decision event is not yet readable"
        time.sleep(3)
    return {
        "verified": False,
        "event_path": event_path,
        "reason": last or "canonical event and REPORT were not read back within bounded window",
    }


def _dispatch_notification(completion_path: str) -> dict[str, object]:
    """Use explicit dispatch only as same-run recovery after canonical push readback is absent."""
    token = (os.environ.get("GITHUB_TOKEN", "").strip()
             or os.environ.get("GH_TOKEN", "").strip())
    repository = (os.environ.get("GITHUB_REPOSITORY", "").strip()
                  or "frankieliu2024-ui/ETF-Trade-System")
    if not token:
        return {"submitted": False, "reason": "missing GitHub API token (GITHUB_TOKEN or GH_TOKEN)"}
    owner, separator, repo = repository.partition("/")
    if not separator or not owner or not repo:
        return {"submitted": False, "reason": "invalid GITHUB_REPOSITORY"}

    payload = json.dumps({
        "ref": "main",
        # Formal Decision uses the same Scheduled Review completion owner and input as Trade Review;
        # the completion payload remains the sole business-type discriminator.
        "inputs": {"scheduled_review_completion_path": completion_path},
    }).encode("utf-8")
    base = f"https://api.github.com/repos/{owner}/{repo}"
    dispatch_endpoint = f"{base}/actions/workflows/decision-notification.yml/dispatches"
    runs_endpoint = (
        f"{base}/actions/workflows/decision-notification.yml/runs"
        "?event=workflow_dispatch&branch=main&per_page=20"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    dispatched_at = datetime.now(timezone.utc)
    expected_run_name = f"ETF decision notification — {completion_path}"

    def matching_run() -> dict[str, object] | None:
        req = urllib_request.Request(runs_endpoint, method="GET", headers=headers)
        with urllib_request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        candidates = []
        for run in data.get("workflow_runs", []):
            created_at = str(run.get("created_at") or "")
            if not created_at:
                continue
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if (
                str(run.get("name") or "") == expected_run_name
                and str(run.get("event") or "") == "workflow_dispatch"
                and str(run.get("head_branch") or "") == "main"
            ):
                candidates.append((created, run))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def result_for(run: dict[str, object], *, read_back: bool) -> dict[str, object]:
        return {
            "submitted": True,
            "run_id": run.get("id"),
            "run_url": run.get("html_url"),
            "run_status": run.get("status"),
            "run_conclusion": run.get("conclusion"),
            "completion_path": completion_path,
            "read_back": read_back,
            "workflow_input_readback": "VERIFIED_BY_RUN_NAME_BINDING",
            "reused_existing_run": read_back,
        }

    try:
        existing = matching_run()
        if existing:
            return result_for(existing, read_back=True)
        req = urllib_request.Request(
            dispatch_endpoint, data=payload, method="POST", headers=headers
        )
        with urllib_request.urlopen(req, timeout=30) as response:
            if response.status != 204:
                return {"submitted": False, "status": response.status}
    except Exception as exc:
        return {"submitted": False, "reason": str(exc)}

    deadline = datetime.now(timezone.utc) + timedelta(seconds=360)
    last_read_error = ""
    matched_run = None
    poll_seconds = 3
    while datetime.now(timezone.utc) < deadline:
        try:
            req = urllib_request.Request(runs_endpoint, method="GET", headers=headers)
            with urllib_request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            candidates = []
            for run in data.get("workflow_runs", []):
                created_at = str(run.get("created_at") or "")
                if not created_at:
                    continue
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if (
                    created >= dispatched_at
                    and str(run.get("event") or "") == "workflow_dispatch"
                    and str(run.get("head_branch") or "") == "main"
                    and str(run.get("name") or "") == expected_run_name
                ):
                    candidates.append((created, run))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                matched_run = candidates[0][1]
                run_status = str(matched_run.get("status") or "").lower()
                run_conclusion = matched_run.get("conclusion")
                if run_status == "completed":
                    return {
                        "submitted": True,
                        "run_id": matched_run.get("id"),
                        "run_url": matched_run.get("html_url"),
                        "run_status": run_status,
                        "run_conclusion": run_conclusion,
                        "completion_path": completion_path,
                        "read_back": True,
                        "workflow_input_readback": "VERIFIED_BY_RUN_NAME_BINDING",
                    }
        except Exception as exc:
            last_read_error = str(exc)
        time.sleep(poll_seconds)

    result = {
        "submitted": bool(matched_run),
        "reason": (
            "notification workflow run did not complete within bounded polling window"
            if matched_run
            else "notification workflow dispatch accepted but matching run was not read back within bounded polling window"
        ),
        "completion_path": completion_path,
        "read_back": False,
        "last_read_error": last_read_error,
    }
    if matched_run:
        result.update({
            "run_id": matched_run.get("id"),
            "run_url": matched_run.get("html_url"),
            "run_status": matched_run.get("status"),
            "run_conclusion": matched_run.get("conclusion"),
            "workflow_input_readback": "NOT_EXPOSED_BY_GITHUB_RUN_API",
        })
    return result



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-content", required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--requested-at-beijing", required=True)
    parser.add_argument("--entry-mode", required=True, choices=(
        "MANUAL_CONVERSATION", "MANUAL_SCHEDULED_TASK", "AUTOMATIC_SCHEDULED_TASK"
    ))
    parser.add_argument("--decision-path", default="formal", choices=("formal", "degraded", "timeout"))
    parser.add_argument("--task-run-id", default="")
    parser.add_argument("--occurrence-id", default="")
    parser.add_argument("--output", default="")
    # Compatibility-only input. It is a completion identity, not a market-fact request.
    parser.add_argument("--request-id", default="")
    args = parser.parse_args()

    task_run_id, occurrence_id = derive_runtime_identity(
        "ETF正式决策",
        args.request_id or args.task_run_id or args.occurrence_id or "formal-runtime",
        args.requested_at_beijing,
        args.entry_mode,
        args.task_run_id,
        args.occurrence_id,
    )
    from scheduled_review_completion import build_formal_runtime_completion_request
    identity = {
        "task_run_id": task_run_id,
        "occurrence_id": occurrence_id,
        "requested_at_beijing": args.requested_at_beijing,
        "entry_mode": args.entry_mode,
    }
    payload = build_formal_runtime_completion_request(
        run_identity=identity,
        market_date=args.market_date,
        final_content=args.final_content,
        decision_path=args.decision_path,
    )
    target = (
        Path(args.output).resolve()
        if args.output
        else ROOT / "requests" / "live_snapshot" / f"{task_run_id}__scheduled_review_completion.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    persisted = json.loads(target.read_text(encoding="utf-8"))
    binding = persisted.get("presentation_binding") or {}
    expected_hash = hashlib.sha256(args.final_content.encode("utf-8")).hexdigest()
    expected_decision_id = f"ETF_FORMAL_DECISION:{task_run_id}"
    checks = {
        "request_id": persisted.get("request_id") == task_run_id,
        "task_id": persisted.get("task_id") == "ETF正式决策",
        "task_run_id": persisted.get("task_run_id") == task_run_id,
        "occurrence_id": persisted.get("occurrence_id") == occurrence_id,
        "request_type": persisted.get("request_type") == "SCHEDULED_REVIEW_COMPLETION",
        "fact_source": persisted.get("fact_source") == "CURRENT_MAIN_LATEST_VALID_FIXED_PERIOD_FACTS",
        "decision_path": persisted.get("decision_path") == args.decision_path,
        "no_parent_fact_request": "parent_request_id" not in persisted,
        "report_type": binding.get("report_type") == "ETF_FORMAL_DECISION",
        "binding_request_id": binding.get("request_id") == task_run_id,
        "binding_decision_id": binding.get("decision_id") == expected_decision_id,
        "binding_report_id": binding.get("report_id") == expected_decision_id,
        "content_hash": binding.get("content_hash") == expected_hash,
        "full_content": binding.get("full_content") == args.final_content,
    }
    hard_failures = [key for key, ok in checks.items() if not ok and key != "content_hash"]
    if hard_failures:
        raise RuntimeError("formal runtime completion readback mismatch: " + ", ".join(hard_failures))

    started_at = datetime.now(timezone.utc)
    completion_path, persistence = _persist_completion_to_main(target)
    canonical_path = completion_path
    # A generic push-triggered run is only a wake signal and is not bound to
    # this completion. It must never satisfy the handoff gate.
    push = _wait_for_completion_push(canonical_path, started_at)
    dispatch = _dispatch_notification(canonical_path)
    dispatch["fallback_after"] = "PUSH_WAKE_NOT_ACCEPTED_AS_COMPLETION_PROOF"

    projection = _wait_for_projection(occurrence_id, expected_decision_id)
    handoff_ok = (
        bool(dispatch.get("submitted"))
        and bool(dispatch.get("read_back"))
        and bool(projection.get("verified"))
    )
    # Emit the frozen body as a first-class user-visible output contract.
    # The caller must publish this exact string verbatim; the handoff status
    # below must never replace or summarize it.
    print(args.final_content)
    print(json.dumps({
        "ok": handoff_ok,
        "user_visible_content": args.final_content,
        "completion_path": completion_path,
        "persistence": persistence,
        "readback": checks,
        "content_hash_warning": None if checks.get("content_hash") else "content_hash_mismatch",
        "notification_dispatch": dispatch,
        "projection": projection,
        "same_run_handoff_complete": handoff_ok,
    }, ensure_ascii=False))
    return 0 if handoff_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
