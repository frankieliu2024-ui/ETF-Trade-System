from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


EXPERIENCE_TRADE_INDEX_START = "### 2.1 2026-07-13以来完整证券成交索引"
EXPERIENCE_TRADE_INDEX_END = "### 2.2 银证转账与非交易现金流水"
TRADE_EVENT_MARKER_RE = re.compile(r"TRADE_EVENT:([A-Za-z0-9_.-]+)")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def trade_signature(trade: dict) -> tuple:
    # Trade identity is anchored to the actual execution timestamp. A later
    # canonical adoption/confirmation timestamp is metadata about when the
    # repository accepted the fact and must not turn one historical execution
    # into a second trade identity.
    stamp = str(
        trade.get("datetime")
        or trade.get("executed_at_beijing")
        or trade.get("executed_at")
        or trade.get("trade_time")
        or trade.get("confirmed_at_beijing")
        or ""
    ).replace("T", " ")[:19]
    return (
        str(trade.get("code") or ""),
        str(trade.get("side") or trade.get("action") or "").upper(),
        float(trade.get("quantity") or 0),
        round(float(trade.get("price") or 0), 6),
        stamp,
    )


def confirmed_fee_amount(trade: dict) -> float:
    if str(trade.get("fee_status") or "").upper() != "CONFIRMED":
        return 0.0
    raw = trade.get("fee_amount")
    if raw is None:
        raw = trade.get("fee")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _table_number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("−", "-")
    if not text or text in {"-", "—", "--"} or "待确认" in text or "未知" in text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _etf_universe_codes(root: Path) -> set[str]:
    universe = read_json(root / "config" / "market" / "etf_monitor_universe.json", {}) or {}
    return {
        str(item.get("code") or "").strip()
        for item in (universe.get("objects") or [])
        if isinstance(item, dict) and item.get("code")
    }


def _is_etf_trade(trade: dict, universe_codes: set[str]) -> bool:
    asset_type = str(trade.get("asset_type") or "").upper()
    if asset_type in {"ETF", "FUND"}:
        return True
    if asset_type:
        return False
    code = str(trade.get("code") or "").strip()
    name = str(trade.get("name") or "")
    return code in universe_codes or "ETF" in name.upper()


def _is_reconstructed_etf_trade(trade: dict, universe_codes: set[str]) -> bool:
    asset_type = str(trade.get("asset_type") or "").upper()
    if asset_type in {"ETF", "FUND"}:
        return True
    if asset_type:
        return False
    source = str(trade.get("source") or "")
    event_backed = bool(trade.get("entered_events_trades")) or source.startswith("events/trades/")
    if not event_backed:
        return True
    code = str(trade.get("code") or "").strip()
    name = str(trade.get("name") or "")
    return code in universe_codes or "ETF" in name.upper()


def _event_execution_stamp(root: Path, remark: str) -> tuple[str | None, str | None]:
    """Resolve a formal Experience row marker to its event execution time.

    Historical backfill projection may display canonical adoption time in the
    human-readable table. The embedded TRADE_EVENT marker binds that row to the
    machine event, whose execution timestamp remains the economic identity.
    """
    match = TRADE_EVENT_MARKER_RE.search(str(remark or ""))
    if not match:
        return None, None
    event_id = match.group(1)
    event = read_json(root / "events" / "trades" / f"{event_id}.json", {}) or {}
    stamp = (
        event.get("executed_at_beijing")
        or event.get("executed_at")
        or event.get("trade_time")
        or event.get("datetime")
    )
    if not stamp:
        return None, event_id
    return str(stamp).replace("T", " ")[:19], event_id


