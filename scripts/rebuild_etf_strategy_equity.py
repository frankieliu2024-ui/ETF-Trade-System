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
from datetime import date
from pathlib import Path

try:
    from confirmed_trade_facts import (
        canonical_etf_trade_facts,
        recover_canonical_etf_trade_facts,
        trade_signature,
        confirmed_fee_amount,
    )
except ModuleNotFoundError:
    from scripts.confirmed_trade_facts import (
        canonical_etf_trade_facts,
        recover_canonical_etf_trade_facts,
        trade_signature,
        confirmed_fee_amount,
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
        market_date = str(payload.get("market_date") or path.stem)
        for row in payload.get("features") or []:
            code = str(row.get("code") or "").strip()
            if not code or str(row.get("quality_status") or "PASS").upper() != "PASS":
                continue
            close = row.get("close")
            if close is None:
                close = row.get("close_price")
            prices[market_date][code] = _f(close, f"{market_date}/{code}/close")
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
        for code, code_lots in sorted(lots.items()):
            qty = sum(x[0] for x in code_lots)
            if qty <= 1e-9:
                continue
            if code not in prices[day]:
                raise ValueError(f"missing date-aligned close for held ETF {code} on {day}")
            mv = qty * prices[day][code]
            market_value += mv
            positions[code] = {"quantity": round(qty, 6), "close": prices[day][code], "market_value": round(mv, 2), "cost_basis": round(sum(q * p for q, p in code_lots), 2)}
        equity = round(cash + market_value, 2)
        high = max(high, equity)
        rows.append({"date": day, "cash": round(cash, 2), "market_value": round(market_value, 2), "strategy_equity_gross": equity, "realized_pnl_gross": round(realized, 2), "drawdown_amount": round(equity - high, 2), "drawdown_pct": round((equity / high - 1) * 100, 4), "positions": positions, "quality_status": "COMPLETE"})
    current = rows[-1]
    return {"schema_version": "1.0-canonical-replay-candidate", "read_only_research": True, "trade_accounting": "FIFO", "replay_start": "2026-07-13", "replay_cutoff": cutoff, "input_source_identity": {"trade_owner": "confirmed_trade_facts.canonical_etf_trade_facts", "price_owner": "events/research/daily_features", "price_semantics": "date-aligned daily close / EOD-equivalent"}, "trades": facts, "summary": {"starting_etf_strategy_capital": STARTING_CAPITAL, "current_gross_strategy_equity": current["strategy_equity_gross"], "current_strategy_return_pct_gross": round((current["strategy_equity_gross"] / STARTING_CAPITAL - 1) * 100, 4), "current_cumulative_pnl_gross": round(current["strategy_equity_gross"] - STARTING_CAPITAL, 2), "max_drawdown_amount": min(x["drawdown_amount"] for x in rows), "max_drawdown_pct": min(x["drawdown_pct"] for x in rows), "gross_realized_pnl": round(realized, 2), "confirmed_fees_separate": round(fee_total, 2), "pending_fees_do_not_block_gross": True, "trade_fact_count": len(facts), "price_date_count": len(dates), "coverage_status": "COMPLETE"}, "series": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--price-dir", type=Path)
    parser.add_argument("--cutoff")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(ROOT, price_dir=args.price_dir, cutoff=args.cutoff)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "cutoff": result["replay_cutoff"], "equity": result["summary"]["current_gross_strategy_equity"], "return_pct": result["summary"]["current_strategy_return_pct_gross"]}))


if __name__ == "__main__":
    main()
