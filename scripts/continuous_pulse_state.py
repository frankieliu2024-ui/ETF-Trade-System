"""Shared, deterministic state transitions for comparable market pulses.

This module is deliberately side-effect free. Producers provide facts; the
canonical notification path decides whether a transition has user value.
"""
from __future__ import annotations


def _number(value):
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _direction(item: dict) -> str:
    value = str(item.get("direction") or "").upper()
    if value in {"UP", "POSITIVE", "STRONG", "DOWN", "NEGATIVE", "WEAK", "MIXED", "DIVERGED"}:
        return value
    spot = item.get("discovery_spot") if isinstance(item.get("discovery_spot"), dict) else {}
    change = _number(item.get("change_pct"))
    if change is None:
        change = _number(spot.get("change_pct"))
    if change is None:
        change = _number(item.get("day_change_pct"))
    if change is None:
        return "UNKNOWN"
    return "UP" if change > 0 else "DOWN" if change < 0 else "FLAT"


def transition(before: dict | None, after: dict | None) -> str:
    """Classify one object's current pulse against its prior comparable pulse."""
    old, new = before or {}, after or {}
    if not old:
        return "INITIAL"
    old_dir, new_dir = _direction(old), _direction(new)
    if old_dir not in {"UNKNOWN", "FLAT"} and new_dir not in {"UNKNOWN", "FLAT"} and old_dir != new_dir:
        return "REVERSING"
    old_spot = old.get("discovery_spot") if isinstance(old.get("discovery_spot"), dict) else {}
    new_spot = new.get("discovery_spot") if isinstance(new.get("discovery_spot"), dict) else {}
    old_value = _number(old.get("change_pct"))
    new_value = _number(new.get("change_pct"))
    if old_value is None:
        old_value = _number(old_spot.get("change_pct"))
    if new_value is None:
        new_value = _number(new_spot.get("change_pct"))
    if old_value is None:
        old_value = _number(old.get("day_change_pct"))
    if new_value is None:
        new_value = _number(new.get("day_change_pct"))
    if old_value is not None and new_value is not None:
        if abs(new_value) > abs(old_value) + 0.05:
            return "STRENGTHENING"
        if abs(new_value) + 0.05 < abs(old_value):
            return "WEAKENING"
    if old_dir == new_dir and new_dir not in {"UNKNOWN", "FLAT"}:
        return "PERSISTING"
    return "CHANGED"


def material_transition(state: str) -> bool:
    return state in {"INITIAL", "STRENGTHENING", "WEAKENING", "REVERSING", "RECOVERING", "DIVERGING", "CONFIRMED_TRANSMISSION", "FAILED_TRANSMISSION", "RISK_ESCALATING", "RISK_EASING"}

