from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

try:
    from state_manager import atomic_json_write
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write

try:
    from decision_trade_link import resolve_link
except ModuleNotFoundError:
    from scripts.decision_trade_link import resolve_link


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(new, old):
    n, o = _safe_float(new), _safe_float(old)
    if n is None or o in (None, 0.0):
        return None
    return round((n / o - 1.0) * 100.0, 4)


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
        rows.append({"evidence_id": evidence_id, "change": change, "decision_effect": effect})
    return ("EXPLICIT" if rows else "INVALID"), rows, warnings


def _daily_history(root: Path) -> tuple[list[str], dict[str, dict]]:
    by_date: dict[str, dict] = {}
    base = root / "events/research/daily_features"
    for path in sorted(base.glob("*.json")) if base.exists() else []:
        obj = _load(path)
        date = str(obj.get("market_date") or "")
        if date:
            by_date[date] = obj
    return sorted(by_date), by_date


def _name_map(root: Path) -> dict[str, str]:
    cfg = _load(root / "config/market/etf_monitor_universe.json")
    return {str(x.get("code")): str(x.get("name")) for x in (cfg.get("objects") or []) if x.get("code")}


def _infer_action_object(event: dict, names: dict[str, str]) -> tuple[str, str, str]:
    formal = event.get("formal_decision") or {}
    amount = str(formal.get("amount_action") or "")
    main = str(formal.get("main_candidate") or "")
    explicit = str(event.get("candidate_code") or formal.get("candidate_code") or formal.get("code") or "")
    codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", amount)
    code = explicit or (codes[0] if len(set(codes)) == 1 else "")
    if not code:
        main_codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", main)
        code = main_codes[0] if len(set(main_codes)) == 1 else ""
    text = amount
    if code and code in amount:
        segments = re.split(r"[；;。]", amount)
        matched = [x for x in segments if code in x]
        if matched:
            text = matched[0]
    if any(k in text for k in ("卖出", "退出", "降低风险", "减持", "释放")):
        action = "SELL"
    elif any(k in text for k in ("买入", "Trial", "Confirm", "新增")) and "新增0元" not in text:
        action = "BUY"
    elif code:
        action = "OBSERVE_OR_HOLD"
    else:
        action = "NO_PRIMARY_OBJECT"
    name = str(event.get("candidate_name") or names.get(code) or "")
    return code, name, action


def _start_prices(event: dict) -> dict[str, float]:
    prices: dict[str, float] = {}
    for row in ((event.get("comparison_snapshot") or {}).get("items") or []):
        code = str(row.get("code") or "")
        price = _safe_float(row.get("price"))
        if code and price not in (None, 0.0):
            prices[code] = price
    code = str(event.get("candidate_code") or "")
    p = _safe_float(event.get("price_at_decision"))
    if code and p not in (None, 0.0):
        prices[code] = p
    return prices


def _future_row(daily: dict, code: str) -> dict:
    return next((x for x in (daily.get("features") or []) if str(x.get("code") or "") == code), {})


