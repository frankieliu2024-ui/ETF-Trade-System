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
DEFAULT_A_SHARE_STOCK_POLICY = {"primary": "tencent_qq", "fallback": ["hithink_finance", "eastmoney_push2"], "direct_only": True}
DEFAULT_A_SHARE_INDEX_POLICY = {"primary": "tencent_qq", "fallback": ["hithink_finance"], "direct_only": True}
A_SHARE_SUFFIXES = (".SH", ".SZ")
A_SHARE_INDEX_CODES = {"000001", "399006", "000688"}

def infer_asset_identity(object_id: str, *, asset_class: str = "", code: str = "", market: str = "") -> dict[str, str]:
    """Derive one canonical market/type identity before provider selection.

    Explicit asset_class/market wins; otherwise only unambiguous six-digit
    mainland symbols are inferred. Overseas symbols remain UNKNOWN until their
    verified market metadata is supplied, preventing unsafe provider guessing.
    """
    normalized_code = str(code or object_id or "").strip().upper()
    normalized_class = str(asset_class or "").strip().upper()
    normalized_market = str(market or "").strip().upper()
    suffix = normalized_code.rsplit(".", 1)[-1] if "." in normalized_code else ""
    if not normalized_market and suffix in {"SH", "SZ", "BJ"}:
        normalized_market = "CN"
    if normalized_market == "CN" and normalized_class in {"ETF", "STOCK", "INDEX"}:
        return {"market": "CN", "asset_class": normalized_class, "code": normalized_code}
    if normalized_market == "CN" and normalized_code.isdigit() and len(normalized_code) == 6:
        inferred_class = "INDEX" if normalized_code in A_SHARE_INDEX_CODES else "UNKNOWN"
        return {"market": "CN", "asset_class": inferred_class, "code": normalized_code}
    return {"market": normalized_market or "UNKNOWN", "asset_class": normalized_class or "UNKNOWN", "code": normalized_code}

def resolve_provider_policy(config: dict[str, Any], object_id: str, *, asset_class: str = "", code: str = "", market: str = "") -> dict[str, Any] | None:
    identity = infer_asset_identity(object_id, asset_class=asset_class, code=code, market=market)
    explicit = (config.get("object_fallback_policy") or {}).get(str(object_id))
    if isinstance(explicit, dict) and explicit.get("primary"):
        return {**dict(explicit), "identity": identity, "resolution": "explicit_object_override"}
    defaults = (config.get("asset_type_provider_defaults") or {})
    key = f"{identity['market']}:{identity['asset_class']}"
    configured = defaults.get(key)
    if isinstance(configured, dict) and configured.get("primary"):
        return {**dict(configured), "identity": identity, "resolution": "asset_type_default"}
    return None

def load_provider_priority(root: Path) -> dict[str, Any]:
    return json.loads((root / "config/market/provider_priority.json").read_text(encoding="utf-8"))

def is_a_share_etf_identity(thscode: str, code: str = "") -> bool:
    return str(thscode or "").upper().endswith(A_SHARE_SUFFIXES) and str(code or "").isdigit() and len(str(code)) == 6

def resolve_object_policy(config: dict[str, Any], object_id: str, *, asset_class: str = "", code: str = "") -> dict[str, Any] | None:
    resolved = resolve_provider_policy(config, object_id, asset_class=asset_class, code=code)
    if resolved:
        return {key: value for key, value in resolved.items() if key not in {"identity", "resolution"}}
    if str(asset_class).upper() == "ETF" and is_a_share_etf_identity(object_id, code):
        default = config.get("default_object_fallback_policy") or DEFAULT_A_SHARE_ETF_POLICY
        return dict(default) if isinstance(default, dict) and default.get("primary") else None
    if str(asset_class).upper() == "STOCK" and str(object_id).upper().endswith(A_SHARE_SUFFIXES):
        return dict(DEFAULT_A_SHARE_STOCK_POLICY)
    if str(asset_class).upper() == "INDEX" and str(object_id).upper().endswith(A_SHARE_SUFFIXES):
        return dict(DEFAULT_A_SHARE_INDEX_POLICY)
    return None

def effective_etf_policies(config: dict[str, Any], universe: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in universe:
        code, thscode = str(item.get("code") or ""), str(item.get("thscode") or "")
        policy = resolve_object_policy(config, thscode, asset_class="ETF", code=code)
        if policy:
            result[thscode] = policy
    return result
