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




def _account_positions(account: dict) -> dict:
    positions = account.get("positions") if isinstance(account, dict) else []
    result = {}
    for row in positions or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or row.get("security_code") or row.get("symbol") or "").strip()
        asset_class = str(row.get("asset_class") or row.get("security_type") or row.get("position_type") or "").upper()
        if not code or asset_class in {"OTHER", "CONVERTIBLE_BOND", "BOND", "CASH", "MONEY", "CASH_EQUIVALENT"}:
            continue
        qty = f(row.get("quantity") or row.get("qty") or row.get("shares"))
        value = f(row.get("market_value") or row.get("value") or row.get("marketValue"))
        price = f(row.get("current_price") or row.get("price"))
        if value is None and qty is not None and price is not None:
            value = round(qty * price, 2)
        if price is None and qty not in (None, 0) and value is not None:
            price = value / qty
        result[code] = {
            "code": code,
            "name": row.get("name") or row.get("security_name") or "",
            "quantity": qty,
            "market_value": value,
            "price": price,
            "asset_class": str(row.get("asset_class") or row.get("security_type") or row.get("position_type") or "").upper(),
        }
    return result


def _non_security_value(account: dict) -> float:
    positions = account.get("positions") if isinstance(account, dict) else []
    security_classes = {"ETF", "EQUITY", "STOCK", "FUND", "SECURITY"}
    total = 0.0
    for row in positions or []:
        if not isinstance(row, dict):
            continue
        asset_class = str(row.get("asset_class") or row.get("security_type") or row.get("position_type") or "").upper()
        if asset_class in security_classes:
            continue
        value = f(row.get("market_value") or row.get("value") or row.get("marketValue"))
        if value is not None:
            total += value
    return round(total, 2)


def _load_prior_account_snapshot(prior_market_date: str) -> dict:
    candidates = []
    for name in sorted(glob.glob(str(ROOT / "requests/live_snapshot/*.json"))):
        payload = load_json(Path(name), {})
        fact = payload.get("account_fact") if isinstance(payload.get("account_fact"), dict) else payload
        if not isinstance(fact, dict) or not isinstance(fact.get("positions"), list):
            continue
        fact_date = str(
            fact.get("last_confirmed_market_date")
            or fact.get("market_date")
            or payload.get("last_confirmed_market_date")
            or ""
        )[:10]
        if fact_date and fact_date <= prior_market_date:
            candidates.append((fact_date, str(name), fact))
    if not candidates:
        return {}
    candidates.sort()
    return candidates[-1][2]


