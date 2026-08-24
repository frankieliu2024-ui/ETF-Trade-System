from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from state_manager import atomic_json_write

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
DASHBOARD = ROOT / "ETF当前状态_DASHBOARD.md"
ARCHIVE = ROOT / "ETF市场行情档案_2026.md"
EXPERIENCE = ROOT / "ETF交易复盘与经验库_2026.md"
ACCOUNT = ROOT / "data/state/account_fact.json"
START = "<!-- AUTO_STATE_SYNC_START -->"
END = "<!-- AUTO_STATE_SYNC_END -->"
TRADE_START = "<!-- AUTO_TRADE_EVENTS_START -->"
TRADE_END = "<!-- AUTO_TRADE_EVENTS_END -->"
CASE_START = "<!-- AUTO_CASE_INTAKE_START -->"
CASE_END = "<!-- AUTO_CASE_INTAKE_END -->"
REVIEW_ARCHIVE_START = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->"
REVIEW_ARCHIVE_END = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->"
REVIEW_EXPERIENCE_START = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->"
REVIEW_EXPERIENCE_END = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def replace_block(text: str, start: str, end: str, block: str, insert_after_heading: bool = False) -> str:
    managed = f"{start}\n{block.rstrip()}\n{end}"
    if start in text and end in text:
        a = text.index(start)
        b = text.index(end, a) + len(end)
        return text[:a] + managed + text[b:]
    if insert_after_heading:
        lines = text.splitlines()
        pos = 1 if lines and lines[0].startswith("#") else 0
        lines[pos:pos] = ["", managed, ""]
        return "\n".join(lines).rstrip() + "\n"
    return text.rstrip() + "\n\n" + managed + "\n"


def append_managed_line(text: str, start: str, end: str, line: str) -> str:
    if start in text and end in text:
        a = text.index(start) + len(start)
        b = text.index(end, a)
        existing = text[a:b].strip()
        body = (existing + "\n" + line).strip() if existing else line
        return text[:a] + "\n" + body + "\n" + text[b:]
    return text.rstrip() + f"\n\n{start}\n{line}\n{end}\n"


def upsert_managed_line(text: str, start: str, end: str, key: str, line: str) -> str:
    tagged = f"{key}｜{line}"
    if start in text and end in text:
        a = text.index(start) + len(start)
        b = text.index(end, a)
        existing_lines = [x for x in text[a:b].strip().splitlines() if x.strip()]
        kept = [x for x in existing_lines if not x.startswith(f"{key}｜")]
        kept.append(tagged)
        body = "\n".join(kept)
        return text[:a] + "\n" + body + "\n" + text[b:]
    return text.rstrip() + f"\n\n{start}\n{tagged}\n{end}\n"


def money(v: object) -> str:
    try:
        return f"{float(v):,.2f}元"
    except Exception:
        return "—"


def display_name(p: dict) -> str:
    return f"{p.get('name', '')}（{p.get('code', '')}）"


def build_dashboard_block(account: dict, decision: dict | None, request: dict) -> str:
    positions = account.get("positions") or []
    etfs = [p for p in positions if p.get("asset_type") == "ETF"]
    stocks = [p for p in positions if p.get("asset_type") == "STOCK"]
    etf_pnl = sum(float(p.get("holding_pnl") or 0) for p in etfs)
    risk_rate = etf_pnl / 200000.0 * 100.0
    total_asset = float(account.get("total_asset") or 0)
    exposure = (float(account.get("stock_market_value") or 0) / total_asset * 100.0) if total_asset else 0.0
    scenario = request.get("interaction_scenario") or "UNSPECIFIED"
    lines = [
        "## 云端实时状态（自动同步）",
        "",
        f"> 更新时间：{account.get('updated_at','')}  ",
        f"> 来源：{account.get('source','')}  ",
        f"> 场景：{scenario}  ",
        "> 本区块只同步已确认账户事实与ChatGPT已形成的正式决策；自动程序不得自行推导交易权限或下单。",
        "",
        "|项目|最新事实|",
        "|-|-|",
        f"|总资产|{money(account.get('total_asset'))}|",
        f"|股票市值|{money(account.get('stock_market_value'))}|",
        f"|可用资金|{money(account.get('cash'))}|",
        f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|",
        f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|",
        f"|账户总风险暴露率|约{exposure:.2f}%|",
        f"|ETF持仓浮动盈亏|{money(etf_pnl)}|",
        f"|ETF策略风险率|约{risk_rate:.2f}%（仅按MASTER固定20万元计划本金计算，不由本脚本推导风险许可）|",
        "",
        "### 当前持仓事实",
        "",
        "|标的|数量|成本|现价|市值|浮动盈亏|",
        "|-|-:|-:|-:|-:|-:|",
    ]
    for p in positions:
        lines.append(
            f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|{float(p.get('cost') or 0):.3f}|{float(p.get('last_price') or 0):.3f}|{money(p.get('market_value'))}|{money(p.get('holding_pnl'))}（{float(p.get('holding_pnl_pct') or 0):+.2f}%）|"
        )
    etf_names = "、".join(display_name(p) for p in etfs) or "无"
    stock_names = "、".join(display_name(p) for p in stocks) or "无"
    lines += ["", f"持仓ETF：{etf_names}。", f"账户个股：{stock_names}。"]
    if decision:
        title = "最近一次正式收盘复盘" if scenario == "POST_CLOSE_REVIEW" else "最近一次正式盘中决策"
        lines += [
            "",
            f"### {title}",
            "",
            f"- 风险许可：{decision.get('risk_permission','未提供')}",
            f"- 生命周期：{decision.get('lifecycle','未提供')}",
            f"- 唯一主候选：{decision.get('main_candidate','无新的主候选。')}",
            f"- 金额与动作：{decision.get('amount_action','未提供')}",
            f"- 最大风险或0元主因：{decision.get('decisive_reason','未提供')}",
            f"- 决策数据时点：{decision.get('data_as_of_beijing','未提供')}",
        ]
    else:
        lines += ["", "最近一次正式决策未随本次同步请求提供；脚本不自行推断，保留人工/ChatGPT正式决议。"]
    lines += ["", f"同步请求：`{request.get('request_id','')}`。"]
    return "\n".join(lines)


