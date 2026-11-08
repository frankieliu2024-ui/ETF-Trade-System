#!/usr/bin/env python3
"""Research-only batch collector using the canonical Hyperliquid exposure map."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

HL_URL = "https://api.hyperliquid.xyz/info"
ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = ROOT / "config" / "market" / "hyperliquid_etf_exposure_map.json"
DEFAULT_OBJECTS = [
    "xyz:CXMT","xyz:SMH","xyz:SOXL","xyz:DRAM","xyz:NVDA","xyz:AMD","xyz:SNDK",
    "xyz:XYZ100","xyz:SP500","xyz:EWY","xyz:EWJ","xyz:EWT","xyz:GOLD",
    "xyz:COPPER","xyz:BRENTOIL","xyz:XLE","xyz:XBI","xyz:BABA","xyz:AVGO",
    "xyz:NBIS","para:COHR","xyz:GIGADEV",
]

def post(payload: dict):
    req = Request(HL_URL, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)

def load_mapping() -> dict[str, list[dict]]:
    payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for row in payload.get("asset_mappings", []):
        asset = str(row.get("asset") or "").strip()
        if asset:
            result.setdefault(asset, []).append(row)
    return result

def candles(coin: str, start_ms: int, end_ms: int) -> dict:
    payload = {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1d", "startTime": start_ms, "endTime": end_ms}}
    try:
        rows = post(payload)
        if not isinstance(rows, list):
            return {"status": "INVALID_RESPONSE", "coin": coin, "rows": []}
        return {"status": "PASS" if rows else "EMPTY", "coin": coin, "rows": rows}
    except Exception as exc:
        return {"status": "FAILED", "coin": coin, "rows": [],
                "error": f"{type(exc).__name__}: {exc}"}

def run(coins: list[str], start_ms: int, end_ms: int) -> dict:
    mapping = load_mapping()
    results = []
    for coin in coins:
        observed = candles(coin, start_ms, end_ms)
        rows = mapping.get(coin, [])
        results.append({
            **observed,
            "canonical_mapping": rows,
            "mapping_status": "CONFIGURED" if rows else "UNMAPPED",
        })
    return {
        "schema_version": "research-hl-overnight-v3",
        "mode": "RESEARCH_ONLY_EXPLORATORY",
        "read_only": True,
        "canonical_mapping_source": str(MAPPING_PATH.relative_to(ROOT)),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "pit_window": {"start_ms": start_ms, "end_ms": end_ms},
        "objects": results,
        "research_status": "READY_FOR_A_SHARE_ALIGNMENT",
        "decision_boundary": "No production state, formal decision, trading permission, amount or order is generated.",
        "required_followups": [
            "use canonical family/name_markers/etf_codes without creating a second mapping source",
            "add transmission level and result variable only in research output",
            "calculate overnight-to-open gap and post-open outcomes",
            "control A-share prior-day return and run holdout/rolling checks",
        ],
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", nargs="+", default=DEFAULT_OBJECTS)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = json.dumps(run(args.coins, args.start_ms, args.end_ms),
                        ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
    print(result)