def _strict_decomposition(
    prior_account: dict,
    current_account: dict,
    trade_rows: list,
    statement_complete: bool,
    observed_change: float | None,
    confirmed_fees: float,
) -> dict:
    result = {
        "status": "NOT_COMPLETE",
        "method": "FAIL_CLOSED",
        "price_change_effect": None,
        "realized_pnl": None,
        "unrealized_pnl": None,
        "fee_effect": round(-confirmed_fees, 2),
        "other_asset_effect": None,
        "residual": None,
        "reason": "",
        "security_components": [],
    }
    if not statement_complete:
        result["reason"] = "COMPLETE_EXECUTED_TRANSACTION_SCOPE_REQUIRED"
        return result
    if observed_change is None or not prior_account or not current_account:
        result["reason"] = "DATE_ALIGNED_ACCOUNT_ENDPOINTS_REQUIRED"
        return result

    start_positions = _account_positions(prior_account)
    end_positions = _account_positions(current_account)
    if not start_positions or not end_positions:
        result["reason"] = "ACCOUNT_POSITIONS_REQUIRED"
        return result

    buys = {}
    sells = {}
    for trade in trade_rows:
        code = str(trade.get("code") or "").strip()
        quantity = f(trade.get("quantity"))
        price = f(trade.get("price"))
        side = str(trade.get("side") or "").upper()
        if not code or quantity is None or quantity <= 0 or price is None:
            result["reason"] = "TRADE_QUANTITY_AND_PRICE_REQUIRED"
            return result
        bucket = buys if side in {"BUY", "B"} else sells if side in {"SELL", "S"} else None
        if bucket is None:
            result["reason"] = "SUPPORTED_TRADE_SIDE_REQUIRED"
            return result
        bucket.setdefault(code, []).append({"quantity": quantity, "price": price, "name": trade.get("name") or ""})

    all_codes = set(start_positions) | set(end_positions) | set(buys) | set(sells)
    for code in sorted(all_codes):
        start_qty = f(start_positions.get(code, {}).get("quantity")) or 0.0
        end_qty = f(end_positions.get(code, {}).get("quantity")) or 0.0
        bought_qty = sum(item["quantity"] for item in buys.get(code, []))
        sold_qty = sum(item["quantity"] for item in sells.get(code, []))
        if abs(end_qty - (start_qty + bought_qty - sold_qty)) > 0.01:
            result["reason"] = "POSITION_QUANTITY_BRIDGE_FAILED"
            return result
    if sells:
        result["reason"] = "SELL_COST_BASIS_REQUIRED"
        return result

    price_effect = 0.0
    components = []
    for code in sorted(all_codes):
        start = start_positions.get(code)
        end = end_positions.get(code)
        if start and f(start.get("quantity")) and end and f(end.get("quantity")):
            start_price = f(start.get("price"))
            end_price = f(end.get("price"))
            if start_price is None or end_price is None:
                result["reason"] = "ENDPOINT_MARKS_REQUIRED"
                return result
            qty = f(start.get("quantity"))
            effect = (end_price - start_price) * qty
            price_effect += effect
            components.append({
                "code": code,
                "name": end.get("name") or start.get("name") or "",
                "quantity": qty,
                "start_price": round(start_price, 6),
                "end_price": round(end_price, 6),
                "effect": round(effect, 2),
                "classification": "UNREALIZED_PNL",
            })
        for item in buys.get(code, []):
            if not end or not f(end.get("quantity")) or f(end.get("price")) is None:
                result["reason"] = "BOUGHT_LOT_ENDPOINT_MARK_REQUIRED"
                return result
            effect = (f(end.get("price")) - item["price"]) * item["quantity"]
            price_effect += effect
            components.append({
                "code": code,
                "name": end.get("name") or item.get("name") or "",
                "quantity": item["quantity"],
                "purchase_price": round(item["price"], 6),
                "end_price": round(f(end.get("price")), 6),
                "effect": round(effect, 2),
                "classification": "UNREALIZED_PNL",
            })

    other_effect = round(_non_security_value(current_account) - _non_security_value(prior_account), 2)
    fee_effect = round(-confirmed_fees, 2)
    residual = round(observed_change - price_effect - fee_effect - other_effect, 2)
    result.update({
        "method": "DATE_ALIGNED_ENDPOINT_MARK_TO_MARKET_PLUS_EXECUTED_BUY_LOTS_PLUS_FEES",
        "price_change_effect": round(price_effect, 2),
        "realized_pnl": 0.0,
        "unrealized_pnl": round(price_effect, 2),
        "other_asset_effect": other_effect,
        "fee_effect": fee_effect,
        "residual": residual,
        "security_components": components,
    })
    if abs(residual) <= 0.01:
        result["status"] = "COMPLETE"
        result["reason"] = "STRICT_BRIDGE_CLOSED"
    else:
        result["reason"] = "STRICT_BRIDGE_RESIDUAL_NONZERO"
    return result


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
                "name": trade.get("name") or trade.get("security_name"),
                "side": side,
                "quantity": f(trade.get("quantity") or trade.get("qty") or trade.get("executed_quantity")),
                "price": f(trade.get("price") or trade.get("executed_price") or trade.get("execution_price")),
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
    statement_scope = account.get("broker_statement_reconciliation") if isinstance(account.get("broker_statement_reconciliation"), dict) else {}
    statement_complete = str(statement_scope.get("statement_scope_status") or "").upper() == "COMPLETE_EXECUTED_TRANSACTION_SCOPE"
    prior_account = _load_prior_account_snapshot((end - timedelta(days=7)).isoformat())
    strict_decomposition = _strict_decomposition(
        prior_account=prior_account,
        current_account=account,
        trade_rows=trade_rows,
        statement_complete=statement_complete,
        observed_change=observed_change,
        confirmed_fees=confirmed_fees,
    )
    execution_reconciliation = {
        "statement_scope_status": statement_scope.get("statement_scope_status") or "NOT_AVAILABLE",
        "executed_transaction_scope_status": statement_scope.get("executed_transaction_scope_status") or "NOT_AVAILABLE",
        "capital_release_status": statement_scope.get("capital_release_status") or "NOT_CONFIRMED",
        "round_trip_status": statement_scope.get("round_trip_status") or "NOT_PROVEN",
        "complete_statement_boundary": statement_scope.get("boundary") or "",
    }
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
        "execution_reconciliation": execution_reconciliation,
        "strict_decomposition": strict_decomposition,
        "unresolved_components": {
            "price_change_effect": (
                "RECONCILED" if strict_decomposition["status"] == "COMPLETE"
                else "NOT_SEPARATELY_RECONCILED"
            ),
            "realized_pnl": (
                "RECONCILED" if strict_decomposition["status"] == "COMPLETE"
                else "NOT_SEPARATELY_RECONCILED"
            ),
            "unrealized_pnl": (
                "RECONCILED" if strict_decomposition["status"] == "COMPLETE"
                else "NOT_SEPARATELY_RECONCILED"
            ),
            "complete_order_and_cancellation_flow": (
                "OUT_OF_SCOPE_FOR_EXECUTED_TRANSACTION_RECONCILIATION"
                if statement_complete else "NOT_AVAILABLE"
            ),
            "same_day_round_trip_or_net_zero_trades": (
                "NO_EXECUTED_ROUND_TRIP_OR_NET_ZERO_TRADE_IDENTIFIED"
                if statement_complete else "NOT_PROVEN"
            ),
        },
        "strict_decomposition_status": strict_decomposition["status"],
        "boundary": "具备日期对齐的周初/周末持仓、完整成交对账和可核对费用时，按端点持仓市值变动、成交后新增批次市值变动、已实现盈亏和费用严格闭合；缺失卖出成本基础、端点持仓或完整成交范围时自动保持未完成，不猜测拆分。",
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
