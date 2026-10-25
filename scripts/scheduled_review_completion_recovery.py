"""Idempotent current-main persistence for Scheduled Review completion handoff."""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib import request as urllib_request

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
MAX_ATTEMPTS = 3
RETRY_SECONDS = 2


def persist_completion_with_recovery(target: Path) -> tuple[str, dict[str, object]]:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip() or "frankieliu2024-ui/ETF-Trade-System"
    if not token:
        raise RuntimeError("scheduled review completion requires GitHub API token")
    owner, sep, repo = repository.partition("/")
    if not sep or not owner or not repo:
        raise RuntimeError("invalid GITHUB_REPOSITORY")
    target = target.resolve()
    relative = target.relative_to(ROOT).as_posix()
    content = target.read_bytes()
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/contents/{relative}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            existing_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
            try:
                with urllib_request.urlopen(existing_req, timeout=30) as response:
                    existing = json.loads(response.read().decode("utf-8"))
                remote = base64.b64decode(str(existing.get("content") or "").replace("\\n", ""))
                if remote != content:
                    raise RuntimeError("different completion already exists on main")
                return relative, {"written": False, "already_present": True, "read_back": True, "attempts": attempt}
            except Exception as exc:
                if getattr(exc, "code", None) != 404:
                    if isinstance(exc, RuntimeError):
                        raise
                    raise
            payload = json.dumps({"message": "Persist Scheduled Review completion (recovery-safe)", "content": base64.b64encode(content).decode("ascii"), "branch": "main"}).encode("utf-8")
            write_req = urllib_request.Request(endpoint, data=payload, method="PUT", headers=headers)
            with urllib_request.urlopen(write_req, timeout=30) as response:
                if response.status not in (200, 201):
                    raise RuntimeError(f"completion write returned HTTP {response.status}")
            verify_req = urllib_request.Request(endpoint + "?ref=main", method="GET", headers=headers)
            with urllib_request.urlopen(verify_req, timeout=30) as response:
                verified = json.loads(response.read().decode("utf-8"))
            if base64.b64decode(str(verified.get("content") or "").replace("\\n", "")) != content:
                raise RuntimeError("completion readback content mismatch")
            return relative, {"written": True, "read_back": True, "attempts": attempt, "commit_sha": (verified.get("commit") or {}).get("sha")}
        except Exception as exc:
            last_error = str(exc)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SECONDS)
    raise RuntimeError(f"scheduled review completion recovery exhausted after {MAX_ATTEMPTS} attempts: {last_error}")
