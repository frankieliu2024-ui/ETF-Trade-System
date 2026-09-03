"""Merge notification state produced by a runner with the current main copy."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from notification_center import revalidate_pending_notifications
except ImportError:
    from scripts.notification_center import revalidate_pending_notifications


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "2.2", "notifications": [], "recent": []}


def _identity_tokens(item: dict) -> set[str]:
    return {
        str(item.get(name) or "")
        for name in ("notification_id", "source_event_id", "key")
        if str(item.get(name) or "")
    }


def _timestamp(item: dict) -> datetime | None:
    values = [item.get(name) for name in ("last_attempted_at", "sent_at", "created_at", "updated_at")]
    parsed = []
    for value in values:
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            parsed.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return max(parsed) if parsed else None


def _status_rank(item: dict) -> int:
    return {
        "CREATED": 1,
        "FAILED": 2,
        "SENT": 3,
        "WAITING_CONFIRMATION": 4,
        "CONFIRMED": 5,
        "ARCHIVED": 6,
    }.get(str(item.get("lifecycle_status") or "").upper(), 0)


def _merge_same_event(base: dict, incoming: dict) -> dict:
    """Merge one identity by newest attempt, then strongest deterministic state.

    A newer attempt is a legitimate state transition even when it reports a
    failure. An older runner can never replace newer evidence. Missing fields
    are carried forward so an old runner cannot erase a prior send receipt.
    """
    base_time, incoming_time = _timestamp(base), _timestamp(incoming)
    base_rank, incoming_rank = _status_rank(base), _status_rank(incoming)
    if incoming_rank > base_rank:
        winner, older = incoming, base
    elif base_rank > incoming_rank:
        winner, older = base, incoming
    elif base_time is None and incoming_time is None:
        winner, older = base, incoming
    elif base_time is None:
        winner, older = incoming, base
    elif incoming_time is None or incoming_time < base_time:
        winner, older = base, incoming
    else:
        winner, older = incoming, base
    merged = dict(older)
    merged.update(winner)
    return merged


def _newer_state(base: dict, incoming: dict) -> dict:
    base_time, incoming_time = _timestamp(base), _timestamp(incoming)
    if base_time is None:
        return incoming
    if incoming_time is None or incoming_time < base_time:
        return base
    return incoming


def merge_notification_state(base: dict, incoming: dict) -> dict:
    items: list[dict] = []

    def add(item: dict) -> None:
        tokens = _identity_tokens(item)
        if not tokens:
            return
        for index, existing in enumerate(items):
            if tokens & _identity_tokens(existing):
                items[index] = _merge_same_event(existing, item)
                return
        items.append(item)

    for item in base.get("notifications") or []:
        add(item)
    for item in incoming.get("notifications") or []:
        add(item)
    result = dict(_newer_state(base, incoming))
    result["schema_version"] = max(str(base.get("schema_version") or ""), str(incoming.get("schema_version") or ""))
    result["notifications"] = items
    result["recent"] = [
        {"key": x.get("source_event_id"), "type": x.get("event_type"), "title": x.get("title"), "content": x.get("content"), "source": x.get("source"), "user_severity": x.get("user_severity"), "user_action": x.get("user_action"), "status": x.get("lifecycle_status"), "attempted_at": x.get("last_attempted_at") or x.get("sent_at") or x.get("created_at"), "response": x.get("response") or {}, "notification_id": x.get("notification_id"), "lifecycle_status": x.get("lifecycle_status")}
        for x in items[-200:]
    ]
    result["notifications"] = revalidate_pending_notifications(result["notifications"])
    result["pending_questions"] = [x.get("notification_id") for x in result["notifications"] if x.get("lifecycle_status") == "WAITING_CONFIRMATION"]
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
