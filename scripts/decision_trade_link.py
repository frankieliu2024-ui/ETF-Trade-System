from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Canonical completion inputs may carry a Beijing timestamp without an
        # explicit offset. Normalize it before PIT comparison; naive/aware
        # comparison must never crash the consistency gate.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed
    except ValueError:
        return None


def _decision_mentions_trade(event: dict, trade: dict) -> bool:
    formal = event.get("formal_decision") or {}
    binding = event.get("presentation_binding") or {}
    text = json.dumps(formal, ensure_ascii=False) + " " + str(binding.get("full_content") or event.get("final_content") or "")
    code = str(trade.get("code") or "")
    name = str(trade.get("name") or "")
    if code and code not in text and name and name not in text:
        return False
    side = str(trade.get("side") or "").upper()
    if side in {"SELL", "S", "卖", "卖出"} or "卖" in side:
        return any(k in text for k in ("卖出", "退出", "降低风险", "减持", "释放"))
    if side in {"BUY", "B", "买", "买入"} or "买" in side:
        return any(k in text for k in ("买入", "Trial", "Confirm", "新增"))
    return True


def _effective_time(event: dict) -> datetime | None:
    """Return exact business-effective decision time when available."""
    return _time(event.get("decision_effective_at_beijing") or event.get("issued_at_beijing") or event.get("decision_time_beijing") or event.get("reviewed_at_beijing") or event.get("recorded_at_beijing"))


def _load_attribution_correction(root: Path, trade: dict) -> tuple[str, dict, str]:
    event_id = str(trade.get("event_id") or "").strip()
    request_dir = root / "requests" / "trade_fact_correction"
    matches = []
    for path in sorted(request_dir.glob("*.json")) if request_dir.exists() else []:
        obj = _load(path)
        if str(obj.get("correction_type") or "").upper() == "DECISION_ATTRIBUTION" and str(obj.get("trade_event_id") or "") == event_id and str(obj.get("status") or "").upper() in {"APPLIED", "RECONCILED"}:
            matches.append(obj)
    ids = {str(x.get("linked_decision_id") or "") for x in matches} - {""}
    if not matches: return "", {}, "NO_CANONICAL_CORRECTION"
    if len(matches) != 1 or len(ids) != 1: return "", {}, "CORRECTION_CONFLICT"
    return next(iter(ids)), matches[0], "CANONICAL_CORRECTION"



def _completion_candidates(root: Path, trade: dict, trade_time: datetime) -> list[tuple[datetime, str, dict]]:
    """Read existing Formal Decision completions as decision evidence.

    A completion is not a second decision object. It is the already-frozen
    business decision record produced by the task. This fallback is used only
    when no canonical events/decisions object exists, and only for a PIT-valid
    completion that explicitly names the traded code and compatible action.
    """
    completion_dir = root / "requests" / "live_snapshot"
    code = str(trade.get("code") or "").strip()
    name = str(trade.get("name") or "").strip()
    side = str(trade.get("side") or "").upper()
    if not code or not completion_dir.exists():
        return []
    candidates: list[tuple[datetime, str, dict]] = []
    for path in sorted(completion_dir.glob("*__scheduled_review_completion.json")):
        completion = _load(path)
        if (
            str(completion.get("task_name") or completion.get("task_id") or "").strip() != "ETF正式决策"
            or (completion.get("report_type") and str(completion.get("report_type") or "").upper() != "ETF_FORMAL_DECISION")
        ):
            continue
        decision_time = _time(completion.get("requested_at_beijing"))
        if decision_time is None or decision_time > trade_time:
            continue
        content = str(completion.get("final_content") or "")
        if code not in content and (not name or name not in content):
            continue
        if side in {"BUY", "B", "买", "买入"} or "买" in side:
            if not any(token in content for token in ("Trial", "Confirm", "新增部署", "买入")):
                continue
        elif side in {"SELL", "S", "卖", "卖出"} or "卖" in side:
            if not any(token in content for token in ("减持", "退出", "释放", "卖出", "降低风险")):
                continue
        proxy_id = f"FORMAL_COMPLETION:{completion.get('request_id') or path.stem}"
        proxy = {
            "event_type": "FORMAL_DECISION_COMPLETION_EVIDENCE",
            "decision_id": proxy_id,
            "request_id": completion.get("request_id"),
            "parent_request_id": completion.get("parent_request_id"),
            "task_id": completion.get("task_id"),
            "report_type": completion.get("report_type"),
            "decision_effective_at_beijing": completion.get("requested_at_beijing"),
            "issued_at_beijing": completion.get("requested_at_beijing"),
            "recorded_at_beijing": completion.get("requested_at_beijing"),
            "decision_effective_ordering": "BEFORE_EXECUTION",
            "timing_quality": "USER_CONFIRMED_BOUNDED",
            "candidate_code": code,
            "candidate_name": name,
            "formal_decision": {
                "completion_request_id": completion.get("request_id"),
                "final_content": content,
                "source": completion.get("source"),
            },
            "attribution_source": str(path.relative_to(root)).replace("\\", "/"),
        }
        candidates.append((decision_time, proxy_id, proxy))
    return candidates