def _build_outcome(root: Path, event: dict, names: dict[str, str], dates: list[str], by_date: dict[str, dict], used: list[dict]) -> dict:
    decision_id = str(event.get("decision_id") or "")
    decision_date = str(event.get("market_date") or "")
    code, name, action = _infer_action_object(event, names)
    starts = _start_prices(event)
    price0 = starts.get(code)
    later_dates = [d for d in dates if d > decision_date]
    horizons: dict[str, dict] = {}
    direction = 1.0 if action == "BUY" else (-1.0 if action == "SELL" else 0.0)
    for h in (1, 3, 5):
        key = f"T_plus_{h}"
        if len(later_dates) < h:
            horizons[key] = {"status": "PENDING", "target_trading_day_index": h}
            continue
        date = later_dates[h - 1]
        row = _future_row(by_date[date], code) if code else {}
        candidate_return = _pct(row.get("close"), price0) if row and price0 not in (None, 0.0) else None
        peer_returns = []
        for peer_code, start in starts.items():
            peer = _future_row(by_date[date], peer_code)
            r = _pct(peer.get("close"), start) if peer else None
            if r is not None:
                peer_returns.append(r)
        peer_median = round(median(peer_returns), 4) if peer_returns else None
        relative = round(candidate_return - peer_median, 4) if candidate_return is not None and peer_median is not None else None
        signed = round(direction * candidate_return, 4) if direction and candidate_return is not None else None
        signed_relative = round(direction * relative, 4) if direction and relative is not None else None
        horizons[key] = {
            "status": "MATURED" if candidate_return is not None else "DATA_MISSING",
            "market_date": date,
            "candidate_close_return_pct": candidate_return,
            "etf_universe_median_return_pct": peer_median,
            "relative_to_etf_universe_median_pct_points": relative,
            "decision_direction_adjusted_return_pct": signed,
            "decision_direction_adjusted_relative_pct_points": signed_relative,
            "interpretation": "BUY后上涨为正；SELL后继续下跌为正。相对值用于避免把全市场共同涨跌误记为研究贡献。" if direction else "观察/持有类决策不计算方向调整收益。",
        }
    linked_trades = []
    trade_dir = root / "events/trades"
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = _load(path)
        resolved_id, _, link_status = resolve_link(root, trade, str(trade.get("linked_decision_id") or ""))
        if resolved_id == decision_id:
            linked_trades.append({
                "event_id": trade.get("event_id") or path.stem,
                "confirmed_at_beijing": trade.get("confirmed_at_beijing"),
                "side": trade.get("side"),
                "quantity": trade.get("quantity"),
                "price": trade.get("price"),
                "pit_link_status": link_status,
            })
    return {
        "schema_version": "2.0",
        "decision_id": decision_id,
        "market_date": decision_date,
        "candidate_code": code,
        "candidate_name": name,
        "display_name": f"{name}（{code}）" if name and code else "",
        "action_class": action,
        "price_at_decision": price0,
        "research_evidence_used": used,
        "horizons": horizons,
        "linked_trades": linked_trades,
        "read_only": True,
        "method_note": "严格使用决策日之后已经形成的交易日日线事实；T+1/T+3/T+5未到时保持PENDING，不用未来信息补值。ETF全集相对结果使用决策时点comparison_snapshot中的各ETF价格作为统一起点。",
        "decision_boundary": "结果只用于判断质量、执行质量和研究贡献复盘；不自动修改MASTER、不生成交易动作、不把相关性解释为因果。",
    }


