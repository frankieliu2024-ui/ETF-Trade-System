from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median

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


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time(text: object) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)


def pct(new, old):
    new_v, old_v = safe_float(new), safe_float(old)
    if new_v is None or old_v in (None, 0.0):
        return None
    return round((new_v / old_v - 1.0) * 100.0, 4)


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
    lines = ["## 云端实时状态（自动同步）", "", f"> 更新时间：{account.get('updated_at','')}  ", f"> 来源：{account.get('source','')}  ", f"> 场景：{scenario}  ", "> 本区块只同步已确认账户事实与ChatGPT已形成的正式决策；自动程序不得自行推导交易权限或下单。", "", "|项目|最新事实|", "|-|-|", f"|总资产|{money(account.get('total_asset'))}|", f"|股票市值|{money(account.get('stock_market_value'))}|", f"|可用资金|{money(account.get('cash'))}|", f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|", f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|", f"|账户总风险暴露率|约{exposure:.2f}%|", f"|ETF持仓浮动盈亏|{money(etf_pnl)}|", f"|ETF策略风险率|约{risk_rate:.2f}%（仅按MASTER固定20万元计划本金计算，不由本脚本推导风险许可）|", "", "### 当前持仓事实", "", "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|"]
    for p in positions:
        lines.append(f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|{float(p.get('cost') or 0):.3f}|{float(p.get('last_price') or 0):.3f}|{money(p.get('market_value'))}|{money(p.get('holding_pnl'))}（{float(p.get('holding_pnl_pct') or 0):+.2f}%）|")
    lines += ["", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。", f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。"]
    if decision:
        title = "最近一次正式收盘复盘" if scenario == "POST_CLOSE_REVIEW" else "最近一次正式盘中决策"
        lines += ["", f"### {title}", "", f"- 风险许可：{decision.get('risk_permission','未提供')}", f"- 生命周期：{decision.get('lifecycle','未提供')}", f"- 唯一主候选：{decision.get('main_candidate','无新的主候选。')}", f"- 金额与动作：{decision.get('amount_action','未提供')}", f"- 最大风险或0元主因：{decision.get('decisive_reason','未提供')}", f"- 决策数据时点：{decision.get('data_as_of_beijing','未提供')}"]
    else:
        lines += ["", "最近一次正式决策未随本次同步请求提供；脚本不自行推断，保留人工/ChatGPT正式决议。"]
    lines += ["", f"同步请求：`{request.get('request_id','')}`。"]
    return "\n".join(lines)


def select_point_in_time_snapshot(market_date: str, decision_time: str) -> tuple[str, dict, str]:
    cutoff = parse_time(decision_time)
    if not market_date or cutoff is None:
        return "", {}, "NO_VALID_DECISION_TIME"
    candidates = []
    for path in sorted((ROOT / "data/market/snapshots").glob(f"{market_date}_*.json")):
        try:
            snap = load_json(path)
        except Exception:
            continue
        if snap.get("market_date") != market_date or snap.get("quality_status") != "PASS":
            continue
        captured = parse_time(snap.get("captured_at_beijing") or snap.get("captured_at"))
        if captured is not None and captured <= cutoff:
            candidates.append((captured, path, snap))
    if not candidates:
        return "", {}, "NO_PRIOR_SNAPSHOT"
    _, path, snap = max(candidates, key=lambda x: x[0])
    return str(path.relative_to(ROOT)).replace("\\", "/"), snap, "POINT_IN_TIME_PRIOR_OR_EQUAL"


def build_comparison_snapshot(snapshot: dict) -> dict:
    universe = load_json(ROOT / "config/market/etf_monitor_universe.json")
    names = {str(x.get("code")): str(x.get("name")) for x in (universe.get("objects") or []) if x.get("code")}
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}
    changes = [safe_float(rows[c].get("change_pct")) for c in names if c in rows]
    changes = [x for x in changes if x is not None]
    med = median(changes) if changes else None
    sh = safe_float((rows.get("000001") or {}).get("change_pct"))
    cyb = safe_float((rows.get("399006") or {}).get("change_pct"))
    items = []
    for code, name in names.items():
        row = rows.get(code)
        if not row:
            continue
        change = safe_float(row.get("change_pct"))
        items.append({"code": code, "name": name, "display_name": f"{name}（{code}）", "as_of_beijing": row.get("as_of_beijing"), "price": row.get("close"), "change_pct": change, "vs_universe_median_pct_points": round(change - med, 4) if change is not None and med is not None else None, "vs_shanghai_pct_points": round(change - sh, 4) if change is not None and sh is not None else None, "vs_chinext_pct_points": round(change - cyb, 4) if change is not None and cyb is not None else None})
    ranked = sorted([x for x in items if x.get("change_pct") is not None], key=lambda x: x["change_pct"], reverse=True)
    rank_map = {x["code"]: i + 1 for i, x in enumerate(ranked)}
    for item in items:
        item["descriptive_daily_return_rank"] = rank_map.get(item["code"])
    return {"as_of_beijing": snapshot.get("captured_at_beijing"), "market_phase": snapshot.get("market_phase"), "etf_count": len(items), "items": items, "interpretation_rule": "保留正式决策时点的全ETF横截面证据，用于以后验证候选选择质量；当日涨跌排名只是描述维度，不是资本效率评分，不生成轮动动作。"}


