from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
OUT = STATE / "execution_reconciliation.json"
TZ = timezone(timedelta(hours=8))
LOOKBACK_DAYS = 5


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def position_map(account: dict) -> dict[str, dict]:
    return {str(p.get("code") or ""): p for p in (account.get("positions") or []) if p.get("code")}


def git_previous_account() -> tuple[dict, str]:
    """Return account_fact immediately before its latest committed change.

    This is used only for deterministic quantity-delta detection. It never turns
    an observed delta into a trade fact without user confirmation.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-n", "1", "--format=%H", "--", "data/state/account_fact.json"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        change_sha = result.stdout.strip()
        if not change_sha:
            return {}, "NO_ACCOUNT_HISTORY"
        show = subprocess.run(
            ["git", "show", f"{change_sha}^:data/state/account_fact.json"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if show.returncode != 0 or not show.stdout.strip():
            return {}, "NO_PRIOR_ACCOUNT_VERSION"
        return json.loads(show.stdout), f"GIT_PARENT_OF_{change_sha[:12]}"
    except (OSError, json.JSONDecodeError):
        return {}, "ACCOUNT_HISTORY_UNAVAILABLE"


def decision_is_executable_trial(event: dict) -> bool:
    formal = event.get("formal_decision") or {}
    amount = str(formal.get("amount_action") or "")
    lifecycle = str(formal.get("lifecycle") or "")
    risk = str(formal.get("risk_permission") or "")
    text = " ".join([amount, lifecycle, risk])
    has_trial = "Trial" in text
    has_5000 = bool(re.search(r"(?:5,?000|5000)\s*元?", text))
    prohibited = any(x in amount for x in ["新增0元", "0元", "禁止新增"])
    return has_trial and has_5000 and not prohibited


def recent_trial_intents() -> list[dict]:
    cutoff = datetime.now(TZ) - timedelta(days=LOOKBACK_DAYS)
    intents = []
    for path in (ROOT / "events" / "decisions").glob("*.json"):
        event = read_json(path, {})
        if event.get("event_type") != "FORMAL_DECISION" or not decision_is_executable_trial(event):
            continue
        dt = parse_dt(event.get("decision_time_beijing"))
        if dt is None or dt < cutoff:
            continue
        code = str(event.get("candidate_code") or "")
        if not code:
            continue
        formal = event.get("formal_decision") or {}
        intents.append({
            "decision_id": str(event.get("decision_id") or path.stem),
            "decision_time": event.get("decision_time_beijing"),
            "decision_market_date": event.get("market_date") or (dt.date().isoformat() if dt else ""),
            "code": code,
            "name": str(event.get("candidate_name") or ""),
            "lifecycle": "Trial",
            "planned_amount_yuan": 5000,
            "amount_action": formal.get("amount_action"),
            "source_event": str(path.relative_to(ROOT)).replace("\\", "/"),
        })
    intents.sort(key=lambda x: str(x.get("decision_time") or ""), reverse=True)
    return intents


def trades_for_code(code: str) -> list[dict]:
    rows = []
    for path in (ROOT / "events" / "trades").glob("*.json"):
        trade = read_json(path, {})
        if str(trade.get("code") or "") == code:
            rows.append(trade)
    return rows


def confirmed_against_intent(intent: dict) -> dict | None:
    decision_id = intent["decision_id"]
    decision_dt = parse_dt(intent.get("decision_time"))
    for trade in trades_for_code(intent["code"]):
        if str(trade.get("side") or "").upper() != "BUY":
            continue
        linked = str(trade.get("linked_decision_id") or "")
        trade_dt = parse_dt(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing"))
        if linked == decision_id:
            return trade
        if decision_dt and trade_dt and trade_dt >= decision_dt and trade_dt <= decision_dt + timedelta(days=LOOKBACK_DAYS):
            # Same-code unlinked buy is relevant evidence, but not enough for silent attribution.
            return {**trade, "_same_code_unlinked": True}
    return None


def build() -> dict:
    current = read_json(STATE / "account_fact.json", {})
    previous, history_source = git_previous_account()
    current_pos = position_map(current)
    previous_pos = position_map(previous)
    intents = recent_trial_intents()
    matches = []

    for intent in intents:
        code = intent["code"]
        trade = confirmed_against_intent(intent)
        if trade and not trade.get("_same_code_unlinked"):
            matches.append({
                "status": "CONFIRMED_BY_TRADE_EVENT",
                "requires_user_confirmation": False,
                "intent": intent,
                "trade_event_id": trade.get("event_id"),
                "execution_date": str(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing") or "")[:10],
                "confirmation_date": str(trade.get("confirmed_at_beijing") or "")[:10],
                "lifecycle_t_date": str(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing") or "")[:10],
            })
            continue

        old_qty = float((previous_pos.get(code) or {}).get("quantity") or 0)
        new_qty = float((current_pos.get(code) or {}).get("quantity") or 0)
        delta_qty = new_qty - old_qty
        if delta_qty <= 0:
            if trade and trade.get("_same_code_unlinked"):
                matches.append({
                    "status": "UNLINKED_BUY_TRADE_REQUIRES_ATTRIBUTION",
                    "requires_user_confirmation": True,
                    "intent": intent,
                    "trade_event_id": trade.get("event_id"),
                    "suggested_execution_date": str(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing") or "")[:10],
                    "reason": "发现同代码买入成交，但尚未明确归因到该Trial决策。",
                })
            continue

        row = current_pos.get(code) or {}
        approx_value = None
        cost = row.get("cost")
        try:
            if old_qty == 0 and cost is not None:
                approx_value = round(delta_qty * float(cost), 2)
        except (TypeError, ValueError):
            approx_value = None
        confidence = "HIGH" if old_qty == 0 else "MEDIUM"
        account_date = str(current.get("last_confirmed_market_date") or "")
        suggested_date = str(intent.get("decision_market_date") or account_date)
        matches.append({
            "status": "LIKELY_EXECUTED_REQUIRES_CONFIRMATION",
            "requires_user_confirmation": True,
            "confidence": confidence,
            "intent": intent,
            "observed_account_change": {
                "previous_quantity": old_qty,
                "current_quantity": new_qty,
                "quantity_increase": delta_qty,
                "approx_position_cost_value_yuan": approx_value,
                "account_fact_updated_at": current.get("updated_at"),
                "account_confirmed_market_date": account_date,
                "history_source": history_source,
            },
            "suggested_execution_date": suggested_date,
            "suggested_lifecycle_t_date": suggested_date,
            "confirmation_date": now_text()[:10],
            "user_confirmation_prompt": f"检测到{intent['name'] or code}（{code}）新增持仓，与{suggested_date}的5,000元Trial决策高度匹配。请确认这是否是按该Trial执行的买入；如果是，请确认实际交易日是否为{suggested_date}。",
            "safety_boundary": "这里只做反向对账候选，不自动生成成交事实；交易日与确认日分开记录，只有用户确认后才能回填Trial的T日。",
        })

    actionable = [x for x in matches if x.get("requires_user_confirmation")]
    result = {
        "schema_version": "1.0",
        "generated_at": now_text(),
        "status": "CONFIRMATION_REQUIRED" if actionable else ("RECONCILED" if matches else "NO_MATCH"),
        "lookback_days": LOOKBACK_DAYS,
        "matches": matches,
        "actionable_count": len(actionable),
        "account_fact_updated_at": current.get("updated_at"),
        "design_rule": "微信/正式决策可以先于成交确认；后续账户截图允许反向匹配最近可执行Trial。execution_date、confirmation_date、lifecycle_t_date必须分离，延迟上传不得把上传日误当交易日。",
        "safety_boundary": "不凭账户差异自动认定成交，不自动修改MASTER、交易权限或订单；模糊匹配只请求最小人工确认。",
    }
    write_json(OUT, result)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    build()