def build(root: Path) -> dict:
    decision_dir = root / "events/decisions"
    trade_dir = root / "events/trades"
    outcome_dir = root / "events/research/decision_outcomes"
    outcome_dir.mkdir(parents=True, exist_ok=True)
    names = _name_map(root)
    dates, by_date = _daily_history(root)
    decisions: dict[str, dict] = {}
    module_counts: Counter[str] = Counter()
    decision_status_counts: Counter[str] = Counter()
    warnings: list[dict] = []
    evidence_samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    mature_explicit_decisions = 0

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
        outcome = _build_outcome(root, event, names, dates, by_date, used)
        atomic_json_write(outcome_dir / f"{decision_id}.json", outcome)
        mature_horizons = [v for v in (outcome.get("horizons") or {}).values() if v.get("status") == "MATURED"]
        if status == "EXPLICIT" and mature_horizons:
            mature_explicit_decisions += 1
            for evidence in used:
                eid = evidence["evidence_id"]
                for h, row in (outcome.get("horizons") or {}).items():
                    if row.get("status") != "MATURED":
                        continue
                    abs_effect = _safe_float(row.get("decision_direction_adjusted_return_pct"))
                    rel_effect = _safe_float(row.get("decision_direction_adjusted_relative_pct_points"))
                    if abs_effect is not None:
                        evidence_samples[eid][f"{h}_absolute"].append(abs_effect)
                    if rel_effect is not None:
                        evidence_samples[eid][f"{h}_relative"].append(rel_effect)
        decisions[decision_id] = {
            "decision_id": decision_id,
            "decision_time_beijing": event.get("decision_time_beijing"),
            "attribution_status": status,
            "research_evidence_used": used,
            "outcome": outcome,
        }

    evidence_effectiveness = {}
    for eid, buckets in evidence_samples.items():
        summary = {}
        for key, values in buckets.items():
            summary[key] = {
                "sample_count": len(values),
                "mean_direction_adjusted_value": round(mean(values), 4) if values else None,
                "positive_count": sum(1 for x in values if x > 0),
                "negative_count": sum(1 for x in values if x < 0),
            }
        evidence_effectiveness[eid] = summary

    trade_rows: list[dict] = []
    attributed_trade_count = 0
    explicit_no_research_trade_count = 0
    legacy_unattributed_trade_count = 0
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = _load(path)
        resolved_id, _, link_status = resolve_link(root, trade, str(trade.get("linked_decision_id") or ""))
        d = decisions.get(resolved_id) if resolved_id else None
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
            "linked_decision_id": resolved_id or None,
            "pit_link_status": link_status,
            "research_attribution_status": status,
            "research_evidence_used": used or [],
        })

    trade_rows.sort(key=lambda x: str(x.get("confirmed_at_beijing") or ""), reverse=True)
    total_decisions = sum(decision_status_counts.values())
    explicit_decisions = decision_status_counts.get("EXPLICIT", 0) + decision_status_counts.get("EXPLICIT_NONE", 0)
    coverage = round(explicit_decisions / total_decisions, 4) if total_decisions else 0.0

    return {
        "status": "READY",
        "mode": "EXPLICIT_RESEARCH_CONTRIBUTION_AND_OUTCOME_AUDIT",
        "read_only": True,
        "rule": "只统计正式决策中显式记录的research_evidence_used；最多3项。结果归因按T+1/T+3/T+5自然成熟，不事后补写历史研究贡献，不把共同市场涨跌误记为单一研究的功劳。",
        "decision_attribution": {
            "total_decisions": total_decisions,
            "explicit_attribution_or_none": explicit_decisions,
            "explicit_coverage_ratio": coverage,
            "status_counts": dict(decision_status_counts),
            "mature_explicit_research_decisions": mature_explicit_decisions,
            "evidence_effectiveness": evidence_effectiveness,
            "effectiveness_interpretation": "方向调整收益仅表示使用该研究证据的决策随后表现如何，不证明该证据单独造成结果。样本不足时不得据此修改规则。",
        },
        "trade_attribution": {
            "total_trades": len(trade_rows),
            "attributed_trade_count": attributed_trade_count,
            "explicit_no_research_trade_count": explicit_no_research_trade_count,
            "legacy_or_unlinked_trade_count": legacy_unattributed_trade_count,
            "pit_link_rule": "成交只能归到成交时点之前或同时、对象与动作匹配的正式决策。",
        },
        "evidence_use_counts": dict(module_counts.most_common()),
        "recent_trades": trade_rows[:10],
        "outcome_files": {
            "path": "events/research/decision_outcomes/<decision_id>.json",
            "schema_version": "2.0",
            "maturity_horizons": [1, 3, 5],
        },
        "redundancy_review": {
            "automatic_deletion": False,
            "rule": "只有在足够多显式研究归因且已成熟的正式决策中，某研究长期未改变决策或没有独立风险/复盘价值时，才进入人工删除审查；不按短期胜负自动删研究。",
            "mature_explicit_decision_count": mature_explicit_decisions,
            "sample_sufficient": mature_explicit_decisions >= 20,
        },
        "warnings": warnings[:20],
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(build(root), ensure_ascii=False, indent=2))
