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
        "context_id": str(payload.get("context_id") or ""),
        "formed_at_beijing": _beijing_iso(str(payload.get("generated_at_beijing") or payload.get("generated_at") or occurred_at)),
        "fact_time_beijing": str(payload.get("latest_valid_node") or payload.get("a_share_fact_as_of_beijing") or _beijing_iso(str(payload.get("latest_observation_collected_at_utc") or ""))),
        "source_path": path,
        "status": str(payload.get("status") or payload.get("quality_status") or "UNKNOWN"),
        "coverage_status": str(payload.get("coverage_status") or ""),
        "candidate_count": payload.get("candidate_count") if kind == "DISCOVERY" else None,
        "observation_count": payload.get("observation_count") if kind == "HYPERLIQUID" else None,
        "latest_snapshot": str(payload.get("latest_snapshot") or ""),
        "observation_time_basis": str(payload.get("observation_time_basis") or payload.get("time_basis") or ""),
        "mapping": payload.get("mapping") if kind == "HYPERLIQUID" else None,
        "etf_bindings": payload.get("etf_bindings") if kind == "HYPERLIQUID" else None,
        "binding_candidates": payload.get("binding_candidates") if kind == "HYPERLIQUID" else None,
        "binding_status": str(payload.get("binding_status") or "") if kind == "HYPERLIQUID" else "",
        "binding_generated_at_beijing": str(payload.get("binding_generated_at_beijing") or "") if kind == "HYPERLIQUID" else "",
        "binding_source_discovery_generated_at_beijing": str(payload.get("binding_source_discovery_generated_at_beijing") or "") if kind == "HYPERLIQUID" else "",
    }
    if kind == "DISCOVERY":
        record["candidates"] = [
            {
                "candidate_signature": ":".join(str(item.get(key) or "") for key in ("code", "formal_status", "category", "eligibility")),
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "display_name": str(item.get("display_name") or ""),
                "surfaced_states": item.get("surfaced_states") or [],
                "discovery_spot": item.get("discovery_spot") or {},
                "information_classes": item.get("information_classes") or [],
                "comparison_basis": item.get("comparison_basis") or [],
            }
            for item in (payload.get("candidates") or [])
            if isinstance(item, dict) and (item.get("code") or item.get("name"))
        ]
    else:
        record["observations"] = [
            {
                "asset": str((item.get("asset") or {}).get("name") or ""),
                "family": next((str(mapping.get("family") or "") for mapping in (payload.get("mapping") or []) if str(mapping.get("asset") or "") == str((item.get("asset") or {}).get("name") or "")), ""),
                "quality_status": str(item.get("quality_status") or ""),
                "funding": str((item.get("context") or {}).get("funding") or ""),
                "open_interest": str((item.get("context") or {}).get("openInterest") or ""),
                "premium": str((item.get("context") or {}).get("premium") or ""),
                "mark_px": str((item.get("context") or {}).get("markPx") or ""),
                "mid_px": str((item.get("context") or {}).get("midPx") or ""),
                "prev_day_px": str((item.get("context") or {}).get("prevDayPx") or ""),
            }
            for item in (payload.get("observations") or [])
            if isinstance(item, dict)
        ]
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
        "continuity": {
            "status": "READY" if isinstance(records[-1] if records else None, dict) and str((records[-1] if records else {}).get("identity") or "") and ((records[-1] if records else {}).get("observations") or (records[-1] if records else {}).get("candidates") or []) else "MISSING",
            "latest_identity": str((records[-1] if records else {}).get("identity") or ""),
            "latest_formed_at_beijing": str((records[-1] if records else {}).get("formed_at_beijing") or ""),
            "latest_observation_count": len((records[-1] if records else {}).get("observations") or []),
            "latest_candidate_count": len((records[-1] if records else {}).get("candidates") or []),
            "rule": "正式判断只消费具有identity且含实际对象证据的最新脉冲；缺失时明确降级，不用历史正文或旧状态补齐。",
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest_path = target.with_name(target.stem.replace("_index", "_latest") + target.suffix)
    latest_projection = {
        "schema_version": "1.0",
        "projection_type": f"{kind}_PULSE_LATEST_PROJECTION",
        "read_only": True,
        "latest_market_date": market_date,
        "latest": index.get("latest"),
        "continuity": index.get("continuity") or {},
        "record_count": len(records),
        "source_index": target.as_posix(),
        "retention_rule": "Latest valid pulse only; use the history index for audit history.",
    }
    latest_path.write_text(json.dumps(latest_projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "UPDATED",
        "path": str(target),
        "market_date": market_date,
        "identity": record["identity"],
        "record_count": len(records),
        "latest_path": str(latest_path),
    }


def read_pulse_projection(root: Path, kind: str) -> dict:
    """Read the bounded latest projection, with a one-release legacy fallback."""
    if kind not in {"DISCOVERY", "HYPERLIQUID"}:
        raise ValueError(f"unsupported pulse kind: {kind}")
    stem = "discovery" if kind == "DISCOVERY" else "hyperliquid"
    for name in (f"{stem}_pulse_latest.json", f"{stem}_pulse_index.json"):
        try:
            payload = json.loads((root / "data" / "state" / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}
