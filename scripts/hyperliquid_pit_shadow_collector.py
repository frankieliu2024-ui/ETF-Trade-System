#!/usr/bin/env python3
"""Research-only Hyperliquid PIT shadow collector.

This script never signs, submits, or routes trades. It discovers DEX/asset
metadata dynamically and stores raw observations plus provider/collection
timestamps for later PIT research.
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sys, time
from urllib.request import Request, urlopen

API = "https://api.hyperliquid.xyz/info"
CONFIG = pathlib.Path("config/research/hyperliquid_alpha_stage2.json")

def post(payload: dict) -> object:
    req = Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def discover() -> dict:
    dexes = post({"type": "perpDexs"})
    discovered = []
    for dex in dexes:
        dex_name = None if dex is None else dex.get("name")
        payload = {"type": "metaAndAssetCtxs"}
        if dex_name:
            payload["dex"] = dex_name
        try:
            meta, contexts = post(payload)
            for index, asset in enumerate(meta.get("universe", [])):
                context = contexts[index] if index < len(contexts) else {}
                discovered.append({
                    "dex": dex_name or "default",
                    "asset": asset,
                    "context": context,
                    "provider_timestamp": None,
                })
        except Exception as exc:
            discovered.append({"dex": dex_name or "default", "error": type(exc).__name__, "message": str(exc)})
    return {"dex_count": len(dexes), "observations": discovered}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="data/research/hyperliquid_pit")
    args = parser.parse_args()
    collected_at = utc_now()
    try:
        payload = discover()
        record = {
            "schema_version": "1.0",
            "mode": "RESEARCH_ONLY_HYPERLIQUID_PIT",
            "read_only": True,
            "collected_at_utc": collected_at,
            "provider": "hyperliquid_info_api",
            "request_types": ["perpDexs", "metaAndAssetCtxs"],
            "quality": {
                "status": "PASS",
                "provider_timestamp_available": False,
                "collection_timestamp_is_not_provider_timestamp": True,
                "historical_open_interest_not_inferred": True,
            },
            "data": payload,
        }
    except Exception as exc:
        record = {
            "schema_version": "1.0",
            "mode": "RESEARCH_ONLY_HYPERLIQUID_PIT",
            "read_only": True,
            "collected_at_utc": collected_at,
            "provider": "hyperliquid_info_api",
            "quality": {"status": "FAILED"},
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    now = dt.datetime.now(dt.timezone.utc)
    out = pathlib.Path(args.output_root) / now.strftime("%Y/%m/%d")
    out.mkdir(parents=True, exist_ok=True)
    path = out / (now.strftime("%H%M%S") + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0 if record["quality"]["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
