from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid or unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root is not an object: {path}")
    return value


def validate(root: Path) -> dict:
    context_path = root / "data" / "state" / "overseas_context.json"
    index_path = root / "data" / "state" / "hyperliquid_pulse_index.json"
    context = read_json(context_path)
    index = read_json(index_path)
    hyperliquid = context.get("hyperliquid")
    if not isinstance(hyperliquid, dict):
        raise RuntimeError("overseas_context.json has no hyperliquid object")
    latest = index.get("latest")
    if not isinstance(latest, dict):
        raise RuntimeError("hyperliquid pulse index has no latest record")
    identity = str(latest.get("identity") or "").strip()
    observations = latest.get("observations")
    if not identity:
        raise RuntimeError("hyperliquid pulse index latest identity is empty")
    if not isinstance(observations, list) or not observations:
        raise RuntimeError("hyperliquid pulse index latest has no observations")
    if not any(str(latest.get(key) or "").strip() for key in ("formed_at_beijing", "fact_time_beijing")):
        raise RuntimeError("hyperliquid pulse index latest has no collection/formation timestamp")
    for key in ("etf_bindings", "binding_candidates"):
        if not isinstance(latest.get(key), list):
            raise RuntimeError(f"hyperliquid pulse index latest {key} is not a list")
    if not str(latest.get("binding_status") or "").strip():
        raise RuntimeError("hyperliquid pulse index latest binding_status is empty")
    context_id = str(hyperliquid.get("context_id") or "").strip()
    indexed_context_id = str(latest.get("context_id") or "").strip()
    if context_id and indexed_context_id != context_id:
        raise RuntimeError(
            "hyperliquid pulse index context_id does not match overseas_context context_id"
        )
    continuity = index.get("continuity")
    if not isinstance(continuity, dict) or continuity.get("status") != "READY":
        raise RuntimeError("hyperliquid pulse index continuity is not READY")
    return {
        "status": "PASS",
        "identity": identity,
        "context_id": indexed_context_id or context_id,
        "observation_count": len(observations),
        "index": str(index_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("ETF_SYSTEM_ROOT", "."))
    args = parser.parse_args()
    print(json.dumps(validate(Path(args.root).resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
