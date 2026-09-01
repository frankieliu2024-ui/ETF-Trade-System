"""Deterministic, read-only validation for historical market-fact recovery.

This module does not write CURRENT or manufacture an intraday observation.  It
classifies evidence before the existing market-data writer is allowed to
persist it, and keeps retrieval time separate from the fact's effective time.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


class RecoveryClass(StrEnum):
    RECOVERABLE_EXACT = "RECOVERABLE_EXACT"
    RECOVERABLE_EOD_EQUIVALENT = "RECOVERABLE_EOD_EQUIVALENT"
    PARTIALLY_RECOVERABLE = "PARTIALLY_RECOVERABLE"
    UNRECOVERABLE = "UNRECOVERABLE"


REQUIRED_BAR_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class RecoveryAssessment:
    classification: RecoveryClass
    target_market_date: str
    target_node: str
    coverage: int
    required_count: int
    missing_symbols: tuple[str, ...]
    invalid_symbols: tuple[str, ...]
    reason: str
    can_promote_to_current: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "target_market_date": self.target_market_date,
            "target_node": self.target_node,
            "coverage": self.coverage,
            "required_count": self.required_count,
            "missing_symbols": list(self.missing_symbols),
            "invalid_symbols": list(self.invalid_symbols),
            "reason": self.reason,
            "can_promote_to_current": self.can_promote_to_current,
        }


def _usable_bar(row: dict[str, Any], target_date: str) -> bool:
    if str(row.get("date") or row.get("market_date") or "") != target_date:
        return False
    if str(row.get("quality_status") or "PASS").upper() not in {"PASS", "FRESH", ""}:
        return False
    values = [row.get(field) for field in REQUIRED_BAR_FIELDS]
    if any(value is None for value in values):
        return False
    try:
        opening, high, low, close = (float(value) for value in values)
    except (TypeError, ValueError):
        return False
    return low <= min(opening, high, close) and high >= max(opening, low, close)


def assess_recovery(
    *,
    target_market_date: str,
    target_node: str,
    required_symbols: Iterable[str],
    rows: Iterable[dict[str, Any]],
    evidence_kind: str,
    original_provider_observation_available: bool = False,
) -> RecoveryAssessment:
    """Classify candidate facts without changing any persisted state.

    An exact class requires an explicit target-node/effective timestamp and an
    auditable original observation time.  A daily bar can only be EOD-equivalent
    and never supplies a fabricated intraday timestamp.
    """
    required = tuple(sorted({str(symbol).strip() for symbol in required_symbols if str(symbol).strip()}))
    by_symbol = {str(row.get("symbol") or row.get("thscode") or ""): row for row in rows}
    valid = tuple(symbol for symbol in required if _usable_bar(by_symbol.get(symbol, {}), target_market_date))
    missing = tuple(symbol for symbol in required if symbol not in by_symbol)
    invalid = tuple(symbol for symbol in required if symbol in by_symbol and symbol not in valid)
    coverage = len(valid)
    if not required or coverage == 0:
        classification = RecoveryClass.UNRECOVERABLE
        reason = "no complete, date-aligned candidate facts"
    elif coverage < len(required):
        classification = RecoveryClass.PARTIALLY_RECOVERABLE
        reason = "candidate coverage is incomplete"
    elif evidence_kind == "EXACT_INTRADAY" and original_provider_observation_available:
        classification = RecoveryClass.RECOVERABLE_EXACT
        reason = "complete target-node facts with original provider observation time"
    elif evidence_kind in {"EOD_BAR", "DAILY_BAR", "CLOSE_EQUIVALENT"}:
        classification = RecoveryClass.RECOVERABLE_EOD_EQUIVALENT
        reason = "complete date-aligned daily/close-equivalent facts; original observation time unavailable"
    else:
        classification = RecoveryClass.UNRECOVERABLE
        reason = "evidence kind cannot establish the requested target node"
    return RecoveryAssessment(
        classification=classification,
        target_market_date=target_market_date,
        target_node=target_node,
        coverage=coverage,
        required_count=len(required),
        missing_symbols=missing,
        invalid_symbols=invalid,
        reason=reason,
        can_promote_to_current=classification == RecoveryClass.RECOVERABLE_EXACT,
    )


def build_provenance(
    *,
    target_market_date: str,
    target_node: str,
    effective_market_time_beijing: str,
    historical_retrieved_at_beijing: str,
    provider: str,
    source_reference: str,
    recovery_reason: str,
    evidence_grade: str,
    coverage: dict[str, Any],
    original_provider_observation_time_beijing: str | None = None,
) -> dict[str, Any]:
    """Build auditable metadata; None means genuinely unavailable."""
    return {
        "target_market_date": target_market_date,
        "target_node": target_node,
        "effective_market_time_beijing": effective_market_time_beijing,
        "historical_retrieved_at_beijing": historical_retrieved_at_beijing,
        "original_provider_observation_time_beijing": original_provider_observation_time_beijing,
        "provider": provider,
        "source_reference": source_reference,
        "recovery_reason": recovery_reason,
        "evidence_grade": evidence_grade,
        "coverage": coverage,
        "timestamp_policy": "retrieval time and original observation time are distinct; unavailable observation time remains null",
        "read_only": True,
    }


def should_replace_current(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Prevent historical recovery from moving CURRENT backwards."""
    existing_date = str(existing.get("market_date") or "")
    candidate_date = str(candidate.get("market_date") or candidate.get("target_market_date") or "")
    if existing_date and candidate_date and candidate_date < existing_date:
        return False
    if str(candidate.get("recovery_classification") or "").upper() != RecoveryClass.RECOVERABLE_EXACT.value:
        return False
    return True

