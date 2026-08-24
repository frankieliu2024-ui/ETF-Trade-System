from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

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


def build(root: Path = ROOT) -> dict:
    trade_dir = root / "events/trades"
    decision_dir = root / "events/decisions"
    items = []
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = load_json(path, {})
        linked = str(trade.get("linked_decision_id") or "")
        attr = trade.get("execution_attribution") or {}
        if not linked:
            items.append({"trade_event_id": trade.get("event_id") or path.stem, "status": "UNLINKED_TRADE", "code": trade.get("code"), "name": trade.get("name")})
            continue
        decision_path = decision_dir / f"{linked}.json"
        decision = load_json(decision_path, {}) if decision_path.exists() else {}
        items.append({
            "trade_event_id": trade.get("event_id") or path.stem,
            "decision_id": linked,
            "hypothesis_id": trade.get("hypothesis_id") or decision.get("hypothesis_id"),
            "code": trade.get("code") or decision.get("candidate_code"),
            "name": trade.get("name") or decision.get("candidate_name"),
            "side": trade.get("side"),
            "decision_time_beijing": decision.get("decision_time_beijing"),
            "execution_time_beijing": trade.get("confirmed_at_beijing"),
            "decision_price": attr.get("decision_price", decision.get("price_at_decision")),
            "execution_price": attr.get("execution_price", trade.get("price")),
            "decision_to_execution_seconds": attr.get("decision_to_execution_seconds"),
            "adverse_execution_cost_pct": attr.get("adverse_execution_cost_pct"),
            "status": attr.get("status", "LEGACY_OR_PARTIAL"),
        })
    adverse = [safe_float(x.get("adverse_execution_cost_pct")) for x in items]
    adverse = [x for x in adverse if x is not None]
    delays = [safe_float(x.get("decision_to_execution_seconds")) for x in items]
    delays = [x for x in delays if x is not None and x >= 0]
    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "DECISION_EXECUTION_ATTRIBUTION",
        "read_only": True,
        "trade_event_count": len(items),
        "linked_trade_count": len([x for x in items if x.get("decision_id")]),
        "execution_cost_sample_count": len(adverse),
        "mean_adverse_execution_cost_pct": round(mean(adverse), 4) if adverse else None,
        "delay_sample_count": len(delays),
        "mean_decision_to_execution_seconds": round(mean(delays), 1) if delays else None,
        "items": items,
        "interpretation_rule": "把判断质量与执行质量分开：决策时点后的市场结果评价判断，决策价格与真实成交价之间的偏差评价执行。不得因一次慢成交或一次有利成交自动修改交易规则。",
        "optimization_principle": "只分析能够帮助减少可避免执行损失或提高资本实现效率的差异；不追求零滑点、零延迟或完美执行。",
    }


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data/state/execution_quality.json", result)
    print(json.dumps({"ok": True, "trade_event_count": result.get("trade_event_count"), "linked_trade_count": result.get("linked_trade_count"), "execution_cost_sample_count": result.get("execution_cost_sample_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
