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
    master_feedback = text("scripts/build_research_master_feedback.py")

    check(
        "research:decision_context_consumes_evidence",
        all(token in state_manager for token in ["build_research_evidence_summary", "research_evidence", "relative_strength.json", "use_in_current_decision"]),
        "central decision context must directly consume current research evidence",
    )
    check(
        "research:current_decision_boundary",
        "研究证据直接进入机会判断、统一资本比较、持仓资本效率与正式输出解释" in state_manager,
        "research evidence is a current-decision input but not a parallel trading entry",
    )
    check(
        "research:master_feedback_builder",
        all(token in master_feedback for token in ["RESEARCH_TO_MASTER_MAINTENANCE_FEED", "automatic_master_update", "promotion_gate", '"candidates": []']),
        "MASTER feedback feed exists and automatic MASTER modification is disabled",
    )
    check(
        "research:state_context_wiring",
        all(token in state_context for token in ["build_research_features", "build_research_master_feedback", "research_master_candidates.json", "decision_context.json"]),
        "research current-decision and MASTER-feedback artifacts are wired into state context",
    )
    check(
        "research:no_trade_authority",
        all(token in research_builder for token in ["decision_output_generated", "不自动修改MASTER", "不生成风险许可"]),
        "research builder remains read-only and cannot generate trading authority",
    )

    # Runtime artifacts are checked when present; absence before the first post-deploy state build is not a structural failure.
    research_context = load("data/state/research_context.json", None)
    if isinstance(research_context, dict):
        check("research:runtime_context_read_only", research_context.get("read_only") is True, "research_context must be read_only")
    relative = load("data/state/relative_strength.json", None)
    if isinstance(relative, dict):
        check("research:runtime_relative_read_only", relative.get("read_only") is True, "relative_strength must be read_only")
    candidates = load("data/state/research_master_candidates.json", None)
    if isinstance(candidates, dict):
        check(
            "research:runtime_master_candidates_no_auto_update",
            candidates.get("read_only") is True and candidates.get("automatic_master_update") is False,
            "research MASTER candidate feed must remain read-only with automatic update disabled",
        )

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
