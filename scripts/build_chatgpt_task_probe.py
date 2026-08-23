from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = ""
    probe = {
        "repository": "frankieliu2024-ui/ETF-Trade-System", "latest_commit": commit,
        "CURRENT_path": "data/state/CURRENT.json", "query_context_path": "query_context.json",
        "post_market_review_path": "post_market_review/post_market_review_event.json", "last_update": now_utc(),
        "read_only": True, "access_verified": False,
        "note": "This is a read-only probe. Codex does not assume ChatGPT Scheduled Tasks can access a private GitHub repository.",
    }
    atomic_json_write(ROOT / "chatgpt_task_probe.json", probe)
    print(json.dumps({"ok": True, "latest_commit": commit, "access_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

