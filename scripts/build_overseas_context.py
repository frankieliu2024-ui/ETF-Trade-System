from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from multi_source_market import _request_json, normalize_chart
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.multi_source_market import _request_json, normalize_chart
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CORE = {
    "NDX": "^NDX",
    "SOX": "^SOX",
    "N225": "^N225",
}


def latest_valid_row(payload: dict, symbol: str) -> dict:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    for idx in range(len(timestamps) - 1, -1, -1):
        row = {}
        complete = True
        for field in ("open", "high", "low", "close"):
            values = quote.get(field) or []
            value = values[idx] if idx < len(values) else None
            row[field] = value
            complete = complete and value is not None
        if complete:
            volume_values = quote.get("volume") or []
            row["volume"] = volume_values[idx] if idx < len(volume_values) else None
            row["timestamp"] = timestamps[idx]
            row["date_utc"] = datetime.fromtimestamp(timestamps[idx], timezone.utc).date().isoformat()
            row["provider_timezone"] = result.get("meta", {}).get("timezone", "unknown")
            row["symbol"] = symbol
            return row
    raise RuntimeError("no complete latest row")


def build() -> dict:
    objects = {}
    pass_count = 0
    for object_id, symbol in CORE.items():
        try:
            payload = _request_json(symbol, period="5d")
            normalized = normalize_chart(payload, symbol)
            latest = latest_valid_row(payload, symbol)
            quality = "PASS" if normalized.get("quality", {}).get("pass") else "DEGRADED"
            if quality == "PASS":
                pass_count += 1
            objects[object_id] = {
                "object": object_id,
                "provider": "yahoo_chart_api",
                "symbol": symbol,
                "quality_status": quality,
                "latest": latest,
                "source_latest_date": normalized.get("latest_date"),
                "note": "海外指数仅作市场背景、增强或反向证据，不单独生成ETF交易动作。",
            }
        except Exception as exc:  # preserve partial availability
            objects[object_id] = {
                "object": object_id,
                "provider": "yahoo_chart_api",
                "symbol": symbol,
                "quality_status": "FAILED",
                "error": str(exc)[-500:],
                "note": "失败对象不得用旧值冒充当前状态；其余海外对象可独立保留。",
            }
    overall = "PASS" if pass_count == len(CORE) else ("DEGRADED" if pass_count else "FAILED")
    return {
        "generated_at": now_utc(),
        "provider": "yahoo_chart_api",
        "scope": "CORE_OVERSEAS_INDEX_BACKGROUND",
        "quality_status": overall,
        "objects": objects,
        "decision_boundary": "仅作海外市场背景、增强或反向证据；正式ETF动作仍由MASTER链条和A股/ETF自身反馈决定。",
    }


def main() -> None:
    context = build()
    atomic_json_write(ROOT / "data" / "state" / "overseas_context.json", context)
    print(json.dumps({
        "ok": True,
        "quality_status": context["quality_status"],
        "objects": {k: v["quality_status"] for k, v in context["objects"].items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
