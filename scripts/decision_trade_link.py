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
        return any(k in text for k in ("买入", "Tr…53091 tokens truncated…10] for line in rows]
    last_date = max(dates) if dates else ""
    pending = "无" if projection["pending_fee_count"] == 0 else f"{projection['pending_fee_count']}笔"
    sentence = f"截至{last_date}累计已确认ETF费用{projection['effective_confirmed_fee_sum']:.2f}元；待确认费用：{pending}。"
    return re.sub(r"截至.*?。", sentence, text, count=1)

def write_trade_review_required(event: dict) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    path = ROOT / "events" / "research" / "decision_outcomes" / f"TRADE_COMPLETED_REVIEW_REQUIRED_{event_id}.json"
    if path.exists():
        return
    atomic_json_write(path, {
        "event_type": "TRADE_COMPLETED_REVIEW_REQUIRED",
        "event_id": event_id,
        "notification_id": event.get("notification_id"),
        "related_decision_id": event.get("linked_decision_id"),
        "execution_date": event.get("execution_date"),
        "confirmation_date": event.get("confirmation_date"),
        "security_code": event.get("code"),
        "security_name": event.get("name"),
        "side": event.get("side"),
        "quantity": event.get("quantity"),
        "price": event.get("price"),
        "amount": event.get("amount"),
        "review_status": "PENDING_FORMAL_REVIEW",
        "objective_fields": ["trade_event", "strategy_risk_rate_change", "cash_change", "next_observation_point"],
        "safety_boundary": "只生成客观复盘待办，不自动判断交易正确/错误，不修改MASTER，不生成新的交易动作。",
    })


def _explicit_fact_type(request: dict) -> str:
    return str(
        request.get("formal_fact_type")
        or request.get("fact_type")
        or request.get("ingress_intent")
        or ""
    ).strip().upper()



def _account_fact_trade_is_executable(trade: dict) -> bool:
    """Recognize an executed security row explicitly present in a broker statement.

    Account screenshots may also carry non-ETF account transactions such as
    convertible-bond allotment/payment rows.  Those remain account facts and
    must not be promoted into the ETF execution-event stream.  No trade is
    inferred from balances, positions, or cash deltas.
    """
    if not isinstance(trade, dict):
        return False
    code = str(trade.get("code") or trade.get("security_code") or "").strip()
    side = str(trade.get("side") or trade.get("operation") or "").strip().upper()
    execution_time = (
        trade.get("executed_at")
        or trade.get("executed_at_beijing")
        or trade.get("trade_time")
        or trade.get("trade_time_beijing")
    )
    asset_marker = " ".join(
        str(trade.get(key) or "").strip().upper()
        for key in ("asset_type", "security_type", "trade_type")
    )
    non_execution_markers = ("BOND", "债", "配售", "申购", "中签", "缴款")
    if any(marker in asset_marker for marker in non_execution_markers):
        return False
    valid_side = side in {"BUY", "SELL", "B", "S", "买", "卖", "买入", "卖出"} or "买" in side or "卖" in side
    return bool(
        re.fullmatch(r"\d{6}", code)
        and valid_side
        and trade.get("quantity") is not None
        and trade.get("price") is not None
        and execution_time
    )


def _request_trade_events(request: dict) -> list[dict]:
    """Normalize all explicit broker trade ingress shapes.

    Top-level trade_event/trade_events remain the preferred canonical shape.
    When a broker screenshot request also carries transaction rows under the
    canonical account_fact.trades field, merge only rows with explicit
    execution evidence into the same event path.  Exact duplicates are
    suppressed; distinct statement rows are never discarded.  This closes the
    ingress gap without inferring trades from account balances or positions.
    """
    explicit: list[dict] = []
    plural = request.get("trade_events")
    if isinstance(plural, list) and plural and all(isinstance(item, dict) for item in plural):
        explicit.extend(plural)
    elif "trade_events" in request:
        return []
    singular = request.get("trade_event")
    if isinstance(singular, dict):
        explicit.append(singular)

    account_fact = request.get("account_fact")
    statement_trades = account_fact.get("trades") if isinstance(account_fact, dict) else None
    # Broker screenshot envelopes historically used confirmed_trades for
    # executed rows while the account-fact schema uses trades. Both are
    # explicit broker evidence; normalize them into the same canonical
    # execution-event path and let the existing idempotency key suppress
    # duplicates on replay.
    confirmed_trades = account_fact.get("confirmed_trades") if isinstance(account_fact, dict) else None
    if isinstance(confirmed_trades, list) and confirmed_trades:
        statement_trades = list(statement_trades or []) + list(confirmed_trades)
    if not isinstance(statement_trades, list) or not statement_trades:
        return explicit

    screenshot_confirmed_at = (
        request.get("screenshot_captured_at_beijing")
        or request.get("account_updated_at")
        or account_fact.get("account_screenshot_captured_at_beijing")
        or account_fact.get("updated_at")
        or request.get("requested_at_beijing")
    )
    source = account_fact.get("source") or request.get("source") or "BROKER_SCREENSHOT"

    def same_trade(left: dict, right: dict) -> bool:
        left_code = str(left.get("code") or left.get("security_code") or "").strip()
        right_code = str(right.get("code") or right.get("security_code") or "").strip()
        left_side = str(left.get("side") or left.get("action") or "").upper().strip()
        right_side = str(right.get("side") or right.get("action") or "").upper().strip()
        if not left_code or left_code != right_code or not left_side or left_side != right_side:
            return False
        if str(left.get("quantity") or "") != str(right.get("quantity") or ""):
            return False
        if str(left.get("price") or "") != str(right.get("price") or ""):
            return False
        left_time = str(left.get("executed_at") or left.get("executed_at_beijing") or "").strip()
        right_time = str(right.get("executed_at") or right.get("executed_at_beijing") or "").strip()
        return not left_time or not right_time or left_time == right_time

    normalized = list(explicit)
    for raw in statement_trades:
        if not _account_fact_trade_is_executable(raw):
            continue
        trade = dict(raw)
        if not trade.get("code") and trade.get("security_code"):
            trade["code"] = trade["security_code"]
        if not trade.get("name") and trade.get("security_name"):
            trade["name"] = trade["security_name"]
        if not trade.get("side") and trade.get("operation"):
            operation = str(trade["operation"]).strip()
            trade["side"] = "BUY" if "买" in operation else "SELL" if "卖" in operation else operation
        if not trade.get("executed_at") and trade.get("executed_at_beijing"):
            trade["executed_at"] = trade["executed_at_beijing"]
        if not trade.get("confirmed_at_beijing") and screenshot_confirmed_at:
            trade["confirmed_at_beijing"] = str(screenshot_confirmed_at)
        trade.setdefault("source", source)
        if not any(same_trade(trade, existing) for existing in normalized):
            normalized.append(trade)
    return normalized

def _trade_event_id(request: dict, trade: dict, idempotency_key: str, ordinal: int, total: int) -> str:
    """Use stable, collision-resistant IDs when a batch omits explicit event IDs."""
    explicit = str(trade.get("event_id") or "").strip()
    if explicit:
        return explicit
    request_id = str(request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
    if total == 1:
        return request_id
    suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"{request_id}_{ordinal + 1}_{suffix}"

# Formal Decision completion projection accepts current-main runtime completions.

def _validate_formal_completion_identity(request: dict) -> None:
    """Validate live Formal Decision completions without assuming one transport label.

    The direct task writer may omit request_type and may carry only final_content
    plus identity/hash metadata in presentation_binding.  The frozen final_content
    is the canonical body; an optional duplicated binding body, when present,
    must match it exactly.
    """
    request_type = str(request.get("request_type") or "").strip().upper()
    if request_type not in {"", "SCHEDULED_REVIEW_COMPLETION"}:
        return
    if str(request.get("task_id") or "").strip() != "ETF正式决策":
        return
    if str(request.get("formal_fact_type") or "").strip().upper() not in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}:
        return
    if str(request.get("interaction_scenario") or "").strip().upper() != "FORMAL_DECISION":
        raise ValueError("Formal Decision completion requires interaction_scenario=FORMAL_DECISION")
    for key in ("task_run_id", "occurrence_id", "request_id", "requested_at_beijing"):
        if not str(request.get(key) or "").strip():
            raise ValueError(f"Formal Decision completion missing {key}")
    content = _extract_scheduled_review_final_content(request)
    if not content:
        raise ValueError("Formal Decision completion requires frozen final_content")
    requested = parse_time(request.get("requested_at_beijing"))
    if requested is None:
        raise ValueError("Formal Decision completion requested_at_beijing is invalid")
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    match = re.fullmatch(r"【ETF正式决策】(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (正式判断|降级判断|超时判断)", first_line)
    if not match:
        raise ValueError("Formal Decision FINAL_CONTENT title is invalid")
    title_time = parse_time(match.group(1) + ":00+08:00")
    requested_minute = requested.astimezone(SHANGHAI).replace(second=0, microsecond=0)
    if title_time != requested_minute:
        raise ValueError("Formal Decision FINAL_CONTENT title time does not match this run")

    binding_value = request.get("presentation_binding")
    if not isinstance(binding_value, dict):
        raise ValueError("Formal Decision completion requires presentation_binding")
    binding = dict(binding_value)
    binding_content = str(binding.get("full_content") or "")
    if binding_content and binding_content != content:
        raise ValueError("Formal Decision completion frozen content mismatch")

    supplied_hashes = [
        str(request.get("content_hash") or "").strip(),
        str(binding.get("content_hash") or "").strip(),
    ]
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if any(not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in supplied_hashes):
        raise ValueError("Formal Decision completion content_hash is missing or malformed")
    if any(value.lower() != expected_hash for value in supplied_hashes):
        raise ValueError("Formal Decision completion content_hash does not match final_content")
    request["presentation_binding"] = binding


def is_formal_decision_completion(request: dict) -> bool:
    """Recognize the shared Formal Decision completion contract independent of actor label.

    Live completions normally carry request_type=SCHEDULED_REVIEW_COMPLETION.
    Older valid completions may omit that transport label while retaining the
    full task/report/fact/presentation binding; those must remain replayable
    without mutating the immutable completion object.
    """
    binding = request.get("presentation_binding") if isinstance(request.get("presentation_binding"), dict) else {}
    request_type = str(request.get("request_type") or "").strip().upper()
    return (
        request_type in {"", "SCHEDULED_REVIEW_COMPLETION"}
        and str(request.get("formal_fact_type") or "").strip().upper() in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}
        and str(request.get("task_id") or "").strip() == "ETF正式决策"
        and str(request.get("report_type") or request.get("report_kind") or binding.get("report_type") or "").strip().upper() == "ETF_FORMAL_DECISION"
        and str(request.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
    )


def canonical_ingress_contract_for_request(request: dict) -> dict:
    """Return the actor-side terminal canonical ingress contract for formal facts."""

    fact_type = _explicit_fact_type(request)
    scenario = str(request.get("interaction_scenario") or "").strip().upper()
    channel = str(request.get("channel") or request.get("delivery_channel") or "").strip().upper()
    report_type = str(request.get("report_type") or request.get("report_kind") or "").strip().upper()

    is_broker_fact = is_broker_screenshot_request(request)
    trade_events = request.get("trade_events")
    has_trade_events = isinstance(trade_events, list) and bool(trade_events) and all(isinstance(item, dict) for item in trade_events)
    invalid_trade_events = "trade_events" in request and (
        not has_trade_events or isinstance(request.get("trade_event"), dict)
    )
    is_trade_fact = fact_type in {"CONFIRMED_TRADE", "TRADE_EVENT"} or isinstance(request.get("trade_event"), dict) or has_trade_events
    is_decision_fact = fact_type == "FORMAL_DECISION" or isinstance(request.get("formal_decision"), dict)
    # POST_CLOSE_REVIEW is a session/scenario label as well as a review fact
    # label. An explicit formal_decision payload remains a Decision Fact and
    # must not be diverted into the review-ingress contract merely because the
    # user requested the decision after A-share close.
    is_formal_completion = is_formal_decision_completion(request)
    is_review_fact = (
        (
            fact_type in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_SCHEDULED_TRADE_REVIEW", "POST_CLOSE_REVIEW"}
            and not is_formal_completion
        )
        or (scenario == "POST_CLOSE_REVIEW" and not is_decision_fact and not is_trade_fact and not is_formal_completion)
    )
    is_report_delivery = channel in {"REPORT", "REPORT_DELIVERY"} or report_type in {"ETF_TRADE_REVIEW", "SCHEDULED_TRADE_REVIEW"}

    if is_formal_completion:
        return {
            "required": False,
            "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
            "reason": "formal_decision_completion_projection",
        }

    if not any([is_broker_fact, is_review_fact, is_trade_fact, is_decision_fact]):
        return {
            "required": False,
            "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
            "reason": "not_a_formal_fact_ingress_request",
        }

    receipt = request.get("_ingress_path") or request.get("canonical_request_path") or request.get("request_path")
    submission_result = request.get("canonical_ingress_result") or request.get("canonical_submission_result")
    if isinstance(submission_result, dict):
        result_status = str(submission_result.get("status") or submission_result.get("terminal_state") or "").strip().upper()
        result_receipt = submission_result.get("receipt") or submission_result.get("receipt_path") or submission_result.get("commit_sha")
        if result_receipt and result_status in {"SUBMITTED", "SUCCESS", CANONICAL_INGRESS_SUBMITTED}:
            receipt = result_receipt
    if not receipt:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_durable_ingress_receipt",
        }

    if invalid_trade_events:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "invalid_trade_events",
        }

    if is_broker_fact:
        if isinstance(request.get("account_fact"), dict) or isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "broker_account_fact_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_account_fact",
        }

    if is_review_fact:
        if isinstance(request.get("formal_review"), dict) or isinstance(request.get("review"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "post_close_review_request_submitted",
            }
        if is_report_delivery:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
                "reason": "report_delivery_is_not_canonical_review_ingress",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_review",
        }

    if is_trade_fact:
        if isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "confirmed_trade_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_trade_event",
        }

    if is_decision_fact:
        if isinstance(request.get("formal_decision"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "formal_decision_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_decision",
        }

    return {
        "required": False,
        "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
        "reason": "not_a_formal_fact_ingress_request",
    }


def is_broker_screenshot_request(request: dict) -> bool:
    """Recognize broker facts from an explicit contract, with legacy compatibility.

    Free-form provenance remains metadata; it is not the business contract.
    The historical query-time request is accepted because its stable reason and
    ChatGPT origin explicitly identify a broker screenshot adoption.
    """
    explicit = _explicit_fact_type(request)
    if explicit in {"BROKER_ACCOUNT_SNAPSHOT", "ACCOUNT_FACT_CONFIRMATION", "BROKER_SCREENSHOT"}:
        return True
    if str(request.get("interaction_scenario") or "").strip().upper() == "BROKER_SCREENSHOT_SYNC":
        return True
    if str(request.get("source") or "").strip().upper() == "CHATGPT_USER_BROKER_SCREENSHOT":
        return True
    reason = str(request.get("reason") or "").strip().upper()
    requested_by = str(request.get("requested_by") or "").strip().lower()
    return (
        requested_by == "chatgpt"
        and reason == "USER_BROKER_SCREENSHOT_QUERY_TIME_REFRESH"
    )


def normalize_broker_account_fact_ingress(request: dict, supplied: object) -> tuple[object, int]:
    """Move explicitly typed non-security screenshot rows out of positions.

    No security identity is inferred. Rows are moved only when the ingress
    explicitly declares a non-security account asset type and supplies a name.
    Unknown security-like rows remain fail-closed.
    """
    if not is_broker_screenshot_request(request) or not isinstance(supplied, dict):
        return supplied, 0
    positions = supplied.get("positions")
    if not isinstance(positions, list):
        return supplied, 0
    normalized = json.loads(json.dumps(supplied))
    # Explicit screenshot-scoped fields are authoritative for this ingress.
    # Do not let stale uncertainty or timestamps survive a newer confirmed
    # screenshot when the request supplies the replacement value.
    for field in (
        "unconfirmed_visible_items",
        "broker_displayed_at_beijing",
        "account_screenshot_captured_at_beijing",
        "account_fact_derivation_note",
    ):
        if field in supplied:
            normalized[field] = supplied[field]
    # Normalize display-oriented screenshot aliases at the account ingress.
    # Formal Decision later consumes only the latest canonical account_fact.
    aliases = {
        "total_assets": "total_asset",
        "available_cash": "cash",
        "securities_market_value": "stock_market_value",
        "floating_pnl": "holding_pnl",
    }
    for source, target in aliases.items():
        if target not in supplied and normalized.get(source) is not None:
            normalized[target] = normalized[source]
    # A broker screenshot is a new authoritative observation for the
    # screenshot-scoped account fields. Refresh the observation timestamp and
    # derived aliases from this request instead of carrying stale metadata from
    # the prior account snapshot. Historical trade, lifecycle, and settlement
    # records remain preserved by merge_account_fact.
    receipt_time = request.get("account_updated_at") or request.get("submitted_at_beijing")
    if receipt_time:
        normalized["updated_at"] = str(receipt_time)
    market_date = str(request.get("market_date") or "").strip()
    market_boundary = str(request.get("market_fact_boundary_beijing") or "").strip()
    if market_date:
        normalized["market_date"] = market_date
        normalized["last_confirmed_market_date"] = market_date
    if market_boundary:
        normalized["broker_displayed_at_beijing"] = market_boundary
        normalized["screenshot_display_time"] = market_boundary
    if receipt_time:
        normalized["account_screenshot_captured_at_beijing"] = str(receipt_time)
    normalized["account_fact_derivation_note"] = (
        "当前账户金额、现金和持仓来自本次用户提供的券商截图；"
        "未显示代码的可见资产保持未确认，不并入正式ETF持仓。"
    )
    normalized.setdefault("source", request.get("source") or "CHATGPT_USER_BROKER_SCREENSHOT")
    # Keep display aliases synchronized with the current screenshot facts.
    if normalized.get("total_asset") is not None:
        normalized["total_assets"] = normalized["total_asset"]
    if normalized.get("cash") is not None:
        normalized["available_cash"] = normalized["cash"]
        normalized["deployable_cash_before_settlement"] = normalized["cash"]
    if normalized.get("stock_market_value") is not None:
        normalized["total_market_value"] = normalized["stock_market_value"]
    # Keep legacy account readers aligned with the current screenshot aliases.
    if normalized.get("daily_pnl") is not None:
        normalized["day_pnl"] = normalized["daily_pnl"]
    if normalized.get("daily_pnl_pct") is not None:
        normalized["day_pnl_pct"] = normalized["daily_pnl_pct"]
    kept = []
    unresolved = list(normalized.get("unresolved_assets") or [])
    moved = 0
    non_security_types = {"BOND", "CONVERTIBLE_BOND", "DEBT", "ACCOUNT_ASSET"}
    for index, position in enumerate(normalized.get("positions") or []):
        if not isinstance(position, dict):
            kept.append(position)
            continue
        code = str(position.get("code") or "").strip()
        asset_type = str(position.get("asset_type") or "").strip().upper()
        name = str(position.get("name") or position.get("security_name") or "").strip()
        # Explicitly typed, code-confirmed convertible bonds are securities.
        # Preserve their security subtype while using the canonical asset class.
        if re.fullmatch(r"\d{6}", code) and asset_type == "CONVERTIBLE_BOND":
            position = dict(position)
            position["asset_type"] = "OTHER"
            position["security_type"] = "CONVERTIBLE_BOND"
        if not re.fullmatch(r"\d{6}", code) and asset_type in non_security_types and name:
            row = dict(position)
            row["source_position_index"] = index
            row["identity_status"] = "UNRESOLVED_NON_SECURITY_ASSET"
            row["resolution_boundary"] = "No security code inferred from screenshot"
            unresolved.append(row)
            moved += 1
        else:
            kept.append(position)
    # Persist per-row normalization even when no unresolved asset was moved.
    normalized["positions"] = kept
    if moved:
        normalized["unresolved_assets"] = unresolved
    return normalized, moved


def validate_broker_account_fact_ingress(request: dict, supplied: object) -> str:
    """Fail fast on malformed ChatGPT-translated broker account facts.

    This validates transport shape only. It does not infer security identity,
    trading permission, or market facts from names.
    """
    if not is_broker_screenshot_request(request):
        return ""
    if supplied is None:
        # A broker screenshot request must carry the canonical account_fact
        # envelope. Silently accepting account_after_screenshot (or any other
        # non-canonical field) leaves the request durable but unconsumed and
        # makes the downstream gate appear stuck.
        if "account_after_screenshot" in request or "positions" in request:
            return "broker screenshot request must provide account_fact"
        return ""
    if not isinstance(supplied, dict):
        return "broker account_fact must be an object"
    positions = supplied.get("positions")
    if positions is None:
        return ""
    if not isinstance(positions, list):
        return "broker account_fact.positions must be a list"
    seen: set[str] = set()
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            return f"broker account_fact.positions[{index}] must be an object"
        code = str(position.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return f"broker account_fact.positions[{index}].code must be a six-digit security code"
        if code in seen:
            return f"broker account_fact.positions contains duplicate code {code}"
        seen.add(code)
        if position.get("quantity") is None:
            return f"broker account_fact.positions[{index}].quantity is required"
        try:
            float(position.get("quantity"))
        except (TypeError, ValueError):
            return f"broker account_fact.positions[{index}].quantity must be numeric"
        asset_type = str(position.get("asset_type") or "").strip().upper()
        if asset_type and asset_type not in {"ETF", "STOCK", "FUND", "OTHER"}:
            return f"broker account_fact.positions[{index}].asset_type is invalid"
    return ""


def merge_account_fact(prior: dict, supplied: dict) -> dict:
    """Merge confirmed screenshot facts and derive settlement cash canonically."""
    merged = json.loads(json.dumps(prior or {}))
    for key, value in (supplied or {}).items():
        if key != "settlement_obligations" and value is not None:
            merged[key] = json.loads(json.dumps(value))

    # Account facts have one date authority only.  The broker-confirmed
    # account date is stored as last_confirmed_market_date; legacy
    # account-level market_date is deliberately removed so it cannot drift
    # from the confirmed account fact and mislead downstream consumers.
    confirmed_market_date = (
        supplied.get("last_confirmed_market_date")
        or supplied.get("account_fact_market_date")
        or supplied.get("market_date")
        or merged.get("last_confirmed_market_date")
    )
    if confirmed_market_date:
        merged["last_confirmed_market_date"] = str(confirmed_market_date)
    merged.pop("market_date", None)

    # An ordinary screenshot is a partial observation.  Empty history-shaped
    # defaults from an ingress adapter must not erase canonical trade,
    # decision, fee, lifecycle, or reconciliation facts.
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key in (prior or {}) and (key not in (supplied or {}) or supplied.get(key) in (None, [], {})):
            merged[key] = json.loads(json.dumps(prior[key]))
    merged["settlement_obligations"] = _merge_settlement_obligations(prior or {}, supplied or {})
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key not in merged and key in (prior or {}):
            merged[key] = json.loads(json.dumps(prior[key]))
    _canonicalize_settlement_account(merged)
    _annotate_confirmed_ipo_origins(merged)
    _reconcile_known_ipo_events(merged)
    return merged


def process_historical_backfill_request(request: dict) -> dict:
    """Persist confirmed historical trade facts without touching current account state."""
    if str(request.get("ingress_mode") or "").upper() != "HISTORICAL_BACKFILL":
        raise ValueError("historical backfill mode is required")
    trades = request.get("historical_trades")
    if not isinstance(trades, list) or not trades:
        raise ValueError("historical_trades must be a non-empty list")
    trade_dir = ROOT / "events" / "trades"
    trade_dir.mkdir(parents=True, exist_ok=True)
    created = enriched = 0
    for raw in trades:
        if not isinstance(raw, dict):
            raise ValueError("historical trade must be an object")
        code, side = str(raw.get("code") or "").strip(), str(raw.get("side") or "").upper()
        quantity, price = safe_float(raw.get("quantity")), safe_float(raw.get("price"))
        executed_at = str(raw.get("executed_at") or raw.get("executed_at_beijing") or "").strip()
        confirmed_at = str(raw.get("confirmed_at_beijing") or "").strip()
        adoption_at = str(raw.get("historical_fact_adopted_at") or request.get("historical_fact_adopted_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds")).strip()
        market_date = str(raw.get("market_date") or executed_at[:10]).strip()
        if not code or side not in {"BUY", "SELL"} or quantity is None or price is None or not executed_at or not adoption_at or not market_date:
            raise ValueError("historical trade core fields are incomplete")
        event_id = str(raw.get("event_id") or f"HISTORICAL_{market_date}_{code}_{side}_{format(quantity, '.12g')}_{format(price, '.12g')}")
        key = str(raw.get("idempotency_key") or f"HISTORICAL_BACKFILL|{code}|{side}|{format(quantity, '.12g')}|{format(price, '.12g')}|{executed_at}")
        path = trade_dir / f"{event_id}.json"
        existing = load_json(path) if path.exists() else None
        if existing:
            for field, expected in {"code": code, "side": side, "quantity": quantity, "price": price}.items():
                actual = existing.get(field)
                numeric_field = field in {"quantity", "price"}
                normalized_actual = safe_float(actual) if numeric_field else actual
                normalized_expected = expected
                mismatch = (
                    normalized_actual is None or normalized_actual != normalized_expected
                    if numeric_field
                    else str(actual) != str(expected)
                )
                if mismatch:
                    source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                    detail = (
                        f"historical core mismatch: {event_id}:{field}; "
                        f"existing_value={actual!r}; existing_type={type(actual).__name__}; "
                        f"expected_value={expected!r}; expected_type={type(expected).__name__}; "
                        f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                        f"normalized_existing={normalized_actual!r}; normalized_existing_type={type(normalized_actual).__name__}; "
                        f"normalized_expected={normalized_expected!r}; normalized_expected_type={type(normalized_expected).__name__}"
                    )
                    raise ValueError(detail)
            existing_executed_at = existing.get("executed_at") or existing.get("executed_at_beijing")
            if existing_executed_at and str(existing_executed_at) != executed_at:
                source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                raise ValueError(
                    f"historical core mismatch: {event_id}:executed_at; "
                    f"existing_value={existing_executed_at!r}; existing_type={type(existing_executed_at).__name__}; "
                    f"expected_value={executed_at!r}; expected_type={type(executed_at).__name__}; "
                    f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                    f"normalized_existing={str(existing_executed_at)!r}; normalized_existing_type=str; "
                    f"normalized_expected={executed_at!r}; normalized_expected_type=str"
                )
            changed = False
            if not existing.get("historical_backfill") and confirmed_at:
                existing["confirmed_at_beijing"] = confirmed_at
                existing["confirmation_time_semantics"] = "RELIABLE_HISTORICAL_CONFIRMATION"
                changed = True
            for field, value in {"historical_fact_adopted_at": adoption_at, "executed_at": executed_at, "executed_at_beijing": executed_at, "market_date": market_date, "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount"), "fee_amount": raw.get("fee_amount"), "fee": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "source": raw.get("source"), "source_type": raw.get("source_type") or "user_confirmed_historical_fact", "source_period": request.get("source_period") or "2026-07-13..2026-09-15", "historical_execution_time_preserved": True, "historical_backfill": True}.items():
                if value not in (None, "") and existing.get(field) != value:
                    existing[field] = value
                    changed = True
            if changed:
                atomic_json_write(path, existing)
                enriched += 1
            continue
        event = {
            "event_id": event_id, "idempotency_key": key, "event_type": "HISTORICAL_BACKFILL_TRADE",
            "historical_backfill": True, "code": code, "side": side, "quantity": quantity, "price": price,
            "executed_at": executed_at, "executed_at_beijing": executed_at,
            "confirmed_at_beijing": confirmed_at or adoption_at, "historical_fact_adopted_at": adoption_at, "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if confirmed_at else "CANONICAL_ADOPTION_TIME", "market_date": market_date,
            "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount", raw.get("amount")),
            "fee": raw.get("fee", raw.get("fee_amount")), "fee_amount": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "name": raw.get("name"),
            "source": raw.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
            "source_type": raw.get("source_type") or "user_confirmed_historical_fact",
            "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
            "historical_execution_time_preserved": True,
            "source_confidence": raw.get("source_confidence") or "USER_CONFIRMED",
            "execution_status": "EXECUTED", "replay_semantics": "FACT_ENRICHMENT_ONLY",
        }
        atomic_json_write(path, event)
        created += 1
    acquisition = request.get("acquisition")
    if acquisition:
        if not isinstance(acquisition, dict) or str(acquisition.get("code")) != "301689":
            raise ValueError("invalid acquisition contract")
        aid = str(acquisition.get("event_id") or "HISTORICAL_ACQUISITION_20260901_301689_500")
        apath = trade_dir / f"{aid}.json"
        if not apath.exists():
            atomic_json_write(apath, {
                "event_id": aid, "event_type": "IPO_ALLOTMENT_ACQUISITION",
                "historical_backfill": True, "asset_type": "STOCK", "code": "301689",
                "quantity": 500, "price": 16.0, "market_date": "2026-09-01",
                "executed_at": acquisition.get("executed_at") or "2026-09-01T00:00:00+08:00",
                "confirmed_at_beijing": acquisition.get("confirmed_at_beijing") or adoption_at,
                "historical_fact_adopted_at": adoption_at,
                "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if acquisition.get("confirmed_at_beijing") else "CANONICAL_ADOPTION_TIME",
                "source_type": acquisition.get("source_type") or "user_confirmed_historical_fact",
                "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
                "historical_execution_time_preserved": True,
                "lot_source": "confirmed_settlement_obligation",
                "source": acquisition.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
                "execution_status": "ACQUIRED",
            })
            created += 1
        for path in trade_dir.glob("*.json"):
            event = load_json(path)
            if (str(event.get("code") or "") != "301689"
                    or str(event.get("side") or "").upper() != "SELL"
                    or safe_float(event.get("quantity")) != 500.0):
                continue
            if event.get("lot_source_event_id") != aid:
                event["lot_source_event_id"] = aid
                event["lot_source_relation"] = "ACQUISITION_TO_SELL"
                atomic_json_write(path, event)
                enriched += 1
    return {"ok": True, "ingress_mode": "HISTORICAL_BACKFILL", "created": created, "metadata_enriched": enriched, "current_account_mutated": False, "current_state_mutated": False}

