from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.hyperliquid.xyz/info"
CONFIG = Path(__file__).resolve().parents[1] / "config/market/hyperliquid_etf_exposure_map.json"


def _post(payload: dict) -> object:
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _collection_as_of_beijing(utc_text: str) -> str:
    try:
        value = datetime.fromisoformat(str(utc_text).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai")).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def _session_metadata(collected_at_utc: str) -> dict:
    """Describe bounded ETF consumption without creating a task chain."""
    try:
        value = datetime.fromisoformat(str(collected_at_utc).replace("Z", "+00:00"))
        beijing = value.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai"))
        minute = beijing.hour * 60 + beijing.minute
        phase = ("A_SHARE_ACTIVE_WINDOW" if 8 * 60 <= minute < 16 * 60 else
                 "PREOPEN_OVERNIGHT_AGGREGATION" if minute < 8 * 60 else
                 "OUTSIDE_CONTINUOUS_PULSE_WINDOW")
        return {"policy": "OVERNIGHT_AGGREGATION_PLUS_DAYTIME_PULSE",
                "continuous_pulse_window_beijing": "08:00-16:00",
                "overnight_aggregation_boundary_beijing": "08:00",
                "observed_at_beijing": beijing.isoformat(timespec="seconds"),
                "consumption_phase": phase,
                "task_consumption": {"SYSTEM_REVIEW": ["08:30", "19:30"], "FORMAL_DECISION": ["09:00", "10:00", "11:00", "13:30", "14:30"], "TRADE_REVIEW": ["12:30", "20:30"]},
                "decision_role": "READ_ONLY_EXTERNAL_EVIDENCE; never independently grants permission or order"}
    except (TypeError, ValueError):
        return {"policy": "OVERNIGHT_AGGREGATION_PLUS_DAYTIME_PULSE", "continuous_pulse_window_beijing": "08:00-16:00", "overnight_aggregation_boundary_beijing": "08:00", "observed_at_beijing": "", "consumption_phase": "UNKNOWN_TIME_BOUNDARY", "task_consumption": {}, "decision_role": "READ_ONLY_EXTERNAL_EVIDENCE; never independently grants permission or order"}


def build() -> dict:
    started = _now()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    targets = set(config["focused_assets"])
    collection_mode = str(os.environ.get("HYPERLIQUID_COLLECTION_MODE") or "CORE").strip().upper()
    full_scan = collection_mode == "FULL"
    catalog = []
    observations = []
    failures = []
    try:
        dexes = _post({"type": "perpDexs"})
        if not isinstance(dexes, list):
            raise ValueError("perpDexs response is not a list")
    except Exception as exc:
        finished = _now()
        return {
            "schema_version": "1.0", "mode": "FORMAL_EXTERNAL_DRIVER_DISCOVERY_INPUT",
            "provider": "hyperliquid_info_api", "read_only": True,
            "collection_started_at_utc": started, "collection_finished_at_utc": finished,
            "started_at_utc": started, "finished_at_utc": finished,
            "quality_status": "FAILED", "dex_count": 0, "catalog_count": 0,
            "focused_count": 0, "missing_focused_assets": sorted(targets),
            "dex_failures": [{"dex": "DIRECTORY", "error": type(exc).__name__,
                              "message": str(exc)[-300:]}],
            "catalog": [], "observations": [],
            "mapping": config["asset_mappings"],
            "time_basis": "Hyperliquid endpoint lacks event timestamps; collected_at_beijing is the observation completion boundary, not an event timestamp. Formal consumers must preserve context_id, collection window, latest observation time and actual age.",
            "decision_boundary": config["boundary"],
            "session_policy": _session_metadata(finished)
        }
    for dex in dexes:
        dex_name = (dex or {}).get("name") or "default"
        request = {"type": "metaAndAssetCtxs"}
        if dex_name != "default":
            request["dex"] = dex_name
        request_started = _now()
        try:
            response = _post(request)
            request_finished = _now()
            if not isinstance(response, list) or len(response) != 2:
                raise ValueError("invalid metaAndAssetCtxs response")
            meta, contexts = response
            universe = (meta or {}).get("universe") or []
            if not isinstance(universe, list) or not isinstance(contexts, list):
                raise ValueError("invalid universe or contexts")
            if len(contexts) != len(universe):
                raise ValueError("universe and contexts length mismatch")
            for index, asset in enumerate(universe):
                name = str((asset or {}).get("name") or "")
                if not name:
                    raise ValueError("asset without name")
                catalog.append({"dex": dex_name, "asset": asset})
                context = contexts[index]
                if not isinstance(context, dict):
                    raise ValueError(f"invalid context for {name}")
                oi = context.get("openInterest")
                volume = context.get("dayNtlVlm")
                active = bool(oi not in (None, "", 0, "0") or volume not in (None, "", 0, "0"))
                # Core pulses collect the configured focus list only. Full discovery
                # pulses additionally retain active catalog assets for new-object recall.
                # Unmapped assets remain external facts and cannot create ETF candidates.
                if name in targets or (full_scan and active):
                    observations.append({
                        "dex": dex_name, "asset": asset, "context": context,
                        "observation_scope": "FOCUSED" if name in targets else "DYNAMIC_ACTIVE_CATALOG",
                        "provider_timestamp": None,
                        "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT",
                        "request_started_at_utc": request_started,
                        "request_finished_at_utc": request_finished,
                        "collected_at_utc": request_finished,
                        "collected_at_beijing": _collection_as_of_beijing(request_finished),
                        "observation_time_basis": "COLLECTION_FINISHED_TIME_AS_VISIBLE_STATE_BOUNDARY",
                        "market_activity_status": "ACTIVE" if active else "INACTIVE_OR_NO_ACTIVITY",
                        "quality_status": "PASS" if active else "DEGRADED",
                    })
        except Exception as exc:
            failures.append({"dex": dex_name, "error": type(exc).__name__, "message": str(exc)[-300:]})
    observed = {str(x["asset"].get("name")) for x in observations}
    missing = sorted(targets - observed)
    collected = sorted(str(x.get("collected_at_utc") or "") for x in observations if x.get("collected_at_utc"))
    latest_collected = collected[-1] if collected else ""
    earliest_collected = collected[0] if collected else ""
    status = "PASS" if not failures and not missing and all(x.get("quality_status") == "PASS" for x in observations) else ("DEGRADED" if observations else "FAILED")
    finished = _now()
    provenance_payload = {
        "collection_started_at_utc": started,
        "collection_finished_at_utc": finished,
        "earliest_observation_collected_at_utc": earliest_collected,
        "latest_observation_collected_at_utc": latest_collected,
        "focused_assets": sorted(str(x.get("asset", {}).get("name") or "") for x in observations if x.get("observation_scope") == "FOCUSED"),
    }
    context_id = "hl-" + hashlib.sha256(json.dumps(provenance_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    # Publish one canonical object-level fact shape for every collected object.
    # Downstream consumers must read these fields rather than reconstructing them.
    mapping_by_asset = {
        str(item.get("asset") or ""): item
        for item in (config.get("asset_mappings") or [])
        if isinstance(item, dict) and str(item.get("asset") or "")
    }
    for row in observations:
        raw = row.get("asset") if isinstance(row.get("asset"), dict) else {}
        asset_name = str(raw.get("name") or row.get("asset") or "")
        mapping = mapping_by_asset.get(asset_name, {})
        row["object_id"] = asset_name
        row["object_name"] = asset_name
        row["economic_family"] = str(mapping.get("family") or "")
        row["context_id"] = context_id
        row["observation_as_of"] = str(row.get("collected_at_beijing") or "")
        row["fact_time_boundary"] = str(row.get("collected_at_beijing") or "")
        row["standardization_status"] = "PASS" if row.get("observation_as_of") else "FAIL"
        row["standardization_reason"] = "" if row.get("observation_as_of") else "采集完成但缺少对象采集时间"
        ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
        row["normalized_fact"] = {
            "current_price": ctx.get("markPx"),
            "reference_price": ctx.get("prevDayPx"),
            "funding_rate": ctx.get("funding"),
            "open_interest": ctx.get("openInterest"),
            "premium_discount": ctx.get("premium"),
            "liquidity": ctx.get("dayNtlVlm"),
            "data_quality": row.get("quality_status") or "UNKNOWN",
        }
    return {
        "schema_version": "1.0", "mode": "FORMAL_EXTERNAL_DRIVER_DISCOVERY_INPUT",
        "provider": "hyperliquid_info_api", "read_only": True,
        "collection_started_at_utc": started, "collection_finished_at_utc": finished,
        "started_at_utc": started, "finished_at_utc": finished,
        "context_id": context_id,
        "earliest_observation_collected_at_utc": earliest_collected,
        "latest_observation_collected_at_utc": latest_collected,
        "collection_mode": collection_mode,
        "full_scan": full_scan,
        "observation_count": len(observations),
        "quality_status": status, "dex_count": len(dexes),
        "catalog_count": len(catalog),
        "focused_count": sum(1 for x in observations if x.get("observation_scope") == "FOCUSED"),
        "dynamic_active_count": sum(1 for x in observations if x.get("observation_scope") == "DYNAMIC_ACTIVE_CATALOG"),
        "missing_focused_assets": missing, "dex_failures": failures,
        "catalog": catalog, "observations": observations,
        "mapping": config["asset_mappings"],
        "time_basis": "Hyperliquid endpoint lacks event timestamps; collected_at_beijing is the latest state visible to this run and must be aligned to the nearest legal A-share fact.",
        "decision_boundary": config["boundary"],
        "session_policy": _session_metadata(latest_collected or finished)
    }
