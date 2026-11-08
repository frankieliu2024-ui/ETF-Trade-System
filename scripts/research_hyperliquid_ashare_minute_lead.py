#!/usr/bin/env python3
"""Research-only batch collector for Hyperliquid overnight lead studies."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

HL_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_OBJECTS = [
    "xyz:CXMT","SMH","SOXL","DRAM","NVDA","AMD","SNDK","XYZ100","SP500",
    "EWY","EWJ","EWT","GOLD","COPPER","BRENTOIL","XLE","XBI","BABA",
    "AVGO","NBIS","COHR","GIGADEV",
]

def post(payload: dict):
    req = Request(HL_URL, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)

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
    started = datetime.now(timezone.utc).isoformat()
    results = [candles(coin, start_ms, end_ms) for coin in coins]
    return {
        "schema_version": "research-hl-overnight-v2",
        "mode": "RESEARCH_ONLY_EXPLORATORY",
        "read_only": True,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "pit_window": {"start_ms": start_ms, "end_ms": end_ms},
        "objects": results,
        "mapping_status": "NOT_INFERRED",
        "research_status": "READY_FOR_A_SHARE_ALIGNMENT",
        "decision_boundary": "No production state, formal decision, trading permission, amount or order is generated.",
        "required_followups": [
            "align each object independently to a documented A-share target",
            "calculate overnight-to-open gap and post-open outcomes",
            "control A-share prior-day return and run holdout/rolling checks",
            "audit timestamps, missingness, costs and executable prices",
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
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result + "\n")
    print(result)