def resolve_hypothesis_id(decision: dict, code: str, market_date: str, decision_id: str) -> tuple[str, str]:
    explicit = str(decision.get("hypothesis_id") or "").strip()
    if explicit:
        return explicit, "EXPLICIT"
    lifecycle = str(decision.get("lifecycle") or "")
    if code and "Trial" in lifecycle:
        return f"HYP_{code}_{market_date.replace('-', '')}_{decision_id[-8:]}", "NEW_TRIAL"
    prior_dir = ROOT / "events/decisions"
    if code and prior_dir.exists():
        priors = []
        for path in prior_dir.glob("*.json"):
            try:
                obj = load_json(path)
            except Exception:
                continue
            if str(obj.get("candidate_code") or "") == code and obj.get("hypothesis_id"):
                priors.append(obj)
        if priors:
            priors.sort(key=lambda x: str(x.get("decision_time_beijing") or ""))
            latest = priors[-1]
            if not latest.get("hypothesis_closed"):
                return str(latest["hypothesis_id"]), "CARRY_FORWARD_PRIOR"
            return "", "PRIOR_HYPOTHESIS_CLOSED"
    return "", "UNRESOLVED"


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
    request_id = str(request.get("request_id") or "")
    fingerprint = hashlib.sha256(json.dumps({"request_id": request_id, "market_date": market_date, "formal_decision": decision}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    decision_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(decision.get("decision_id") or request_id or f"{market_date}_{fingerprint[:12]}"))

    decision_time = str(decision.get("data_as_of_beijing") or "")
    if not decision_time:
        decision_time = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    cutoff = parse_time(decision_time)
    snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(market_date, decision_time)
    supplied_price = safe_float(decision.get("price_at_decision"))
    supplied_as_of = str(decision.get("price_as_of_beijing") or "")
    supplied_time = parse_time(supplied_as_of)
    supplied_point_in_time = supplied_price is not None and supplied_time is not None and cutoff is not None and supplied_time <= cutoff
    price_at_decision = supplied_price if supplied_point_in_time else None
    price_as_of = supplied_as_of if supplied_point_in_time else ""
    price_source = "FORMAL_DECISION_SUPPLIED_POINT_IN_TIME" if supplied_point_in_time else ""
    if price_at_decision is None and code and snapshot:
        row = next((x for x in (snapshot.get("rows") or []) if str(x.get("symbol")) == code and x.get("quality_status") == "PASS"), None)
        if row:
            price_at_decision = row.get("close")
            price_as_of = str(row.get("as_of_beijing") or "")
            price_source = "POINT_IN_TIME_SNAPSHOT"
    if supplied_price is not None and not supplied_point_in_time and not price_source:
        pit_status = "SUPPLIED_PRICE_REJECTED_NO_VERIFIABLE_POINT_IN_TIME"

    hypothesis_id, hypothesis_link_status = resolve_hypothesis_id(decision, code, market_date, decision_id)
    lifecycle = str(decision.get("lifecycle") or "")
    hypothesis_closed = "退出" in lifecycle or str(decision.get("hypothesis_status") or "").upper() == "CLOSED"
    comparison = build_comparison_snapshot(snapshot) if snapshot else {"items": [], "interpretation_rule": "决策时点无可用历史快照，不使用未来数据补齐。"}
    event = {"event_type": "FORMAL_DECISION", "decision_id": decision_id, "fingerprint": fingerprint, "market_date": market_date, "decision_time_beijing": decision_time, "interaction_scenario": request.get("interaction_scenario"), "candidate_code": code, "candidate_name": name, "hypothesis_id": hypothesis_id, "hypothesis_link_status": hypothesis_link_status, "hypothesis_closed": hypothesis_closed, "price_at_decision": price_at_decision, "price_as_of_beijing": price_as_of, "price_source_snapshot": snapshot_rel, "price_source": price_source, "point_in_time_status": pit_status, "comparison_snapshot": comparison, "formal_decision": decision, "read_only_research_event": True, "decision_boundary": "只保存ChatGPT已经形成的正式决策和决策时点可见证据。禁止使用决策时点之后的行情回填价格或比较快照；研究留痕用于验证候选选择、假设生命周期、判断与执行质量，不自行推导交易权限。", "recorded_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
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
    event = {"event_type": "FORMAL_POST_CLOSE_REVIEW", "market_date": market_date, "account_updated_at": account.get("updated_at"), "fingerprint": fingerprint, "request_id": request.get("request_id"), "review": review, "updated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds")}
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(event_path, event)
    archive_entry = str(review.get("archive_entry") or "").strip()
    if archive_entry:
        ARCHIVE.write_text(upsert_managed_line(ARCHIVE.read_text(encoding="utf-8"), REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry), encoding="utf-8")
    experience_entry = str(review.get("experience_entry") or "").strip()
    if experience_entry:
        EXPERIENCE.write_text(upsert_managed_line(EXPERIENCE.read_text(encoding="utf-8"), REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry), encoding="utf-8")
    return True, False


def execution_attribution(trade: dict, linked_decision_id: str) -> dict:
    if not linked_decision_id:
        return {"status": "NO_LINKED_DECISION"}
    path = ROOT / "events/decisions" / f"{linked_decision_id}.json"
    if not path.exists():
        return {"status": "LINKED_DECISION_NOT_FOUND"}
    decision = load_json(path)
    dprice = safe_float(decision.get("price_at_decision"))
    eprice = safe_float(trade.get("price"))
    diff_pct = pct(eprice, dprice) if dprice not in (None, 0.0) and eprice is not None else None
    side = str(trade.get("side") or "").upper()
    is_buy = side in {"BUY", "B", "买", "买入"} or "买" in side
    is_sell = side in {"SELL", "S", "卖", "卖出"} or "卖" in side
    adverse = diff_pct if is_buy else (-diff_pct if is_sell and diff_pct is not None else None)
    dt0 = parse_time(decision.get("decision_time_beijing"))
    dt1 = parse_time(trade.get("confirmed_at_beijing"))
    delay = round((dt1 - dt0).total_seconds(), 1) if dt0 and dt1 else None
    return {"status": "READY" if dprice is not None and eprice is not None else "PARTIAL", "decision_id": linked_decision_id, "hypothesis_id": decision.get("hypothesis_id"), "decision_price": dprice, "execution_price": eprice, "execution_price_vs_decision_pct": diff_pct, "adverse_execution_cost_pct": adverse, "decision_to_execution_seconds": delay, "method_note": "正的adverse_execution_cost_pct表示相对正式决策价格出现不利执行偏差；买入价更高或卖出价更低均为正。该指标分离判断质量与执行质量，不改变交易权限。"}



def _trade_idempotency_key(trade: dict, confirmed_at: str) -> str:
    explicit = str(trade.get("idempotency_key") or "").strip()
    return explicit or "|".join(str(trade.get(key) or "") for key in ("code", "side", "quantity", "price")) + "|" + str(confirmed_at)


def _find_existing_trade(trade: dict, confirmed_at: str, key: str) -> dict | None:
    directory = ROOT / "events" / "trades"
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try: prior = load_json(path)
        except Exception: continue
        if prior.get("idempotency_key") == key or (all(str(prior.get(k) or "") == str(trade.get(k) or "") for k in ("code", "side", "quantity", "price")) and str(prior.get("confirmed_at_beijing") or "") == str(confirmed_at)):
            return prior
    return None

def _latest_trade_event_id() -> str:
    directory = ROOT / "events" / "trades"
    candidates = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            event = load_json(path)
        except Exception:
            continue
        event_id = str(event.get("event_id") or "")
        if event_id:
            candidates.append((str(event.get("confirmed_at_beijing") or ""), event_id))
    return max(candidates)[1] if candidates else ""



def _account_change_events(prior: dict, current: dict, request: dict, trade: dict | None) -> list[dict]:
    """Record only observable account deltas; never infer an unexplained trade."""
    prior_positions = {str(x.get("code")): x for x in (prior.get("positions") or []) if x.get("code")}
    current_positions = {str(x.get("code")): x for x in (current.get("positions") or []) if x.get("code")}
    codes = sorted(set(prior_positions) | set(current_positions))
    confirmed_code = str((trade or {}).get("code") or "")
    confirmed_side = str((trade or {}).get("side") or "").upper()
    confirmed_qty = safe_float((trade or {}).get("quantity"))
    event_type = str(request.get("account_change_event_type") or "").strip() or (
        "USER_REPORTED_TRADE" if trade else "BROKER_SCREENSHOT_CHANGE"
    )
    event_time = str((trade or {}).get("confirmed_at_beijing") or current.get("updated_at") or "")
    events: list[dict] = []
    for code in codes:
        before = safe_float(prior_positions.get(code, {}).get("quantity")) or 0.0
        after = safe_float(current_positions.get(code, {}).get("quantity")) or 0.0
        delta = round(after - before, 8)
        if delta == 0:
            continue
        row = current_positions.get(code) or prior_positions.get(code) or {}
        explained = bool(
            trade and code == confirmed_code and confirmed_qty is not None
            and abs(abs(delta) - confirmed_qty) < 1e-8
            and ((delta > 0 and confirmed_side in {"BUY", "B", "买入", "买"})
                 or (delta < 0 and confirmed_side in {"SELL", "S", "卖出", "卖"}))
        )
        key_body = {"event_type": event_type, "code": code, "delta": delta, "event_time": event_time}
        key = "account_change_" + hashlib.sha256(json.dumps(key_body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        events.append({
            "event_id": key, "idempotency_key": key, "event_type": event_type,
            "event_time": event_time, "object": display_name(row),
            "code": code, "quantity_before": before, "quantity_after": after,
            "quantity_delta": delta,
            "change_summary": ("买入/新增持仓" if delta > 0 else "卖出/减少持仓"),
            "reconciliation_status": "RECONCILED_BY_CONFIRMED_TRADE" if explained else "UNRECONCILED_ACCOUNT_CHANGE",
            "source": str(current.get("source") or request.get("source") or "USER_CONFIRMED"),
            "read_only": True,
        })
    for field in ("cash", "total_asset"):
        before = safe_float(prior.get(field))
        after = safe_float(current.get(field))
        if before is None or after is None or abs(after - before) < 0.005:
            continue
        key_body = {"event_type": event_type, "field": field, "delta": round(after - before, 2), "event_time": event_time}
        key = "account_change_" + hashlib.sha256(json.dumps(key_body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        events.append({
            "event_id": key, "idempotency_key": key, "event_type": event_type,
            "event_time": event_time, "object": field, "change_summary": f"{field}变化 {after - before:+.2f}",
            "amount_before": before, "amount_after": after, "amount_delta": round(after - before, 2),
            "reconciliation_status": "RECONCILED_BY_CONFIRMED_TRADE" if trade else "UNRECONCILED_ACCOUNT_CHANGE",
            "source": str(current.get("source") or request.get("source") or "USER_CONFIRMED"),
            "read_only": True,
        })
    return events


def _apply_trade_to_account(prior: dict, trade: dict) -> dict:
    """Safely carry a user-confirmed trade into the account fact when no screenshot is supplied."""
    account = json.loads(json.dumps(prior))
    account.setdefault("positions", [])
    code = str(trade.get("code") or "")
    side = str(trade.get("side") or "").upper()
    qty = safe_float(trade.get("quantity"))
    amount = safe_float(trade.get("amount"))
    if not code or qty is None or qty <= 0 or amount is None:
        return account
    sign = 1 if side in {"BUY", "B", "买入", "买"} else -1 if side in {"SELL", "S", "卖出", "卖"} else 0
    if sign == 0:
        return account
    position = next((p for p in account["positions"] if str(p.get("code")) == code), None)
    if position is None:
        if sign < 0:
            return account
        position = {"asset_type": trade.get("asset_type") or "ETF", "name": trade.get("name") or code, "code": code, "quantity": 0, "cost": safe_float(trade.get("price")) or 0, "last_price": safe_float(trade.get("price")) or 0, "market_value": 0, "holding_pnl": 0, "holding_pnl_pct": 0}
        account["positions"].append(position)
    before = safe_float(position.get("quantity")) or 0.0
    after = before + sign * qty
    if after < -1e-8:
        return account
    position["quantity"] = int(after) if abs(after - round(after)) < 1e-8 else after
    if sign > 0 and position.get("cost") in (None, 0, 0.0):
        position["cost"] = safe_float(trade.get("price")) or position.get("cost") or 0
    if position.get("last_price") in (None, 0, 0.0):
        position["last_price"] = safe_float(trade.get("price")) or 0
    position["market_value"] = round(float(position.get("last_price") or 0) * float(position["quantity"]), 2)
    cash_delta = -sign * amount
    account["cash"] = round((safe_float(account.get("cash")) or 0) + cash_delta, 2)
    if account.get("total_asset") is not None:
        account["total_asset"] = round((safe_float(account.get("total_asset")) or 0) + cash_delta, 2)
    account["updated_at"] = str(trade.get("confirmed_at_beijing") or account.get("updated_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds"))
    account["last_confirmed_market_date"] = account["updated_at"][:10]
    account["status"] = "VALID"
    account["source"] = str(trade.get("source") or "USER_REPORTED_TRADE_CONFIRMED")
    account["validity_mode"] = "EVENT_DRIVEN_TRADE_CONFIRMED"
    return account


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    req_path = (ROOT / args.request_path).resolve()
    if ROOT not in req_path.parents or not req_path.exists():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    trade = request.get("trade_event")
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    if not supplied_account and isinstance(trade, dict):
        supplied_account = _apply_trade_to_account(prior_account, trade)
    if supplied_account:
        supplied_account = json.loads(json.dumps(supplied_account))
        if "formal_action" not in supplied_account and prior_account.get("formal_action"):
            supplied_account["formal_action"] = prior_account["formal_action"]
        supplied_account.setdefault("status", "VALID")
        supplied_account.setdefault("validity_mode", "EVENT_DRIVEN_CARRY_FORWARD")
        supplied_account.setdefault("orders", [])
        supplied_account.setdefault("trades", [])
        prior_events = prior_account.get("account_change_events_after_confirmed_at") or []
        new_events = _account_change_events(prior_account, supplied_account, request, trade)
        known = {str(x.get("idempotency_key") or x.get("event_id") or "") for x in prior_events}
        supplied_account["account_change_events_after_confirmed_at"] = prior_events + [x for x in new_events if str(x.get("idempotency_key")) not in known]
        atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    latest_trade_event_id = _latest_trade_event_id()
    if latest_trade_event_id:
        current_path = ROOT / "data/state/CURRENT.json"
        current = load_json(current_path) if current_path.exists() else {}
        if current.get("last_trade_event_id") != latest_trade_event_id:
            current["last_trade_event_id"] = latest_trade_event_id
            current["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            atomic_json_write(current_path, current)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")
    decision_recorded, decision_id = record_formal_decision(request)
    if decision_recorded:
        decision = request.get("formal_decision") or {}
        prior_action = account.get("formal_action") or {}
        same_executed_decision = (
            str(prior_action.get("decision_id") or "") == decision_id
            and str(prior_action.get("execution_status") or "").upper() == "EXECUTED"
        )
        # Replaying the same formal decision is idempotent: an executed action
        # must never be downgraded back to PENDING. A genuinely new decision
        # may still become the current pending action.
        if not same_executed_decision:
            account["formal_action"] = {"action": decision.get("action") or decision.get("amount_action") or "", "quantity": decision.get("quantity"), "decision_id": decision_id, "decision_time": decision.get("decision_time") or decision.get("data_as_of_beijing") or datetime.now(SHANGHAI).isoformat(timespec="seconds"), "source": "CHATGPT_FORMAL_DECISION", "lifecycle": decision.get("lifecycle"), "applicable_object": decision.get("candidate_code") or decision.get("code") or "", "validity": "ACTIVE", "execution_status": "PENDING"}
            atomic_json_write(ACCOUNT, account)
    dashboard = replace_block(DASHBOARD.read_text(encoding="utf-8"), START, END, build_dashboard_block(account, request.get("formal_decision"), request), insert_after_heading=True)
    DASHBOARD.write_text(dashboard, encoding="utf-8")
    trade_event_recorded = False
    if trade:
        confirmed_at = trade.get("confirmed_at_beijing") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
        else:
            event_id = str(trade.get("event_id") or request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
            linked_decision_id = str(trade.get("decision_id") or decision_id or "")
            attribution = execution_attribution({**trade, "confirmed_at_beijing": confirmed_at}, linked_decision_id)
            event = {"event_id": event_id, "idempotency_key": idempotency_key, "confirmed_at_beijing": confirmed_at, "name": trade.get("name"), "code": trade.get("code"), "side": trade.get("side"), "quantity": trade.get("quantity"), "price": trade.get("price"), "amount": trade.get("amount"), "lifecycle": trade.get("lifecycle"), "source": trade.get("source", account.get("source")), "linked_decision_id": linked_decision_id or None, "hypothesis_id": trade.get("hypothesis_id") or attribution.get("hypothesis_id") or None, "execution_status": "EXECUTED", "execution_attribution": attribution}
            event_path = ROOT / "events" / "trades" / f"{event_id}.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(event_path, event)
            ARCHIVE.write_text(append_managed_line(ARCHIVE.read_text(encoding="utf-8"), TRADE_START, TRADE_END, f"- {event['confirmed_at_beijing']}：{event.get('name')}（{event.get('code')}）{event.get('side')} {event.get('quantity')}份/股，成交价{event.get('price')}，金额{event.get('amount')}；来源：{event.get('source')}。"), encoding="utf-8")
            EXPERIENCE.write_text(append_managed_line(EXPERIENCE.read_text(encoding="utf-8"), CASE_START, CASE_END, f"- 待复盘CASE｜{event['confirmed_at_beijing']}｜{event.get('name')}（{event.get('code')}）｜{event.get('side')} {event.get('quantity')}份/股｜生命周期：{event.get('lifecycle') or '待确认'}｜仅登记真实成交，复盘结论留待盘后形成。"), encoding="utf-8")
            trade_event_recorded = True
        account["formal_action"] = {**(account.get("formal_action") or {}), "execution_status": "EXECUTED", "execution_fact_ref": f"events/trades/{event_id}.json", "last_executed_event_id": event_id}
        atomic_json_write(ACCOUNT, account)
    review_recorded, review_idempotent = record_post_close_review(account, request)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": True, "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
