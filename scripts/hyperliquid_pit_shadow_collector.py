#!/usr/bin/env python3
"""Research-only Hyperliquid PIT shadow collector.

The collector is intentionally artifact-only. It never signs, submits, routes,
or writes ETF production state. It dynamically discovers the market catalog,
but retains high-frequency contexts only for configured research targets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from urllib.request import Request, urlopen

API = "https://api.hyperliquid.xyz/info"
CONFIG = pathlib.Path("config/research/hyperliquid_alpha_stage2.json")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def post(payload: dict) -> tuple[object, str]:
    requested_at = utc_now()
    req = Request(
        API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=30) as response:
        return json.load(response), requested_at


def load_config(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def target_names(config: dict) -> set[str]:
    p0 = config["targets"]["P0"]["hyperliquid_assets"]
    p2 = config["targets"]["P2"]["hyperliquid_assets"]
    return set(p0 + p2)


def discover(config: dict, started_at: str) -> dict:
    dex_payload, dex_requested_at = post({"type": "perpDexs"})
    if not isinstance(dex_payload, list):
        raise ValueError("perpDexs response is not a list")

    catalog = []
    focus = []
    dex_quality = []
    targets = target_names(config)

    for dex in dex_payload:
        dex_name = None if dex is None else dex.get("name")
        dex_id = dex_name or "default"
        request = {"type": "metaAndAssetCtxs"}
        if dex_name:
            request["dex"] = dex_name
        dex_started = utc_now()
        try:
            response, requested_at = post(request)
            if not isinstance(response, list) or len(response) != 2:
                raise ValueError("metaAndAssetCtxs response must be [meta, contexts]")
            meta, contexts = response
            universe = meta.get("universe", []) if isinstance(meta, dict) else []
            if not isinstance(universe, list) or not isinstance(contexts, list):
                raise ValueError("invalid universe or contexts")
            names = []
            for index, asset in enumerate(universe):
                name = asset.get("name") if isinstance(asset, dict) else None
                if name is None:
                    raise ValueError("asset entry missing name")
                names.append(name)
                # The provider does not expose an observation timestamp in this
                # endpoint. Keep it explicitly unavailable; never substitute
                # request or collection time for provider time.
                row = {
                    "dex": dex_id,
                    "asset": asset,
                    "provider_timestamp": None,
                    "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT",
                }
                if name in targets and index < len(contexts):
                    row["context"] = contexts[index]
                    focus.append(row)
                catalog.append({
                    "dex": dex_id,
                    "asset": asset,
                    "provider_timestamp": None,
                    "provider_timestamp_status": "NOT_PROVIDED_BY_ENDPOINT",
                })
            missing_contexts = len(contexts) < len(universe)
            dex_quality.append({
                "dex": dex_id,
                "status": "DEGRADED" if missing_contexts else "PASS",
                "started_at_utc": dex_started,
                "finished_at_utc": utc_now(),
                "requested_at_utc": requested_at,
                "asset_count": len(universe),
                "focused_asset_count": sum(1 for n in names if n in targets),
                "missing_contexts": missing_contexts,
                "error": None,
            })
        except Exception as exc:
            dex_quality.append({
                "dex": dex_id,
                "status": "DEGRADED",
                "started_at_utc": dex_started,
                "finished_at_utc": utc_now(),
                "requested_at_utc": None,
                "asset_count": 0,
                "focused_asset_count": 0,
                "missing_contexts": True,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })

    focused_names = {row["asset"]["name"] for row in focus}
    missing_targets = sorted(targets - focused_names)
    any_degraded = any(item["status"] != "PASS" for item in dex_quality)
    return {
        "dex_count": len(dex_payload),
        "started_at_utc": started_at,
        "dex_requested_at_utc": dex_requested_at,
        "catalog": catalog,
        "focused_contexts": focus,
        "quality": {
            "status": "DEGRADED" if any_degraded or missing_targets else "PASS",
            "per_dex": dex_quality,
            "missing_required_targets": missing_targets,
            "provider_timestamp_available": False,
            "collection_timestamp_is_not_provider_timestamp": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="data/research/hyperliquid_pit")
    parser.add_argument("--config", default=str(CONFIG))
    args = parser.parse_args()
    started_at = utc_now()
    config = load_config(pathlib.Path(args.config))
    try:
        data = discover(config, started_at)
        error = None
    except Exception as exc:
        data = None
        error = {"type": type(exc).__name__, "message": str(exc)}

    finished_at = utc_now()
    status = "FAILED" if error else data["quality"]["status"]
    record = {
        "schema_version": "1.1",
        "mode": "RESEARCH_ONLY_HYPERLIQUID_PIT",
        "read_only": True,
        "production_eligible": False,
        "provider": "hyperliquid_info_api",
        "request_types": ["perpDexs", "metaAndAssetCtxs"],
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "collection_timestamp_is_not_provider_timestamp": True,
        "quality": {"status": status, "error": error},
        "data": data,
    }
    now = dt.datetime.now(dt.timezone.utc)
    out = pathlib.Path(args.output_root) / now.strftime("%Y/%m/%d")
    out.mkdir(parents=True, exist_ok=True)
    path = out / (now.strftime("%H%M%S") + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
