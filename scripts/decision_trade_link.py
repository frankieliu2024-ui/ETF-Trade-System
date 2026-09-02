from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _decision_mentions_trade(event: dict, trade: dict) -> bool:
    formal = event.get("formal_decision") or {}
    text = json.dumps(formal, ensure_ascii=False)
    code = str(trade.get("code") or "")
    name = str(trade.get("name") or "")
    if code and code not in text and name and name not in text:
        return False
    side = str(trade.get("side") or "").upper()
    if side in {"SELL", "S", "卖", "卖出"} or "卖" in side:
        return any(k in text for k in ("卖出", "退出", "降低风险", "减持", "释放"))
    if side in {"BUY", "B", "买", "买入"} or "买" in side:
        return any(k in text for k in ("买入", "Trial", "Confirm", "新增"))
    return True


def _effective_time(event: dict) -> datetime | None:
    """Return exact business-effective decision time when available."""
    return _time(event.get("decision_effective_at_beijing") or event.get("issued_at_beijing") or event.get("decision_time_beijing"))


def resolve_link(root: Path, trade: dict, requested_id: str = "") -> tuple[str, dict, str]:
    """Return a decision no later than the confirmed trade time.

    Explicit links are kept only when PIT-valid. Otherwise choose the latest prior
    formal decision that actually mentions the traded object and compatible action.
    Never link a trade to a later confirmation event.
    """
    trade_time = _time(trade.get("confirmed_at_beijing"))
    decision_dir = root / "events" / "decisions"
    if trade_time is None or not decision_dir.exists():
        return "", {}, "NO_VALID_TRADE_TIME"

    requested_id = str(requested_id or "")
    if requested_id:
        path = decision_dir / f"{requested_id}.json"
        event = _load(path) if path.exists() else {}
        decision_time = _effective_time(event)
        recorded_time = _time(event.get("recorded_at_beijing"))
        if event and _decision_mentions_trade(event, trade):
            if decision_time and decision_time <= trade_time:
                return requested_id, event, "EXPLICIT_PIT_VALID"
            if (
                recorded_time and recorded_time >= trade_time
                and str(event.get("decision_effective_ordering") or "").upper() == "BEFORE_EXECUTION"
                and str(event.get("timing_quality") or "").upper() in {"USER_CONFIRMED_BOUNDED", "USER_CONFIRMED"}
            ):
                return requested_id, event, "EXPLICIT_ASYNC_CANONICALIZATION"

    candidates: list[tuple[datetime, str, dict]] = []
    for path in decision_dir.glob("*.json"):
        event = _load(path)
        decision_time = _effective_time(event)
        if decision_time is None or decision_time > trade_time:
            continue
        if not _decision_mentions_trade(event, trade):
            continue
        candidates.append((decision_time, str(event.get("decision_id") or path.stem), event))
    if not candidates:
        return "", {}, "NO_PRIOR_MATCHING_DECISION"
    _, decision_id, event = max(candidates, key=lambda x: x[0])
    return decision_id, event, "AUTO_PRIOR_MATCH"


def decision_price_for_trade(event: dict, trade: dict):
    price = event.get("price_at_decision")
    try:
        if price is not None:
            return float(price)
    except (TypeError, ValueError):
        pass
    code = str(trade.get("code") or "")
    for row in ((event.get("comparison_snapshot") or {}).get("items") or []):
        if str(row.get("code") or "") == code:
            try:
                return float(row.get("price"))
            except (TypeError, ValueError):
                return None
    return None
