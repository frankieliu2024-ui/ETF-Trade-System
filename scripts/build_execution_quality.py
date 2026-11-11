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




def canonical_ineligibility(root: Path, trade: dict) -> dict:
    """Return an explicit review closure for a trade that cannot be legally linked."""
    execution_date = str(trade.get("execution_date") or trade.get("confirmed_at_beijing") or "")[:10]
    event_id = str(trade.get("event_id") or "").strip()
    if not execution_date or not event_id:
        return {}
    review = load_json(root / "events" / "reviews" / f"{execution_date}.json", {})
    payload = review.get("review") if isinstance(review.get("review"), dict) else review
    mapping = ((payload.get("account_close") or {}).get("case_mapping") or payload.get("case_mapping") or {}) if isinstance(payload, dict) else {}
    for item in mapping.get("ineligible_executed_trades") or []:
        if (
            str(item.get("trade_event_id") or "") == event_id
            and str(item.get("eligibility") or "").upper() == "EXPLICIT_CANONICAL_INELIGIBILITY"
        ):
            return item

    # User-confirmed broker facts may classify a same-day stock purchase as
    # an IPO base position. It is outside the ETF Formal Decision domain and
    # must be closed explicitly, without inventing a decision_id.
    account = load_json(root / "data" / "state" / "account_fact.json", {})
    for fact in account.get("trades") or []:
        if (
            str(fact.get("code") or "") == str(trade.get("code") or "")
            and str(fact.get("side") or "").upper() == str(trade.get("side") or "").upper()
            and str(fact.get("asset_type") or "").upper() == "STOCK"
            and "打新底仓" in str(fact.get("role") or "")
            and abs(float(fact.get("quantity") or 0) - float(trade.get("quantity") or 0)) < 0.01
            and abs(float(fact.get("amount") or 0) - float(trade.get("amount") or 0)) < 0.01
        ):
            return {
                "trade_event_id": event_id,
                "eligibility": "EXPLICIT_CANONICAL_INELIGIBILITY",
                "reason": "用户确认的非ETF打新底仓成交，不属于ETF正式决策归因域",
                "source": "data/state/account_fact.json",
                "role": fact.get("role"),
                "asset_type": fact.get("asset_type"),
            }
    return {}


def execution_time_for_trade(root: Path, trade: dict) -> str | None:
    """Prefer the broker-confirmed execution timestamp over screenshot confirmation time."""
    event_id = str(trade.get("event_id") or "").strip()
    request_dir = root / "requests" / "live_snapshot"
    if event_id and request_dir.exists():
        for path in sorted(request_dir.glob("*.json")):
            request = load_json(path, {})
            nested = request.get("trade_event") if isinstance(request.get("trade_event"), dict) else {}
            if str(nested.get("event_id") or "").strip() != event_id:
                continue
            executed = nested.get("executed_at") or nested.get("executed_at_beijing")
            if executed:
                return str(executed)
    # Broker screenshot ingress stores the exact execution timestamp under the
    # canonical account fact when the request does not carry a nested trade
    # object.  The screenshot receipt time is only a confirmation boundary and
    # must never be used as execution time when this fact is available.
    account = load_json(root / "data" / "state" / "account_fact.json", {})
    for fact in account.get("confirmed_trades") or []:
        if not isinstance(fact, dict):
            continue
        if all(str(fact.get(key) or "") == str(trade.get(key) or "") for key in ("code", "side", "quantity", "price")):
            executed = fact.get("executed_at_beijing") or fact.get("executed_at")
            if executed:
                return str(executed)
    return str(
        trade.get("executed_at")
        or trade.get("executed_at_beijing")
        or trade.get("confirmed_at_beijing")
        or ""
    ) or None


