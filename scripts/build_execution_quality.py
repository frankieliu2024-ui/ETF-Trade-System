from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean

try:
    from state_manager import atomic_json_write, now_utc
    from decision_trade_link import resolve_link, decision_price_for_trade
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc
    from scripts.decision_trade_link import resolve_link, decision_price_for_trade

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def build(root: Path = ROOT) -> dict:
    trade_dir = root / "events/trades"
    items = []
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = load_json(path, {})
        requested = str(trade.get("linked_decision_id") or "")
        linked, decision, link_status = resolve_link(root, trade, requested)
        if not linked:
            items.append({
                "trade_event_id": trade.get("event_id") or path.stem,
                "status": "UNLINKED_TRADE",
                "link_status": link_status,
                "code": trade.get("code"),
                "name": trade.get("name"),
            })
            continue

        decision_price = decision_price_for_trade(decision, trade)
        execution_price = safe_float(trade.get("price"))
        diff_pct = None
        if decision_price not in (None, 0.0) and execution_price is not None:
            diff_pct = (execution_price / decision_price - 1.0) * 100.0
        side = str(trade.get("side") or "").upper()
        is_buy = side in {"BUY", "B", "买", "买入"} or "买" in side
        is_sell = side in {"SELL", "S", "卖", "卖出"} or "卖" in side
        adverse = diff_pct if is_buy else (-diff_pct if is_sell and diff_pct is not None else None)
        t0 = parse_time(decision.get("decision_time_beijing"))
        t1 = parse_time(trade.get("confirmed_at_beijing"))
        delay = (t1 - t0).total_seconds() if t0 and t1 else None

        items.append({
            "trade_event_id": trade.get("event_id") or path.stem,
            "decision_id": linked,
            "raw_linked_decision_id": requested or None,
            "link_status": link_status,
            "hypothesis_id": trade.get("hypothesis_id") or decision.get("hypothesis_id"),
            "code": trade.get("code") or decision.get("candidate_code"),
            "name": trade.get("name") or decision.get("candidate_name"),
            "side": trade.get("side"),
            "decision_time_beijing": decision.get("decision_time_beijing"),
            "execution_time_beijing": trade.get("confirmed_at_beijing"),
            "decision_price": decision_price,
            "execution_price": execution_price,
            "decision_to_execution_seconds": round(delay, 1) if delay is not None and delay >= 0 else None,
            "adverse_execution_cost_pct": round(adverse, 4) if adverse is not None else None,
            "status": "READY" if decision_price is not None and execution_price is not None and delay is not None and delay >= 0 else "PARTIAL",
        })

    adverse = [safe_float(x.get("adverse_execution_cost_pct")) for x in items]
    adverse = [x for x in adverse if x is not None]
    delays = [safe_float(x.get("decision_to_execution_seconds")) for x in items]
    delays = [x for x in delays if x is not None and x >= 0]
    corrected_links = len([x for x in items if x.get("link_status") == "AUTO_PRIOR_MATCH"])
    return {
        "schema_version": "1.1",
        "generated_at": now_utc(),
        "mode": "DECISION_EXECUTION_ATTRIBUTION",
        "read_only": True,
        "trade_event_count": len(items),
        "linked_trade_count": len([x for x in items if x.get("decision_id")]),
        "corrected_future_or_invalid_link_count": corrected_links,
        "execution_cost_sample_count": len(adverse),
        "mean_adverse_execution_cost_pct": round(mean(adverse), 4) if adverse else None,
        "delay_sample_count": len(delays),
        "mean_decision_to_execution_seconds": round(mean(delays), 1) if delays else None,
        "items": items,
        "interpretation_rule": "成交只能归因到成交时点之前或同时的正式决策。决策后的确认事件不得反向链接到成交；判断质量与执行质量分开评价。",
        "optimization_principle": "只分析能够帮助减少可避免执行损失或提高资本实现效率的差异；不追求零滑点、零延迟或完美执行。",
    }


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data/state/execution_quality.json", result)
    print(json.dumps({
        "ok": True,
        "trade_event_count": result.get("trade_event_count"),
        "linked_trade_count": result.get("linked_trade_count"),
        "corrected_future_or_invalid_link_count": result.get("corrected_future_or_invalid_link_count"),
        "execution_cost_sample_count": result.get("execution_cost_sample_count"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
