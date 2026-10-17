"""Canonical provider-policy resolution for monitored objects.

Static object overrides are reserved for verified exceptions. Ordinary mainland
A-share ETFs resolve through one registered default chain, so new holdings or
observations do not require a manual config entry.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_A_SHARE_ETF_POLICY = {"primary": "tencent_qq", "fallback": ["hithink_finance", "eastmoney_push2"], "direct_only": True}
A_SHARE_SUFFIXES = (".SH", ".SZ")

def load_provider_priority(root: Path) -> dict[str, Any]:
    return json.loads((root / "config/market/provider_priority.json").read_text(encoding="utf-8"))

def is_a_share_etf_identity(thscode: str, code: str = "") -> bool:
    return str(thscode or "").upper().endswith(A_SHARE_SUFFIXES) and str(code or "").isdigit() and len(str(code)) == 6

def resolve_object_policy(config: dict[str, Any], object_id: str, *, asset_class: str = "", code: str = "") -> dict[str, Any] | None:
    explicit = (config.get("object_fallback_policy") or {}).get(str(object_id))
    if isinstance(explicit, dict) and explicit.get("primary"):
        return dict(explicit)
    if str(asset_class).upper() == "ETF" and is_a_share_etf_identity(object_id, code):
        default = config.get("default_object_fallback_policy") or DEFAULT_A_SHARE_ETF_POLICY
        return dict(default) if isinstance(default, dict) and default.get("primary") else None
    return None

def effective_etf_policies(config: dict[str, Any], universe: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in universe:
        code, thscode = str(item.get("code") or ""), str(item.get("thscode") or "")
        policy = resolve_object_policy(config, thscode, asset_class="ETF", code=code)
        if policy:
            result[thscode] = policy
    return result
