from __future__ import annotations

import json
import re
from pathlib import Path


def _read_json(root: Path, relative: str) -> dict:
    try:
        value = json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def validate_trade_review_presentation_semantics(root: Path, review: dict, final_content: str) -> None:
    """Block direct prose contradictions against the request-bound object projections."""
    content = str(final_content or "")
    discovery = review.get("formal_discovery") if isinstance(review, dict) else None
    opportunity = review.get("formal_opportunity") if isinstance(review, dict) else None
    if not isinstance(discovery, dict):
        discovery = _read_json(root, "data/state/formal_discovery_current.json")
    if not isinstance(opportunity, dict):
        opportunity = _read_json(root, "data/state/formal_opportunity_current.json")
    correction = re.compile(r"(?:不能|不得|不可|不应|不能将|不得将).{0,20}(?:写成|断言|误读|等同|替代)")
    status = str(discovery.get("status") or "").upper()
    candidates = discovery.get("candidates") or []
    if status in {"READY", "COMPLETED", "COMPLETE"} and candidates:
        missing = re.compile(r"(?:没有|缺失|未形成|不可用|未完成).{0,16}(?:DISCOVERY|全市场发现)|(?:DISCOVERY|全市场发现).{0,16}(?:没有|缺失|未形成|不可用|未完成)", re.IGNORECASE)
        if missing.search(content) and not correction.search(content):
            raise ValueError(f"trade review prose contradicts current Discovery: status={status}, candidate_count={len(candidates)}")
    opportunity_status = str(opportunity.get("status") or "").upper()
    opportunity_count = opportunity.get("opportunity_count")
    if opportunity_count is not None and opportunity_status not in {"", "UNKNOWN", "MISSING"}:
        zero = re.compile(r"(?:机会(?:ETF)?(?:数量)?|机会层).{0,8}(?:为|是)?\s*0|没有机会", re.IGNORECASE)
        if opportunity_count not in (0, "0") and zero.search(content) and not correction.search(content):
            raise ValueError(f"trade review prose contradicts current opportunity layer: status={opportunity_status}, opportunity_count={opportunity_count}")


def normalize_verified_close_data_time(data_time: dict) -> dict:
    normalized = dict(data_time or {})
    if str(normalized.get("close_data_status") or "").upper() == "VERIFIED_SESSION_CLOSE":
        normalized.pop("close_data_gap", None)
    return normalized
