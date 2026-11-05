from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _beijing_iso(value: str) -> str:
    parsed = _parse(value)
    if not parsed:
        return str(value or "")
    return parsed.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai")).isoformat(timespec="seconds")


def append_pulse_history(root: Path, *, kind: str, payload: dict, path: str, identity: str, occurred_at: str = "") -> dict:
    """Append a bounded, idempotent history index beside the current projection.

    This is an audit/index projection of an existing producer, not a second
    decision chain. It preserves same-day pulse provenance for downstream tasks.
    """
    if kind not in {"DISCOVERY", "HYPERLIQUID"}:
        raise ValueError(f"unsupported pulse kind: {kind}")
    market_date = str(payload.get("market_date") or "")
    if not market_date:
        generated = _parse(str(payload.get("generated_at_beijing") or payload.get("generated_at") or occurred_at))
        market_date = generated.date().isoformat() if generated else ""
    if not market_date:
        return {"status": "SKIPPED", "reason": "missing_market_date"}
    target = root / "data" / "state" / ("discovery_pulse_index.json" if kind == "DISCOVERY" else "hyperliquid_pulse_index.json")
    try:
        index = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        index = {}
    days = index.get("days") if isinstance(index.get("days"), dict) else {}
    records = days.get(market_date) if isinstance(days.get(market_date), list) else []
    record = {
        "kind": kind,
        "identity": str(identity or ""),
        "formed_at_beijing": _beijing_iso(str(payload.get("generated_at_beijing") or payload.get("generated_at") or occurred_at)),
        "fact_time_beijing": str(payload.get("latest_valid_node") or payload.get("a_share_fact_as_of_beijing") or _beijing_iso(str(payload.get("latest_observation_collected_at_utc") or ""))),
        "source_path": path,
        "status": str(payload.get("status") or payload.get("quality_status") or "UNKNOWN"),
        "coverage_status": str(payload.get("coverage_status") or ""),
        "candidate_count": payload.get("candidate_count") if kind == "DISCOVERY" else None,
        "observation_count": payload.get("observation_count") if kind == "HYPERLIQUID" else None,
        "latest_snapshot": str(payload.get("latest_snapshot") or ""),
    }
    if not record["identity"]:
        record["identity"] = f"{kind}:{record['formed_at_beijing']}:{record['source_path']}"
    records = [item for item in records if str(item.get("identity") or "") != record["identity"]]
    records.append(record)
    records.sort(key=lambda item: str(item.get("formed_at_beijing") or ""))
    days[market_date] = records[-288:]
    index = {
        "schema_version": "1.0",
        "projection_type": f"{kind}_PULSE_HISTORY_INDEX",
        "read_only": True,
        "latest_market_date": market_date,
        "latest": records[-1] if records else None,
        "days": dict(sorted(days.items())[-31:]),
        "retention_rule": "Keep up to 288 pulses per day and 31 market days; this index does not create decisions or permissions.",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "UPDATED", "path": str(target), "market_date": market_date, "identity": record["identity"]}
