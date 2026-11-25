"""Canonical, deterministic ETF-only FIFO replay producer.

The producer is deliberately side-effect free: it reads canonical trade facts
and date-aligned daily research facts and emits a candidate JSON document. A
separate, governed state writer must decide whether to persist the candidate.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    from query_time_market_refresh import _explicit_etf_session_reference, _policy
    from confirmed_trade_facts import (
        canonical_etf_trade_facts,
        recover_canonical_etf_trade_facts,
        trade_signature,
        confirmed_fee_amount,
        experience_etf_trade_index_facts,
    )
except ModuleNotFoundError:
    from scripts.query_time_market_refresh import _explicit_etf_session_reference, _policy
    from scripts.confirmed_trade_facts import (
        canonical_etf_trade_facts,
        recover_canonical_etf_trade_facts,
        trade_signature,
        confirmed_fee_amount,
        experience_etf_trade_index_facts,
    )

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STARTING_CAPITAL = 200000.0


def _f(value, name):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value != value or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def _stamp(t):
    return str(t.get("datetime") or t.get("confirmed_at_beijing") or t.get("executed_at_beijing") or t.get("executed_at") or "").replace("T", " ")[:19]


def _load_prices(root: Path, price_dir: Path | None = None):
    directory = price_dir or root / "events" / "research" / "daily_features"
    prices = defaultdict(dict)
    if not directory.exists():
        raise ValueError(f"price directory missing: {directory}")
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        # A daily-feature document is an eligible replay valuation date only when
        # its canonical document-level quality is PASS. Row-level PASS cannot
        # upgrade a partial/DEGRADED intraday document into an EOD-equivalent day.
        if str(payload.get("quality_status") or "PASS").upper() != "PASS":
            continue
        market_date = str(payload.get("market_date") or path.stem)
        for row in payload.get("features") or []:
            code = str(row.get("code") or "").strip()
            if not code or str(row.get("quality_status") or "PASS").upper() != "PASS":
                continue
            close = row.get("close")
            if close is None:
                close = row.get("close_price")
            prices[market_date][code] = _f(close, f"{market_date}/{code}/close")
    # Historical daily-feature files can predate an ETF's admission to the
    # monitored universe.  Preserve the canonical close semantics by filling
    # only missing object/date pairs from the quality-passed canonical market
    # snapshot for the same date; never overwrite a research feature row.
    snapshot_dir = root / "data" / "market" / "snapshots"
    for path in sorted(snapshot_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("quality_status") or "PASS").upper() != "PASS":
            continue
        market_date = str(payload.get("market_date") or "")
        if not market_date:
            continue
        for row in payload.get("rows") or []:
            if str(row.get("quality_status") or "PASS").upper() != "PASS":
                continue
            code = str(row.get("symbol") or "").strip()
            if code and code not in prices[market_date]:
                close = row.get("close")
                if close is not None:
                    prices[market_date][code] = _f(close, f"{market_date}/{code}/close")
    # A newly held object may first appear in the monitored universe after its
    # trade date.  When the next eligible daily feature explicitly carries its
    # previous close, that field is a date-aligned EOD-equivalent fact for the
    # missing prior date.  Use it only for a missing object/date pair and never
    # replace an observed close or infer a value from a trade price.
    feature_docs = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("quality_status") or "PASS").upper() != "PASS":
            continue
        feature_docs.append((str(payload.get("market_date") or path.stem), payload))
    for index, (market_date, payload) in enumerate(feature_docs):
        if index == 0:
            continue
        prior_date = feature_docs[index - 1][0]
        for row in payload.get("features") or []:
            code = str(row.get("code") or "").strip()
            previous_close = row.get("prev_close")
            if code and code not in prices[prior_date] and previous_close is not None:
                prices[prior_date][code] = _f(previous_close, f"{prior_date}/{code}/prev_close")
    return prices


def _trade_day(t):
    stamp = _stamp(t)
    if len(stamp) < 10:
        raise ValueError("trade fact has no effective date")
    return date.fromisoformat(stamp[:10])


def replay(root: Path = ROOT, *, price_dir: Path | None = None, cutoff: str | None = None) -> dict:
    state = json.loads((root / "data" / "state" / "etf_strategy_equity.json").read_text(encoding="utf-8"))
    prices = _load_prices(root, price_dir)
    reconstructed = state.get("trades") or []
    summary = state.get("summary") or {}
    expected_count = int(summary.get("trade_fact_count") or summary.get("trade_count") or 0)
    facts = canonical_etf_trade_facts(root, reconstructed)
    if expected_count and len(facts) < expected_count:
        facts = recover_canonical_etf_trade_facts(root, reconstructed, expected_count)

    # A persisted count can remain numerically complete while a historical
    # predecessor is absent from the canonical projection (for example, a
    # FACT_ENRICHMENT_ONLY adoption row alongside its later SELL). Do not let
    # FIFO fail only after state writing has started. Recover only the formal
    # historical BUY facts required to make an already-recorded SELL replayable;
    # never infer a trade from price, position, or broker balance.
    facts.sort(key=lambda t: (_stamp(t), trade_signature(t)))
    historical = experience_etf_trade_index_facts(root)
    # Include reconstructed seed rows as recovery candidates too: canonical
    # projection may have filtered a legacy BUY while retaining its paired SELL.
    predecessor_candidates = historical + list(reconstructed)
    known = {trade_signature(t) for t in facts}
    lots = defaultdict(float)
    for trade in facts:
        code = str(trade.get("code") or "")
        side = str(trade.get("side") or trade.get("action") or "").upper()
        qty = float(trade.get("quantity") or 0)
        if side == "BUY":
            lots[code] += qty
        elif side == "SELL":
            lots[code] -= qty
    missing_codes = {code for code, qty in lots.items() if qty < -1e-9}
    if missing_codes:
        for candidate in predecessor_candidates:
            code = str(candidate.get("code") or "")
            if code not in missing_codes or str(candidate.get("side") or "").upper() != "BUY":
                continue
            signature = trade_signature(candidate)
            if signature in known:
                continue
            facts.append(candidate)
            known.add(signature)
            lots[code] += float(candidate.get("quantity") or 0)
            if lots[code] >= -1e-9:
                missing_codes.discard(code)
        if missing_codes:
            raise ValueError("unmatched historical SELL predecessor(s): " + ",".join(sorted(missing_codes)))
        facts.sort(key=lambda t: (_stamp(t), trade_signature(t)))
    unique = {trade_signature(t) for t in facts}
    if len(unique) != len(facts):
        raise ValueError("duplicate canonical trade facts")
    dates = sorted(prices)
    if cutoff:
        dates = [d for d in dates if d <= cutoff]
    if not dates:
        raise ValueError("no valid daily price dates")
    cutoff = dates[-1]
    lots = defaultdict(list)
    cash = STARTING_CAPITAL
    realized = 0.0
    fee_total = 0.0
    rows = []
    trade_index = 0
    high = STARTING_CAPITAL
    for day in dates:
        while trade_index < len(facts) and _trade_day(facts[trade_index]) <= date.fromisoformat(day):
            t = facts[trade_index]
            code = str(t.get("code") or "")
            side = str(t.get("side") or t.get("action") or "").upper()
            qty = _f(t.get("quantity"), "quantity")
            price = _f(t.get("price"), "price")
            if qty == 0 or side not in {"BUY", "SELL"}:
                raise ValueError("trade fact has invalid side or zero quantity")
            gross = round(qty * price, 2)
            fee_total += confirmed_fee_amount(t)
            if side == "BUY":
                cash -= gross
                lots[code].append([qty, price])
            else:
                remaining = qty
                while remaining > 1e-9:
                    if not lots[code]:
                        raise ValueError(f"missing FIFO lot for SELL {code}")
                    lot_qty, lot_price = lots[code][0]
                    consumed = min(remaining, lot_qty)
                    realized += consumed * (price - lot_price)
                    lot_qty -= consumed
                    remaining -= consumed
                    if lot_qty <= 1e-9:
                        lots[code].pop(0)
                    else:
                        lots[code][0][0] = lot_qty
                cash += gross
            trade_index += 1
        market_value = 0.0
        positions = {}
        missing_held_prices = []
        for code, code_lots in sorted(lots.items()):
            qty = sum(x[0] for x in code_lots)
            if qty <= 1e-9:
                continue
            if code not in prices[day]:
                missing_held_prices.append(code)
                continue
            mv = qty * prices[day][code]
            market_value += mv
            positions[code] = {"quantity": round(qty, 6), "close": prices[day][code], "market_value": round(mv, 2), "cost_basis": round(sum(q * p for q, p in code_lots), 2)}
        if missing_held_prices:
            # An intraday trade may become canonical before the current market
            # date has an EOD-equivalent daily feature for the new holding.
            # Never manufacture a close or publish a partial strategy-equity row.
            # A historical/non-latest gap remains a hard data error.
            if day != dates[-1]:
                raise ValueError(f"missing date-aligned close for held ETF {','.join(missing_held_prices)} on {day}")
            break
        equity = round(cash + market_value, 2)
        high = max(high, equity)
        rows.append({"date": day, "cash": round(cash, 2), "market_value": round(market_value, 2), "strategy_equity_gross": equity, "realized_pnl_gross": round(realized, 2), "drawdown_amount": round(equity - high, 2), "drawdown_pct": round((equity / high - 1) * 100, 4), "positions": positions, "quality_status": "COMPLETE"})
    if not rows:
        raise ValueError("no complete date-aligned ETF strategy valuation rows")
    current = rows[-1]
    pending_latest_valuation = current["date"] != cutoff
    pending_fee_count = sum(1 for t in facts if str(t.get("fee_status") or "").upper() != "CONFIRMED")
    fee_status = "ALL_RECORDED_TRADE_FEES_CONFIRMED" if pending_fee_count == 0 else f"{pending_fee_count} RECORDED TRADE FEE(S) PENDING_OR_NOT_YET_DISPLAYED"
    return {"schema_version": "1.0-canonical-replay-candidate", "read_only_research": True, "trade_accounting": "FIFO", "replay_start": "2026-07-13", "replay_cutoff": current["date"], "input_source_identity": {"trade_owner": "confirmed_trade_facts.canonical_etf_trade_facts", "price_owner": "events/research/daily_features", "price_semantics": "date-aligned daily close / EOD-equivalent"}, "trades": facts, "summary": {"starting_etf_strategy_capital": STARTING_CAPITAL, "current_gross_strategy_equity": current["strategy_equity_gross"], "current_strategy_return_pct_gross": round((current["strategy_equity_gross"] / STARTING_CAPITAL - 1) * 100, 4), "current_cumulative_pnl_gross": round(current["strategy_equity_gross"] - STARTING_CAPITAL, 2), "max_drawdown_amount": min(x["drawdown_amount"] for x in rows), "max_drawdown_pct": min(x["drawdown_pct"] for x in rows), "gross_realized_pnl": round(realized, 2), "confirmed_fees_separate": round(fee_total, 2), "known_fees": round(fee_total, 2), "pending_fee_count": pending_fee_count, "fee_status": fee_status, "pending_fees_do_not_block_gross": True, "trade_fact_count": len(facts), "price_date_count": len(dates), "coverage_status": "DEGRADED_PENDING_LATEST_VALUATION" if pending_latest_valuation else "COMPLETE", "pending_latest_valuation": pending_latest_valuation}, "series": rows}


def _current_account_valuation(root: Path, base: dict) -> dict:
    """Extend the canonical replay with a current-date account valuation.

    Historical daily replay and current valuation are different contracts:
    an old date-aligned gap must remain visible, but it must not prevent today's
    risk from being marked using today's confirmed account fact and valid market
    snapshot. This remains inside the existing canonical equity owner.
    """
    account = json.loads((root / "data" / "state" / "account_fact.json").read_text(encoding="utf-8"))
    current = json.loads((root / "data" / "state" / "CURRENT.json").read_text(encoding="utf-8"))
    market_date = str(current.get("market_date") or account.get("last_confirmed_market_date") or "")
    snapshot_name = str(current.get("latest_snapshot") or "")
    snapshot_path = root / "data" / "market" / "snapshots" / Path(snapshot_name).name
    if not market_date or not snapshot_path.exists():
        raise ValueError("current account valuation requires a current valid market snapshot")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if str(snapshot.get("market_date") or "") != market_date or str(snapshot.get("quality_status") or "").upper() != "PASS":
        raise ValueError("current account valuation requires a PASS snapshot for the current market date")
    snapshot_rows = {str(row.get("symbol") or "").strip(): row for row in (snapshot.get("rows") or []) if isinstance(row, dict) and str(row.get("quality_status") or "PASS").upper() == "PASS"}
    universe = json.loads((root / "config" / "market" / "etf_monitor_universe.json").read_text(encoding="utf-8"))
    etf_codes = {str(item.get("code") or "").strip() for item in (universe.get("objects") or []) if isinstance(item, dict) and item.get("code")}
    held = {}
    for position in account.get("positions") or []:
        code = str(position.get("code") or "").strip()
        if code not in etf_codes or float(position.get("quantity") or 0) <= 0:
            continue
        row = snapshot_rows.get(code)
        quote_source = "current_valid_market_snapshot"
        if row is None or row.get("close") is None:
            # A newly acquired ETF may not have been in the scheduled snapshot
            # universe. Refresh only that held object through the existing
            # canonical direct-provider chain; never value it from its trade
            # price or from a stale prior snapshot.
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            thscode = f"{code}.SH" if code.startswith(("5", "6")) else f"{code}.SZ"
            quote = _explicit_etf_session_reference(code, root, now)
            if str(quote.get("quality_status") or "").upper() != "PASS" or quote.get("latest_price") is None:
                raise ValueError(f"current held ETF quote is not a valid completed-session reference for {code}")
            row = {"close": quote["latest_price"], "quality_status": "PASS"}
            quote_source = "current_held_object_direct_provider_refresh"
        quantity = float(position.get("quantity") or 0)
        close = _f(row.get("close"), f"{market_date}/{code}/close")
        held[code] = {"quantity": round(quantity, 6), "close": close, "market_value": round(quantity * close, 2), "cost_basis": round(quantity * float(position.get("cost") or 0), 2), "quote_source": quote_source}
    if not held:
        raise ValueError("current account valuation has no held ETF positions")
    base_row = (base.get("series") or [])[-1]
    cash = float(base_row.get("cash") or 0)
    base_cutoff = str(base.get("replay_cutoff") or base_row.get("date") or "")
    for trade in base.get("trades") or []:
        stamp = _stamp(trade)
        if stamp[:10] <= base_cutoff or stamp[:10] > market_date or str(trade.get("code") or "").strip() not in etf_codes:
            continue
        gross = float(trade.get("quantity") or 0) * float(trade.get("price") or 0)
        fee = confirmed_fee_amount(trade)
        if str(trade.get("side") or trade.get("action") or "").upper() == "BUY":
            cash -= gross + fee
        elif str(trade.get("side") or trade.get("action") or "").upper() == "SELL":
            cash += gross - fee
    market_value = round(sum(item["market_value"] for item in held.values()), 2)
    equity = round(cash + market_value, 2)
    previous_high = max((float(row.get("strategy_equity_gross") or 0) for row in (base.get("series") or [])), default=STARTING_CAPITAL)
    high = max(previous_high, equity)
    row = {"date": market_date, "cash": round(cash, 2), "market_value": market_value, "strategy_equity_gross": equity, "realized_pnl_gross": base_row.get("realized_pnl_gross", 0), "drawdown_amount": round(equity - high, 2), "drawdown_pct": round((equity / high - 1) * 100, 4) if high else 0, "positions": held, "quality_status": "CURRENT_ACCOUNT_SNAPSHOT", "valuation_source": "account_fact + current_valid_market_snapshot"}
    result = dict(base)
    result["replay_cutoff"] = market_date
    result["as_of_transaction_date"] = market_date
    result["series"] = list(base.get("series") or []) + [row]
    summary = dict(base.get("summary") or {})
    summary.update({"current_gross_strategy_equity": equity, "current_strategy_return_pct_gross": round((equity / STARTING_CAPITAL - 1) * 100, 4), "current_cumulative_pnl_gross": round(equity - STARTING_CAPITAL, 2), "max_drawdown_amount": min(float(x.get("drawdown_amount") or 0) for x in result["series"]), "max_drawdown_pct": min(float(x.get("drawdown_pct") or 0) for x in result["series"]), "coverage_status": "CURRENT_ACCOUNT_SNAPSHOT_WITH_HISTORICAL_REPLAY_WARNING", "pending_latest_valuation": False, "current_valuation_market_date": market_date, "current_valuation_source": "account_fact + current_valid_market_snapshot", "historical_replay_status": "WARNING_HISTORICAL_DATE_ALIGNED_GAP", "historical_replay_cutoff": base_cutoff})
    result["summary"] = summary
    result["current_valuation_status"] = "PASS"
    result["current_valuation_market_date"] = market_date
    result["current_valuation_snapshot"] = snapshot_name
    return result


def refresh_current_account_valuation_only(root: Path = ROOT) -> dict:
    """Refresh only the current market-date valuation from existing canonical state."""
    state_path = root / "data" / "state" / "etf_strategy_equity.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current_path = root / "data" / "state" / "CURRENT.json"
    current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else {}
    current_date = str(current.get("market_date") or state.get("current_valuation_market_date") or state.get("replay_cutoff") or "")
    if not current_date:
        raise ValueError("current-only valuation requires a market date")
    series = list(state.get("series") or [])
    prior = [row for row in series if str(row.get("date") or "") < current_date]
    same_day = [row for row in series if str(row.get("date") or "") == current_date]
    base = dict(state)
    if prior:
        base["series"] = prior
        base["replay_cutoff"] = str(prior[-1].get("date") or "")
    elif same_day:
        base["series"] = same_day[:1]
        base["replay_cutoff"] = current_date
    else:
        raise ValueError("current-only valuation has no canonical base row")
    refreshed = _current_account_valuation(root, base)
    refreshed["series"] = prior + [refreshed["series"][-1]] if prior else [refreshed["series"][-1]]
    refreshed["summary"]["historical_replay_status"] = "REUSED_ACCOUNT_MAINTENANCE_STATE"
    refreshed["summary"]["current_valuation_source"] = "account_fact + current_valid_market_snapshot"
    state_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return refreshed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--price-dir", type=Path)
    parser.add_argument("--cutoff")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--current-only", action="store_true")
    args = parser.parse_args()
    historical_error = ""
    if args.current_only:
        result = refresh_current_account_valuation_only(ROOT)
    else:
        try:
            result = replay(ROOT, price_dir=args.price_dir, cutoff=args.cutoff)
    except ValueError as exc:
        historical_error = str(exc)
        if "missing date-aligned close" not in historical_error:
            raise
        # Historical date-aligned coverage gaps must not prevent today's
        # confirmed account valuation. Retry the same canonical replay only
        # on progressively earlier eligible dates, so the base remains a
        # genuinely successful historical prefix rather than retrying the
        # original failing cutoff. Never infer a missing close from a trade.
        state = json.loads((ROOT / "data" / "state" / "etf_strategy_equity.json").read_text(encoding="utf-8"))
        missing_date = historical_error.rsplit(" on ", 1)[-1].strip()
        candidate_dates = sorted(_load_prices(ROOT, args.price_dir).keys())
        candidate_dates = [day for day in candidate_dates if day < missing_date]
        if not candidate_dates:
            raise ValueError("no successful historical replay prefix before missing date") from exc
        base = None
        prefix_error = historical_error
        for base_cutoff in reversed(candidate_dates):
            try:
                base = replay(ROOT, price_dir=args.price_dir, cutoff=base_cutoff)
                break
            except ValueError as prefix_exc:
                prefix_error = str(prefix_exc)
                if "missing date-aligned close" not in prefix_error:
                    raise
        if base is None:
            raise ValueError("no successful historical replay prefix before missing date") from exc
        result = _current_account_valuation(ROOT, base)
        result["summary"]["historical_replay_error"] = historical_error
        result["summary"]["historical_replay_prefix_error"] = prefix_error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "cutoff": result["replay_cutoff"], "equity": result["summary"]["current_gross_strategy_equity"], "return_pct": result["summary"]["current_strategy_return_pct_gross"], "current_valuation_status": result.get("current_valuation_status", "HISTORICAL_REPLAY")}))


if __name__ == "__main__":
    main()
