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
        def _number(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        # Compare with the latest prior legal pulse, including the prior
        # market date for the first pre-open pulse. This preserves overnight
        # continuity instead of silently resetting at midnight.
        prior_record = records[-1] if records else None
        if prior_record is None:
            prior_dates = [key for key in days if str(key) < market_date and isinstance(days.get(key), list) and days.get(key)]
            if prior_dates:
                prior_record = sorted(days[max(prior_dates)], key=lambda item: str(item.get("formed_at_beijing") or ""))[-1]
        previous_records = prior_record.get("observations") if isinstance(prior_record, dict) else []
        previous_by_asset = {
            str(item.get("asset") or ""): item
            for item in (previous_records or [])
            if isinstance(item, dict) and str(item.get("asset") or "")
        }

        observations = []
        for item in (payload.get("observations") or []):
            if not isinstance(item, dict):
                continue
            asset = str((item.get("asset") or {}).get("name") or "")
            context = item.get("context") or {}
            previous = previous_by_asset.get(asset) or {}
            current_values = {
                "funding": str(context.get("funding") or ""),
                "open_interest": str(context.get("openInterest") or ""),
                "premium": str(context.get("premium") or ""),
                "mark_px": str(context.get("markPx") or ""),
            }
            pulse_delta = {}
            pulse_delta_pct = {}
            for key, current in current_values.items():
                now_value = _number(current)
                old_value = _number(previous.get(key))
                if now_value is None or old_value is None:
                    pulse_delta[key] = None
                    pulse_delta_pct[key] = None
                else:
                    pulse_delta[key] = now_value - old_value
                    pulse_delta_pct[key] = None if old_value == 0 else (now_value - old_value) / abs(old_value)
            current_as_of = _parse(str(item.get("collected_at_beijing") or ""))
            previous_as_of = _parse(str(previous.get("observation_as_of") or ""))
            comparison_age_seconds = (
                int((current_as_of - previous_as_of).total_seconds())
                if current_as_of and previous_as_of and current_as_of >= previous_as_of else None
            )
            comparison_horizon = (
                "INTRADAY_10M" if comparison_age_seconds is not None and comparison_age_seconds <= 15 * 60
                else "INTRADAY_SHORT" if comparison_age_seconds is not None and comparison_age_seconds < 2 * 60 * 60
                else "OVERNIGHT_OR_LONG_GAP" if comparison_age_seconds is not None
                else "UNKNOWN"
            )

            mark = _number(current_values["mark_px"])
            prev_day = _number(context.get("prevDayPx"))
            funding = _number(current_values["funding"])
            oi_delta = pulse_delta.get("open_interest")
            if mark is not None and prev_day not in (None, 0) and oi_delta is not None:
                price_direction = "UP" if mark > prev_day else "DOWN" if mark < prev_day else "FLAT"
                if price_direction == "UP" and oi_delta > 0:
                    positioning_signal = "PRICE_UP_OI_UP"
                elif price_direction == "UP" and oi_delta < 0:
                    positioning_signal = "PRICE_UP_OI_DOWN"
                elif price_direction == "DOWN" and oi_delta > 0:
                    positioning_signal = "PRICE_DOWN_OI_UP"
                elif price_direction == "DOWN" and oi_delta < 0:
                    positioning_signal = "PRICE_DOWN_OI_DOWN"
                else:
                    positioning_signal = "PRICE_FLAT"
            else:
                positioning_signal = "SINGLE_PULSE_ONLY"

            if funding is None:
                funding_signal = "UNAVAILABLE"
            elif funding > 0:
                funding_signal = "POSITIVE_FUNDING_LONGS_PAY"
            elif funding < 0:
                funding_signal = "NEGATIVE_FUNDING_SHORTS_PAY"
            else:
                funding_signal = "NEUTRAL_FUNDING"

            premium = _number(current_values["premium"])
            if premium is None:
                pricing_signal = "UNAVAILABLE"
            elif premium > 0:
                pricing_signal = "PREMIUM_ABOVE_REFERENCE"
            elif premium < 0:
                pricing_signal = "DISCOUNT_BELOW_REFERENCE"
            else:
                pricing_signal = "AT_REFERENCE"

            if positioning_signal == "PRICE_UP_OI_UP" and funding is not None and funding > 0:
                crowding_signal = "LONG_CROWDING_RISK"
            elif positioning_signal == "PRICE_DOWN_OI_UP" and funding is not None and funding < 0:
                crowding_signal = "SHORT_CROWDING_RISK"
            elif positioning_signal == "SINGLE_PULSE_ONLY":
                crowding_signal = "INSUFFICIENT_HISTORY"
            else:
                crowding_signal = "NO_CLEAR_CROWDING"

            observations.append({
                "object_id": str(item.get("object_id") or asset),
                "object_name": str(item.get("object_name") or asset),
                "asset": asset,
                "economic_family": str(item.get("economic_family") or next((str(mapping.get("family") or "") for mapping in (payload.get("mapping") or []) if str(mapping.get("asset") or "") == asset), "")),
                "context_id": str(item.get("context_id") or payload.get("context_id") or ""),
                "fact_time_boundary": str(item.get("fact_time_boundary") or item.get("collected_at_beijing") or ""),
                "quality_status": str(item.get("quality_status") or ""),
                "observation_as_of": str(item.get("collected_at_beijing") or ""),
                "provider_timestamp_status": str(item.get("provider_timestamp_status") or "NOT_PROVIDED_BY_ENDPOINT"),
                "funding": current_values["funding"],
                "open_interest": current_values["open_interest"],
                "premium": current_values["premium"],
                "mark_px": current_values["mark_px"],
                "mid_px": str(context.get("midPx") or ""),
                "prev_day_px": str(context.get("prevDayPx") or ""),
                "previous_pulse_as_of": str(previous.get("observation_as_of") or ""),
                "pulse_comparison_status": "COMPARABLE" if previous and comparison_age_seconds is not None else "NOT_COMPARABLE",
                "comparison_reason": "" if previous and comparison_age_seconds is not None else (
                    "缺少同对象上一合法脉冲" if not previous else "当前或上一脉冲缺少合法对象时间"
                ),
                "comparison_age_seconds": comparison_age_seconds,
                "comparison_horizon": comparison_horizon,
                "pulse_delta": pulse_delta,
                "pulse_delta_pct": pulse_delta_pct,
                "positioning_signal": positioning_signal,
                "funding_signal": funding_signal,
                "pricing_signal": pricing_signal,
                "crowding_signal": crowding_signal,
            })
        record["observations"] = observations
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
