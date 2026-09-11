from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def trade_signature(trade: dict) -> tuple:
    stamp = str(
        trade.get("datetime")
        or trade.get("confirmed_at_beijing")
        or trade.get("executed_at_beijing")
        or trade.get("executed_at")
        or trade.get("trade_time")
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


def _etf_universe_codes(root: Path) -> set[str]:
    universe = read_json(root / "config" / "market" / "etf_monitor_universe.json", {}) or {}
    return {
        str(item.get("code") or "").strip()
        for item in (universe.get("objects") or [])
        if isinstance(item, dict) and item.get("code")
    }


def _is_etf_trade(trade: dict, universe_codes: set[str]) -> bool:
    """Classify a raw executed event using explicit type first, then canonical ETF universe."""
    asset_type = str(trade.get("asset_type") or "").upper()
    if asset_type in {"ETF", "FUND"}:
        return True
    if asset_type:
        return False
    code = str(trade.get("code") or "").strip()
    name = str(trade.get("name") or "")
    return code in universe_codes or "ETF" in name.upper()


def _is_reconstructed_etf_trade(trade: dict, universe_codes: set[str]) -> bool:
    """Classify reconstructed rows while preserving legacy ETF-strategy scope.

    Event-backed rows without asset_type must be checked against the canonical
    ETF universe; older strategy reconstruction rows remain ETF-scoped.
    """
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
def canonical_etf_trade_facts(root: Path, reconstructed_trades: list[dict]) -> list[dict]:
    """Return one deduplicated ETF-only fact set for position, fee and count projections."""
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


def canonical_etf_fee_projection(root: Path, reconstructed_trades: list[dict]) -> dict:
    facts = canonical_etf_trade_facts(root, reconstructed_trades)
    confirmed = round(sum(confirmed_fee_amount(t) for t in facts), 2)
    pending = [t for t in facts if str(t.get("fee_status") or "").upper() != "CONFIRMED"]
    return {
        "effective_confirmed_fee_sum": confirmed,
        "pending_fee_count": len(pending),
        "canonical_trade_count": len(facts),
        "pending_trades": pending,
        "canonical_trades": facts,
    }


def effective_confirmed_fee_fact(root: Path, reconstructed_trades: list[dict]) -> dict:
    reconstructed_etf = [t for t in reconstructed_trades if _is_reconstructed_etf_trade(t, universe_codes)]
    projection = canonical_etf_fee_projection(root, reconstructed_trades)
    reconstructed_signatures = {trade_signature(t) for t in reconstructed_etf}
    overlays = [
        t for t in projection["canonical_trades"]
        if trade_signature(t) not in reconstructed_signatures
    ]
    reconstructed = round(sum(confirmed_fee_amount(t) for t in reconstructed_etf), 2)
    overlay_fee = round(sum(confirmed_fee_amount(t) for t in overlays), 2)
    return {
        "reconstructed_confirmed_fee_sum": reconstructed,
        "executed_event_overlay_count": len(overlays),
        "executed_event_confirmed_fee_sum": overlay_fee,
        "effective_confirmed_fee_sum": projection["effective_confirmed_fee_sum"],
        "overlay_events": overlays,
        "pending_fee_count": projection["pending_fee_count"],
        "canonical_trade_count": projection["canonical_trade_count"],
    }


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
        stamp = str(
            event.get("updated_at_beijing")
            or event.get("account_updated_at")
            or review.get("market_date")
            or path.stem
        )
        candidates.append((stamp, fee, str(path.relative_to(root))))
    if not candidates:
        return None
    stamp, fee, source = max(candidates, key=lambda x: x[0])
    if fee is None:
        return None
    return {"confirmed_etf_fees": round(fee, 2), "source": source, "stamp": stamp}
