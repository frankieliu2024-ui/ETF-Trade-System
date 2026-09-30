from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from formal_file_mutation_gateway import upsert_formal_line
from process_state_sync_request import sync_experience_transaction_index
from confirmed_trade_facts import canonical_etf_fee_projection, trade_signature

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


def net_cash_effect(event: dict, fee: float) -> float | None:
    """Derive the canonical cash movement from side, gross amount and fee."""
    gross = safe_float(event.get("amount"))
    if gross is None:
        gross = safe_float(event.get("gross_amount"))
    if gross is None:
        return None
    side = str(event.get("side") or event.get("action") or "").upper()
    if side == "SELL":
        return round(gross - fee, 2)
    if side == "BUY":
        return round(-(gross + fee), 2)
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
    row["cash_flow_amount"] = net_cash_effect(event, fee)
    return row


def pending_fee_count(trades: list[dict], root: Path | None = None) -> int:
    if root is not None:
        return int(canonical_etf_fee_projection(root, trades)["pending_fee_count"])
    return sum(1 for t in trades if str(t.get("fee_status") or "").upper() != "CONFIRMED")


def rebuild_known_net(equity: dict, root: Path | None = None) -> None:
    trades = equity.get("trades") or []
    summary = equity.setdefault("summary", {})
    projection = canonical_etf_fee_projection(root, trades) if root is not None else None
    confirmed_fees = (
        projection["effective_confirmed_fee_sum"]
        if projection is not None
        else round(sum(float(t.get("fee_amount") or 0) for t in trades if str(t.get("fee_status") or "").upper() == "CONFIRMED"), 2)
    )
    gross_equity = safe_float(summary.get("current_gross_strategy_equity"))
    start_capital = safe_float(summary.get("starting_etf_strategy_capital")) or 200000.0
    gross_pnl = safe_float(summary.get("current_cumulative_pnl_gross"))
    if gross_equity is None:
        raise RuntimeError("etf_strategy_equity lacks gross basis required for deterministic fee correction")
    if gross_pnl is None:
        gross_pnl = round(gross_equity - start_capital, 2)
        summary["current_cumulative_pnl_gross"] = gross_pnl
    high_watermark = safe_float(summary.get("known_net_high_watermark")) or start_capital

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

    pending = projection["pending_fee_count"] if projection is not None else pending_fee_count(trades)
    fee_summary = equity.setdefault("fee_summary", {})
    fee_summary["etf_confirmed_fees"] = confirmed_fees
    fee_summary["etf_pending_fees"] = pending
    fee_summary["pending_note"] = "canonical ETF fee projection"
    summary["known_fees"] = confirmed_fees
    summary["pending_fee_count"] = pending
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
    if projection is not None:
        summary["trade_count"] = projection["canonical_trade_count"]
        summary["known_net_status"] += "; CANONICAL_EVENT_OVERLAYS_INCLUDED"


