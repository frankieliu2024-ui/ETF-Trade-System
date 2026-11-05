from __future__ import annotations

import json
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


def build() -> dict:
    started = _now()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    targets = set(config["focused_assets"])
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
            "time_basis": "Hyperliquid endpoint lacks event timestamps; collected_at_beijing is the latest state visible to this run and must be aligned to the nearest legal A-share fact.",
            "decision_boundary": config["boundary"]
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
                if name in targets:
                    context = contexts[index]
                    if not isinstance(context, dict):
                        raise ValueError(f"invalid context for {name}")
                    oi = context.get("openInterest")
                    volume = context.get("dayNtlVlm")
                    active = bool(oi not in (None, "", 0, "0") or volume not in (None, "", 0, "0"))
                    observations.append({
                        "dex": dex_name, "asset": asset, "context": context,
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
    status = "PASS" if not failures and not missing and all(x.get("quality_status") == "PASS" for x in observations) else ("DEGRADED" if observations else "FAILED")
    finished = _now()
    return {
        "schema_version": "1.0", "mode": "FORMAL_EXTERNAL_DRIVER_DISCOVERY_INPUT",
        "provider": "hyperliquid_info_api", "read_only": True,
        "collection_started_at_utc": started, "collection_finished_at_utc": finished,
        "started_at_utc": started, "finished_at_utc": finished,
        "quality_status": status, "dex_count": len(dexes),
        "catalog_count": len(catalog), "focused_count": len(observations),
        "missing_focused_assets": missing, "dex_failures": failures,
        "catalog": catalog, "observations": observations,
        "mapping": config["asset_mappings"],
        "time_basis": "Hyperliquid endpoint lacks event timestamps; collected_at_beijing is the latest state visible to this run and must be aligned to the nearest legal A-share fact.",
        "decision_boundary": config["boundary"]
    }