def resolve_link(root: Path, trade: dict, requested_id: str = "") -> tuple[str, dict, str]:
    """Return a decision no later than the confirmed trade time.

    Explicit links are kept only when PIT-valid. Otherwise choose the latest prior
    formal decision that actually mentions the traded object and compatible action.
    Never link a trade to a later confirmation event.
    """
    trade_time = _time(trade.get("confirmed_at_beijing"))
    decision_dir = root / "events" / "decisions"
    if trade_time is None or not decision_dir.exists():
        return "", {}, "NO_VALID_TRADE_TIME"

    corrected_id, _correction, correction_status = _load_attribution_correction(root, trade)
    if correction_status == "CORRECTION_CONFLICT":
        return "", {}, correction_status
    if corrected_id:
        decision = _load(decision_dir / f"{corrected_id}.json")
        if not decision:
            # Canonical scheduled Formal Decisions are owned by the review
            # event store.  Attribution corrections may therefore point to a
            # reviewed decision rather than an older events/decisions file.
            for candidate in sorted((root / "events" / "reviews").glob("formal_*.json")):
                obj = _load(candidate)
                if str(obj.get("decision_id") or "") == corrected_id:
                    decision = obj
                    break
        dt = _effective_time(decision)
        tt = _time(trade.get("confirmed_at_beijing") or trade.get("executed_at_beijing"))
        if not decision or not dt or not tt or dt >= tt:
            return "", {}, "CORRECTION_PIT_INVALID"
        if not _decision_mentions_trade(decision, trade):
            return "", {}, "CORRECTION_ACTION_MISMATCH"
        return corrected_id, decision, correction_status
    requested_id = str(requested_id or "")
    if requested_id:
        path = decision_dir / f"{requested_id}.json"
        event = _load(path) if path.exists() else {}
        decision_time = _effective_time(event)
        recorded_time = _time(event.get("recorded_at_beijing"))
        if event and _decision_mentions_trade(event, trade):
            if decision_time and decision_time <= trade_time:
                return requested_id, event, "EXPLICIT_PIT_VALID"
            if (
                recorded_time and recorded_time >= trade_time
                and str(event.get("decision_effective_ordering") or "").upper() == "BEFORE_EXECUTION"
                and str(event.get("timing_quality") or "").upper() in {"USER_CONFIRMED_BOUNDED", "USER_CONFIRMED"}
            ):
                return requested_id, event, "EXPLICIT_ASYNC_CANONICALIZATION"

    candidates: list[tuple[datetime, str, dict]] = []
    for path in decision_dir.glob("*.json"):
        event = _load(path)
        decision_time = _effective_time(event)
        if decision_time is None or decision_time > trade_time:
            continue
        if not _decision_mentions_trade(event, trade):
            continue
        candidates.append((decision_time, str(event.get("decision_id") or path.stem), event))
    if candidates:
        _, decision_id, event = max(candidates, key=lambda x: x[0])
        return decision_id, event, "AUTO_PRIOR_MATCH"

    completion_candidates = _completion_candidates(root, trade, trade_time)
    if completion_candidates:
        _, decision_id, event = max(completion_candidates, key=lambda x: x[0])
        return decision_id, event, "CANONICAL_COMPLETION_PIT_MATCH"
    return "", {}, "NO_PRIOR_MATCHING_DECISION"


def decision_price_for_trade(event: dict, trade: dict):
    """Return only an action-object price, never a comparison candidate price."""
    code = str(trade.get("code") or "")
    if str(event.get("candidate_code") or "") == code:
        price = event.get("price_at_decision")
        try:
            if price is not None:
                return float(price)
        except (TypeError, ValueError):
            pass

    # A multi-action decision may carry an explicit per-action price.  The
    # comparison snapshot is evidence for candidate selection, not a traded
    # object's decision price and must never be used for slippage attribution.
    formal = event.get("formal_decision") or {}
    actions = formal.get("actions") or formal.get("action_objects") or []
    if isinstance(actions, dict):
        actions = list(actions.values())
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            continue
        action_code = str(action.get("code") or action.get("security_code") or "").strip()
        if action_code != code:
            continue
        price = action.get("decision_price") or action.get("price_at_decision") or action.get("price")
        try:
            if price is not None:
                return float(price)
        except (TypeError, ValueError):
            pass
    return None
