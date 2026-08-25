from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


VALID_NODE_STATUS = {"READY", "DEGRADED", "BLOCKED", "NON_TRADING_DAY"}
EVENT_TYPES = {
    "TRADE_EXECUTED", "ACCOUNT_UPDATED", "DATA_ERROR", "SYSTEM_ERROR",
    "RULE_VERSION_CHANGE", "MANUAL_OVERRIDE",
}


class StateConflictError(RuntimeError):
    """Raised when a write would overwrite a file changed since it was read."""


def root_from_env() -> Path:
    return Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_json_write(path: Path, value: Any, expected_sha256: str | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected_sha256 is not None:
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        if current != expected_sha256:
            raise StateConflictError(f"write conflict: {path}")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_current() -> dict[str, Any]:
    return {
        "market_date": "", "latest_valid_node": "", "captured_at": "", "node_status": "NON_TRADING_DAY",
        "latest_snapshot": "", "snapshot_commit": "", "superseded_nodes": [], "data_freshness": {},
        "account_fact": {"status": "MISSING", "updated_at": "", "source": ""}, "needs_account_update": True,
        "last_trade_event_id": "", "rules_version": "V2.2.16", "generated_at": "",
    }


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def read_current(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current = default_current()
    current.update(read_json(root / "data" / "state" / "CURRENT.json", {}))
    if not isinstance(current.get("data_freshness"), dict):
        current["data_freshness"] = {}
    if not isinstance(current.get("superseded_nodes"), list):
        current["superseded_nodes"] = []
    return current


def read_account_fact(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    default = {"updated_at": "", "source": "BROKER_SCREENSHOT", "status": "MISSING", "total_asset": None, "cash": None, "positions": [], "orders": [], "trades": []}
    fact = read_json(root / "data" / "state" / "account_fact.json", default)
    default.update(fact)
    if default["status"] not in {"MISSING", "STALE", "VALID", "CONFLICT"}:
        raise ValueError("invalid account_fact.status")
    return default


def update_current(*, root: Path | None = None, market_date: str, node: str, captured_at: str, latest_snapshot: str = "", snapshot_commit: str = "", node_status: str = "READY", data_freshness: dict[str, Any] | None = None, expected_sha256: str | None = None) -> dict[str, Any]:
    if node_status not in VALID_NODE_STATUS:
        raise ValueError(f"invalid node_status: {node_status}")
    root = root or root_from_env()
    current = read_current(root)
    account = read_account_fact(root)
    previous_node = current.get("latest_valid_node", "")
    previous_date = current.get("market_date", "")
    is_valid = node_status in {"READY", "DEGRADED"}
    if is_valid and previous_node and (previous_date, previous_node) != (market_date, node):
        superseded = current.setdefault("superseded_nodes", [])
        entry = {"node": previous_node, "market_date": previous_date, "superseded_at": now_utc()}
        if entry not in superseded:
            superseded.append(entry)
    if is_valid:
        current.update({"market_date": market_date, "latest_valid_node": node, "captured_at": captured_at, "latest_snapshot": latest_snapshot, "snapshot_commit": snapshot_commit})
    current["node_status"] = node_status
    current["data_freshness"] = data_freshness or current.get("data_freshness", {})
    current["account_fact"] = {k: account.get(k, "") for k in ("status", "updated_at", "source")}
    current["needs_account_update"] = account["status"] != "VALID"
    current["generated_at"] = now_utc()
    atomic_json_write(root / "data" / "state" / "CURRENT.json", current, expected_sha256)
    return current


def event_id(event_type: str, source: str, payload: dict[str, Any]) -> str:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type}")
    body = canonical({"event_type": event_type, "source": source, "payload": payload})
    return "evt_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def append_event(*, root: Path | None = None, event_type: str, source: str, payload: dict[str, Any], git_commit: str = "", explicit_event_id: str | None = None) -> tuple[dict[str, Any], bool]:
    root = root or root_from_env()
    path = root / "events" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    eid = explicit_event_id or event_id(event_type, source, payload)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                existing[record["event_id"]] = record
    if eid in existing:
        return existing[eid], False
    record = {"event_id": eid, "event_type": event_type, "created_at": now_utc(), "source": source, "payload": payload, "git_commit": git_commit}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(record) + "\n")
    if event_type == "TRADE_EXECUTED":
        current = read_current(root)
        current["last_trade_event_id"] = eid
        current["generated_at"] = now_utc()
        atomic_json_write(root / "data" / "state" / "CURRENT.json", current)
    return record, True


def build_dashboard_candidate(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current = read_current(root)
    account = read_account_fact(root)
    return {
        "generated_at": now_utc(), "source": "Codex state layer", "current_risk_state": "REQUIRES_MANUAL_CONFIRMATION",
        "current_node": current.get("latest_valid_node", ""), "node_status": current.get("node_status", "NON_TRADING_DAY"),
        "current_candidate": None, "data_status": current.get("data_freshness", {}), "account_fact_status": account["status"],
        "needs_account_update": account["status"] != "VALID", "manual_confirmation_required": True, "write_target": "candidate_only",
        "prohibited_outputs": ["trade_amount", "buy_action", "sell_action", "order"],
    }


def evaluate_context_freshness(root: Path, current: dict[str, Any]) -> dict[str, Any]:
    freshness = dict(current.get("data_freshness") or {})
    raw = freshness.get("captured_at_beijing") or freshness.get("captured_at") or current.get("captured_at", "")
    try:
        captured = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        age_seconds = None
    policy = read_json(root / "config" / "runtime_policy.json", {})
    fresh_max = int(policy.get("fresh_max_age_seconds", 900))
    degraded_max = int(policy.get("degraded_max_age_seconds", 1500))
    if age_seconds is None:
        status = "STALE"
    elif age_seconds <= fresh_max:
        status = "FRESH"
    elif age_seconds <= degraded_max:
        status = "DEGRADED"
    else:
        status = "STALE"
    freshness.update({"status": status, "age_seconds": age_seconds, "fresh_max_age_seconds": fresh_max, "degraded_max_age_seconds": degraded_max})
    return freshness


def build_research_evidence_summary(root: Path) -> dict[str, Any]:
    """Aggregate current and changed research evidence without creating trade signals."""
    research = read_json(root / "data" / "state" / "research_context.json", {"status": "MISSING", "read_only": True})
    relative = read_json(root / "data" / "state" / "relative_strength.json", {"items": [], "read_only": True})
    delta = read_json(root / "data" / "state" / "research_evidence_delta.json", {"items": [], "read_only": True})
    skfolio_risk = read_json(root / "data" / "state" / "skfolio_risk_evidence.json", {"status": "MISSING", "decision_eligible": False, "trade_signal": None, "read_only": True})
    delta_map = {str(x.get("code")): x for x in (delta.get("items") or []) if x.get("code")}
    items = []
    for row in relative.get("items") or []:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        if not code:
            continue
        d = delta_map.get(code) or {}
        items.append({
            "display_name": f"{name}（{code}）" if name else code, "code": code, "name": name,
            "close_return_pct": row.get("close_return_pct"),
            "descriptive_daily_return_rank": row.get("rank"),
            "vs_universe_median_pct_points": row.get("vs_universe_median_pct_points"),
            "vs_shanghai_pct_points": row.get("vs_shanghai_pct_points"), "vs_chinext_pct_points": row.get("vs_chinext_pct_points"),
            "recent_slope_pct_per_10m": row.get("recent_slope_pct_per_10m"), "sampling_coverage": row.get("sampling_coverage"),
            "evidence_delta": d.get("delta_from_prior_research_node", {}),
            "comparison_dimensions": d.get("comparison_dimensions", []),
            "capital_efficiency_score": None,
        })
    return {
        "status": research.get("status", "MISSING"), "market_date": relative.get("market_date") or research.get("market_date"),
        "as_of_beijing": relative.get("as_of_beijing"), "evidence_time_semantics": delta.get("evidence_time_semantics", "UNKNOWN"),
        "prior_research_as_of_beijing": delta.get("prior_as_of_beijing", ""), "etf_evidence": items,
        "skfolio_risk_evidence": skfolio_risk,
        "historical_evidence_status": "ACCUMULATING" if research.get("status") == "READY" else "INSUFFICIENT_OR_MISSING",
        "use_in_current_decision": True,
        "interpretation_rule": "盘中优先解释相对上一研究节点真正新增、增强或减弱的证据，并结合价格结构、成交承接、生命周期和风险收益进行统一比较；当日收益排名仅为描述维度，不是资本效率排名。",
        "output_rule": "正式输出若研究证据变化能改变候选比较、机会状态、金额、持仓资本效率、0元主因或最大风险，应自然语言说明变化；无决策影响时不重复陈列全部指标。",
        "capital_efficiency_rule": "不生成综合资本效率分数或机械排名。不同ETF表达不同风险因子；下一单位资本效率由多维研究证据与MASTER完整决策链共同判断。",
        "master_feedback_rule": "当前研究证据可直接改善MASTER执行时的分析；只有经过MASTER第8.1规定审查的高质量专项研究，或多个真实CASE反复暴露的同类问题，才进入MASTER维护。机器不得自动修改MASTER。",
        "optimization_principle": "系统不追求完美交易系统。只保留能够改善事前收益效率、风险边界、执行质量或复盘学习的复杂度；在可接受风险范围内实现可实现收益最大化。",
        "read_only": True,
    }



def _snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in (snapshot.get("rows") if isinstance(snapshot, dict) else []) if isinstance(x, dict)]


def build_data_quality_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    out = {"pass_count": 0, "degraded_count": 0, "failed_count": 0, "stale_count": 0, "failed_objects": [], "degraded_objects": [], "stale_objects": [], "read_only": True}
    for row in _snapshot_rows(snapshot):
        code = str(row.get("symbol") or row.get("thscode") or "")
        label = f"{row.get('name')}（{code}）" if row.get("name") else code
        status = str(row.get("quality_status") or row.get("status") or "").upper()
        if status == "PASS": out["pass_count"] += 1
        elif status in {"FAILED", "FAIL"}: out["failed_count"] += 1; out["failed_objects"].append(label)
        elif status in {"DEGRADED", "PARTIAL"}: out["degraded_count"] += 1; out["degraded_objects"].append(label)
        if status == "STALE" or str(row.get("freshness") or "").upper() == "STALE": out["stale_count"] += 1; out["stale_objects"].append(label)
    out["source"] = "latest_snapshot_rows"
    return out


def build_analysis_coverage(root: Path, snapshot: dict[str, Any], account: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    universe = read_json(root / "config" / "market" / "etf_monitor_universe.json", {})
    etfs = {str(x.get("code")) for x in universe.get("objects", []) if x.get("code")}
    rows = _snapshot_rows(snapshot)
    by_code = {str(x.get("symbol") or ""): x for x in rows}
    stocks_ctx = read_json(root / "data" / "state" / "stock_market_context.json", {})
    positions = account.get("positions") or []
    held = {str(x.get("code")) for x in positions if x.get("asset_type") == "ETF" and float(x.get("quantity") or 0) > 0}
    stocks = {str(x.get("code")) for x in positions if x.get("asset_type") == "STOCK" and float(x.get("quantity") or 0) > 0}
    def usable(row): return bool(row) and str(row.get("quality_status") or row.get("status") or "").upper() not in {"FAILED", "FAIL"}
    observed = etfs - held
    missing = sorted((etfs | {"000001", "399006"}) - set(by_code))
    status = "INCOMPLETE" if missing else ("DEGRADED" if quality["failed_count"] or quality["degraded_count"] or quality["stale_count"] else "COMPLETE")
    stock_objects = stocks_ctx.get("objects") or {}
    core_rows = [x for x in rows if x.get("asset_class") in {"ETF", "A_SHARE_INDEX"}]
    core_pass = sum(str(x.get("quality_status") or "").upper() == "PASS" for x in core_rows)
    core_failed = sum(str(x.get("quality_status") or "").upper() in {"FAILED", "FAIL"} for x in core_rows)
    core_degraded = sum(str(x.get("quality_status") or "").upper() in {"DEGRADED", "PARTIAL"} for x in core_rows)
    account_stock_pass = sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() == "PASS" for x in stocks)
    account_stock_failed = sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() in {"FAILED", "FAIL"} for x in stocks)
    return {"indices_total": 2, "indices_available": sum(usable(x) for x in rows if x.get("asset_class") == "A_SHARE_INDEX"), "held_etfs_total": len(held), "held_etfs_available": sum(usable(by_code.get(x)) for x in held), "observed_etfs_total": len(observed), "observed_etfs_available": sum(usable(by_code.get(x)) for x in observed), "account_stocks_total": len(stocks), "account_stocks_available": sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() in {"PASS", "DEGRADED"} for x in stocks), "core_market_pass": core_pass, "core_market_failed": core_failed, "core_market_degraded": core_degraded, "account_stock_market_pass": account_stock_pass, "account_stock_market_failed": account_stock_failed, "cash_available": account.get("cash") is not None, "failed_objects": quality["failed_objects"], "degraded_objects": quality["degraded_objects"] + quality["stale_objects"], "missing_objects": missing, "coverage_status": status, "read_only": True}


