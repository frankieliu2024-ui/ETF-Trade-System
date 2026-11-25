from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

try:
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
STATE_PATH = "data/state/fixed_ratio_position_metrics.json"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(position):
    return _number(position.get("current_price") or position.get("last_price"))


def _cost(position):
    return _number(position.get("cost") or position.get("cost_price"))


def _return_pct(price, cost):
    if price is None or cost in (None, 0):
        return None
    return (price - cost) / cost * 100.0


def _load(root: Path) -> dict:
    path = root / STATE_PATH
    if not path.exists():
        return {"schema_version": 1, "positions": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"schema_version": 1, "positions": {}}
    except (OSError, json.JSONDecodeError, TypeError):
        return {"schema_version": 1, "positions": {}}


def refresh_fixed_ratio_metrics(root: Path, account: dict) -> dict:
    """Maintain one monotonic high-water record per confirmed holding lifecycle.

    A new BUY fact, a re-entry after zero quantity, or a quantity increase starts
    a new baseline. Otherwise the high-water return cannot decrease.
    """
    state = _load(root)
    records = state.setdefault("positions", {})
    now = str(account.get("updated_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds"))
    trades = account.get("trades") or []
    for position in account.get("positions") or []:
        code = str(position.get("code") or "").strip()
        if not code:
            continue
        quantity = int(position.get("quantity") or 0)
        cost = _cost(position)
        price = _price(position)
        record = records.get(code) if isinstance(records.get(code), dict) else {}
        seen_trade_ids = set(record.get("seen_trade_event_ids") or [])
        new_buy = False
        for trade in trades:
            if str(trade.get("code") or "").strip() != code:
                continue
            event_id = str(trade.get("event_id") or "").strip()
            if str(trade.get("side") or "").upper() == "BUY" and event_id and event_id not in seen_trade_ids:
                new_buy = True
            if event_id:
                seen_trade_ids.add(event_id)
        reset = (
            not record
            or str(record.get("lifecycle_status") or "").upper() == "CLOSED"
            or new_buy
            or quantity > int(record.get("last_quantity") or 0)
        )
        if quantity <= 0:
            record.update({
                "code": code,
                "lifecycle_status": "CLOSED",
                "last_quantity": 0,
                "updated_at": now,
                "seen_trade_event_ids": sorted(seen_trade_ids),
            })
            records[code] = record
            continue
        if reset:
            baseline_cost = cost
            current_return = _return_pct(price, baseline_cost)
            high_water = max(0.0, current_return) if current_return is not None else 0.0
            record = {
                "code": code,
                "lifecycle_id": f"{code}-{now}-{uuid4().hex[:8]}",
                "lifecycle_status": "ACTIVE",
                "baseline_cost": baseline_cost,
                "baseline_at": now,
                "high_water_return_pct": high_water,
                "high_water_at": now,
                "last_quantity": quantity,
                "updated_at": now,
                "seen_trade_event_ids": sorted(seen_trade_ids),
            }
        else:
            baseline_cost = _number(record.get("baseline_cost")) or cost
            current_return = _return_pct(price, baseline_cost)
            old_high = _number(record.get("high_water_return_pct"))
            high_water = max(old_high if old_high is not None else 0.0,
                             current_return if current_return is not None else 0.0)
            if high_water != old_high:
                record["high_water_at"] = now
            record.update({
                "lifecycle_status": "ACTIVE",
                "baseline_cost": baseline_cost,
                "high_water_return_pct": high_water,
                "last_quantity": quantity,
                "updated_at": now,
                "seen_trade_event_ids": sorted(seen_trade_ids),
            })
        records[code] = record
    state["schema_version"] = 1
    state["updated_at"] = now
    atomic_json_write(root / STATE_PATH, state)
    return records
