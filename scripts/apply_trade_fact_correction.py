from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from formal_file_mutation_gateway import upsert_formal_line
from process_state_sync_request import sync_experience_transaction_index

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
EQUITY = STATE / "etf_strategy_equity.json"
DASHBOARD = ROOT / "ETF当前状态_DASHBOARD.md"
ARCHIVE = ROOT / "ETF市场行情档案_2026.md"
EXPERIENCE = ROOT / "ETF交易复盘与经验库_2026.md"
TZ = timezone(timedelta(hours=8))
DASH_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
DASH_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"
ARCHIVE_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
ARCHIVE_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"
EXPERIENCE_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
EXPERIENCE_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def equity_row_from_event(event: dict, fee: float) -> dict:
    """Project an already-recorded event when the auxiliary ledger lags it."""
    stamp = str(event.get("confirmed_at_beijing") or event.get("execution_at") or "")
    stamp = stamp.replace("T", " ")[:19]
    side = str(event.get("side") or event.get("action") or "").upper()
    gross = safe_float(event.get("amount"))
    row = {
        "datetime": stamp,
        "name": event.get("name"),
        "code": event.get("code"),
        "side": side,
        "quantity": event.get("quantity"),
        "price": event.get("price"),
        "gross_amount": gross,
        "source": f"events/trades/{event.get('event_id')}.json",
        "source_confidence": event.get("source_confidence") or event.get("source"),
        "entered_events_trades": True,
        "duplicate_check": f"unique_event_id_{event.get('event_id')}",
        "realized_pnl": None,
        "fee_amount": round(fee, 2),
        "fee_status": "CONFIRMED",
    }
    if gross is not None:
        row["cash_flow_amount"] = round(gross - fee, 2) if side == "SELL" else round(-(gross + fee), 2) if side == "BUY" else None
    return row



def pending_fee_count(trades: list[dict]) -> int:
    return sum(1 for t in trades if str(t.get("fee_status") or "").upper() != "CONFIRMED")