def build_scheduled_pulse_health(root: Path, current: dict[str, Any]) -> dict[str, Any]:
    runtime = read_json(root / "data" / "state" / "runtime_health.json", {})
    value = runtime.get("scheduled_pulse_health")
    if isinstance(value, dict): return value
    scheduled = str(runtime.get("scheduled_for") or "")
    trigger = str(runtime.get("trigger_mode") or "").upper()
    observed = [scheduled] if scheduled and ("SCHEDULE" in trigger or scheduled.startswith("cron:")) else []
    return {"expected_slots": [], "observed_slots": observed, "missing_slots": [], "status": "WARNING" if current.get("market_date") and not observed else "UNKNOWN", "observation_basis": "仅当前runtime记录，未调用Actions API，无法区分cron未触发与触发后未产出。", "read_only": True}


def build_point_in_time_summary(current: dict[str, Any], account: dict[str, Any], snapshot: dict[str, Any], generated_at: str) -> dict[str, Any]:
    return {"account_fact_time": str(account.get("updated_at") or ""), "market_snapshot_time": str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or current.get("captured_at") or ""), "market_phase": snapshot.get("market_phase") or (current.get("data_freshness") or {}).get("market_phase") or "", "context_generated_time": generated_at, "source_separation": "账户事实时间、行情provider时间和context生成时间分别保留；截图时间不得冒充行情时间。", "read_only": True}


