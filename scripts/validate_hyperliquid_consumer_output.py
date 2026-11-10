"""Hard gate for Hyperliquid consumer prose.

The gate does not create business conclusions. It verifies that a consumer
actually renders conclusions from the shared contribution packet.
"""

from __future__ import annotations

from typing import Any


GENERIC_PHRASES = (
    "提供背景",
    "辅助参考",
    "可用于判断",
    "方向确认",
    "外部证据显示",
    "全球风险偏好变化",
    "无法判断",
    "没有明确增量价值",
)


class HyperliquidConsumerContractError(ValueError):
    pass


def validate_consumer_output(*, text: str, contributions: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate rendered consumer prose against ETF-level contribution rows."""
    rendered = str(text or "")
    rows = [row for row in contributions if isinstance(row, dict)]
    if not rows:
        raise HyperliquidConsumerContractError("缺少Hyperliquid ETF级贡献结论")
    required = {
        "ashare_code",
        "decision_effect",
        "priority_effect",
        "next_trigger",
    }
    errors: list[str] = []
    for row in rows:
        missing = sorted(key for key in required if not str(row.get(key) or "").strip())
        if missing:
            errors.append(f"{row.get('ashare_code') or 'UNKNOWN'}缺少字段:{','.join(missing)}")
            continue
        code = str(row["ashare_code"])
        if code not in rendered:
            errors.append(f"{code}未出现在消费者正文")
        if str(row["decision_effect"]) not in rendered and str(row["priority_effect"]) not in rendered:
            errors.append(f"{code}缺少明确决策作用")
        if str(row["next_trigger"]) not in rendered:
            errors.append(f"{code}缺少下一触发条件")
    generic = [phrase for phrase in GENERIC_PHRASES if phrase in rendered]
    if generic:
        errors.append("存在泛化表述:" + "、".join(generic))
    if errors:
        raise HyperliquidConsumerContractError("；".join(errors))
    return {"status": "PASS", "row_count": len(rows)}


def validate_hyperliquid_consumer_output(text: str, packet: dict[str, Any]) -> dict[str, Any]:
    return validate_consumer_output(
        text=text,
        contributions=list(packet.get("etf_contributions") or []),
    )


def validate_contribution_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the shared packet before any consumer can read it."""
    if not isinstance(packet, dict):
        raise HyperliquidConsumerContractError("Hyperliquid贡献包不是对象")
    objects = list(packet.get("mapped_objects") or [])
    rows = list(packet.get("etf_contributions") or [])
    if not objects or not rows:
        raise HyperliquidConsumerContractError("Hyperliquid贡献包缺少对象级或ETF级结论")
    errors = []
    for item in objects:
        for key in ("object_id", "observation_as_of", "direction_signal", "decision_effect"):
            if not str(item.get(key) or "").strip():
                errors.append(f"对象缺少字段:{key}")
        signal = item.get("signal_fields") if isinstance(item.get("signal_fields"), dict) else {}
        if signal.get("pulse_comparison_status") == "COMPARABLE" and signal.get("comparison_reason"):
            errors.append(f"{item.get('object_id')}可比较但残留比较缺失原因")
    for row in rows:
        for key in ("ashare_code", "decision_effect", "priority_effect", "next_trigger"):
            if not str(row.get(key) or "").strip():
                errors.append(f"{row.get('ashare_code') or 'UNKNOWN'}缺少字段:{key}")
    if errors:
        raise HyperliquidConsumerContractError("；".join(errors))
    return {"status": "PASS", "object_count": len(objects), "etf_row_count": len(rows)}