def rebuild_known_net(equity: dict) -> None:
    trades = equity.get("trades") or []
    summary = equity.setdefault("summary", {})
    confirmed_fees = round(sum(float(t.get("fee_amount") or 0) for t in trades if str(t.get("fee_status") or "").upper() == "CONFIRMED"), 2)
    gross_equity = safe_float(summary.get("current_gross_strategy_equity"))
    gross_pnl = safe_float(summary.get("current_cumulative_pnl_gross"))
    start_capital = safe_float(summary.get("starting_etf_strategy_capital")) or 200000.0
    high_watermark = safe_float(summary.get("known_net_high_watermark")) or start_capital
    if gross_equity is None or gross_pnl is None:
        raise RuntimeError("etf_strategy_equity lacks gross basis required for deterministic fee correction")

    net_equity = round(gross_equity - confirmed_fees, 2)
    net_pnl = round(gross_pnl - confirmed_fees, 2)
    net_return_pct = round(net_pnl / start_capital * 100.0, 2)
    current_dd_amount = round(net_equity - high_watermark, 2)
    current_dd_pct = round(current_dd_amount / high_watermark * 100.0, 2) if high_watermark else None
    prior_max_dd = safe_float(summary.get("known_net_max_drawdown_amount"))
    prior_max_dd_pct = safe_float(summary.get("known_net_max_drawdown_pct"))
    prior_max_low = summary.get("known_net_max_drawdown_low")
    if prior_max_dd is None or current_dd_amount < prior_max_dd:
        max_dd_amount = current_dd_amount
        max_dd_pct = current_dd_pct
        max_low = datetime.now(TZ).date().isoformat()
    else:
        max_dd_amount = prior_max_dd
        max_dd_pct = prior_max_dd_pct
        max_low = prior_max_low

    pending = pending_fee_count(trades)
    summary["known_fees"] = confirmed_fees
    summary["unknown_fee_flag"] = pending > 0
    summary["fee_status"] = "ALL_RECORDED_TRADE_FEES_CONFIRMED" if pending == 0 else f"{pending} RECORDED TRADE FEE(S) PENDING_OR_NOT_YET_DISPLAYED"
    summary["known_net_status"] = f"KNOWN_NET_DEDUCTS_{confirmed_fees:.2f}_CONFIRMED_ETF_FEES" + ("; NOT_FINAL_NET" if pending else "; RECORDED_FEES_COMPLETE")
    summary["known_net_current_strategy_equity"] = net_equity
    summary["known_net_current_cumulative_pnl"] = net_pnl
    summary["known_net_current_strategy_return_pct"] = net_return_pct
    summary["known_net_current_drawdown_amount"] = current_dd_amount
    summary["known_net_current_drawdown_pct"] = current_dd_pct
    summary["known_net_max_drawdown_amount"] = max_dd_amount
    summary["known_net_max_drawdown_pct"] = max_dd_pct
    summary["known_net_max_drawdown_low"] = max_low
    summary["known_net_equity_data_quality"] = "KNOWN_NET_COMPLETE_FOR_RECORDED_TRADE_FEES" if pending == 0 else "KNOWN_NET_PARTIAL_PENDING_RECORDED_TRADE_FEES"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill newly confirmed metadata for an already-recorded trade without creating a new trade.")
    parser.add_argument("request_path")
    args = parser.parse_args()
    request_path = (ROOT / args.request_path).resolve()
    if ROOT not in request_path.parents or not request_path.exists():
        raise RuntimeError("invalid correction request path")
    req = read_json(request_path, {}) or {}
    event_id = str(req.get("trade_event_id") or "").strip()
    fee = safe_float(req.get("fee_amount"))
    if not event_id or fee is None or fee < 0:
        raise RuntimeError("trade_event_id and non-negative fee_amount are required")

    event_path = ROOT / "events" / "trades" / f"{event_id}.json"
    event = read_json(event_path, {}) or {}
    if not event:
        raise RuntimeError("existing trade event not found; correction cannot create a trade")
    prior_fee = safe_float(event.get("fee_amount"))
    prior_status = str(event.get("fee_status") or "").upper()
    already_confirmed_same = prior_status == "CONFIRMED" and prior_fee is not None and abs(prior_fee - fee) < 0.005
    if prior_status == "CONFIRMED" and prior_fee is not None and not already_confirmed_same:
        raise RuntimeError("existing confirmed fee differs; explicit correction conflict requires manual review")

    stamp = datetime.now(TZ).isoformat(timespec="seconds")
    if not already_confirmed_same:
        event["fee_amount"] = round(fee, 2)
        event["fee_status"] = "CONFIRMED"
        event["fee_confirmed_at_beijing"] = str(req.get("evidence_time_beijing") or stamp)
        event["fee_source"] = str(req.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
        event["fact_updated_at_beijing"] = stamp
        write_json(event_path, event)

    equity = read_json(EQUITY, {}) or {}
    trades = equity.get("trades") or []
    source_ref = f"events/trades/{event_id}.json"
    matches = [t for t in trades if str(t.get("source") or "") == source_ref or str(t.get("duplicate_check") or "") == f"unique_event_id_{event_id}"]
    if len(matches) > 1:
        raise RuntimeError("strategy equity does not contain exactly one matching trade; refuse partial correction")
    if matches:
        row = matches[0]
        row["fee_amount"] = round(fee, 2)
        row["fee_status"] = "CONFIRMED"
        gross = safe_float(row.get("gross_amount"))
        if gross is not None:
            side = str(row.get("side") or "").upper()
            row["cash_flow_amount"] = round(gross - fee, 2) if side == "SELL" else round(-(gross + fee), 2) if side == "BUY" else row.get("cash_flow_amount")
    else:
        # The event is authoritative.  A lagging auxiliary reconstruction must
        # converge by adding exactly this event, never by creating a new event.
        equity.setdefault("trades", []).append(equity_row_from_event(event, fee))
        equity.setdefault("summary", {})["trade_count"] = len(equity["trades"])
    rebuild_known_net(equity)
    equity["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    write_json(EQUITY, equity)

    # Reconcile the canonical transaction index by the existing trade event.
    # This updates both marker-based rows and legacy rows that predate the
    # TRADE_EVENT marker; it never creates a second trade or CASE.
    sync_experience_transaction_index(event)

    target = f"{event.get('name') or ''}（{event.get('code') or ''}）"
    correction_key = f"FEE_{event_id}"
    date = str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10]
    fee_source = str(event.get("fee_source") or req.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
    line = f"{date} 已有成交事实补充：{target}成交费用确认{fee:.2f}元；原成交数量、价格、方向和交易日不变；来源：{fee_source}。"
    upsert_formal_line(ROOT, ARCHIVE.name, ARCHIVE_START, ARCHIVE_END, correction_key, f"- {line}")
    upsert_formal_line(ROOT, EXPERIENCE.name, EXPERIENCE_START, EXPERIENCE_END, correction_key, f"- 事实补充｜{line} 不新增CASE、不改变历史交易判断。")
    summary = equity.get("summary") or {}
    dashboard_line = f"成交费用补充：{target} {fee:.2f}元已确认；ETF累计已确认费用{float(summary.get('known_fees') or 0):.2f}元；ETF策略Known-net收益率约{float(summary.get('known_net_current_strategy_return_pct') or 0):.2f}%。"
    upsert_formal_line(ROOT, DASHBOARD.name, DASH_START, DASH_END, correction_key, f"- {dashboard_line}")

    receipt = {
        "status": "RECONCILED" if already_confirmed_same else "APPLIED",
        "trade_event_id": event_id,
        "security": target,
        "fee_amount": round(fee, 2),
        "trade_event_updated": not already_confirmed_same,
        "equity_updated": True,
        "dashboard_updated": True,
        "archive_updated": True,
        "experience_updated": True,
        "master_updated": False,
        "pending_fee_count": pending_fee_count(trades),
        "known_fees": summary.get("known_fees"),
        "known_net_current_strategy_return_pct": summary.get("known_net_current_strategy_return_pct"),
        "applied_at_beijing": stamp,
        "safety_boundary": "Only enriches an existing trade with user/broker-confirmed fee metadata; never creates a trade or changes quantity, price, side, execution date, MASTER, risk permission or trading action.",
    }
    receipt_path = STATE / "trade_fact_correction_receipt.json"
    write_json(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