def account_fact_is_older(prior: dict, supplied: dict) -> bool:
    old_time = parse_time(prior.get("updated_at"))
    new_time = parse_time(supplied.get("updated_at"))
    return bool(old_time and new_time and new_time < old_time)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    parser.add_argument("--formal-replay-source-commit", default="")
    parser.add_argument("--formal-replay-dwp-commit", default="")
    args = parser.parse_args()
    forensic_historical_replay = bool(str(args.formal_replay_source_commit or "").strip()) and os.environ.get("ETF_FORMAL_REPLAY_FORENSIC") == "1"
    reset_formal_replay_forensic_trace(forensic_historical_replay)
    raw_request_path = Path(str(args.request_path or "").strip())
    # The workflow may pass either the repository-relative ingress path or the
    # absolute path after its execution-time-main reset/recovery step. Resolve
    # both forms through the same root boundary; never allow an outside path.
    req_path = (raw_request_path if raw_request_path.is_absolute() else ROOT / raw_request_path).resolve()
    if req_path == ROOT or ROOT not in req_path.parents or not req_path.is_file():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    request["_ingress_path"] = str(req_path.relative_to(ROOT)).replace("\\\\", "/")

    # Scheduled Review actors may omit the derived scenario field even though
    # the canonical request type and formal fact type identify it
    # deterministically. Normalize only this registered ingress; do not infer
    # identity from task names, timestamps, or historical objects.
    if (
        str(request.get("request_type") or "").strip().upper()
        == "SCHEDULED_REVIEW_COMPLETION"
        and not str(request.get("interaction_scenario") or "").strip()
    ):
        scenario_by_fact_type = {
            "FORMAL_POST_CLOSE_REVIEW": "POST_CLOSE_REVIEW",
            "FORMAL_SCHEDULED_TRADE_REVIEW": "SCHEDULED_TRADE_REVIEW",
            "FORMAL_SCHEDULED_SYSTEM_REVIEW": "SCHEDULED_SYSTEM_REVIEW",
            "FORMAL_MIDDAY_REVIEW": "MIDDAY_REVIEW",
        }
        fact_type = str(request.get("formal_fact_type") or "").strip().upper()
        scenario = scenario_by_fact_type.get(fact_type)
        if not scenario:
            raise ValueError(
                "scheduled review completion requires a registered interaction_scenario"
            )
        request["interaction_scenario"] = scenario

    _validate_formal_completion_identity(request)

    # New Formal Decision identities must not bypass the structured Business
    # Decision Source ingress. Historical legacy completion artifacts remain
    # readable/replayable, but a legacy envelope whose parent request was
    # created after the production cutover is rejected at the canonical
    # state-sync gateway even if a caller bypasses the CLI builder.
    legacy_manual_completion = (
        str(request.get("request_type") or "").strip().upper() == "STATE_SYNC_ONLY"
        and str(request.get("source") or "").strip().upper() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
        and str(request.get("formal_fact_type") or "").strip().upper() == "FORMAL_DECISION"
    )
    if legacy_manual_completion:
        parent_id = str(request.get("parent_request_id") or "").strip()
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        parent = load_json(parent_path) if parent_id and parent_path.exists() else {}
        parent_requested = str(parent.get("requested_at_beijing") or request.get("requested_at_beijing") or "").strip()
        production_cutover = "2026-09-25T17:17:43+08:00"
        if parent_requested and parent_requested >= production_cutover:
            raise ValueError(
                "new Formal Decision legacy completion bypass is forbidden; "
                "use BUSINESS_DECISION_SOURCE with structured decision_response"
            )

    # Formal Decision completion is the frozen, request-bound Scheduled Review
    # handoff. New live completions are self-contained and must not depend on a
    # BUSINESS_DECISION_SOURCE/BDS artifact. Historical BDS replay is handled by
    # the explicit source_dispatch path below and is not part of live completion.
    original_source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    completion_dispatch = is_formal_decision_completion(request)
    if completion_dispatch:
        request.setdefault("parent_request_id", str(request.get("request_id") or "").strip())
        recorded, idempotent, event_path = record_formal_decision_report_event(request)
        print(json.dumps({
            "ok": True,
            "request_id": request.get("request_id"),
            "decision_id": request.get("decision_id"),
            "formal_decision_event": True,
            "canonical_publication": event_path,
            "idempotent_replay": idempotent,
            "bds_required": False,
        }, ensure_ascii=False))
        return 0

    # Explicit Business Decision Source dispatch. The ingress file itself is
    # the immutable durable Source artifact; projection only occurs after the
    # Phase 1 gate and never calls decision logic or refreshes market facts.
    source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    source_fingerprint_value = ""
    source_reply_ready = False
    if source_dispatch:
        parent_id = str(request.get("parent_request_id") or "").strip()
        if not parent_id or any(part in parent_id for part in ("/", "\\", "..")):
            raise ValueError("business decision source requires a safe parent Formal Decision request")
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        if not parent_path.exists():
            raise ValueError("business decision source parent Formal Decision request is not durable")
        parent_request = load_json(parent_path)
        if str(parent_request.get("request_id") or "").strip() != parent_id:
            raise ValueError("business decision source parent request identity mismatch")

        replay_source_commit = str(args.formal_replay_source_commit or "").strip()
        if replay_source_commit:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", replay_source_commit):
                raise ValueError("formal replay source commit must be a full commit SHA")
            request_rel = str(req_path.relative_to(ROOT)).replace("\\\\", "/")
            historical_source = subprocess.run(
                ["git", "show", f"{replay_source_commit}:{request_rel}"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_source.returncode != 0 or historical_source.stdout.encode("utf-8") != req_path.read_bytes():
                raise ValueError("formal replay source bytes must match the immutable source-introduction commit")
            replay_dwp_commit = str(args.formal_replay_dwp_commit or replay_source_commit).strip()
            historical_query = subprocess.run(
                ["git", "show", f"{replay_dwp_commit}:data/state/query_context.json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_query.returncode != 0 or not historical_query.stdout.strip():
                raise ValueError("formal replay DWP commit is missing historical request-bound query_context")
            query_context = json.loads(historical_query.stdout)
            decision_fact_pack = query_context.get("decision_fact_pack") or {}
            historical_account = decision_fact_pack.get("account_fact")
            if not isinstance(historical_account, dict) or not historical_account:
                raise ValueError("formal replay DWP commit is missing request-bound account fact")
            request["_historical_replay"] = True
            request["_historical_account_fact"] = json.loads(json.dumps(historical_account))
            # Keep discovery eligibility bound to the same immutable DWP context;
            # execution-time query_context may belong to a later request.
            request["_historical_query_context"] = query_context
        else:
            query_path = ROOT / "data" / "state" / "query_context.json"
            query_context = load_json(query_path) if query_path.exists() else {}
        packet = query_context.get("decision_fact_pack") or {}
        trigger = packet.get("trigger") or {}
        if str(trigger.get("request_id") or "").strip() != parent_id:
            # BUSINESS_DECISION_SOURCE is itself the durable actor handoff.  The
            # workflow may reset the workspace to latest main after the
            # request-bound query_context was produced; in that case the
            # execution-time projection must recover the same DWP from this
            # immutable BDS instead of accepting a later/empty DWP or failing
            # on a workspace-handoff artifact.  This is not a new request,
            # refresh, fact producer, or decision identity.
            bds_dwp = request.get("decision_work_package")
            request_type = str(request.get("request_type") or "").upper()
            if request_type == BUSINESS_DECISION_SOURCE and isinstance(bds_dwp, dict) and bds_dwp.get("problem_graph"):
                recovered_packet = dict(packet) if isinstance(packet, dict) else {}
                recovered_trigger = dict(trigger) if isinstance(trigger, dict) else {}
                recovered_trigger["request_id"] = parent_id
                recovered_packet["trigger"] = recovered_trigger
                recovered_packet["decision_work_package"] = json.loads(json.dumps(bds_dwp))
                query_context = dict(query_context) if isinstance(query_context, dict) else {}
                query_context["decision_fact_pack"] = recovered_packet
                packet = recovered_packet
                trigger = recovered_trigger
                request["_dwp_recovered_from_durable_bds"] = True
            else:
                qualifier = "historical" if replay_source_commit else "current"
                raise ValueError(f"business decision source requires {qualifier} request-bound Decision Work Package")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("A_HISTORICAL_PACKET", obj=packet, dwp=packet.get("decision_work_package"), object_type="query_context.decision_fact_pack", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
            emit_formal_replay_forensic_fingerprint("B_PACKET_DWP", obj=packet, dwp=packet.get("decision_work_package"), object_type="packet.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Historical replay is provenance-bound: only the DWP loaded from the
        # explicitly selected historical query_context may define the problem graph.
        # A request-local/current DWP must never override that PIT boundary.
        if replay_source_commit:
            decision_work_package = packet.get("decision_work_package") or {}
        else:
            decision_work_package = request.get("decision_work_package") or packet.get("decision_work_package") or {}
        if not isinstance(decision_work_package, dict) or not decision_work_package.get("problem_graph"):
            raise ValueError("business decision source Decision Work Package is missing")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("C_REQUEST_DWP_FREEZE", obj=request, dwp=decision_work_package, object_type="request.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Freeze the already-validated request-bound opportunity domain onto the
        # in-memory ingress object. record_formal_decision() must validate the
        # deterministic projection against the exact DWP that produced it,
        # including REJECTed Discovery evaluation inputs, rather than falling
        # back to the later post-disposition Observation set.
        request["decision_work_package"] = json.loads(json.dumps(decision_work_package))

        supplied_decision = request.get("formal_decision")
        source_payload = dict(supplied_decision) if isinstance(supplied_decision, dict) else {}
        # Preserve the request-bound DWP in the durable BDS. The source
        # payload is the child handoff, but its validation and completion
        # recovery require the exact work package that produced the answer.
        source_payload["decision_work_package"] = json.loads(json.dumps(decision_work_package))
        # BUSINESS_DECISION_SOURCE carries Formal ingress identity at the top level.\n        # Preserve it when projecting the internal source payload; otherwise the\n        # validator sees an empty formal_entry_source even though the durable BDS\n        # is correctly bound to the actual entry.\n        for identity_field in (\n            "formal_entry_source",\n            "entry_mode",\n            "task_id",\n            "task_run_id",\n            "occurrence_id",\n        ):\n            if identity_field in request:\n                source_payload.setdefault(identity_field, request.get(identity_field))\n        current_context = query_context.get("current") or {}
        source_payload.setdefault("request_id", str(request.get("request_id") or f"{parent_id}__business_decision_source"))
        source_payload.setdefault("parent_request_id", parent_id)
        source_payload.setdefault("decision_id", str(request.get("decision_id") or f"{parent_id}_decision"))
        source_payload.setdefault("consumed_snapshot", str(request.get("consumed_snapshot") or current_context.get("latest_snapshot") or ""))
        source_payload.setdefault("requested_at_beijing", parent_request.get("requested_at_beijing") or parent_request.get("request_time_beijing"))
        source_payload.setdefault("market_date", query_context.get("market_date") or parent_request.get("market_date"))
        source_payload.setdefault("data_as_of_beijing", query_context.get("generated_at_beijing") or source_payload.get("requested_at_beijing"))
        source_payload["request_type"] = BUSINESS_DECISION_SOURCE
        decision_response = request.get("decision_response")
        request_presentation = request.get("presentation_content")
        response_presentation = (
            decision_response.get("presentation_content")
            if isinstance(decision_response, dict)
            else None
        )
        supplied_presentations = [
            value for value in (request_presentation, response_presentation)
            if value not in (None, "")
        ]
        if supplied_presentations and len(set(str(value) for value in supplied_presentations)) != 1:
            raise ValueError("formal presentation content conflicts across request entry points")
        presentation_content = (
            response_presentation
            if response_presentation not in (None, "")
            else request_presentation
        )
        # A Business Decision Source without the frozen user-visible body is
        # not a valid Formal Decision completion input.  Do not publish a
        # half-complete decision event that notification_center cannot safely
        # consume.
        if presentation_content in (None, ""):
            raise ValueError(
                "business decision source requires frozen presentation_content"
            )
        if presentation_content not in (None, ""):
            source_payload["presentation_content"] = presentation_content
            binding = request.get("presentation_binding")
            source_payload["presentation_binding"] = (
                dict(binding) if isinstance(binding, dict) else {}
            )
            source_payload["presentation_binding"]["full_content"] = presentation_content
            nested = source_payload.get("decision_response")
            if not isinstance(nested, dict):
                nested = {}
            nested["presentation_content"] = presentation_content
            source_payload["decision_response"] = nested
            request["presentation_content"] = presentation_content
        if isinstance(supplied_decision, dict) and supplied_decision:
            # A pre-shaped formal_decision is only legitimate for an explicitly
            # selected historical forensic replay. Current production ingress
            # must always project actor answers through the request-bound DWP.
            # Otherwise numeric/positional answers can bypass the problem_id
            # coverage contract and still reach the canonical writer.
            if not forensic_historical_replay:
                raise ValueError(
                    "current Business Decision Source ingress must use "
                    "actor-only decision_response keyed by request-bound problem_id"
                )
            source_payload = source_payload
        else:
            if not isinstance(decision_response, dict):
                raise ValueError("business decision source requires actor-only structured decision_response")
            source_payload = project_decision_response(source_payload, decision_response, decision_work_package, forensic_historical_replay=forensic_historical_replay)
            # The projection builder returns a canonical source object, but
            # ingress identity belongs to the immutable request envelope. Keep
            # that identity bound across the same-run projection boundary;
            # otherwise the downstream source validator sees an anonymous
            # source even though the parent request carried a valid Formal
            # entry/source mapping.
            for identity_field in (
                "formal_entry_source",
                "entry_mode",
                "task_id",
                "task_run_id",
                "occurrence_id",
            ):
                if identity_field in request:
                    source_payload[identity_field] = request.get(identity_field)
            for canonical_field in (
                "opportunity_status",
                "continued_holding_opportunity_cost",
                "decision_evidence_consumption",
            ):
                if request.get(canonical_field) not in (None, "", [], {}):
                    source_payload[canonical_field] = request.get(canonical_field)
        # Resolve and validate the exact request-bound PIT snapshot before
        # projecting the Business Decision Source. This path previously used
        # snapshot_rel/snapshot without selecting them, so a valid manual
        # Formal Decision could fail with NameError before canonical writing.
        decision_time = str(
            source_payload.get("requested_at_beijing")
            or parent_request.get("requested_at_beijing")
            or parent_request.get("request_time_beijing")
            or ""
        ).strip()
        availability_time = str(
            query_context.get("generated_at_beijing")
            or source_payload.get("data_as_of_beijing")
            or decision_time
            or ""
        ).strip()
        snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(
            str(source_payload.get("market_date") or "").strip(),
            decision_time,
            availability_time=availability_time,
            consumed_snapshot=str(source_payload.get("consumed_snapshot") or "").strip(),
        )
        if not snapshot_rel:
            raise ValueError(
                "business decision source requires a legal request-bound PIT snapshot: "
                + pit_status
            )
        source_payload["request_bound_fact_binding"] = {
            "snapshot_id": snapshot_rel,
            "market_date": str(snapshot.get("market_date") or market_date or "").strip(),
            "market_phase": str(snapshot.get("market_phase") or "").strip(),
            "captured_at": str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
            "pit_status": pit_status,
        }
        source = validate_source(
            source_payload,
            expected_snapshot=str(request.get("consumed_snapshot") or source_payload.get("consumed_snapshot") or "").strip(),
            expected_market_date=str(snapshot.get("market_date") or market_date or "").strip(),
            expected_market_phase=str(snapshot.get("market_phase") or "").strip(),
            expected_captured_at=str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
        )
        # Bind the selected request-bound DWP only after Source identity and
        # fingerprinting.  It is validation context, not mutable Source
        # business content, so replay/idempotency fingerprints remain stable.
        source = freeze_source_work_package(source, decision_work_package)
        source_fingerprint_value = source["fingerprint"]
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("D_SOURCE_BINDING", obj=source, dwp=source.get("decision_work_package"), object_type="business_decision_source.bound", provenance=f"historical_query_context:{replay_dwp_commit}", source_fingerprint_value=source_fingerprint_value, historical=True)
        source_reply_ready = True
        request["_business_decision_source"] = source
        request["_source_fingerprint"] = source_fingerprint_value
        # Deterministic projection only: no new facts, ranking, or investment
        # judgment is introduced by this branch.
        projected = build_formal_completion_from_source(source)
        projected.pop("request_type", None)
        projected.pop("source_type", None)
        projected.pop("source_durable", None)
        projected.pop("reply_ready", None)
        projected.pop("projection_status", None)
        projected.pop("fingerprint", None)
        request["formal_decision"] = projected
        _persist_formal_opportunity_projection(ROOT, source)
    if original_source_dispatch:
        print(json.dumps({"ok": True, "request_id": request.get("request_id"), "business_decision_source_persisted": True, "completion_required": True}, ensure_ascii=False))
        return 0
    system_review_recorded, system_review_idempotent = record_scheduled_system_review(request)
    if system_review_recorded:
        result = {"ok": True, "request_id": request.get("request_id"), "system_review_completion": True, "idempotent_replay": system_review_idempotent, "canonical_publication": "events/reviews/system_" + str(((request.get("presentation_binding") or {}).get("task_run_id") or request.get("task_run_id") or "")).strip() + ".json"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if str(request.get("ingress_mode") or "").upper() == "HISTORICAL_BACKFILL":
        result = process_historical_backfill_request(request)
        result.update({
            "request_id": request.get("request_id"),
            "trade_event_recorded": True,
            "account_sync_status": "NOT_APPLICABLE",
            "canonical_ingress_state": CANONICAL_INGRESS_SUBMITTED,
            "canonical_ingress_failure_reason": "",
            "source_dispatch": source_dispatch,
            "source_durable": bool(source_dispatch),
            "source_immutable": bool(source_dispatch),
            "source_fingerprint": source_fingerprint_value,
            "reply_ready": source_reply_ready,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    canonical_ingress_contract = canonical_ingress_contract_for_request(request)
    trades = _request_trade_events(request)
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    existing_trades = []
    for trade in trades:
        confirmed_at = (
            trade.get("confirmed_at_beijing") or request.get("requested_at_beijing")
            or trade.get("executed_at") or prior_account.get("updated_at")
        )
        replay_key = _trade_idempotency_key(trade, confirmed_at)
        existing = _find_existing_trade(trade, confirmed_at, replay_key)
        existing_trades.append(existing)
        if request.get("account_fact") is None and existing is None:
            supplied_account = _apply_trade_to_account(supplied_account or prior_account, trade)
    account_sync_status = "NOT_APPLICABLE"
    if is_broker_screenshot_request(request) and not isinstance(supplied_account, dict) and not trades:
        result = {
            "ok": False,
            "request_id": request.get("request_id"),
            "interaction_scenario": request.get("interaction_scenario"),
            "status": "NO_ACCOUNT_FACT",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "dashboard_updated": False,
            "detail": "Broker screenshot request has no request-scoped account_fact; market success must not be treated as account sync success.",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if (
        canonical_ingress_contract["required"]
        and canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
        and canonical_ingress_contract["reason"] != "missing_account_fact"
    ):
        print(json.dumps({
            "ok": False,
            "status": "CANONICAL_INGRESS_FAILED_EXPLICITLY",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "request": str(req_path.relative_to(ROOT)).replace("\\", "/"),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    supplied_account, normalized_non_security_asset_count = normalize_broker_account_fact_ingress(request, supplied_account)
    broker_shape_error = validate_broker_account_fact_ingress(request, supplied_account)
    if broker_shape_error:
        print(json.dumps({
            "ok": False,
            "request_id": request.get("request_id"),
            "status": "BROKER_ACCOUNT_FACT_SCHEMA_INVALID",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "canonical_ingress_failure_reason": "broker_account_fact_schema_invalid",
            "dashboard_updated": False,
            "detail": broker_shape_error,
        }, ensure_ascii=False))
        return 1

    if isinstance(supplied_account, dict):
        supplied_account = merge_account_fact(prior_account, supplied_account)
        supplied_account.setdefault("status", "VALID")
        supplied_account.setdefault("validity_mode", "EVENT_DRIVEN_CARRY_FORWARD")
        if account_fact_is_older(prior_account, supplied_account):
            account_sync_status = "STALE_ACCOUNT_FACT_IGNORED"
        else:
            # Use the canonicalized event history returned by merge_account_fact:
            # it may have upgraded a previously unresolved IPO registration.
            prior_events = supplied_account.get("account_change_events_after_confirmed_at") or []
            new_events = _account_change_events(prior_account, supplied_account, request, trades)
            known = {str(x.get("idempotency_key") or x.get("event_id") or "") for x in prior_events}
            supplied_account["account_change_events_after_confirmed_at"] = prior_events + [x for x in new_events if str(x.get("idempotency_key")) not in known]
            if supplied_account == prior_account:
                account_sync_status = "ACCOUNT_SYNC_IDEMPOTENT_NOOP"
            else:
                account_sync_status = "ACCOUNT_FACT_UPDATED"
                atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")
    decision_recorded, decision_id = record_formal_decision(request)
    formal_decision_current = False
    if decision_recorded:
        formal_decision_current, formal_action_changed = project_current_formal_action(
            account, request, decision_id
        )
        if formal_action_changed:
            atomic_json_write(ACCOUNT, account)
    trade_event_recorded = False
    persisted_trade_events = []
    for trade_index, trade in enumerate(trades):
        confirmed_at = trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = existing_trades[trade_index] or _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
            # A request-scoped replay may repair an earlier unlinked trade, but
            # never by guessing from the latest account action.  The replay must
            # carry an explicit decision identity or a same-request formal decision.
            explicit_replay_decision_id = str(trade.get("linked_decision_id") or trade.get("decision_id") or decision_id or "").strip()
            if not str(event.get("linked_decision_id") or "").strip() and explicit_replay_decision_id:
                event["linked_decision_id"] = explicit_replay_decision_id
            # A request-scoped confirmation time is authoritative for an
            # idempotent replay.  Never replace an earlier PIT confirmation with
            # the current account snapshot's updated_at.
            changed = False
            requested_confirmed_at = str(trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or "").strip()
            if requested_confirmed_at and str(event.get("confirmed_at_beijing") or "") != requested_confirmed_at:
                event["confirmed_at_beijing"] = requested_confirmed_at
                event["execution_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["confirmation_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["idempotency_key"] = _trade_idempotency_key(trade, requested_confirmed_at)
                changed = True
            # Idempotent fee/account replays must also converge stale linkage and
            # attribution written by an older producer; they never create another event.
            link_trade = {
                **trade,
                "confirmed_at_beijing": requested_confirmed_at
                or str(event.get("confirmed_at_beijing") or confirmed_at),
            }
            if trade.get("executed_at_beijing") or trade.get("executed_at"):
                event["executed_at_beijing"] = trade.get("executed_at_beijing") or trade.get("executed_at")
                changed = True
            linked_decision_id = resolve_trade_linked_decision_id(
                link_trade,
                decision_id,
                existing_linked_decision_id=str(event.get("linked_decision_id") or ""),
            )
            if str(event.get("linked_decision_id") or "") != linked_decision_id:
                event["linked_decision_id"] = linked_decision_id or None
                changed = True
            refreshed_attribution = execution_attribution(event, linked_decision_id)
            refreshed_hypothesis = refreshed_attribution.get("hypothesis_id")
            if (
                event.get("execution_attribution") != refreshed_attribution
                or event.get("hypothesis_id") != refreshed_hypothesis
            ):
                event["execution_attribution"] = refreshed_attribution
                event["hypothesis_id"] = refreshed_hypothesis
                changed = True
            if changed:
                atomic_json_write(ROOT / "events" / "trades" / f"{event_id}.json", event)
        else:
            event_id = _trade_event_id(request, trade, idempotency_key, trade_index, len(trades))
            link_trade = {**trade, "confirmed_at_beijing": confirmed_at}
            linked_decision_id = resolve_trade_linked_decision_id(link_trade, decision_id)
            attribution = execution_attribution(link_trade, linked_decision_id)
            event = {"event_id": event_id, "idempotency_key": idempotency_key, "confirmed_at_beijing": confirmed_at, "executed_at_beijing": trade.get("executed_at_beijing") or trade.get("executed_at"), "execution_date": trade.get("execution_date") or str(confirmed_at)[:10], "confirmation_date": trade.get("market_date") or str(confirmed_at)[:10], "notification_id": trade.get("notification_id"), "name": trade.get("name"), "code": trade.get("code"), "side": trade.get("side"), "quantity": trade.get("quantity"), "price": trade.get("price"), "amount": trade.get("amount"), "lifecycle": trade.get("lifecycle"), "source": trade.get("source", account.get("source")), "source_confidence": trade.get("source_confidence"), "linked_decision_id": linked_decision_id or None, "hypothesis_id": trade.get("hypothesis_id") or attribution.get("hypothesis_id") or None, "execution_status": "EXECUTED", "execution_attribution": attribution}
            event_path = ROOT / "events" / "trades" / f"{event_id}.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(event_path, event)
            trade_event_recorded = True
        # Always reconcile event-backed human-readable records, including idempotent replay.
        # This repairs a missing archive/CASE line without creating a second trade event.
        archive_line = f"- {event['confirmed_at_beijing']}：{event.get('name')}（{event.get('code')}）{event.get('side')} {int(event.get('quantity') or 0):,}份/股，成交价{event.get('price')}，成交本金{float(event.get('amount') or 0):,.2f}元；来源：{event.get('source')}。"
        case_line = f"- 待复盘CASE｜{event['confirmed_at_beijing']}｜{event.get('name')}（{event.get('code')}）｜{event.get('side')} {int(event.get('quantity') or 0):,}份/股｜生命周期：{event.get('lifecycle') or '待确认'}｜仅登记真实成交，复盘结论留待盘后形成。"
        upsert_formal_line(ROOT, ARCHIVE.name, TRADE_START, TRADE_END, event_id, archive_line, before_heading="## 6. 历史Excel与专项数据来源")
        upsert_formal_line(ROOT, EXPERIENCE.name, CASE_START, CASE_END, event_id, case_line, before_heading="## 3. 历史研究与专项回测")
        sync_experience_transaction_index(event)
        linked_event_decision_id = str(event.get("linked_decision_id") or "").strip()
        prior_formal_action = account.get("formal_action") or {}
        if linked_event_decision_id:
            decision_path = ROOT / "events" / "decisions" / f"{linked_event_decision_id}.json"
            linked_decision = load_json(decision_path) if decision_path.exists() else {}
            linked_formal = linked_decision.get("formal_decision") if isinstance(linked_decision.get("formal_decision"), dict) else {}
            account["formal_action"] = {
                **prior_formal_action,
                "action": linked_formal.get("action") or linked_formal.get("amount_action") or prior_formal_action.get("action") or "",
                "quantity": linked_formal.get("quantity", prior_formal_action.get("quantity")),
                "decision_id": linked_event_decision_id,
                "decision_time": linked_formal.get("decision_time") or linked_formal.get("data_as_of_beijing") or linked_decision.get("decision_time_beijing") or prior_formal_action.get("decision_time"),
                "source": "CHATGPT_FORMAL_DECISION",
                "lifecycle": linked_formal.get("lifecycle") or prior_formal_action.get("lifecycle"),
                "applicable_object": linked_formal.get("candidate_code") or linked_formal.get("code") or linked_decision.get("candidate_code") or prior_formal_action.get("applicable_object") or "",
                "validity": "ACTIVE",
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        else:
            account["formal_action"] = {
                **prior_formal_action,
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        atomic_json_write(ACCOUNT, account)
        write_trade_review_required(event)
        persisted_trade_events.append((trade, event))

    # Refresh the CURRENT trade mirror only after this request's trade events
    # are durable.  Doing this before the loop leaves CURRENT and downstream
    # trigger construction one confirmed trade behind.
    latest_trade_event_id = _latest_trade_event_id()
    if latest_trade_event_id:
        current_path = ROOT / "data/state/CURRENT.json"
        current = load_json(current_path) if current_path.exists() else {}
        if current.get("last_trade_event_id") != latest_trade_event_id:
            current["last_trade_event_id"] = latest_trade_event_id
            current["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            atomic_json_write(current_path, current)

    sync_current_account_mirror(ROOT, account)
    review_recorded, review_idempotent = record_post_close_review(account, request)
    unavailable_recorded, unavailable_idempotent = (False, False)
    for trade, event in persisted_trade_events:
        recorded, idempotent = record_unrecoverable_review_prerequisite(account, request, event)
        unavailable_recorded = unavailable_recorded or recorded
        unavailable_idempotent = unavailable_idempotent or idempotent
    # Keep the three human-readable fact documents synchronized before the
    # final Dashboard render.  sync_formal_files owns factual account projection
    # but must not overwrite the lifecycle projection rendered below.
    account_membership_changed = account_sync_status == "ACCOUNT_FACT_UPDATED" or bool(trade_event_recorded)
    observation_universe_changed = persist_monitor_universe(
        ROOT, account, (request.get("formal_decision") or {}) if formal_decision_current else {}
    ) if (formal_decision_current or account_membership_changed) else False
    formal_files_sync = sync_formal_files(ROOT, account)
    # There is exactly one Dashboard projection owner: sync_formal_files.py.
    # Do not rebuild the Dashboard through the legacy state-sync renderer here;
    # that path omits the canonical lifecycle projection and reintroduces stale
    # P&L values into the lifecycle "下一节点" column.
    dashboard_sync = sync_dashboard_projection(ROOT, account)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": bool(dashboard_sync.get("dashboard_changed")), "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent, "review_prerequisite_unavailable_recorded": unavailable_recorded, "review_prerequisite_unavailable_idempotent_noop": unavailable_idempotent, "formal_files_sync": formal_files_sync, "observation_universe_changed": observation_universe_changed, "account_sync_status": account_sync_status, "normalized_non_security_asset_count": normalized_non_security_asset_count, "canonical_ingress_state": canonical_ingress_contract["terminal_state"], "canonical_ingress_failure_reason": None if canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_SUBMITTED else canonical_ingress_contract["reason"]}
    if review_recorded and str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION":
        # The completion projection owner needs the exact event produced by the
        # same request.  Trade Review events are keyed by market_date, unlike
        # System Review events keyed by occurrence/task_run_id.
        market_date = str(request.get("market_date") or "").strip()
        if not market_date:
            raise ValueError("SCHEDULED_REVIEW_COMPLETION requires market_date")
        if (
            str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION"
            and str(request.get("formal_fact_type") or "").strip().upper() in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}
            and str((request.get("presentation_binding") or {}).get("task_id") or "").strip() == "ETF正式决策"
            and str(request.get("occurrence_id") or "").strip()
        ):
            result["canonical_publication"] = f"events/reviews/formal_{request['occurrence_id']}.json"
        else:
            result["canonical_publication"] = f"events/reviews/{market_date}.json"
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


@@FILE:scripts/state_manager.py@@
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from market_quote_router import build_market_quote_context
except ModuleNotFoundError:
    from scripts.market_quote_router import build_market_quote_context
try:
    from rules_version import current_rule_version
except ModuleNotFoundError:
    from scripts.rules_version import current_rule_version
try:
    from lifecycle_state import build_lifecycle_projection
except ModuleNotFoundError:
    from scripts.lifecycle_state import build_lifecycle_projection


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
        "last_trade_event_id": "", "rules_version": "", "generated_at": "",
    }


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if raw.endswith("\\n"):
            return json.loads(raw[:-2] + "\n")
        if path.name == "runtime_health.json" and "Extra data" in str(exc):
            decoder = json.JSONDecoder()
            index = 0
            last = None
            while index < len(raw):
                while index < len(raw) and raw[index].isspace():
                    index += 1
                if index >= len(raw):
                    break
                value, end = decoder.raw_decode(raw, index)
                last = value
                index = end
            if last is not None:
                return last
        raise



def read_json_checked(path: Path, fallback: Any) -> tuple[Any, str]:
    """Read JSON with an auditable result; never collapse read failure into empty data."""
    if not path.exists():
        return fallback, "NOT_FOUND"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return fallback, "READ_UNVERIFIED"
    if raw == "":
        # A zero-byte JSON read cannot prove that the canonical object is empty.
        # Treat it as an unverified transport/read result so consumers re-read the
        # same source instead of projecting a false missing/empty state.
        return fallback, "READ_UNVERIFIED"
    try:
        return json.loads(raw), "READ_OK"
    except json.JSONDecodeError:
        return fallback, "INVALID_CONTENT"

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
    current["account_fact"] = {k: account.get(k, "") for k in (
        "status", "updated_at", "source", "cash",
        "reserved_cash_for_settlement", "deployable_cash",
        "settlement_constraint_status",
    )}
    current["settlement_obligations"] = account.get("settlement_obligations") or []
    current["reserved_cash_for_settlement"] = account.get("reserved_cash_for_settlement", 0)
    current["deployable_cash"] = account.get("deployable_cash")
    current["needs_account_update"] = account["status"] != "VALID"
    version = current_rule_version(root)
    if version:
        current["rules_version"] = version
    formal_action = account.get("formal_action") or {}
    if str(formal_action.get("execution_status") or "").upper() == "EXECUTED" and formal_action.get("last_executed_event_id"):
        current["last_trade_event_id"] = str(formal_action.get("last_executed_event_id"))
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
        "generated_at": now_utc(), "source": "Codex state layer", "current_risk_state": "FACTUAL_PROJECTION_READY",
        "current_node": current.get("latest_valid_node", ""), "node_status": current.get("node_status", "NON_TRADING_DAY"),
        "current_candidate": None, "data_status": current.get("data_freshness", {}), "account_fact_status": account["status"],
        "needs_account_update": account["status"] != "VALID", "manual_confirmation_required": False, "write_target": "dashboard_projection",
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
            "vs_shanghai_pct_points": row.get("vs_shanghai_pct_points"), "vs_chinext_pct_points": row.get("vs_chinext_pct_points"), "vs_star50_pct_points": row.get("vs_star50_pct_points"),
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
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else []
    return [x for x in (rows or []) if isinstance(x, dict)]


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
    expected_indices = {"000001", "000688", "399006"}
    rows = _snapshot_rows(snapshot)
    by_code = {str(x.get("symbol") or ""): x for x in rows}
    stocks_ctx = read_json(root / "data" / "state" / "stock_market_context.json", {})
    try:
        from build_stock_context import active_account_asset_codes
    except ModuleNotFoundError:
        from scripts.build_stock_context import active_account_asset_codes
    memberships = active_account_asset_codes(root, account)
    held = memberships["etf"]
    stocks = memberships["stocks"]
    def usable(row): return bool(row) and str(row.get("quality_status") or row.get("status") or "").upper() not in {"FAILED", "FAIL"}
    observed = etfs - held
    missing_held_etfs = sorted(code for code in held if code not in by_code)
    stock_objects = stocks_ctx.get("objects") or {}
    missing_account_stocks = sorted(
        code for code in stocks
        if str((stock_objects.get(code) or {}).get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}
    )
    missing = sorted((etfs | held | expected_indices) - set(by_code))
    status = "INCOMPLETE" if (missing or missing_account_stocks) else ("DEGRADED" if quality["failed_count"] or quality["degraded_count"] or quality["stale_count"] else "COMPLETE")
    core_rows = [x for x in rows if x.get("asset_class") in {"ETF", "A_SHARE_INDEX"}]
    core_pass = sum(str(x.get("quality_status") or "").upper() == "PASS" for x in core_rows)
    core_failed = sum(str(x.get("quality_status") or "").upper() in {"FAILED", "FAIL"} for x in core_rows)
    core_degraded = sum(str(x.get("quality_status") or "").upper() in {"DEGRADED", "PARTIAL"} for x in core_rows)
    account_stock_pass = sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() == "PASS" for x in stocks)
    account_stock_failed = sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() in {"FAILED", "FAIL"} for x in stocks)
    return {"indices_total": len(expected_indices), "indices_available": sum(usable(x) for x in rows if x.get("asset_class") == "A_SHARE_INDEX"), "held_etfs_total": len(held), "held_etfs_available": sum(usable(by_code.get(x)) for x in held), "observed_etfs_total": len(observed), "observed_etfs_available": sum(usable(by_code.get(x)) for x in observed), "account_stocks_total": len(stocks), "account_stocks_available": sum(str((stock_objects.get(x) or {}).get("quality_status") or "").upper() in {"PASS", "DEGRADED"} for x in stocks), "core_market_pass": core_pass, "core_market_failed": core_failed, "core_market_degraded": core_degraded, "account_stock_market_pass": account_stock_pass, "account_stock_market_failed": account_stock_failed, "cash_available": account.get("cash") is not None, "failed_objects": quality["failed_objects"], "degraded_objects": quality["degraded_objects"] + quality["stale_objects"], "missing_objects": missing, "missing_held_etfs": missing_held_etfs, "missing_account_stocks": missing_account_stocks, "coverage_status": status, "read_only": True}


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


def _parse_beijing_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.astimezone(timezone(timedelta(hours=8)))


def _latest_formal_review_risk(root: Path) -> dict[str, Any]:
    directory = root / "events" / "reviews"
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    if directory.exists():
        for path in directory.glob("*.json"):
            event = read_json(path, {})
            review = event.get("review") or event.get("formal_review") or {}
            fact = review.get("etf_strategy_known_net") or {}
            try:
                risk = float(fact.get("etf_strategy_risk_rate_pct"))
            except (TypeError, ValueError):
                continue
            updated = _parse_beijing_time(event.get("updated_at_beijing") or event.get("account_updated_at"))
            if updated is None:
                continue
            equity = fact.get("known_net_strategy_equity")
            try:
                equity = float(equity) if equity is not None else None
            except (TypeError, ValueError):
                equity = None
            candidates.append((updated, {"risk_pct": risk, "equity": equity, "updated_at": updated.isoformat(timespec="seconds"), "market_date": event.get("market_date") or review.get("market_date"), "source": str(path.relative_to(root)).replace("\\", "/")}))
    return max(candidates, key=lambda x: x[0])[1] if candidates else {}


def build_etf_strategy_risk_metrics(root: Path) -> dict[str, Any]:
    """Build risk metrics from canonical review facts or a fresh reconstruction.

    Dashboard text is a human-readable projection and is never a machine risk source.
    """
    equity = read_json(root / "data" / "state" / "etf_strategy_equity.json", {})
    summary = equity.get("summary") or {}
    account = read_account_fact(root)
    try:
        from build_stock_context import active_account_asset_codes, normalize_code, position_metric
    except ModuleNotFoundError:
        from scripts.build_stock_context import active_account_asset_codes, normalize_code, position_metric
    memberships = active_account_asset_codes(root, account)
    etf_float = sum(
        position_metric(p, "pnl", "holding_pnl")
        for p in (account.get("positions") or [])
        if normalize_code(p.get("code") or p.get("symbol") or p.get("security_code")) in memberships["etf"]
    )
    capital = float(summary.get("starting_etf_strategy_capital") or 200000)
    reconstruction_risk = summary.get("current_strategy_return_pct_gross", summary.get("known_net_current_strategy_return_pct"))
    reconstruction_as_of = str(equity.get("as_of_transaction_date") or summary.get("as_of_transaction_date") or equity.get("replay_cutoff") or "")
    current = read_json(root / "data" / "state" / "CURRENT.json", {})
    current_market_date = str(current.get("market_date") or "")
    canonical_risk = read_json(root / "data" / "state" / "skfolio_risk_evidence.json", {})
    canonical_risk_status = str(canonical_risk.get("status") or "").upper()
    canonical_alignment = str(canonical_risk.get("data_alignment") or "").upper()
    if canonical_risk_status == "DEGRADED" and canonical_alignment != "ALIGNED_WITH_CURRENT_MARKET_DATE" and summary.get("current_strategy_return_pct_gross") is None:
        return {
            "etf_strategy_risk_pct": None,
            "risk_source": "data/state/skfolio_risk_evidence.json",
            "risk_source_updated_at": canonical_risk.get("generated_at"),
            "reconstruction_risk_pct": reconstruction_risk,
            "reconstruction_as_of_transaction_date": reconstruction_as_of,
            "reconstruction_fresh_for_market_date": False,
            "risk_data_end": canonical_risk.get("risk_data_end"),
            "current_market_date": canonical_risk.get("current_market_date") or current_market_date,
            "data_alignment": canonical_risk.get("data_alignment"),
            "decision_block_reason": canonical_risk.get("decision_block_reason") or "risk_history_not_aligned_to_current_market_date",
            "etf_holding_unrealized_pct": round(etf_float / capital * 100.0, 2),
            "etf_drawdown_from_high_pct": summary.get("known_net_current_drawdown_pct"),
            "equity_data_quality": "RISK_EVIDENCE_STALE_FOR_CURRENT_MARKET_DATE",
            "strategy_equity_known_net": summary.get("known_net_current_strategy_equity"),
            "high_watermark": summary.get("known_net_high_watermark", capital),
            "fee_status": summary.get("fee_status", ""),
            "interpretation": "当前风险历史未覆盖当前市场日期；Dashboard只显示降级状态，不投影陈旧风险率。",
            "read_only": True,
        }
    # Strategy risk is dated to the latest confirmed account/trade fact.
    # A newer intraday market date alone does not invalidate the last valid
    # gross reconstruction when no new account or transaction fact exists.
    # A broker screenshot may advance the account display date without
    # introducing a new trade or cash-flow fact.  In that case the latest
    # canonical Gross replay remains the valid risk owner; only a newer
    # transaction/cash fact requires the replay to catch up.
    account_trade_fact_date = str(
        account.get("trade_fact_as_of")
        or account.get("transaction_fact_as_of")
        or ""
    )
    no_new_trade_observed = bool(account.get("no_new_trade_observed"))
    if no_new_trade_observed and not account_trade_fact_date:
        risk_fact_date = reconstruction_as_of
    else:
        risk_fact_date = str(
            account_trade_fact_date
            or account.get("last_confirmed_market_date")
            or current_market_date
            or ""
        )
    reconstruction_fresh = bool(
        reconstruction_risk is not None
        and reconstruction_as_of
        and risk_fact_date
        and reconstruction_as_of >= risk_fact_date
    )
    formal = _latest_formal_review_risk(root)
    if "current_strategy_return_pct_gross" not in summary and formal.get("risk_pct") is not None:
        risk_pct = round(float(formal["risk_pct"]), 2)
        risk_source = formal.get("source")
        risk_source_updated_at = formal.get("updated_at")
        strategy_equity = formal.get("equity") if formal.get("equity") is not None else summary.get("known_net_current_strategy_equity")
        risk_data_quality = "FORMAL_REVIEW_PRIMARY; RECONSTRUCTION_AUXILIARY"
    elif reconstruction_fresh:
        risk_pct = round(float(reconstruction_risk), 2)
        risk_source = "data/state/etf_strategy_equity.json"
        risk_source_updated_at = equity.get("generated_at")
        strategy_equity = summary.get("current_gross_strategy_equity")
        risk_data_quality = summary.get("known_net_equity_data_quality") or summary.get("equity_coverage_status")
    else:
        # Keep the canonical Gross owner visible when the latest valuation is
        # pending, but expose the freshness limitation explicitly.  A missing
        # value is reserved for the case where no Gross reconstruction exists;
        # Dashboard/old Known-net evidence must never fill that gap.
        gross_reconstruction = summary.get("current_strategy_return_pct_gross")
        risk_pct = round(float(gross_reconstruction), 2) if gross_reconstruction is not None else None
        risk_source = "data/state/etf_strategy_equity.json" if gross_reconstruction is not None else ""
        risk_source_updated_at = equity.get("generated_at") if gross_reconstruction is not None else ""
        strategy_equity = summary.get("current_gross_strategy_equity") if gross_reconstruction is not None else summary.get("known_net_current_strategy_equity")
        risk_data_quality = "DEGRADED_PENDING_LATEST_VALUATION" if gross_reconstruction is not None else "RISK_FACT_UNAVAILABLE"
    return {
        "etf_strategy_risk_pct": risk_pct,
        "risk_source": risk_source,
        "risk_source_updated_at": risk_source_updated_at,
        "reconstruction_risk_pct": reconstruction_risk,
        "reconstruction_as_of_transaction_date": reconstruction_as_of,
        "reconstruction_fresh_for_market_date": reconstruction_fresh,
        "etf_holding_unrealized_pct": round(etf_float / capital * 100.0, 2),
        "etf_drawdown_from_high_pct": summary.get("known_net_current_drawdown_pct"),
        "equity_data_quality": risk_data_quality,
        "strategy_equity_known_net": strategy_equity,
        "high_watermark": summary.get("known_net_high_watermark", capital),
        "fee_status": summary.get("fee_status", ""),
        "interpretation": "唯一ETF策略风险率决定风险区间；正式review优先，合格且新鲜的canonical reconstruction仅在无正式review时使用；Dashboard仅作人类projection。持仓浮盈亏率与高水位回撤率只解释持仓压力和近期改善/恶化。",
        "read_only": True,
    }




def build_account_funding_summary(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "cash": account.get("cash"),
        "reserved_cash_for_settlement": account.get("reserved_cash_for_settlement", 0),
        "deployable_cash": account.get("deployable_cash"),
        "settlement_obligations": account.get("settlement_obligations") or [],
        "settlement_constraint_status": account.get("settlement_constraint_status", "NONE"),
        "settlement_cash_shortfall": account.get("settlement_cash_shortfall"),
        "funding_rule": "正式金额判断只使用canonical deployable_cash；锁定待结算资金不得用于Trial或Confirm。",
        "read_only": True,
    }


def build_decision_trace(root: Path, timing: dict[str, str] | None = None) -> dict[str, Any]:
    """Build observability metadata from the existing canonical market identity."""
    current = read_current(root)
    snapshot = str(current.get("latest_snapshot") or "")
    snapshot_commit = str(current.get("snapshot_commit") or "")
    market_date = str(current.get("market_date") or "")
    node = str(current.get("latest_valid_node") or "")
    identity = "|".join((market_date, node, snapshot, snapshot_commit))
    trace_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    metadata = {
        "trace_id": f"formal-decision:{trace_id}",
        "market_date": market_date,
        "market_node": node,
        "snapshot": snapshot,
        "snapshot_commit": snapshot_commit,
        "boundary": "MINIMUM_DECISION_CONTEXT_READY_NOT_ESTABLISHED",
        "timing": {},
    }
    if timing:
        metadata["timing"] = dict(timing)
    return metadata


def _extend_analysis_coverage_with_discovery(base: dict[str, Any], formal_discovery: dict[str, Any] | None) -> dict[str, Any]:
    discovery = formal_discovery or {}
    candidates = [x for x in (discovery.get("candidates") or []) if isinstance(x, dict)]
    available = sum(
        str((x.get("historical_context") or {}).get("status") or "").upper() == "READY"
        and (x.get("formal_quote") or {}).get("latest_price") is not None
        for x in candidates
    )
    return {
        **base,
        "discovered_etfs_total": len(candidates),
        "discovered_etfs_available": available,
        "formal_discovery_status": discovery.get("status") or "NOT_REQUESTED",
        "formal_discovery_boundary": "节点临时正式评估对象；不改变持仓/观察管理身份。",
    }


def _extend_capital_comparison_with_discovery(base: dict[str, Any], formal_discovery: dict[str, Any] | None) -> dict[str, Any]:
    """Expose Discovery as node-opportunity evidence before final judgment.

    Observation is a continuous-information identity and is independent from the
    node-level opportunity role.  The machine layer must not grant trade authority
    or create a final capital ranking, but it must preserve that a Discovery object
    can be qualified as a node-level opportunity without first entering Observation.
    """
    discovery = formal_discovery or {}
    candidates = [x for x in (discovery.get("candidates") or []) if isinstance(x, dict)]
    eligibility_inputs = []
    for item in candidates:
        code = str(item.get("code") or "")
        if not code:
            continue
        history_ready = (item.get("historical_context") or {}).get("status") == "READY"
        quote_status = str(item.get("formal_quote_status") or "UNAVAILABLE").upper()
        if not history_ready:
            data_availability = "DEGRADED"
        elif quote_status == "READY":
            data_availability = "READY"
        elif quote_status == "SESSION_REFERENCE_READY":
            data_availability = "REFERENCE_READY"
        else:
            data_availability = "UNAVAILABLE"
        eligibility_inputs.append({
            "display_name": item.get("display_name") or f'{item.get("name", code)}（{code}）',
            "code": code,
            "category": "OBSERVATION_EVALUATION_INPUT",
            "eligibility": "OBSERVATION_FULL_EVALUATION",
            "data_availability": data_availability,
            "formal_quote_status": quote_status,
            "discovery_evidence": item,
            "action_boundary": "必须先完成本节点机会资格评估；Discovery不产生交易权限。持续Observation的ADMIT/RETAIN是独立身份判断，不是节点级机会形成的前置条件；只有正式形成机会资格后才能进入统一资本竞争。",
        })
    return {
        **base,
        "formal_discovery_included": False,
        "formal_discovery_candidate_count": len(eligibility_inputs),
        "formal_discovery_ingress_status": "ELIGIBILITY_ONLY",
        "formal_discovery_coverage_status": discovery.get("coverage_status") or "NOT_REQUESTED",
        "observation_eligibility_inputs": eligibility_inputs,
        "next_unit_capital_use": "由ChatGPT按MASTER对现金、全部持仓、已完成本节点机会资格判断的Discovery/观察机会、可释放资本及其他合法资本状态完成最终比较；Discovery本身不直接形成交易动作或最终排名。",
        "order_semantics": "LEGAL_CAPITAL_UNIVERSE_ONLY_NOT_RANKING",
    }

def build_decision_context(root: Path | None = None, observability: dict[str, Any] | None = None, formal_discovery: dict[str, Any] | None = None, decision_request_time: datetime | None = None, market_quote_context: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or root_from_env()
    current, account = read_current(root), read_account_fact(root)
    dashboard = root / "ETF当前状态_DASHBOARD.md"
    latest = current.get("latest_snapshot", "")
    snapshot = read_json(root / latest, {}) if latest else {}
    effective = evaluate_context_freshness(root, current)
    generated = now_utc()
    quality = build_data_quality_summary(snapshot)
    trace = observability or build_decision_trace(root)
    analysis_coverage = _extend_analysis_coverage_with_discovery(
        build_analysis_coverage(root, snapshot, account, quality), formal_discovery
    )
    capital_comparison = _extend_capital_comparison_with_discovery(
        read_json(root / "data" / "state" / "capital_efficiency_ranking.json", {"status": "NOT_BUILT", "ordered_candidates": [], "read_only": True}),
        formal_discovery,
    )
    return {
        "observability": trace, "generated_at": generated, "rules_version": current_rule_version(root) or str(current.get("rules_version") or ""), "market_date": current.get("market_date", ""), "latest_node": current.get("latest_valid_node", ""), "current": current, "latest_snapshot": snapshot, "data_status": effective, "freshness_at_context_build": effective,
        "data_quality_summary": quality, "account_funding": build_account_funding_summary(account), "etf_strategy_risk_metrics": build_etf_strategy_risk_metrics(root), "analysis_coverage": analysis_coverage, "point_in_time": build_point_in_time_summary(current, account, snapshot, generated), "scheduled_pulse_health": build_scheduled_pulse_health(root, current), "formal_action": build_formal_action_summary(account),
        # Query-time formal analysis already completed freshness assurance and
        # owns the request-bound quote snapshot. Reusing that exact context
        # prevents a second refresh/read from shifting the PIT boundary.
        "market_quote_router": market_quote_context if market_quote_context is not None else build_market_quote_context(root, decision_request_time=decision_request_time), "lifecycle_projection": build_lifecycle_projection(root),
        "decision_trigger": read_json(root / "data" / "state" / "decision_trigger.json", {"status": "NOT_BUILT", "requires_formal_reassessment": False, "read_only": True}),
        "capital_efficiency_ranking": capital_comparison,
        "intraday_path_features": read_json(root / "data" / "state" / "intraday_path_features.json", {"status": "MISSING", "features": []}), "research_evidence": build_research_evidence_summary(root),
        "research_context_file": "data/state/research_context.json", "relative_strength_file": "data/state/relative_strength.json", "research_evidence_delta_file": "data/state/research_evidence_delta.json",
        "research_master_feedback": {"current_decision": "研究证据及其节点变化直接进入机会判断、统一资本比较、持仓资本效率与正式输出解释。", "master_maintenance": "研究结论只有通过MASTER第8.1正式研究转化机制后才可修改MASTER；自动程序只提供证据，不修改规则。"},
        "dashboard_source": str(dashboard.relative_to(root)).replace("\\", "/"), "dashboard_summary": {"maintenance_mode": "candidate_only", "automatic_overwrite": False, "automatic_trade_output": False}, "account_fact_status": account["status"], "needs_account_screenshot": account["status"] != "VALID",
        "formal_etf_discovery": formal_discovery or {"status": "NOT_REQUESTED", "candidates": []}, "interaction_boundary": "ChatGPT聊天负责账户截图与正式交易判断；本文件不生成交易动作。正式机会比较完整覆盖全部持仓/观察ETF，并在本节点存在合格池外发现对象时把其作为临时正式评估输入加入同一次MASTER与全资本竞争；发现本身不产生管理身份或交易权限。",
    }

@@FILE:events/trades/20261111_1804_broker_close_trade_588370.json@@
{
  "event_id": "20261111_1804_broker_close_trade_588370",
  "idempotency_key": "588370|BUY|1400|1.691|2026-11-11T18:04:00+08:00",
  "confirmed_at_beijing": "2026-11-11T18:04:00+08:00",
  "execution_date": "2026-11-11",
  "confirmation_date": "2026-11-11",
  "notification_id": null,
  "name": "科创50增强ETF",
  "code": "588370",
  "side": "BUY",
  "quantity": 1400,
  "price": 1.691,
  "amount": 2367.4,
  "lifecycle": null,
  "source": "CHATGPT_USER_BROKER_SCREENSHOT",
  "source_confidence": null,
  "linked_decision_id": null,
  "hypothesis_id": null,
  "execution_status": "EXECUTED",
  "execution_attribution": {
    "status": "NO_LINKED_DECISION"
  },
  "executed_at_beijing": "2026-11-11T14:35:21+08:00"
}