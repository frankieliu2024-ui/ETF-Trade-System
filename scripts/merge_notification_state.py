"""Merge notification state produced by a runner with the current main copy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "2.2", "notifications": [], "recent": []}


def merge_notification_state(base: dict, incoming: dict) -> dict:
    items: list[dict] = []
    positions: dict[tuple[str, str], int] = {}

    def add(item: dict) -> None:
        key = (str(item.get("notification_id") or ""), str(item.get("source_event_id") or item.get("key") or ""))
        if key[0] or key[1]:
            if key in positions:
                items[positions[key]] = item
            else:
                positions[key] = len(items)
                items.append(item)

    for item in base.get("notifications") or []:
        add(item)
    for item in incoming.get("notifications") or []:
        add(item)
    result = dict(base)
    result.update({k: incoming[k] for k in ("schema_version", "updated_at", "last_status", "last_type", "last_title", "policy", "safety_boundary") if k in incoming})
    result["notifications"] = items
    result["recent"] = [
        {"key": x.get("source_event_id"), "type": x.get("event_type"), "title": x.get("title"), "content": x.get("content"), "source": x.get("source"), "user_severity": x.get("user_severity"), "user_action": x.get("user_action"), "status": x.get("lifecycle_status"), "attempted_at": x.get("last_attempted_at") or x.get("sent_at") or x.get("created_at"), "response": x.get("response") or {}, "notification_id": x.get("notification_id"), "lifecycle_status": x.get("lifecycle_status")}
        for x in items[-200:]
    ]
    result["pending_questions"] = [x.get("notification_id") for x in items if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--incoming", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    merged = merge_notification_state(_load(Path(args.base)), _load(Path(args.incoming)))
    Path(args.output).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

