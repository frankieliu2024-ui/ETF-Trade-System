from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from build_stock_context import active_account_asset_codes
except ModuleNotFoundError:
    from scripts.build_stock_context import active_account_asset_codes

try:
    from runtime_self_heal import assess as reassess_runtime_health
except ModuleNotFoundError:
    from scripts.runtime_self_heal import assess as reassess_runtime_health

from confirmed_trade_facts import (
    canonical_etf_trade_facts,
    recover_canonical_etf_trade_facts,
    effective_confirmed_fee_fact,
    latest_formal_review_confirmed_fees,
    trade_signature,
)

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
STATE = ROOT / "data" / "state"
EQUITY = STATE / "etf_strategy_equity.json"
ACCOUNT = STATE / "account_fact.json"
CONSISTENCY = STATE / "system_consistency.json"


def consistency_path() -> Path:
    """Use the validated acceptance report when handed off; otherwise canonical state."""
    return Path(os.environ.get("ETF_CONSISTENCY_REPORT_PATH", str(CONSISTENCY)))
SELF_HEAL = STATE / "self_healing_status.json"
RUNTIME_HEALTH = STATE / "runtime_health.json"
CURRENT = STATE / "CURRENT.json"
MAINTENANCE = STATE / "maintenance_health.json"
DIAGNOSTIC = STATE / "maintenance_diagnostic.json"


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
    series = equity.get("series") or []
    canonical_replay = str(equity.get("schema_version") or "").startswith("1.0-canonical-replay") and bool(series)
    expected_trade_count = int(summary.get("trade_fact_count") or summary.get("trade_count") or 0)

    # FACT_ENRICHMENT_ONLY rows were historically persisted inside the replay
    # trade array even though they describe already-recorded economic executions.
    # During the one-way migration to canonical execution identity, allow only
    # the exact declared-count delta explained by those rows. Any other deficit
    # still uses the existing bounded formal-index recovery and remains fail-closed.
    enrichment_only_count = sum(
        1 for t in trades
        if str(t.get("replay_semantics") or "").upper() == "FACT_ENRICHMENT_ONLY"
    )
    trades_for_positions = canonical_etf_trade_facts(ROOT, trades)
    enrichment_count_migration = bool(
        expected_trade_count
        and expected_trade_count - len(trades_for_positions) == enrichment_only_count
        and enrichment_only_count > 0
    )
    if expected_trade_count and len(trades_for_positions) < expected_trade_count and not enrichment_count_migration:
        trades_for_positions = recover_canonical_etf_trade_facts(ROOT, trades, expected_trade_count)
    reconstructed_signatures = {
        trade_signature(t) for t in trades
        if str(t.get("replay_semantics") or "").upper() != "FACT_ENRICHMENT_ONLY"
    }
    overlay_events = [t for t in trades_for_positions if trade_signature(t) not in reconstructed_signatures]

    ledger_qty: dict[str, float] = defaultdict(float)
    position_basis = "AUXILIARY_EQUITY_RECONSTRUCTION_PLUS_EXECUTED_TRADE_EVENTS"
    current_replay_row = series[-1] if canonical_replay else {}
    replay_complete = bool(canonical_replay and str(current_replay_row.get("quality_status") or "").upper() == "COMPLETE")
    if canonical_replay:
        position_basis = "CANONICAL_REPLAY_FINAL_COMPLETE_POSITION_IDENTITY"
        for code, position in (current_replay_row.get("positions") or {}).items():
            ledger_qty[str(code)] = float((position or {}).get("quantity") or 0)
    else:
        for t in trades_for_positions:
            code = str(t.get("code") or "")
            qty = float(t.get("quantity") or 0)
            if not code:
                continue
            side = str(t.get("side") or t.get("action") or "").upper()
            if side == "BUY":
                ledger_qty[code] += qty
            elif side == "SELL":
                ledger_qty[code] -= qty

    account_qty: dict[str, float] = {}
    etf_codes = active_account_asset_codes(ROOT, account)["etf"]
    for p in account.get("positions") or []:
        code = str(p.get("code") or "")
        if code in etf_codes:
            account_qty[code] = float(p.get("quantity") or 0)

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

    fee_fact = effective_confirmed_fee_fact(ROOT, trades)
    reconstructed_fee_sum = rounded(fee_fact["reconstructed_confirmed_fee_sum"], 2)
    effective_fee_sum = rounded(fee_fact["effective_confirmed_fee_sum"], 2)
    summary_known_fees = rounded(summary.get("known_fees") or 0, 2)
    auxiliary_fee_ok = abs(effective_fee_sum - summary_known_fees) < 0.011

    formal_fee_fact = latest_formal_review_confirmed_fees(ROOT)
    formal_review_fee = rounded(formal_fee_fact["confirmed_etf_fees"], 2) if formal_fee_fact else None
    formal_review_fee_ok = formal_review_fee is None or abs(effective_fee_sum - formal_review_fee) < 0.011
    fee_ok = auxiliary_fee_ok and formal_review_fee_ok

    gross_equity = float(summary.get("current_gross_strategy_equity") or 0)
    if canonical_replay:
        strategy_cash = float(current_replay_row.get("cash") or 0)
        etf_mv = float(current_replay_row.get("market_value") or 0)
        row_equity = float(current_replay_row.get("strategy_equity_gross") or 0)
        equity_diff = rounded((strategy_cash + etf_mv) - gross_equity, 2)
        row_diff = rounded(row_equity - gross_equity, 2)
        equity_ok = replay_complete and abs(equity_diff) < 0.011 and abs(row_diff) < 0.011
    else:
        strategy_cash = float(summary.get("strategy_cash_current") or 0)
        etf_mv = float(summary.get("current_etf_market_value") or 0)
        equity_diff = rounded((strategy_cash + etf_mv) - gross_equity, 2)
        row_diff = None
        equity_ok = abs(equity_diff) < 0.011 and str(summary.get("equity_reconciliation_status") or "").startswith("RECONCILED")

    trade_count_ok = expected_trade_count == len(trades_for_positions) or enrichment_count_migration
    overall = quantity_ok and equity_ok and trade_count_ok
    return {
        "status": "PASS" if overall else "FAIL",
        "trade_count": len(trades_for_positions),
        "trade_count_matches_summary": trade_count_ok,
        "declared_trade_count": expected_trade_count,
        "fact_enrichment_only_count": enrichment_only_count,
        "trade_count_migration": "FACT_ENRICHMENT_ONLY_EXACT_DELTA" if enrichment_count_migration else None,
        "executed_trade_event_overlay_count": len(overlay_events),
        "position_ledger_basis": position_basis,
        "position_reconciliation": {"status": "PASS" if quantity_ok else "FAIL", "checks": quantity_checks},
        "known_fee_reconciliation": {
            "status": "PASS" if fee_ok else "WARNING",
            "blocking": False,
            "note": "fee attribution mismatch remains visible but cannot block reconciled account/trade/equity state",
            "auxiliary_reconstructed_confirmed_fee_sum": reconstructed_fee_sum,
            "auxiliary_summary_known_fees": summary_known_fees,
            "auxiliary_difference": rounded(effective_fee_sum - summary_known_fees, 2),
            "executed_event_overlay_count": int(fee_fact["executed_event_overlay_count"]),
            "executed_event_confirmed_fee_sum": rounded(fee_fact["executed_event_confirmed_fee_sum"], 2),
            "effective_confirmed_fee_sum": effective_fee_sum,
            "formal_review_confirmed_fees": formal_review_fee,
            "formal_review_source": formal_fee_fact.get("source") if formal_fee_fact else None,
            "formal_review_difference": rounded(effective_fee_sum - formal_review_fee, 2) if formal_review_fee is not None else None,
            "basis": "AUXILIARY_RECONSTRUCTION_PLUS_DEDUPED_EXECUTED_EVENT_OVERLAY_CROSSCHECKED_WITH_LATEST_FORMAL_REVIEW",
        },
        "gross_equity_reconciliation": {
            "status": "PASS" if equity_ok else "FAIL",
            "strategy_cash": strategy_cash,
            "current_etf_market_value": etf_mv,
            "reported_gross_equity": gross_equity,
            "difference": equity_diff,
            "series_row_difference": row_diff,
            "canonical_replay_complete": replay_complete if canonical_replay else None,
        },
    }


def main() -> int:
    now = datetime.now(TZ).isoformat(timespec="seconds")
    consistency = read_json(consistency_path(), {}) or {}
    persisted_self_heal = read_json(SELF_HEAL, {}) or {}
    self_heal = reassess_runtime_health()
    self_heal["reassessment_source"] = "runtime_self_heal.assess"
    if not isinstance(self_heal, dict) or not self_heal:
        self_heal = persisted_self_heal
    runtime = read_json(RUNTIME_HEALTH, {}) or {}
    current = read_json(CURRENT, {}) or {}
    rec = reconcile()

    consistency_status = str(consistency.get("status") or "").upper()
    hard_error_count = int(consistency.get("hard_error_count") or 0)
    consistency_ok = consistency_status == "PASS" or (consistency_status == "WARNING" and hard_error_count == 0)
    self_heal_class = str(self_heal.get("classification") or "UNKNOWN").upper()
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

    print(json.dumps(health, ensure_ascii=False))
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())