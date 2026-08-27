from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/state/system_consistency.json"


def text(path: str) -> str:
    p = ROOT / path
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load(path: str, fallback):
    p = ROOT / path
    if not p.exists():
        return fallback
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def main() -> int:
    report = load("data/state/system_consistency.json", {})
    checks = list(report.get("checks") or [])
    errors = list(report.get("errors") or [])
    warnings = list(report.get("warnings") or [])

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    state_manager = text("scripts/state_manager.py")
    state_context = text("scripts/build_state_context.py")
    research_builder = text("scripts/build_research_features.py")
    evidence_delta = text("scripts/build_research_evidence_delta.py")
    master_feedback = text("scripts/build_research_master_feedback.py")
    state_sync = text("scripts/process_state_sync_request.py")
    execution_quality = text("scripts/build_execution_quality.py")
    execution_bridge = text("scripts/build_research_execution_bridge.py")
    backfill = text("scripts/backfill_research_daily_history.py")
    backfill_workflow = text(".github/workflows/research-backfill.yml")

    check(
        "research:decision_context_consumes_evidence",
        all(token in state_manager for token in ["build_research_evidence_summary", "research_evidence", "relative_strength.json", "use_in_current_decision"]),
        "central decision context must directly consume current research evidence",
    )
    check(
        "research:evidence_delta_consumed",
        all(token in state_manager for token in ["research_evidence_delta.json", "evidence_delta", "prior_research_as_of_beijing"]),
        "decision context must consume changes versus the prior research node, not only static levels",
    )
    check(
        "research:no_composite_capital_efficiency_score",
        "capital_efficiency_score\": None" in state_manager and "不生成综合资本效率分数" in state_manager,
        "capital efficiency remains a multidimensional MASTER judgment rather than a hidden score",
    )
    check(
        "research:current_decision_boundary",
        "研究证据及其节点变化直接进入机会判断、统一资本比较、持仓资本效率与正式输出解释" in state_manager,
        "research evidence is a current-decision input but not a parallel trading entry",
    )
    check(
        "research:master_feedback_builder",
        all(token in master_feedback for token in ["RESEARCH_TO_MASTER_MAINTENANCE_FEED", "automatic_master_update", "promotion_gate", '"candidates": []']),
        "MASTER feedback feed exists and automatic MASTER modification is disabled",
    )
    check(
        "research:state_context_wiring",
        all(token in state_context for token in ["build_research_features", "build_research_evidence_delta", "build_execution_quality", "build_research_master_feedback", "research_master_candidates.json", "decision_context.json"]),
        "research evidence, delta, execution attribution and MASTER-feedback artifacts are wired into state context",
    )
    check(
        "research:no_trade_authority",
        all(token in research_builder for token in ["decision_output_generated", "不自动修改MASTER", "不生成风险许可"]),
        "research builder remains read-only and cannot generate trading authority",
    )
    check(
        "research:execution_bridge_wired",
        "build_research_execution_bridge" in state_context and all(token in execution_bridge for token in ["RESEARCH_TO_EXECUTION_READ_ONLY_BRIDGE", "research_conclusions_must_be_synthesized", "ipo_base_stock_evidence", "stock_buy_capital_source_rule", "stock_sell_destination_rule"]),
        "research conclusions must be synthesized into execution evidence covering IPO base stocks and capital source/destination without creating orders",
    )
    check(
        "research:execution_bridge_no_auto_trade",
        all(token in execution_bridge for token in ["automatic_trade\": False", "trade_signal\": None", "separate_decisions_rule"]),
        "research-to-execution bridge must remain read-only and must not auto trade or mechanically rotate capital",
    )
    check(
        "research:master_ipo_base_capital_bridge",
        all(token in text("ETF规则_MASTER.md") for token in ["研究结论先归纳", "打新底仓个股出现独立、可证伪", "卖出后资金去向", "研究证据可以直接改变"]),
        "MASTER must explicitly connect synthesized research evidence to IPO base stock capital-source and capital-destination decisions",
    )
    check(
        "research:point_in_time_decision_price",
        all(token in state_sync for token in ["select_point_in_time_snapshot", "captured <= cutoff", "NO_PRIOR_SNAPSHOT", "POINT_IN_TIME_SNAPSHOT"]),
        "formal decision price and comparison evidence must not use a snapshot after decision time",
    )
    check(
        "research:decision_comparison_snapshot",
        all(token in state_sync for token in ["build_comparison_snapshot", "comparison_snapshot", "descriptive_daily_return_rank", "不是资本效率评分"]),
        "formal decisions preserve the full ETF cross-section for later selection-alpha review",
    )
    check(
        "research:hypothesis_lifecycle_link",
        all(token in state_sync for token in ["resolve_hypothesis_id", "hypothesis_id", "CARRY_FORWARD_PRIOR", "hypothesis_closed"]),
        "Trial/Confirm/holding decisions can be linked by hypothesis without creating a new trading lifecycle label",
    )
    check(
        "research:execution_attribution",
        all(token in state_sync for token in ["execution_attribution", "adverse_execution_cost_pct", "decision_to_execution_seconds"]) and "DECISION_EXECUTION_ATTRIBUTION" in execution_quality,
        "decision quality and execution quality are separated and attributable",
    )
    check(
        "research:evidence_delta_builder",
        all(token in evidence_delta for token in ["RESEARCH_EVIDENCE_DELTA", "IMPROVED", "WEAKENED", "UNCHANGED", "capital_efficiency_score"]),
        "research layer explicitly tracks what changed between decision nodes without a composite score",
    )
    check(
        "research:historical_backfill_boundary",
        all(token in backfill for token in ["historical_backfill", "historical_decision_prohibited", "不伪造历史ChatGPT决策"]) and "requests/research_backfill" in backfill_workflow,
        "historical ETF facts can be backfilled without manufacturing historical decisions",
    )
    check(
        "research:optimization_not_perfection",
        "不追求完美" in state_manager and all(token in state_context for token in ["不打造完美交易系统", "事前收益效率"]),
        "research complexity is retained only when it improves ex-ante return efficiency or another explicit system objective, not for perfection-seeking",
    )

    research_context = load("data/state/research_context.json", None)
    if isinstance(research_context, dict):
        check("research:runtime_context_read_only", research_context.get("read_only") is True, "research_context must be read_only")
    relative = load("data/state/relative_strength.json", None)
    if isinstance(relative, dict):
        check("research:runtime_relative_read_only", relative.get("read_only") is True, "relative_strength must be read_only")
    execution_summary = load("data/state/research_execution_summary.json", None)
    if isinstance(execution_summary, dict):
        check("research:runtime_execution_summary_read_only", execution_summary.get("read_only") is True and execution_summary.get("automatic_trade") is False and execution_summary.get("trade_signal") is None, "research_execution_summary must be read-only, decision-usable evidence without automatic trading")
    candidates = load("data/state/research_master_candidates.json", None)
    if isinstance(candidates, dict):
        check("research:runtime_master_candidates_no_auto_update", candidates.get("read_only") is True and candidates.get("automatic_master_update") is False, "research MASTER candidate feed must remain read-only with automatic update disabled")

    report["checks"] = checks
    report["errors"] = errors
    report["warnings"] = warnings
    report["hard_error_count"] = len(errors)
    report["warning_count"] = len(warnings)
    report["status"] = "FAIL" if errors else ("WARNING" if warnings else "PASS")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "total_checks": len(checks), "hard_error_count": len(errors)}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
