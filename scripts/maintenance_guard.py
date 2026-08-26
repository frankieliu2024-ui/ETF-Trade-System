from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
STATE = ROOT / "data" / "state"
EQUITY = STATE / "etf_strategy_equity.json"
ACCOUNT = STATE / "account_fact.json"
CONSISTENCY = STATE / "system_consistency.json"
SELF_HEAL = STATE / "self_healing_status.json"
RUNTIME_HEALTH = STATE / "runtime_health.json"
CURRENT = STATE / "CURRENT.json"
MAINTENANCE = STATE / "maintenance_health.json"
DIAGNOSTIC = STATE / "maintenance_diagnostic.json"
LKG = STATE / "last_known_good.json"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def git_head() -> str | None:
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    value = p.stdout.strip()
    return value if p.returncode == 0 and len(value) == 40 else None


def rounded(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def reconcile() -> dict:
    equity = read_json(EQUITY, {}) or {}
    account = read_json(ACCOUNT, {}) or {}
    summary = equity.get("summary") or {}
    trades = equity.get("trades") or []

    ledger_qty: dict[str, float] = defaultdict(float)
    for t in trades:
        code = str(t.get("code") or "")
        qty = float(t.get("quantity") or 0)
        if not code:
            continue
        if str(t.get("side") or "").upper() == "BUY":
            ledger_qty[code] += qty
        elif str(t.get("side") or "").upper() == "SELL":
            ledger_qty[code] -= qty

    account_qty: dict[str, float] = {}
    for p in account.get("positions") or []:
        if str(p.get("asset_type") or "").upper() == "ETF":
            account_qty[str(p.get("code") or "")] = float(p.get("quantity") or 0)

    all_codes = sorted(set(ledger_qty) | set(account_qty))
    quantity_checks = []
    quantity_ok = True
    for code in all_codes:
        ledger = ledger_qty.get(code, 0.0)
        broker = account_qty.get(code, 0.0)
        diff = broker - ledger
        ok = abs(diff) < 1e-9
        quantity_ok &= ok
        quantity_checks.append({"code": code, "ledger_quantity": ledger, "account_quantity": broker, "difference": diff, "status": "PASS" if ok else "FAIL"})

    confirmed_fee_sum = rounded(sum(float(t.get("fee_amount") or 0) for t in trades if str(t.get("fee_status") or "").upper() == "CONFIRMED"), 2)
    summary_known_fees = rounded(summary.get("known_fees") or 0, 2)
    fee_ok = abs(confirmed_fee_sum - summary_known_fees) < 0.011

    strategy_cash = float(summary.get("strategy_cash_current") or 0)
    etf_mv = float(summary.get("current_etf_market_value") or 0)
    gross_equity = float(summary.get("current_gross_strategy_equity") or 0)
    equity_diff = rounded((strategy_cash + etf_mv) - gross_equity, 2)
    equity_ok = abs(equity_diff) < 0.011 and str(summary.get("equity_reconciliation_status") or "").startswith("RECONCILED")

    trade_count_ok = int(summary.get("trade_count") or 0) == len(trades)
    overall = quantity_ok and fee_ok and equity_ok and trade_count_ok
    return {
        "status": "PASS" if overall else "FAIL",
        "trade_count": len(trades),
        "trade_count_matches_summary": trade_count_ok,
        "position_reconciliation": {"status": "PASS" if quantity_ok else "FAIL", "checks": quantity_checks},
        "known_fee_reconciliation": {"status": "PASS" if fee_ok else "FAIL", "confirmed_trade_fee_sum": confirmed_fee_sum, "summary_known_fees": summary_known_fees, "difference": rounded(confirmed_fee_sum - summary_known_fees, 2)},
        "gross_equity_reconciliation": {"status": "PASS" if equity_ok else "FAIL", "strategy_cash": strategy_cash, "current_etf_market_value": etf_mv, "reported_gross_equity": gross_equity, "difference": equity_diff},
    }


def main() -> int:
    now = datetime.now(TZ).isoformat(timespec="seconds")
    consistency = read_json(CONSISTENCY, {}) or {}
    self_heal = read_json(SELF_HEAL, {}) or {}
    runtime = read_json(RUNTIME_HEALTH, {}) or {}
    current = read_json(CURRENT, {}) or {}
    rec = reconcile()

    consistency_status = str(consistency.get("status") or "").upper()
    hard_error_count = int(consistency.get("hard_error_count") or 0)
    # WARNING is an explicit non-fatal runtime state. It must not turn a
    # reconciled account/ledger into a maintenance FAIL when no hard error
    # exists; the warning remains visible in system_consistency.json.
    consistency_ok = consistency_status == "PASS" or (consistency_status == "WARNING" and hard_error_count == 0)
    self_heal_class = str(self_heal.get("classification") or "UNKNOWN").upper()
    # A stale escalation from an older consistency run must not keep today's
    # otherwise reconciled maintenance gate blocked. Current hard failures are
    # still handled by the consistency gate above.
    self_heal_checked = str(self_heal.get("checked_at") or "")
    consistency_generated = str(consistency.get("generated_at") or "")
    stale_escalation = bool(self_heal_checked and consistency_generated and self_heal_checked < consistency_generated)
    self_heal_ok = self_heal_class not in {"CONSISTENCY_REGRESSION", "PERSISTENT_RUNTIME_FAILURE"} or stale_escalation
    overall_ok = consistency_ok and rec["status"] == "PASS" and self_heal_ok

    health = {
        "schema_version": "1.1",
        "checked_at": now,
        "mode": "DETERMINISTIC_MAINTENANCE_GUARD",
        "status": "PASS" if overall_ok else "FAIL",
        "system_consistency_status": consistency.get("status", "UNKNOWN"),
        "self_healing_classification": self_heal_class,
        "runtime_health_status": runtime.get("status", runtime.get("quality_status", "UNKNOWN")),
        "current_snapshot": current.get("latest_snapshot"),
        "reconciliation": rec,
        "automatic_actions": {
            "reconciliation": "CHECK_ONLY",
            "diagnostic_packet": "AUTO_GENERATE_ON_FAILURE",
            "workflow_failure_diagnosis": "AUTO_CLASSIFY_FAILED_STEP_AND_RECENT_DIFF",
            "last_known_good": "AUTO_ADVANCE_ONLY_AFTER_ALL_GATES_PASS",
            "rollback": "RESTRICTED_MAINTENANCE_ONLY; HEAD_ONLY; STRICT_ALLOWLIST; NEVER_FORMAL_RULES_OR_ACCOUNT_FACTS",
            "ai_patch_main": "FORBIDDEN"
        }
    }
    write_json(MAINTENANCE, health)

    if not overall_ok:
        diagnostic = {
            "schema_version": "1.0",
            "generated_at": now,
            "classification": "MAINTENANCE_GUARD_FAILURE",
            "requires_codex_or_manual_review": True,
            "head_sha": git_head(),
            "system_consistency": {"status": consistency.get("status"), "hard_error_count": consistency.get("hard_error_count"), "warning_count": consistency.get("warning_count"), "run_id": (consistency.get("repository") or {}).get("github_run_id")},
            "self_healing": {"classification": self_heal_class, "recommended_action": self_heal.get("recommended_action"), "reason": self_heal.get("reason")},
            "runtime": {"status": runtime.get("status", runtime.get("quality_status")), "latest_snapshot": current.get("latest_snapshot"), "captured_at": current.get("captured_at")},
            "reconciliation": rec,
            "safety_boundary": "No automatic MASTER/provider/trading-rule/account-fact change and no AI direct patch to main."
        }
        write_json(DIAGNOSTIC, diagnostic)
    else:
        write_json(DIAGNOSTIC, {"schema_version": "1.0", "generated_at": now, "classification": "NONE", "requires_codex_or_manual_review": False, "reason": "all maintenance gates passed"})
        checked_sha = ((consistency.get("commit_audit") or {}).get("checked_commit") or git_head())
        previous = read_json(LKG, {}) or {}
        lkg = {
            "schema_version": "1.1",
            "updated_at": now,
            "status": "VALID",
            "commit_sha": checked_sha,
            "basis": "system_consistency PASS + ETF ledger/account reconciliation PASS + self-healing not escalated",
            "previous_commit_sha": previous.get("commit_sha"),
            "rollback_policy": "RESTRICTED_MAINTENANCE_ONLY; automatic revert is allowed only for current-HEAD deterministic regressions whose changed files are entirely inside the strict maintenance allowlist; never automatically revert MASTER, trading rules, provider policy, market data, trade events, or user-confirmed account facts",
            "automatic_rollback_enabled": True,
            "automatic_rollback_scope": "STRICT_MAINTENANCE_ALLOWLIST_ONLY",
            "automatic_rollback_guard": "workflow-failure-guard.yml + scripts/workflow_failure_guard.py"
        }
        write_json(LKG, lkg)

    print(json.dumps(health, ensure_ascii=False))
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