def build_formal_action_summary(account: dict[str, Any]) -> dict[str, Any]:
    value = account.get("formal_action") or account.get("last_formal_decision")
    if not isinstance(value, dict): return {"status": "NOT_PROVIDED", "execution_status": "UNKNOWN", "preserve_rule": "普通账户同步不擦除最近正式决策。", "read_only": True}
    return {**value, "read_only": True}


def build_etf_strategy_risk_metrics(root: Path) -> dict[str, Any]:
    """Build the compact risk summary used by decision/query contexts.

    Only etf_strategy_risk_pct defines the formal risk interval. The other
    two percentages are explanatory facts and must not become permissions.
    """
    equity = read_json(root / "data" / "state" / "etf_strategy_equity.json", {})
    summary = equity.get("summary") or {}
    account = read_account_fact(root)
    etf_float = sum(
        float(p.get("holding_pnl") or 0)
        for p in (account.get("positions") or [])
        if p.get("asset_type") == "ETF"
    )
    capital = float(summary.get("starting_etf_strategy_capital") or 200000)
    known_net = summary.get("known_net_current_strategy_return_pct")
    if known_net is None:
        pnl = summary.get("known_net_current_cumulative_pnl")
        known_net = (float(pnl) / capital * 100.0) if pnl is not None else None
    return {
        "etf_strategy_risk_pct": known_net,
        "etf_holding_unrealized_pct": round(etf_float / capital * 100.0, 2),
        "etf_drawdown_from_high_pct": summary.get("known_net_current_drawdown_pct"),
        "equity_data_quality": summary.get("known_net_equity_data_quality") or summary.get("equity_coverage_status"),
        "strategy_equity_known_net": summary.get("known_net_current_strategy_equity"),
        "high_watermark": summary.get("known_net_high_watermark", capital),
        "fee_status": summary.get("fee_status", ""),
        "interpretation": "唯一ETF策略风险率决定风险区间；持仓浮盈亏率与高水位回撤率只解释持仓压力和近期改善/恶化。",
        "read_only": True,
    }


