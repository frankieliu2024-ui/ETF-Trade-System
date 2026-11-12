from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPERIENCE_TRADE_INDEX_START = "### 2.1 2026-07-13以来完整证券成交索引"
EXPERIENCE_TRADE_INDEX_END = "### 2.2 银证转账与非交易现金流水"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def trade_signature(trade: dict) -> tuple:
    # A stable canonical event identity dominates adoption/confirmation clocks.
    # The same event_id may be replayed after a screenshot timestamp or
    # execution-time repair; it must remain one economic execution.
    event_id = str(trade.get("event_id") or "").strip()
    if event_id:
        return ("event_id", event_id)
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
    FACT_ENRICHMENT_ONLY rows are machine overlays already represented by the
    underlying economic trade and must never become replay seed executions.
    """
    if str(trade.get("replay_semantics") or "").upper() == "FACT_ENRICHMENT_ONLY":
        return False
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


def _economic_trade_core(trade: dict) -> tuple:
    """Return economic identity without time for bounded adoption-shadow matching."""
    return (
        str(trade.get("code") or ""),
        str(trade.get("side") or trade.get("action") or "").upper(),
        float(trade.get("quantity") or 0),
        round(float(trade.get("price") or 0), 6),
    )


def _execution_stamp(trade: dict) -> str:
    """Return the broker execution time, never the later adoption/confirmation time."""
    return str(
        trade.get("executed_at_beijing")
        or trade.get("executed_at")
        or trade.get("trade_time")
        or trade.get("datetime")
        or trade.get("confirmed_at_beijing")
        or ""
    ).replace("T", " ")[:19]


def _execution_identity(trade: dict) -> tuple:
    """Identify one economic execution across replay rows and event-backed facts."""
    return _economic_trade_core(trade) + (_execution_stamp(trade),)


def _fact_enrichment_adoption_shadows(root: Path) -> set[tuple]:
    """Return adoption-time signatures that are metadata shadows, not executions."""
    events_dir = root / "events" / "trades"
    shadows: set[tuple] = set()
    if not events_dir.exists():
        return shadows
    for path in sorted(events_dir.glob("*.json")):
        event = read_json(path, {}) or {}
        if str(event.get("replay_semantics") or "").upper() != "FACT_ENRICHMENT_ONLY":
            continue
        core = _economic_trade_core(event)
        if not core[0]:
            continue
        execution_stamp = trade_signature(event)[-1]
        for key in ("historical_fact_adopted_at", "confirmed_at_beijing"):
            stamp = str(event.get(key) or "").replace("T", " ")[:19]
            if stamp and stamp != execution_stamp:
                shadows.add(core + (stamp,))
    return shadows


def experience_etf_trade_index_facts(root: Path) -> list[dict]:
    """Parse the existing formal Experience §2.1 trade index for bounded recovery.

    This is not a second trade ledger. The index is already the repository's
    human-readable historical transaction index. It is used only by callers
    that detect a persisted reconstruction count deficit and need to recover
    predecessor rows from current formal facts. Event files remain authoritative
    for newer machine trade facts and are deduplicated by trade_signature.
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
        fee_value = _table_number(fee)
        fact = {
            "datetime": stamp,
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
        if not _is_etf_trade(fact, universe_codes):
            continue
        signature = trade_signature(fact)
        if signature in seen:
            continue
        seen.add(signature)
        facts.append(fact)
    return facts


def unintegrated_executed_trade_events(root: Path, reconstructed_trades: list[dict]) -> list[dict]:
    known_signatures = {trade_signature(t) for t in reconstructed_trades if str(t.get("replay_semantics") or "").upper() != "FACT_ENRICHMENT_ONLY"}
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
    """Return one deduplicated ETF-only fact set for position, fee and count projections."""
    universe_codes = _etf_universe_codes(root)
    adoption_shadows = _fact_enrichment_adoption_shadows(root)
    facts: list[dict] = []
    seen: set[tuple] = set()
    seen_execution_identities: set[tuple] = set()

    # A correction request retires the earlier event without deleting it from
    # the audit trail. Reconstructed replay rows can predate that event-level
    # retirement, so exclude their superseded event identities before position,
    # fee, and trade-count projection. The replacement event remains eligible
    # through the normal canonical event path below.
    superseded_event_ids = set()
    superseded_signatures = set()
    superseded_cores = set()
    events_dir = root / "events" / "trades"
    if events_dir.exists():
        for event_path in events_dir.glob("*.json"):
            event = read_json(event_path, {}) or {}
            if str(event.get("execution_status") or "").upper() == "SUPERSEDED":
                event_id = str(event.get("event_id") or event_path.stem).strip()
                if event_id:
                    superseded_event_ids.add(event_id)
                signature = trade_signature(event)
                if signature[0]:
                    superseded_signatures.add(signature)
                    superseded_cores.add(_economic_trade_core(event))

    for trade in reconstructed_trades:
        event_id = str(trade.get("event_id") or "").strip()
        # Historical reconstruction rows may omit event_id. Retire the same
        # economic execution by signature after a user-confirmed correction.
        if (event_id and event_id in superseded_event_ids) or trade_signature(trade) in superseded_signatures or _economic_trade_core(trade) in superseded_cores:
            continue
        if not _is_reconstructed_etf_trade(trade, universe_codes):
            continue
        signature = trade_signature(trade)
        if signature in adoption_shadows or signature in seen:
            continue
        seen.add(signature)
        seen_execution_identities.add(_execution_identity(trade))
        facts.append(trade)

    for trade in unintegrated_executed_trade_events(root, reconstructed_trades):
        if not _is_etf_trade(trade, universe_codes):
            continue
        if str(trade.get("replay_semantics") or "").upper() == "FACT_ENRICHMENT_ONLY":
            continue
        signature = trade_signature(trade)
        execution_identity = _execution_identity(trade)
        if signature in adoption_shadows:
            continue
        if signature in seen or execution_identity in seen_execution_identities:
            # Keep one economic trade, but let the canonical event enrich the
            # replay row when event_id/source differs but broker execution is
            # identical. Confirmation time is not an execution identity.
            for existing in facts:
                if not (
                    trade_signature(existing) == signature
                    or _execution_identity(existing) == execution_identity
                ):
                    continue
                if str(trade.get("fee_status") or "").upper() == "CONFIRMED":
                    existing["fee_amount"] = round(confirmed_fee_amount(trade), 2)
                    existing["fee_status"] = "CONFIRMED"
                    for field in ("fee_confirmed_at_beijing", "fee_source", "fact_updated_at_beijing", "net_cash_effect", "cash_flow_amount"):
                        if field in trade:
                            existing[field] = trade[field]
                break
            continue
        seen.add(signature)
        seen_execution_identities.add(execution_identity)
        facts.append(trade)
    return facts


def recover_canonical_etf_trade_facts(root: Path, reconstructed_trades: list[dict], expected_count: int) -> list[dict]:
    """Recover a persisted reconstruction deficit from current formal facts.

    Normal operation uses canonical_etf_trade_facts directly. Recovery is
    allowed only when an existing persisted summary declares a larger expected
    count. Experience §2.1 may fill that exact deficit; event facts are then
    overlaid/deduplicated. A legacy declared count may contract only by the
    exact number of proven FACT_ENRICHMENT_ONLY adoption-time shadows or
    superseded reconstructed rows retired by a confirmed correction event.
    """
    current = canonical_etf_trade_facts(root, reconstructed_trades)
    if expected_count <= 0 or len(current) >= expected_count:
        return current
    # A stale persisted count can be caused by two rows carrying one stable
    # event_id at different confirmation clocks. Once canonicalization has
    # collapsed that identity, historical recovery must not re-add the shadow.
    stable_ids = [
        str(item.get("event_id") or "").strip()
        for item in reconstructed_trades
        if str(item.get("event_id") or "").strip()
    ]
    if len(stable_ids) != len(set(stable_ids)):
        return current
    seed = list(reconstructed_trades) + experience_etf_trade_index_facts(root)
    recovered = canonical_etf_trade_facts(root, seed)
    if len(recovered) == expected_count:
        return recovered
    adoption_shadows = _fact_enrichment_adoption_shadows(root)
    shadow_count = len({trade_signature(t) for t in seed if trade_signature(t) in adoption_shadows})
    if shadow_count and len(recovered) + shadow_count == expected_count:
        return recovered
    superseded_cores = set()
    events_dir = root / "events" / "trades"
    if events_dir.exists():
        for event_path in events_dir.glob("*.json"):
            event = read_json(event_path, {}) or {}
            if str(event.get("execution_status") or "").upper() == "SUPERSEDED":
                core = _economic_trade_core(event)
                if core[0]:
                    superseded_cores.add(core)
    superseded_reconstructed_count = len({
        trade_signature(trade)
        for trade in reconstructed_trades
        if _economic_trade_core(trade) in superseded_cores
    })
    if superseded_reconstructed_count and len(recovered) + superseded_reconstructed_count == expected_count:
        return recovered
    raise ValueError(
        f"formal ETF trade reconstruction deficit: expected {expected_count}, "
        f"recovered {len(recovered)}, adoption_shadows={shadow_count}, "
        f"superseded_reconstructed={superseded_reconstructed_count}"
    )


def _canonical_replay_declared_trade_count(root: Path) -> int:
    """Return the persisted canonical replay's declared trade count, if any.

    This is a bounded migration aid for current formal fee projection. A replay
    state may legitimately carry the accepted complete count before its embedded
    trade rows have converged. In that exact case the existing formal Experience
    index is the registered recovery source, and an exact count mismatch remains
    fail-closed through recover_canonical_etf_trade_facts.
    """
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
    return {
        "effective_confirmed_fee_sum": confirmed,
        "pending_fee_count": len(pending),
        "canonical_trade_count": len(facts),
        "pending_trades": pending,
        "canonical_trades": facts,
        "recovered_from_formal_index": recovered_from_formal_index,
    }


def effective_confirmed_fee_fact(root: Path, reconstructed_trades: list[dict]) -> dict:
    universe_codes = _etf_universe_codes(root)
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
