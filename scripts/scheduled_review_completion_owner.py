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

