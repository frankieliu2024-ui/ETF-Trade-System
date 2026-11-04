from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.hyperliquid.xyz/info"
CONFIG = Path(__file__).resolve().parents[1] / "config/market/hyperliquid_etf_exposure_map.json"


def _post(payload: dict) -> object:
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build() -> dict:
    started = _now()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    targets = set(config["focused_assets"])
    catalog = []
    observations = []
    failures = []
    dexes = _post({"type": "perpDexs"})
    if not isinstance(dexes, list):
        raise ValueError("perpDexs response is not a list")
    for dex in dexes:
        dex_name = (dex or {}).get("name") or "default"
        request = {"type": "metaAndAssetCtxs"}
        if dex_name != "default":
            request["dex"] = dex_name
        try:
            response = _post(request)
            if not isinstance(response, list) or len(response) != 2:
                raise ValueError("invalid metaAndAssetCtxs response")
            meta, contexts = response
            universe = (meta or {}).get("universe") or []
            if not isinstance(universe, list) or not isinstance(contexts, list):
                raise ValueError("invalid universe or contexts")
            for index, asset in enumerate(universe):
                name = str((asset or {}).get("name") or "")
                if not name:
                    raise ValueError("asset without name")
                catalog.append({"dex": dex_name, "asset": asset})
                if name in targets and index < len(contexts):
                    observations.append({
                        "dex": dex_name, "asset": asset, "context": contexts[index],
                        "provider_timestamp": None,
                        "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT",
                        "collected_at_utc": started
                    })
        except Exception as exc:
            failures.append({"dex": dex_name, "error": type(exc).__name__, "message": str(exc)[-300:]})
    observed = {str(x["asset"].get("name")) for x in observations}
    missing = sorted(targets - observed)
    status = "PASS" if not failures and not missing else ("DEGRADED" if observations else "FAILED")
    return {
        "schema_version": "1.0", "mode": "FORMAL_EXTERNAL_DRIVER_DISCOVERY_INPUT",
        "provider": "hyperliquid_info_api", "read_only": True,
        "started_at_utc": started, "finished_at_utc": _now(),
        "quality_status": status, "dex_count": len(dexes),
        "catalog_count": len(catalog), "focused_count": len(observations),
        "missing_focused_assets": missing, "dex_failures": failures,
        "catalog": catalog, "observations": observations,
        "mapping": config["asset_mappings"],
        "decision_boundary": config["boundary"]
    }