def record_formal_decision(request: dict) -> tuple[bool, str]:
    decision = request.get("formal_decision")
    if not isinstance(decision, dict) or not decision:
        return False, ""
    current_path = ROOT / "data/state/CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    market_date = str(request.get("market_date") or current.get("market_date") or "")
    main_candidate = str(decision.get("main_candidate") or "")
    explicit_code = str(decision.get("candidate_code") or decision.get("code") or "")
    match = re.search(r"（(\d{6})）", main_candidate) or re.search(r"(?<!\d)(\d{6})(?!\d)", main_candidate)
    code = explicit_code or (match.group(1) if match else "")
    name = str(decision.get("candidate_name") or "")
    if not name and code:
        name_match = re.search(rf"([^｜+，,；;]+?)（{re.escape(code)}）", main_candidate)
        if name_match:
            name = name_match.group(1).strip()

    snapshot_rel = str(current.get("latest_snapshot") or "")
    snapshot_path = ROOT / snapshot_rel if snapshot_rel else None
    snapshot = load_json(snapshot_path) if snapshot_path and snapshot_path.exists() else {}
    price_at_decision = None
    price_as_of = ""
    if code:
        row = next((x for x in (snapshot.get("rows") or []) if str(x.get("symbol")) == code and x.get("quality_status") == "PASS"), None)
        if row:
            price_at_decision = row.get("close")
            price_as_of = str(row.get("as_of_beijing") or "")

    decision_time = str(decision.get("data_as_of_beijing") or price_as_of or datetime.now(SHANGHAI).isoformat(timespec="seconds"))
    request_id = str(request.get("request_id") or "")
    fingerprint = hashlib.sha256(json.dumps({"request_id": request_id, "market_date": market_date, "formal_decision": decision}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    decision_id = str(decision.get("decision_id") or request_id or f"{market_date}_{fingerprint[:12]}")
    decision_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", decision_id)
    event = {
        "event_type": "FORMAL_DECISION",
        "decision_id": decision_id,
        "fingerprint": fingerprint,
        "market_date": market_date,
        "decision_time_beijing": decision_time,
        "interaction_scenario": request.get("interaction_scenario"),
        "candidate_code": code,
        "candidate_name": name,
        "price_at_decision": price_at_decision,
        "price_as_of_beijing": price_as_of,
        "price_source_snapshot": snapshot_rel,
        "formal_decision": decision,
        "read_only_research_event": True,
        "decision_boundary": "仅保存ChatGPT已经形成的正式决策及当时可见价格，用于后续结果归因；不自行推导交易权限或动作。",
        "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    }
    event_path = ROOT / "events/decisions" / f"{decision_id}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    if event_path.exists():
        prior = load_json(event_path)
        if prior.get("fingerprint") == fingerprint and prior.get("price_source_snapshot") == snapshot_rel:
            return True, decision_id
    atomic_json_write(event_path, event)
    return True, decision_id


def record_post_close_review(account: dict, request: dict) -> tuple[bool, bool]:
    review = request.get("formal_review")
    if request.get("interaction_scenario") != "POST_CLOSE_REVIEW" or not review:
        return False, False
    market_date = str(review.get("market_date") or request.get("market_date") or account.get("last_confirmed_market_date") or "")
    if not market_date:
        raise RuntimeError("POST_CLOSE_REVIEW requires market_date")
    payload = {"market_date": market_date, "account_updated_at": account.get("updated_at"), "formal_review": review}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    event_path = ROOT / "events" / "reviews" / f"{market_date}.json"
    prior = load_json(event_path) if event_path.exists() else {}
    if prior.get("fingerprint") == fingerprint:
        return True, True
    event = {
        "event_type": "FORMAL_POST_CLOSE_REVIEW",
        "market_date": market_date,
        "account_updated_at": account.get("updated_at"),
        "fingerprint": fingerprint,
        "request_id": request.get("request_id"),
        "review": review,
        "updated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    }
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(event_path, event)
    archive_entry = str(review.get("archive_entry") or "").strip()
    if archive_entry:
        archive = upsert_managed_line(ARCHIVE.read_text(encoding="utf-8"), REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry)
        ARCHIVE.write_text(archive, encoding="utf-8")
    experience_entry = str(review.get("experience_entry") or "").strip()
    if experience_entry:
        experience = upsert_managed_line(EXPERIENCE.read_text(encoding="utf-8"), REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry)
        EXPERIENCE.write_text(experience, encoding="utf-8")
    return True, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    req_path = (ROOT / args.request_path).resolve()
    if ROOT not in req_path.parents or not req_path.exists():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)

    supplied_account = request.get("account_fact")
    if supplied_account:
        supplied_account.setdefault("status", "VALID")
        supplied_account.setdefault("validity_mode", "EVENT_DRIVEN_CARRY_FORWARD")
        supplied_account.setdefault("orders", [])
        supplied_account.setdefault("trades", [])
        supplied_account.setdefault("account_change_events_after_confirmed_at", [])
        atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")

    decision_recorded, decision_id = record_formal_decision(request)

    dashboard = DASHBOARD.read_text(encoding="utf-8")
    dashboard = replace_block(dashboard, START, END, build_dashboard_block(account, request.get("formal_decision"), request), insert_after_heading=True)
    DASHBOARD.write_text(dashboard, encoding="utf-8")

    trade = request.get("trade_event")
    if trade:
        event_id = str(trade.get("event_id") or request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
        event = {
            "event_id": event_id,
            "confirmed_at_beijing": trade.get("confirmed_at_beijing") or account.get("updated_at"),
            "name": trade.get("name"),
            "code": trade.get("code"),
            "side": trade.get("side"),
            "quantity": trade.get("quantity"),
            "price": trade.get("price"),
            "amount": trade.get("amount"),
            "lifecycle": trade.get("lifecycle"),
            "source": trade.get("source", account.get("source")),
            "linked_decision_id": trade.get("decision_id") or decision_id or None,
        }
        event_path = ROOT / "events" / "trades" / f"{event_id}.json"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(event_path, event)
        fact_line = f"- {event['confirmed_at_beijing']}：{event.get('name')}（{event.get('code')}）{event.get('side')} {event.get('quantity')}份/股，成交价{event.get('price')}，金额{event.get('amount')}；来源：{event.get('source')}。"
        archive = append_managed_line(ARCHIVE.read_text(encoding="utf-8"), TRADE_START, TRADE_END, fact_line)
        ARCHIVE.write_text(archive, encoding="utf-8")
        case_line = f"- 待复盘CASE｜{event['confirmed_at_beijing']}｜{event.get('name')}（{event.get('code')}）｜{event.get('side')} {event.get('quantity')}份/股｜生命周期：{event.get('lifecycle') or '待确认'}｜仅登记真实成交，复盘结论留待盘后形成。"
        experience = append_managed_line(EXPERIENCE.read_text(encoding="utf-8"), CASE_START, CASE_END, case_line)
        EXPERIENCE.write_text(experience, encoding="utf-8")

    review_recorded, review_idempotent = record_post_close_review(account, request)
    result = {
        "ok": True,
        "request_id": request.get("request_id"),
        "interaction_scenario": request.get("interaction_scenario"),
        "account_updated_at": account.get("updated_at"),
        "dashboard_updated": True,
        "formal_decision_recorded": decision_recorded,
        "formal_decision_id": decision_id,
        "trade_event_recorded": bool(trade),
        "post_close_review_recorded": review_recorded,
        "post_close_review_idempotent_noop": review_idempotent,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