def build_decision_context(root: Path | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current, account = read_current(root), read_account_fact(root)
    dashboard = root / "ETF当前状态_DASHBOARD.md"
    latest = current.get("latest_snapshot", "")
    snapshot = read_json(root / latest, {}) if latest else {}
    effective = evaluate_context_freshness(root, current)
    generated = now_utc()
    quality = build_data_quality_summary(snapshot)
    return {
        "generated_at": generated, "rules_version": "V2.2.16", "market_date": current.get("market_date", ""), "latest_node": current.get("latest_valid_node", ""), "current": current, "latest_snapshot": snapshot, "data_status": effective, "freshness_at_context_build": effective,
        "data_quality_summary": quality, "etf_strategy_risk_metrics": build_etf_strategy_risk_metrics(root), "analysis_coverage": build_analysis_coverage(root, snapshot, account, quality), "point_in_time": build_point_in_time_summary(current, account, snapshot, generated), "scheduled_pulse_health": build_scheduled_pulse_health(root, current), "formal_action": build_formal_action_summary(account),
        "decision_trigger": read_json(root / "data" / "state" / "decision_trigger.json", {"status": "NOT_BUILT", "requires_formal_reassessment": False, "read_only": True}),
        "capital_efficiency_ranking": read_json(root / "data" / "state" / "capital_efficiency_ranking.json", {"status": "NOT_BUILT", "ordered_candidates": [], "read_only": True}),
        "intraday_path_features": read_json(root / "data" / "state" / "intraday_path_features.json", {"status": "MISSING", "features": []}), "research_evidence": build_research_evidence_summary(root),
        "research_context_file": "data/state/research_context.json", "relative_strength_file": "data/state/relative_strength.json", "research_evidence_delta_file": "data/state/research_evidence_delta.json",
        "research_master_feedback": {"current_decision": "研究证据及其节点变化直接进入机会判断、统一资本比较、持仓资本效率与正式输出解释。", "master_maintenance": "研究结论只有通过MASTER第8.1正式研究转化机制后才可修改MASTER；自动程序只提供证据，不修改规则。"},
        "dashboard_source": str(dashboard.relative_to(root)).replace("\\", "/"), "dashboard_summary": {"maintenance_mode": "candidate_only", "automatic_overwrite": False, "automatic_trade_output": False}, "account_fact_status": account["status"], "needs_account_screenshot": account["status"] != "VALID",
        "interaction_boundary": "ChatGPT聊天负责账户截图与正式交易判断；本文件不生成交易动作。日内路径、研究证据及证据变化必须参与完整MASTER判断，但单独均不是交易信号。",
    }