def experience_etf_trade_index_facts(root: Path) -> list[dict]:
    """Parse formal Experience §2.1 as a bounded recovery projection.

    Event-linked rows use the linked machine event's execution timestamp for
    trade identity. This prevents a later canonical-adoption display timestamp
    from being replayed as a second economic trade.
    """
    path = root / "ETF交易复盘与经验库_2026.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if EXPERIENCE_TRADE_INDEX_START not in text or EXPERIENCE_TRADE_INDEX_END not in text:
        return []
    section = text.split(EXPERIENCE_TRADE_INDEX_START, 1)[1].split(EXPERIENCE_TRADE_INDEX_END, 1)[0]
    universe_codes = _etf_universe_codes(root)
    facts: list[dict] = []
    seen: set[tuple] = set()
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("|") or line.startswith("|-") or "|日期时间|" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 10:
            continue
        stamp, name, code, action, quantity, price, gross, fee, cash_flow, remark = cells[:10]
        side = {"买入": "BUY", "卖出": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(action.upper() if action.upper() in {"BUY", "SELL"} else action)
        qty = _table_number(quantity)
        px = _table_number(price)
        if side not in {"BUY", "SELL"} or qty is None or px is None:
            continue
        event_stamp, event_id = _event_execution_stamp(root, remark)
        effective_stamp = event_stamp or stamp
        fee_value = _table_number(fee)
        fact = {
            "datetime": effective_stamp,
            "name": name,
            "code": code,
            "side": side,
            "quantity": qty,
            "price": px,
            "gross_amount": _table_number(gross),
            "fee_amount": fee_value,
            "fee_status": "CONFIRMED" if fee_value is not None else "PENDING",
            "cash_flow_amount": _table_number(cash_flow),
            "source": "experience_trade_index",
            "source_confidence": "FORMAL_HUMAN_READABLE_TRANSACTION_INDEX",
            "entered_events_trades": False,
            "recovery_only": True,
            "remark": remark,
        }
        if event_id:
            fact["linked_trade_event_id"] = event_id
            fact["display_timestamp"] = stamp
            if event_stamp:
                fact["identity_timestamp_source"] = "LINKED_EVENT_EXECUTION_TIME"
        if not _is_etf_trade(fact, universe_codes):
            continue
        signature = trade_signature(fact)
        if signature in seen:
            continue
        seen.add(signature)
        facts.append(fact)
    return facts


def unintegrated_executed_trade_events(root: Path, reconstructed_trades: list[dict]) -> list[dict]:
    known_signatures = {trade_signature(t) for t in reconstructed_trades}
    events_dir = root / "events" / "trades"
    overlays: list[dict] = []
    if not events_dir.exists():
        return overlays
    for path in sorted(events_dir.glob("*.json")):
        event = read_json(path, {}) or {}
        if str(event.get("execution_status") or "").upper() != "EXECUTED":
            continue
        sig = trade_signature(event)
        if not sig[0] or sig in known_signatures:
            continue
        overlays.append(event)
        known_signatures.add(sig)
    return overlays


def canonical_etf_trade_facts(root: Path, reconstructed_trades: list[dict]) -> list[dict]:
    universe_codes = _etf_universe_codes(root)
    facts: list[dict] = []
    seen: set[tuple] = set()
    for trade in reconstructed_trades:
        if not _is_reconstructed_etf_trade(trade, universe_codes):
            continue
        signature = trade_signature(trade)
        if signature in seen:
            continue
        seen.add(signature)
        facts.append(trade)
    for trade in unintegrated_executed_trade_events(root, reconstructed_trades):
        if not _is_etf_trade(trade, universe_codes):
            continue
        signature = trade_signature(trade)
        if signature in seen:
            continue
        seen.add(signature)
        facts.append(trade)
    return facts


def recover_canonical_etf_trade_facts(root: Path, reconstructed_trades: list[dict], expected_count: int) -> list[dict]:
    current = canonical_etf_trade_facts(root, reconstructed_trades)
    if expected_count <= 0 or len(current) >= expected_count:
        return current
    seed = list(reconstructed_trades) + experience_etf_trade_index_facts(root)
    recovered = canonical_etf_trade_facts(root, seed)
    if len(recovered) != expected_count:
        raise ValueError(f"formal ETF trade reconstruction deficit: expected {expected_count}, recovered {len(recovered)}")
    return recovered


def _canonical_replay_declared_trade_count(root: Path) -> int:
    state = read_json(root / "data" / "state" / "etf_strategy_equity.json", {}) or {}
    if not str(state.get("schema_version") or "").startswith("1.0-canonical-replay"):
        return 0
    summary = state.get("summary") or {}
    try:
        return int(summary.get("trade_fact_count") or summary.get("trade_count") or 0)
    except (TypeError, ValueError):
        return 0


def canonical_etf_fee_projection(root: Path, reconstructed_trades: list[dict]) -> dict:
    facts = canonical_etf_trade_facts(root, reconstructed_trades)
    declared_count = _canonical_replay_declared_trade_count(root)
    recovered_from_formal_index = False
    if declared_count and len(facts) < declared_count:
        facts = recover_canonical_etf_trade_facts(root, reconstructed_trades, declared_count)
        recovered_from_formal_index = True
    confirmed = round(sum(confirmed_fee_amount(t) for t in facts), 2)
    pending = [t for t in facts if str(t.get("fee_status") or "").upper() != "CONFIRMED"]
    return {"effective_confirmed_fee_sum": confirmed, "pending_fee_count": len(pending), "canonical_trade_count": len(facts), "pending_trades": pending, "canonical_trades": facts, "recovered_from_formal_index": recovered_from_formal_index}


def effective_confirmed_fee_fact(root: Path, reconstructed_trades: list[dict]) -> dict:
    universe_codes = _etf_universe_codes(root)
    reconstructed_etf = [t for t in reconstructed_trades if _is_reconstructed_etf_trade(t, universe_codes)]
    projection = canonical_etf_fee_projection(root, reconstructed_trades)
    reconstructed_signatures = {trade_signature(t) for t in reconstructed_etf}
    overlays = [t for t in projection["canonical_trades"] if trade_signature(t) not in reconstructed_signatures]
    reconstructed = round(sum(confirmed_fee_amount(t) for t in reconstructed_etf), 2)
    overlay_fee = round(sum(confirmed_fee_amount(t) for t in overlays), 2)
    return {"reconstructed_confirmed_fee_sum": reconstructed, "executed_event_overlay_count": len(overlays), "executed_event_confirmed_fee_sum": overlay_fee, "effective_confirmed_fee_sum": projection["effective_confirmed_fee_sum"], "overlay_events": overlays, "pending_fee_count": projection["pending_fee_count"], "canonical_trade_count": projection["canonical_trade_count"]}


def latest_formal_review_confirmed_fees(root: Path) -> dict | None:
    review_dir = root / "events" / "reviews"
    candidates: list[tuple[str, float | None, str]] = []
    for path in review_dir.glob("*.json") if review_dir.exists() else []:
        event = read_json(path, {}) or {}
        review = event.get("review") or event.get("formal_review") or {}
        fact = review.get("etf_strategy_known_net") or {}
        try:
            fee = float(fact.get("confirmed_etf_fees"))
        except (TypeError, ValueError):
            fee = None
        stamp = str(event.get("updated_at_beijing") or event.get("account_updated_at") or review.get("market_date") or path.stem)
        candidates.append((stamp, fee, str(path.relative_to(root))))
    if not candidates:
        return None
    stamp, fee, source = max(candidates, key=lambda x: x[0])
    if fee is None:
        return None
    return {"confirmed_etf_fees": round(fee, 2), "source": source, "stamp": stamp}
