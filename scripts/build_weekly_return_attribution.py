"""Build an evidence-bounded weekly return attribution bridge.

This is a reconciliation projection, not a new accounting owner. It records the
known account/trade/cash components and leaves realized-vs-unrealized and exact
price attribution open until complete broker and date-aligned holdings data exist.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import date, timedelta
from pathlib import Path

try:
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUTPUT = ROOT / "data/state/weekly_return_attribution.json"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build() -> dict:
    current = load_json(ROOT / "data/state/CURRENT.json", {})
    market_date = str(current.get("market_date") or "").strip()
    node = str(current.get("latest_valid_node") or "").strip().lower()
    if not market_date or node != "close":
        return {"status": "SKIPPED", "reason": "verified_close_not_current", "market_date": market_date, "latest_valid_node": node}

    end = date.fromisoformat(market_date)
    start = end - timedelta(days=4)
    account = load_json(ROOT / "data/state/account_fact.json", {})
    rows = [
        row for row in (account.get("account_change_events_after_confirmed_at") or [])
        if isinstance(row, dict) and str(row.get("event_time") or "")[:10] >= start.isoformat()
        and str(row.get("event_time") or "")[:10] <= end.isoformat()
    ]
    total_rows = sorted(
        [row for row in rows if str(row.get("object") or "") == "total_asset"],
        key=lambda row: str(row.get("event_time") or ""),
    )
    daily_deltas = [
        {
            "event_time": row.get("event_time"),
            "amount_delta": f(row.get("amount_delta")),
            "reconciliation_status": row.get("reconciliation_status"),
            "source": row.get("source"),
        }
        for row in total_rows
    ]
    starting_asset = f(total_rows[0].get("amount_before")) if total_rows else None
    ending_asset = f(total_rows[-1].get("amount_after")) if total_rows else None
    observed_change = round(ending_asset - starting_asset, 2) if starting_asset is not None and ending_asset is not None else None

    trade_rows = []
    for name in sorted(glob.glob(str(ROOT / "events/trades/*.json"))):
        trade = load_json(Path(name), {})
        execution_date = str(trade.get("execution_date") or trade.get("confirmed_at_beijing") or "")[:10]
        if start.isoformat() <= execution_date <= end.isoformat() and str(trade.get("execution_status") or "EXECUTED").upper() == "EXECUTED":
            gross = f(trade.get("amount") or trade.get("gross_amount"))
            fee = f(trade.get("fee_amount")) if str(trade.get("fee_status") or "").upper() == "CONFIRMED" else None
            side = str(trade.get("side") or trade.get("action") or "").upper()
            cash_flow = f(trade.get("cash_flow_amount") or trade.get("net_cash_effect"))
            trade_rows.append({
                "trade_event_id": trade.get("event_id"),
                "code": trade.get("code"),
                "side": side,
                "execution_date": execution_date,
                "gross_amount": gross,
                "fee_amount": fee,
                "cash_flow_amount": cash_flow,
                "linked_decision_id": trade.get("linked_decision_id"),
                "execution_attribution": trade.get("execution_attribution"),
            })
    trade_rows.sort(key=lambda row: (str(row.get("execution_date") or ""), str(row.get("trade_event_id") or "")))
    confirmed_trade_cash_flow = round(sum(row["cash_flow_amount"] for row in trade_rows if row["cash_flow_amount"] is not None), 2)
    confirmed_fees = round(sum(row["fee_amount"] for row in trade_rows if row["fee_amount"] is not None), 2)
    cash_rows = [row for row in rows if str(row.get("object") or "").lower() == "cash"]
    unreconciled_cash_deltas = [
        {
            "event_time": row.get("event_time"),
            "observed_delta": f(row.get("amount_delta")),
            "unreconciled_delta": f(row.get("unreconciled_delta")),
            "status": row.get("reconciliation_status"),
        }
        for row in cash_rows
        if f(row.get("unreconciled_delta")) not in (None, 0.0)
    ]
    artifact = {
        "schema_version": "1.0",
        "layer": "WEEKLY_RETURN_ATTRIBUTION",
        "status": "PARTIAL" if observed_change is not None else "UNAVAILABLE",
        "market_date": market_date,
        "week_start": start.isoformat(),
        "week_end": market_date,
        "latest_valid_node": node,
        "starting_total_asset": starting_asset,
        "ending_total_asset": ending_asset,
        "observed_total_asset_change": observed_change,
        "daily_total_asset_deltas": daily_deltas,
        "confirmed_trade_count": len(trade_rows),
        "confirmed_trades": trade_rows,
        "confirmed_trade_cash_flow": confirmed_trade_cash_flow,
        "confirmed_trade_fees": confirmed_fees,
        "unreconciled_cash_deltas": unreconciled_cash_deltas,
        "known_components": {
            "account_total_asset_change": observed_change,
            "confirmed_trade_cash_flow": confirmed_trade_cash_flow,
            "confirmed_trade_fees": confirmed_fees,
            "daily_account_delta_sum": round(sum(row["amount_delta"] or 0 for row in daily_deltas), 2),
        },
        "unresolved_components": {
            "price_change_effect": "NOT_SEPARATELY_RECONCILED",
            "realized_pnl": "NOT_SEPARATELY_RECONCILED",
            "unrealized_pnl": "NOT_SEPARATELY_RECONCILED",
            "complete_order_and_cancellation_flow": "NOT_AVAILABLE",
            "same_day_round_trip_or_net_zero_trades": "NOT_PROVEN",
        },
        "strict_decomposition_status": "NOT_COMPLETE",
        "boundary": "账户总资产变化与已确认成交/费用先形成桥接；在完整成交、委托撤单、逐日持仓和成本基础齐备前，不把剩余变化拆写成已实现或未实现收益。",
        "read_only": True,
    }
    atomic_json_write(OUTPUT, artifact)
    return {
        "status": artifact["status"],
        "market_date": market_date,
        "observed_total_asset_change": observed_change,
        "confirmed_trade_count": len(trade_rows),
        "strict_decomposition_status": artifact["strict_decomposition_status"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
