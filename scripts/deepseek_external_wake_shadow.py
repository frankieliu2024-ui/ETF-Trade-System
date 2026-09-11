"""Shadow-only DeepSeek external wake classifier.

This process is intended to run outside GitHub Actions. It never writes ETF
state and never invokes a canonical producer or notification workflow.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any

from scripts.llm_research_adapter import _parse_json_content

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
ALLOWED_SNAPSHOT_KEYS = {"observed_at", "session", "market_family", "proxies", "previous_snapshot_hash"}
ALLOWED_PROXY_KEYS = {"change_pct", "session", "freshness_status"}


def normalize_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - ALLOWED_SNAPSHOT_KEYS:
        raise ValueError("snapshot contains non-allowlisted fields")
    proxies = raw.get("proxies")
    if not isinstance(proxies, dict) or not proxies:
        raise ValueError("snapshot proxies are required")
    safe_proxies: dict[str, dict[str, Any]] = {}
    for name, value in proxies.items():
        if not isinstance(name, str) or not isinstance(value, dict) or set(value) - ALLOWED_PROXY_KEYS:
            raise ValueError("proxy contains non-allowlisted fields")
        safe_proxies[name] = dict(value)
    result = {key: raw[key] for key in raw if key != "proxies"}
    result["proxies"] = safe_proxies
    return result


def validate_wake_output(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("wake output is not an object")
    required = {"wake", "wake_class", "confidence", "reason_class"}
    if set(obj) != required or not isinstance(obj["wake"], bool):
        raise ValueError("wake output schema is invalid")
    for key in ("wake_class", "confidence", "reason_class"):
        if not isinstance(obj[key], str) or len(obj[key]) > 120:
            raise ValueError("wake output field is invalid")
    return obj


def classify_snapshot(snapshot: dict[str, Any], api_key: str, *, base_url: str = DEFAULT_BASE_URL,
                      model: str = DEFAULT_MODEL, timeout_seconds: int = 20) -> dict[str, Any]:
    safe_snapshot = normalize_snapshot(snapshot)
    if not api_key:
        return {"status": "NO_WAKE", "marker": None, "reason": "secret_unavailable"}
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify only whether the supplied market snapshot warrants a "
                    "canonical wake. Return JSON only with exactly: wake (boolean), "
                    "wake_class, confidence, reason_class. Never return trade, order, "
                    "amount, notification, provider, or state instructions."
                ),
            },
            {"role": "user", "content": json.dumps(safe_snapshot, ensure_ascii=False, separators=(",", ":"))},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        decision = validate_wake_output(_parse_json_content(payload["choices"][0]["message"]["content"]))
        return {
            "status": "WAKE" if decision["wake"] else "NO_WAKE",
            "marker": "[ETF_L0_WAKE]" if decision["wake"] else None,
            "wake_class": decision["wake_class"],
            "confidence": decision["confidence"],
            "reason_class": decision["reason_class"],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "NO_WAKE", "marker": None, "reason": type(exc).__name__}


def main() -> int:
    try:
        snapshot = json.load(sys.stdin)
        result = classify_snapshot(snapshot, os.environ.get("DEEPSEEK_API_KEY", ""))
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        result = {"status": "NO_WAKE", "marker": None, "reason": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