def build(root: Path = ROOT) -> dict:
    trade_dir = root / "events/trades"
    items = []
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = load_json(path, {})
        if str(trade.get("execution_status") or "").upper() == "SUPERSEDED":
            continue
        requested = str(trade.get("linked_decision_id") or "")
        linked, decision, link_status = resolve_link(root, trade, requested)
        if not linked:
            ineligibility = canonical_ineligibility(root, trade)
            if ineligibility:
                items.append({
                    "trade_event_id": trade.get("event_id") or path.stem,
                    "status": "INELIGIBLE_UNRECOVERABLE_TRADE",
                    "link_status": "EXPLICIT_CANONICAL_INELIGIBILITY",
                    "code": trade.get("code"),
                    "name": trade.get("name"),
                    "ineligibility_reason": ineligibility.get("reason"),
                    "ineligibility_record": ineligibility,
                })
            else:
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
        t0 = parse_time(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or decision.get("decision_time_beijing"))
        execution_time = execution_time_for_trade(root, trade)
        t1 = parse_time(execution_time)
        delay = (t1 - t0).total_seconds() if t0 and t1 else None
        ordering = str(decision.get("decision_effective_ordering") or "").upper()
        timing_quality = str(decision.get("timing_quality") or "").upper()
        bounded_before_execution = ordering == "BEFORE_EXECUTION" and timing_quality in {"USER_CONFIRMED_BOUNDED", "USER_CONFIRMED"}

        items.append({
            "trade_event_id": trade.get("event_id") or path.stem,
            "decision_id": linked,
            "raw_linked_decision_id": requested or None,
            "link_status": link_status,
            "hypothesis_id": trade.get("hypothesis_id") or (decision.get("hypothesis_id") if str(trade.get("code") or "") == str(decision.get("candidate_code") or "") else None),
            "code": trade.get("code") or decision.get("candidate_code"),
            "name": trade.get("name") or decision.get("candidate_name"),
            "side": trade.get("side"),
            "decision_time_beijing": decision.get("decision_time_beijing"),
            "decision_effective_at_beijing": decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing"),
            "recorded_at_beijing": decision.get("recorded_at_beijing"),
            "decision_effective_ordering": decision.get("decision_effective_ordering"),
            "timing_quality": decision.get("timing_quality"),
            "timing_status": "EXACT" if delay is not None and delay >= 0 else "BOUNDED_BEFORE_EXECUTION" if bounded_before_execution else "UNAVAILABLE",
            "execution_time_beijing": execution_time,
            "decision_price": decision_price,
            "execution_price": execution_price,
            "decision_to_execution_seconds": round(delay, 1) if delay is not None and delay >= 0 else None,
            "adverse_execution_cost_pct": round(adverse, 4) if adverse is not None else None,
            "status": "READY" if decision_price is not None and execution_price is not None and ((delay is not None and delay >= 0) or bounded_before_execution) else "PARTIAL",
        })

    unlinked = [x for x in items if x.get("status") == "UNLINKED_TRADE"]
    ineligible = [x for x in items if x.get("status") == "INELIGIBLE_UNRECOVERABLE_TRADE"]
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
        "unlinked_trade_count": len(unlinked),
        "ineligible_unrecoverable_trade_count": len(ineligible),
        "attribution_backlog": {
            "status": "OPEN" if unlinked else "CLEAR",
            "items": unlinked,
            "rule": "未找到PIT有效正式决策时保留UNLINKED_TRADE；不得按时间、标的或持仓结果自动追认decision_id。后续必须补齐正式决策证据，或由正式复盘记录不可恢复归因。",
        },
        "attribution_gate": {
            "status": "BLOCKED" if unlinked else "PASS",
            "unlinked_trade_ids": [str(x.get("trade_event_id") or "") for x in unlinked],
            "rule": "每一笔EXECUTED成交必须存在PIT有效正式决策；无法关联时只能进入硬失败门，不得作为普通警告、不得自动追认、不得进入健康闭环。",
        },
        "corrected_future_or_invalid_link_count": corrected_links,
        "execution_cost_sample_count": len(adverse),
        "mean_adverse_execution_cost_pct": round(mean(adverse), 4) if adverse else None,
        "delay_sample_count": len(delays),
        "mean_decision_to_execution_seconds": round(mean(delays), 1) if delays else None,
        "items": items,
        "interpretation_rule": "成交只能归因到业务有效时间不晚于成交、或有明确用户确认有界先后关系的正式决策。canonical recorded_at可晚于成交，不用于制造负延迟；判断质量与执行质量分开评价。",
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

