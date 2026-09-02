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
    try:
        result = subprocess.run(["git", "log", "-n", "1", "--format=%H", "--", "data/state/account_fact.json"], cwd=ROOT, capture_output=True, text=True, check=False)
        change_sha = result.stdout.strip()
        if not change_sha:
            return {}, "NO_ACCOUNT_HISTORY"
        show = subprocess.run(["git", "show", f"{change_sha}^:data/state/account_fact.json"], cwd=ROOT, capture_output=True, text=True, check=False)
        if show.returncode != 0 or not show.stdout.strip():
            return {}, "NO_PRIOR_ACCOUNT_VERSION"
        return json.loads(show.stdout), f"GIT_PARENT_OF_{change_sha[:12]}"
    except (OSError, json.JSONDecodeError):
        return {}, "ACCOUNT_HISTORY_UNAVAILABLE"


def parse_money(text: str) -> int | None:
    patterns = [
        # Prefer explicitly labelled principal/amount over an earlier price.
        (r"成交(?:金额|本金)\s*[:：]?\s*([\d,]+(?:\.\d+)?)\s*元", 1),
        (r"(?<!\d)(\d+(?:\.\d+)?)\s*[kK](?!\w)", 1000),
        (r"(?<!\d)([\d,]+(?:\.\d+)?)\s*元", 1),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", "")) * multiplier
        except ValueError:
            continue
        if value > 0:
            return int(round(value))
    return None


def extract_sell_intents(text: str) -> list[dict]:
    out = []
    # Chinese security name（code） followed by an explicit sell/reduce/exit action.
    for m in re.finditer(r"([^；;，,。\n]{1,30}?)（(\d{6})）([^；;。\n]{0,60})", text):
        name, code, tail = m.group(1).strip(), m.group(2), m.group(3)
        if not any(x in tail for x in ["卖出", "减持", "降低风险", "退出", "清仓"]):
            continue
        qty_m = re.search(r"([\d,]+)\s*份", tail)
        full_exit = any(x in tail for x in ["全部卖出", "全部退出", "清仓", "全部清仓"])
        out.append({"code": code, "name": name, "side": "SELL", "planned_quantity": int(qty_m.group(1).replace(",", "")) if qty_m else None, "full_exit": full_exit})
    return out


def decision_intents(event: dict) -> list[dict]:
    formal = event.get("formal_decision") or {}
    amount_action = str(formal.get("amount_action") or "")
    lifecycle_text = str(formal.get("lifecycle") or "")
    dt = parse_dt(event.get("decision_time_beijing"))
    base = {
        "decision_id": str(event.get("decision_id") or ""),
        "decision_time": event.get("decision_time_beijing"),
        "decision_market_date": event.get("market_date") or (dt.date().isoformat() if dt else ""),
        "source_event": "",
    }
    intents: list[dict] = []
    code = str(event.get("candidate_code") or "")
    name = str(event.get("candidate_name") or "")
    amount = parse_money(amount_action)

    # Reconciliation consumes the formal decision as a fact. It does not re-enforce
    # MASTER amount buckets or risk-permission legality; those belong to the trading
    # decision layer. Any explicit positive buy amount can therefore be reconciled.
    lifecycle = "Trial" if "Trial" in lifecycle_text else ("Confirm" if "Confirm" in lifecycle_text else "")
    buy_word = any(word in amount_action for word in ["买入", "新增", "加仓", "投入"])
    if code and lifecycle and amount and buy_word:
        intents.append({**base, "code": code, "name": name, "side": "BUY", "lifecycle": lifecycle, "planned_amount_yuan": amount, "planned_quantity": None})

    for sell in extract_sell_intents(amount_action):
        intents.append({**base, **sell, "lifecycle": "EXIT_OR_RISK_REDUCTION", "planned_amount_yuan": None})
    return intents


def recent_intents() -> list[dict]:
    cutoff = datetime.now(TZ) - timedelta(days=LOOKBACK_DAYS)
    intents = []
    for path in (ROOT / "events" / "decisions").glob("*.json"):
        event = read_json(path, {})
        if event.get("event_type") != "FORMAL_DECISION":
            continue
        dt = parse_dt(event.get("decision_time_beijing"))
        if dt is None or dt < cutoff:
            continue
        for intent in decision_intents(event):
            intent["source_event"] = str(path.relative_to(ROOT)).replace("\\", "/")
            intents.append(intent)
    dedup = {}
    for x in intents:
        key = (x["decision_id"], x["code"], x["side"], x["lifecycle"])
        dedup[key] = x
    return sorted(dedup.values(), key=lambda x: str(x.get("decision_time") or ""), reverse=True)


def trades_for_code(code: str) -> list[dict]:
    rows = []
    for path in (ROOT / "events" / "trades").glob("*.json"):
        trade = read_json(path, {})
        if str(trade.get("code") or "") == code:
            rows.append(trade)
    return rows


def relevant_trades(intent: dict) -> list[dict]:
    decision_dt = parse_dt(intent.get("decision_time"))
    out = []
    for trade in trades_for_code(intent["code"]):
        if str(trade.get("side") or "").upper() != intent["side"]:
            continue
        trade_dt = parse_dt(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing"))
        if decision_dt and trade_dt and decision_dt <= trade_dt <= decision_dt + timedelta(days=LOOKBACK_DAYS):
            out.append(trade)
    return out


def planned_vs_actual(intent: dict, actual_qty: float | None, actual_amount: float | None) -> tuple[str, float | None]:
    if intent.get("planned_quantity") and actual_qty is not None:
        ratio = actual_qty / float(intent["planned_quantity"])
    elif intent.get("planned_amount_yuan") and actual_amount is not None:
        ratio = actual_amount / float(intent["planned_amount_yuan"])
    else:
        return "UNKNOWN", None
    if 0.9 <= ratio <= 1.1:
        return "FULL_OR_ROUNDING_MATCH", round(ratio, 4)
    if 0 < ratio < 0.9:
        return "PARTIAL_EXECUTION", round(ratio, 4)
    if ratio > 1.1:
        return "OVER_EXECUTION_OR_MULTIPLE_OPERATIONS", round(ratio, 4)
    return "UNKNOWN", round(ratio, 4)


def build() -> dict:
    current = read_json(STATE / "account_fact.json", {})
    previous, history_source = git_previous_account()
    current_pos, previous_pos = position_map(current), position_map(previous)
    intents = recent_intents()
    matches = []

    for intent in intents:
        code = intent["code"]
        # An exact decision link is canonical attribution even when the
        # broker execution timestamp precedes the later-recorded decision
        # timestamp. Keep only the bounded lookback, but do not require
        # execution_time >= decision_time for an exact linked event.
        decision_dt = parse_dt(intent.get("decision_time"))
        linked = []
        for trade in trades_for_code(code):
            if str(trade.get("side") or "").upper() != intent["side"]:
                continue
            if str(trade.get("linked_decision_id") or "") != intent["decision_id"]:
                continue
            trade_dt = parse_dt(trade.get("executed_at_beijing") or trade.get("confirmed_at_beijing"))
            if decision_dt and trade_dt and abs(trade_dt - decision_dt) > timedelta(days=LOOKBACK_DAYS):
                continue
            linked.append(trade)
        unlinked = [t for t in relevant_trades(intent) if str(t.get("linked_decision_id") or "") != intent["decision_id"]]
        if linked:
            actual_qty = sum(float(t.get("quantity") or 0) for t in linked)
            actual_amount = sum(float(t.get("amount") or 0) for t in linked)
            fill, ratio = planned_vs_actual(intent, actual_qty, actual_amount)
            dates = [str(t.get("executed_at_beijing") or t.get("confirmed_at_beijing") or "")[:10] for t in linked]
            matches.append({"status": "CONFIRMED_BY_TRADE_EVENT", "requires_user_confirmation": False, "intent": intent, "trade_event_ids": [t.get("event_id") for t in linked], "fill_status": fill, "fill_ratio": ratio, "execution_date": min(dates) if dates else "", "confirmation_date": max(str(t.get("confirmed_at_beijing") or "")[:10] for t in linked), "lifecycle_t_date": min(dates) if dates else ""})
            continue

        old_qty = float((previous_pos.get(code) or {}).get("quantity") or 0)
        new_qty = float((current_pos.get(code) or {}).get("quantity") or 0)
        signed_delta = new_qty - old_qty
        observed_qty = signed_delta if intent["side"] == "BUY" else -signed_delta

        if unlinked:
            actual_qty = sum(float(t.get("quantity") or 0) for t in unlinked)
            actual_amount = sum(float(t.get("amount") or 0) for t in unlinked)
            fill, ratio = planned_vs_actual(intent, actual_qty, actual_amount)
            multiple = len(unlinked) > 1
            matches.append({
                "status": "MULTIPLE_OPERATIONS_REQUIRE_DETAIL" if multiple else "UNLINKED_TRADE_REQUIRES_ATTRIBUTION",
                "requires_user_confirmation": True,
                "intent": intent,
                "trade_event_ids": [t.get("event_id") for t in unlinked],
                "fill_status": fill,
                "fill_ratio": ratio,
                "suggested_execution_date": str(unlinked[0].get("executed_at_beijing") or unlinked[0].get("confirmed_at_beijing") or "")[:10],
                "reason": "发现同代码同方向成交，但尚未明确归因到该正式决策。" if not multiple else "同一决策窗口内发现多笔同代码成交，仅靠最终持仓无法安全判断每笔归因。",
            })
            continue

        if observed_qty <= 0:
            continue

        row = current_pos.get(code) or previous_pos.get(code) or {}
        approx_amount = None
        try:
            if intent["side"] == "BUY" and old_qty == 0 and row.get("cost") is not None:
                approx_amount = round(observed_qty * float(row["cost"]), 2)
            elif intent["side"] == "SELL" and row.get("last_price") is not None:
                approx_amount = round(observed_qty * float(row["last_price"]), 2)
        except (TypeError, ValueError):
            pass
        fill, ratio = planned_vs_actual(intent, observed_qty, approx_amount)
        account_date = str(current.get("last_confirmed_market_date") or "")
        suggested_date = str(intent.get("decision_market_date") or account_date)
        ambiguity = fill in {"OVER_EXECUTION_OR_MULTIPLE_OPERATIONS"}
        if intent["side"] == "BUY" and old_qty > 0 and intent.get("planned_amount_yuan"):
            fill = "AMOUNT_NOT_INFERABLE_FROM_MIXED_COST"
            ratio = None
        action_word = "新增" if intent["side"] == "BUY" else "减少"
        lifecycle = intent["lifecycle"]
        prompt = f"检测到{intent['name'] or code}（{code}）持仓{action_word}{int(observed_qty):,}份，与{suggested_date}的{lifecycle}决策相符。请确认是否按该决策执行，并确认实际交易日。"
        if ambiguity:
            prompt += " 实际变化超过计划规模，可能存在多次操作，请补充成交明细后再归因。"
        elif fill == "PARTIAL_EXECUTION":
            prompt += " 当前迹象更像部分成交，请确认实际成交数量/金额。"
        matches.append({
            "status": "MULTIPLE_OPERATIONS_REQUIRE_DETAIL" if ambiguity else ("PARTIAL_EXECUTION_REQUIRES_CONFIRMATION" if fill == "PARTIAL_EXECUTION" else "LIKELY_EXECUTED_REQUIRES_CONFIRMATION"),
            "requires_user_confirmation": True,
            "confidence": "HIGH" if (intent["side"] == "BUY" and old_qty == 0) or intent.get("planned_quantity") else "MEDIUM",
            "intent": intent,
            "fill_status": fill,
            "fill_ratio": ratio,
            "observed_account_change": {"previous_quantity": old_qty, "current_quantity": new_qty, "quantity_change": signed_delta, "directional_quantity": observed_qty, "approx_amount_yuan": approx_amount, "account_fact_updated_at": current.get("updated_at"), "account_confirmed_market_date": account_date, "history_source": history_source},
            "suggested_execution_date": suggested_date,
            "suggested_lifecycle_t_date": suggested_date if lifecycle in {"Trial", "Confirm"} else None,
            "confirmation_date": now_text()[:10],
            "user_confirmation_prompt": prompt,
            "safety_boundary": "这里只生成反向对账候选；不自动生成成交事实。交易日、确认日和生命周期T日分开记录。",
        })

    actionable = [x for x in matches if x.get("requires_user_confirmation")]
    result = {
        "schema_version": "1.1",
        "generated_at": now_text(),
        "status": "CONFIRMATION_REQUIRED" if actionable else ("RECONCILED" if matches else "NO_MATCH"),
        "lookback_days": LOOKBACK_DAYS,
        "supported_intents": ["正式正金额买入", "明确份额减持/卖出", "全部退出/清仓（仅在事实足够时匹配）"],
        "matches": matches,
        "actionable_count": len(actionable),
        "account_fact_updated_at": current.get("updated_at"),
        "design_rule": "对账只消费正式决策事实，不重新执法MASTER金额档或风险许可；允许交易决策与实际成交异步确认，并支持部分成交和多次操作歧义识别。execution_date、confirmation_date、lifecycle_t_date必须分离。",
        "known_limitation": "仅凭最终持仓截图无法唯一还原同日买入后又卖出、卖出后又买回等净数量为零的往返交易；遇到此类情况必须补充券商成交明细。",
        "safety_boundary": "不凭账户差异自动认定成交，不自动修改MASTER、交易权限或订单；模糊、部分或多笔操作只请求最小人工确认。",
    }
    write_json(OUT, result)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    build()