def _apply_attribution_request(req: dict) -> int:
    required = ("request_id", "trade_event_id", "linked_decision_id", "provenance")
    missing = [k for k in required if not req.get(k)]
    if missing: raise RuntimeError("attribution correction missing required fields: " + ", ".join(missing))
    event_id, decision_id = str(req["trade_event_id"]).strip(), str(req["linked_decision_id"]).strip()
    event = read_json(ROOT / "events/trades" / f"{event_id}.json", {}) or {}
    decision = read_json(ROOT / "events/decisions" / f"{decision_id}.json", {}) or {}
    if not event or not decision: raise RuntimeError("attribution correction source fact or Decision Fact is missing")
    from decision_trade_link import _effective_time, _time, _decision_mentions_trade
    dt, tt = _effective_time(decision), _time(event.get("confirmed_at_beijing") or event.get("executed_at_beijing"))
    if str(event.get("execution_status") or "EXECUTED").upper() != "EXECUTED" or not dt or not tt or dt >= tt:
        raise RuntimeError("attribution correction violates executed-trade/PIT contract")
    if not _decision_mentions_trade(decision, event): raise RuntimeError("attribution correction action/security semantics are incompatible")
    p = req["provenance"]
    if not isinstance(p, dict) or not all(str(p.get(k) or "").strip() for k in ("resolver","source_trade","source_decision","parent_request_id")):
        raise RuntimeError("attribution correction provenance is incomplete")
    if p["source_trade"] != f"events/trades/{event_id}.json" or p["source_decision"] != f"events/decisions/{decision_id}.json":
        raise RuntimeError("attribution correction provenance source mismatch")
    rd = ROOT / "requests/trade_fact_correction"; rd.mkdir(parents=True, exist_ok=True)
    existing = [read_json(x, {}) or {} for x in rd.glob("*.json") if str((read_json(x, {}) or {}).get("correction_type") or "").upper() == "DECISION_ATTRIBUTION" and str((read_json(x, {}) or {}).get("trade_event_id") or "") == event_id]
    ids = {str(x.get("linked_decision_id") or "") for x in existing}
    if ids - {decision_id}: raise RuntimeError("existing canonical attribution conflicts with requested Decision Fact")
    req = dict(req); req.update({"correction_type":"DECISION_ATTRIBUTION","status":"APPLIED","immutable_trade_source":True})
    if existing:
        # A pending ingress for the same trade and Decision Fact is the
        # canonical idempotent case.  The processor adds execution metadata
        # (correction_type/status/immutable_trade_source) before persistence,
        # so comparing the raw ingress byte-for-byte would incorrectly turn
        # a retry of the same attribution into a conflict.
        if ids == {decision_id}:
            print(json.dumps({"status":"RECONCILED","trade_event_id":event_id,"linked_decision_id":decision_id,"immutable_trade_source":True},ensure_ascii=False)); return 0
        raise RuntimeError("trade already has a different canonical attribution correction")
    target=rd/f"{req['request_id']}.json"
    if target.exists() and json.dumps(read_json(target,{}),ensure_ascii=False,sort_keys=True)!=json.dumps(req,ensure_ascii=False,sort_keys=True):
        raise RuntimeError("attribution correction request identity conflict")
    if not target.exists(): write_json(target, req)
    print(json.dumps({"status":"APPLIED","trade_event_id":event_id,"linked_decision_id":decision_id,"immutable_trade_source":True},ensure_ascii=False)); return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill newly confirmed metadata for an already-recorded trade without creating a new trade.")
    parser.add_argument("request_path")
    args = parser.parse_args()
    request_path = (ROOT / args.request_path).resolve()
    if ROOT not in request_path.parents or not request_path.exists():
        raise RuntimeError("invalid correction request path")
    req = read_json(request_path, {}) or {}
    if str(req.get("correction_type") or "").upper() == "DECISION_ATTRIBUTION":
        return _apply_attribution_request(req)
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
    expected_net = net_cash_effect(event, fee)
    prior_net = safe_float(event.get("net_cash_effect") or event.get("cash_flow_amount"))
    fee_is_current = prior_status == "CONFIRMED" and prior_fee is not None and abs(prior_fee - fee) < 0.005
    net_cash_is_current = expected_net is None or (prior_net is not None and abs(prior_net - expected_net) < 0.005)
    already_confirmed_same = fee_is_current and net_cash_is_current
    if prior_status == "CONFIRMED" and prior_fee is not None and not fee_is_current:
        raise RuntimeError("existing confirmed fee differs; explicit correction conflict requires manual review")

    stamp = str(req.get("requested_at_beijing") or event.get("fee_confirmed_at_beijing") or datetime.now(TZ).isoformat(timespec="seconds"))
    if not already_confirmed_same:
        event["fee_amount"] = round(fee, 2)
        event["fee_status"] = "CONFIRMED"
        event["fee_confirmed_at_beijing"] = str(req.get("evidence_time_beijing") or stamp)
        event["fee_source"] = str(req.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
        event["fact_updated_at_beijing"] = stamp
        if expected_net is not None:
            event["net_cash_effect"] = expected_net
            event["cash_flow_amount"] = expected_net
        write_json(event_path, event)

    equity = read_json(EQUITY, {}) or {}
    equity_before = json.dumps(equity, ensure_ascii=False, sort_keys=True)
    trades = equity.get("trades") or []
    source_ref = f"events/trades/{event_id}.json"
    # Recovery rows may predate the canonical event and therefore carry only
    # ``source=experience_trade_index``.  Match by the economic trade
    # signature as well, otherwise a fee correction updates the event/index
    # but leaves the auxiliary equity ledger permanently PENDING.
    event_signature = trade_signature(event)
    matches = [
        t for t in trades
        if (
            str(t.get("source") or "") == source_ref
            or str(t.get("duplicate_check") or "") == f"unique_event_id_{event_id}"
            or trade_signature(t) == event_signature
        )
    ]
    if len(matches) > 1:
        raise RuntimeError("strategy equity does not contain exactly one matching trade; refuse partial correction")
    if matches:
        row = matches[0]
        row["fee_amount"] = round(fee, 2)
        row["fee_status"] = "CONFIRMED"
        row["cash_flow_amount"] = net_cash_effect(event, fee)
    else:
        # The event is authoritative.  A lagging auxiliary reconstruction must
        # converge by adding exactly this event, never by creating a new event.
        equity.setdefault("trades", []).append(equity_row_from_event(event, fee))
        equity.setdefault("summary", {})["trade_count"] = len(equity["trades"])
    rebuild_known_net(equity, ROOT)
    equity_after = json.dumps(equity, ensure_ascii=False, sort_keys=True)
    if equity_after != equity_before:
        equity["generated_at"] = stamp
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
        "pending_fee_count": pending_fee_count(trades, ROOT),
        "known_fees": summary.get("known_fees"),
        "known_net_current_strategy_return_pct": summary.get("known_net_current_strategy_return_pct"),
        "applied_at_beijing": stamp,
        "safety_boundary": "Only enriches an existing trade with user/broker-confirmed fee metadata; never creates a trade or changes quantity, price, side, execution date, MASTER, risk permission or trading action.",
    }
    receipt_path = STATE / "trade_fact_correction_receipt.json"
    if read_json(receipt_path, {}) != receipt:
        write_json(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
