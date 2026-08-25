from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_used(raw) -> tuple[str, list[dict], list[str]]:
    warnings: list[str] = []
    if raw is None:
        return "NOT_RECORDED", [], warnings
    if raw == []:
        return "EXPLICIT_NONE", [], warnings
    if not isinstance(raw, list):
        return "INVALID", [], ["research_evidence_used must be a list"]
    rows: list[dict] = []
    if len(raw) > 3:
        warnings.append("research_evidence_used exceeds max 3; audit keeps first 3 only")
    for item in raw[:3]:
        if not isinstance(item, dict):
            warnings.append("non-object research attribution ignored")
            continue
        evidence_id = str(item.get("evidence_id") or item.get("evidence") or "").strip()
        change = str(item.get("change") or item.get("evidence_change") or "").strip()
        effect = str(item.get("decision_effect") or item.get("effect") or "").strip()
        if not evidence_id or not effect:
            warnings.append("research attribution missing evidence_id or decision_effect")
            continue
        rows.append({
            "evidence_id": evidence_id,
            "change": change,
            "decision_effect": effect,
        })
    return ("EXPLICIT" if rows else "INVALID"), rows, warnings


def build(root: Path) -> dict:
    decision_dir = root / "events" / "decisions"
    trade_dir = root / "events" / "trades"
    decisions: dict[str, dict] = {}
    module_counts: Counter[str] = Counter()
    decision_status_counts: Counter[str] = Counter()
    warnings: list[dict] = []

    for path in sorted(decision_dir.glob("*.json")) if decision_dir.exists() else []:
        event = _load(path)
        decision_id = str(event.get("decision_id") or path.stem)
        formal = event.get("formal_decision") or {}
        status, used, row_warnings = _normalize_used(formal.get("research_evidence_used"))
        decision_status_counts[status] += 1
        for row in used:
            module_counts[row["evidence_id"]] += 1
        if row_warnings:
            warnings.append({"decision_id": decision_id, "warnings": row_warnings})
        decisions[decision_id] = {
            "decision_id": decision_id,
            "decision_time_beijing": event.get("decision_time_beijing"),
            "attribution_status": status,
            "research_evidence_used": used,
        }

    trade_rows: list[dict] = []
    attributed_trade_count = 0
    explicit_no_research_trade_count = 0
    legacy_unattributed_trade_count = 0
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = _load(path)
        linked = str(trade.get("linked_decision_id") or "")
        d = decisions.get(linked) if linked else None
        status = d.get("attribution_status") if d else "NO_LINKED_ATTRIBUTION"
        used = d.get("research_evidence_used") if d else []
        if status == "EXPLICIT":
            attributed_trade_count += 1
        elif status == "EXPLICIT_NONE":
            explicit_no_research_trade_count += 1
        else:
            legacy_unattributed_trade_count += 1
        trade_rows.append({
            "event_id": trade.get("event_id") or path.stem,
            "confirmed_at_beijing": trade.get("confirmed_at_beijing"),
            "display_name": f"{trade.get('name','')}（{trade.get('code','')}）",
            "side": trade.get("side"),
            "quantity": trade.get("quantity"),
            "linked_decision_id": linked or None,
            "research_attribution_status": status,
            "research_evidence_used": used or [],
        })

    trade_rows.sort(key=lambda x: str(x.get("confirmed_at_beijing") or ""), reverse=True)
    total_decisions = sum(decision_status_counts.values())
    explicit_decisions = decision_status_counts.get("EXPLICIT", 0) + decision_status_counts.get("EXPLICIT_NONE", 0)
    coverage = round(explicit_decisions / total_decisions, 4) if total_decisions else 0.0

    return {
        "status": "READY",
        "mode": "EXPLICIT_RESEARCH_CONTRIBUTION_AUDIT",
        "read_only": True,
        "rule": "只统计正式决策中显式记录的research_evidence_used；最多3项，每项只回答证据、相对上一节点变化、对本次决策的实际影响。缺失记录不得自动推断研究贡献。",
        "decision_attribution": {
            "total_decisions": total_decisions,
            "explicit_attribution_or_none": explicit_decisions,
            "explicit_coverage_ratio": coverage,
            "status_counts": dict(decision_status_counts),
        },
        "trade_attribution": {
            "total_trades": len(trade_rows),
            "attributed_trade_count": attributed_trade_count,
            "explicit_no_research_trade_count": explicit_no_research_trade_count,
            "legacy_or_unlinked_trade_count": legacy_unattributed_trade_count,
        },
        "evidence_use_counts": dict(module_counts.most_common()),
        "recent_trades": trade_rows[:10],
        "redundancy_review": {
            "automatic_deletion": False,
            "rule": "不得因短期未被引用自动删除研究。只有在足够正式决策样本中长期未改变机会、金额、持仓或卖出判断，且无独立风险/复盘价值时，才进入人工删除审查。",
            "sample_sufficient": explicit_decisions >= 20,
        },
        "warnings": warnings[:20],
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(build(root), ensure_ascii=False, indent=2))
