from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_NODE_STATUS = {"READY", "DEGRADED", "BLOCKED", "NON_TRADING_DAY"}
EVENT_TYPES = {
    "TRADE_EXECUTED", "ACCOUNT_UPDATED", "DATA_ERROR", "SYSTEM_ERROR",
    "RULE_VERSION_CHANGE", "MANUAL_OVERRIDE",
}


class StateConflictError(RuntimeError):
    """Raised when a write would overwrite a file changed since it was read."""


def root_from_env() -> Path:
    return Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_json_write(path: Path, value: Any, expected_sha256: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected_sha256 is not None:
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        if current != expected_sha256:
            raise StateConflictError(f"write conflict: {path}")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_current() -> dict[str, Any]:
    return {
        "market_date": "",
        "latest_valid_node": "",
        "captured_at": "",
        "node_status": "NON_TRADING_DAY",
        "latest_snapshot": "",
        "snapshot_commit": "",
        "superseded_nodes": [],
        "data_freshness": {},
        "account_fact": {"status": "MISSING", "updated_at": "", "source": ""},
        "needs_account_update": True,
        "last_trade_event_id": "",
        "rules_version": "V2.2.15",
        "generated_at": "",
    }


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def read_current(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current = default_current()
    current.update(read_json(root / "data" / "state" / "CURRENT.json", {}))
    if not isinstance(current.get("data_freshness"), dict):
        current["data_freshness"] = {}
    if not isinstance(current.get("superseded_nodes"), list):
        current["superseded_nodes"] = []
    return current


def read_account_fact(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    default = {
        "updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING",
        "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": [],
    }
    fact = read_json(root / "data" / "state" / "account_fact.json", default)
    default.update(fact)
    if default["status"] not in {"MISSING", "STALE", "VALID", "CONFLICT"}:
        raise ValueError("invalid account_fact.status")
    return default


def update_current(
    *, root: Path | None = None, market_date: str, node: str, captured_at: str,
    latest_snapshot: str = "", snapshot_commit: str = "", node_status: str = "READY",
    data_freshness: dict[str, Any] | None = None, expected_sha256: str | None = None,
) -> dict[str, Any]:
    if node_status not in VALID_NODE_STATUS:
        raise ValueError(f"invalid node_status: {node_status}")
    root = root or root_from_env()
    current = read_current(root)
    account = read_account_fact(root)
    previous_node = current.get("latest_valid_node", "")
    previous_date = current.get("market_date", "")
    is_valid = node_status == "READY"
    if is_valid and previous_node and (previous_date, previous_node) != (market_date, node):
        superseded = current.setdefault("superseded_nodes", [])
        entry = {"node": previous_node, "market_date": previous_date, "superseded_at": now_utc()}
        if entry not in superseded:
            superseded.append(entry)
    if is_valid:
        current.update({
            "market_date": market_date, "latest_valid_node": node,
            "captured_at": captured_at, "latest_snapshot": latest_snapshot,
            "snapshot_commit": snapshot_commit,
        })
    current["node_status"] = node_status
    current["data_freshness"] = data_freshness or current.get("data_freshness", {})
    current["account_fact"] = {k: account.get(k, "") for k in ("status", "updated_at", "source")}
    current["needs_account_update"] = account["status"] != "VALID"
    current["generated_at"] = now_utc()
    atomic_json_write(root / "data" / "state" / "CURRENT.json", current, expected_sha256)
    return current


def event_id(event_type: str, source: str, payload: dict[str, Any]) -> str:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type}")
    body = canonical({"event_type": event_type, "source": source, "payload": payload})
    return "evt_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def append_event(
    *, root: Path | None = None, event_type: str, source: str, payload: dict[str, Any],
    git_commit: str = "", explicit_event_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    root = root or root_from_env()
    path = root / "events" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    eid = explicit_event_id or event_id(event_type, source, payload)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                existing[record["event_id"]] = record
    if eid in existing:
        return existing[eid], False
    record = {
        "event_id": eid, "event_type": event_type, "created_at": now_utc(),
        "source": source, "payload": payload, "git_commit": git_commit,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(record) + "\n")
    if event_type == "TRADE_EXECUTED":
        current = read_current(root)
        current["last_trade_event_id"] = eid
        current["generated_at"] = now_utc()
        atomic_json_write(root / "data" / "state" / "CURRENT.json", current)
    return record, True


def build_dashboard_candidate(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current = read_current(root)
    account = read_account_fact(root)
    return {
        "generated_at": now_utc(),
        "source": "Codex state layer",
        "current_risk_state": "REQUIRES_MANUAL_CONFIRMATION",
        "current_node": current.get("latest_valid_node", ""),
        "node_status": current.get("node_status", "NON_TRADING_DAY"),
        "current_candidate": None,
        "data_status": current.get("data_freshness", {}),
        "account_fact_status": account["status"],
        "needs_account_update": account["status"] != "VALID",
        "manual_confirmation_required": True,
        "write_target": "candidate_only",
        "prohibited_outputs": ["trade_amount", "buy_action", "sell_action", "order"],
    }


def build_decision_context(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current = read_current(root)
    account = read_account_fact(root)
    dashboard = root / "system" / "ETF当前状态_DASHBOARD.md"
    latest = current.get("latest_snapshot", "")
    snapshot = read_json(root / latest, {}) if latest else {}
    return {
        "generated_at": now_utc(), "rules_version": "V2.2.15",
        "latest_node": current.get("latest_valid_node", ""),
        "current": current, "latest_snapshot": snapshot,
        "dashboard_source": str(dashboard.relative_to(root)).replace("\\", "/"),
        "account_fact_status": account["status"],
        "needs_account_screenshot": account["status"] != "VALID",
        "interaction_boundary": "ChatGPT聊天负责账户截图与正式交易判断；本文件不生成交易动作。",
    }
