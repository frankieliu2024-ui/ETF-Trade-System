from __future__ import annotations

"""Minimal idempotent maintenance for confirmed account facts.

This module updates only human-readable fact documents plus the account reference
embedded in CURRENT. It never writes rule or permission documents and never
derives orders or lifecycle actions. It is invoked after an accepted state-sync
request, so account_fact remains the machine source of truth while CURRENT keeps
an up-to-date account pointer after the core market snapshot has already been
published.
"""

import argparse
import json
import os
from datetime import timezone, timedelta
from pathlib import Path

from confirmed_trade_facts import effective_confirmed_fee_fact
try:
    from build_stock_context import active_account_asset_codes, normalize_code, position_metric
except ModuleNotFoundError:
    from scripts.build_stock_context import active_account_asset_codes, normalize_code, position_metric
from formal_file_mutation_gateway import (
    replace_managed_block as replace_block,
    write_formal_text_if_changed,
)
from research_artifact_contract import archive_completed_research


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
START_DASH = "<!-- AUTO_STATE_SYNC_START -->"
END_DASH = "<!-- AUTO_STATE_SYNC_END -->"
START_ARCHIVE = "<!-- AUTO_ACCOUNT_FACT_SYNC_START -->"
END_ARCHIVE = "<!-- AUTO_ACCOUNT_FACT_SYNC_END -->"
ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def money(value) -> str:
    try:
        return f"{float(value):,.2f}元"
    except (TypeError, ValueError):
        return "—"


def display_name(position: dict) -> str:
    return f"{position.get('name', '')}（{position.get('code', '')}）"



def latest_canonical_formal_decision(root: Path = ROOT) -> dict:
    """Return the latest canonical FORMAL_DECISION, never a review/stage artifact."""
    candidates = []
    for path in (root / "events" / "decisions").glob("*.json"):
        try:
            event = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if str(event.get("event_type") or "").upper() != "FORMAL_DECISION":
            continue
        decision = event.get("formal_decision") or event.get("decision") or {}
        if not isinstance(decision, dict):
            continue
        stamp = event.get("decision_time_beijing") or event.get("decision_effective_at_beijing") or decision.get("decision_time") or decision.get("decision_effective_at_beijing") or event.get("recorded_at_beijing")
        try:
            from datetime import datetime
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError):
            continue
        candidates.append((parsed, str(path), decision))
    if not candidates:
        return {}
    decision = max(candidates, key=lambda item: (item[0], item[1]))[2]
    lifecycle = decision.get("lifecycle")
    if isinstance(lifecycle, dict):
        lifecycle = "；".join(f"{key}：{value}" for key, value in lifecycle.items())
    return {
        "risk_permission": decision.get("risk_permission") or "未提供",
        "lifecycle": lifecycle or "未提供",
        "main_candidate": decision.get("main_candidate") or decision.get("candidate") or "无新的主候选。",
        "amount_action": decision.get("amount_action") or decision.get("action") or "未提供",
        "decisive_reason": decision.get("decisive_reason") or decision.get("zero_amount_decisive_reason") or "未提供",
        "data_as_of_beijing": decision.get("data_as_of_beijing") or decision.get("data_as_of") or "未提供",
    }


def render_decision_projection(decision: dict) -> str:
    """Render the selector result using the existing Dashboard markdown contract."""
    return "\n".join([
        "### 最近一次正式盘中决策", "",
        f"- 风险许可：{decision.get('risk_permission', '未提供')}",
        f"- 生命周期：{decision.get('lifecycle', '未提供')}",
        f"- 唯一主候选：{decision.get('main_candidate', '无新的主候选。')}",
        f"- 金额与动作：{decision.get('amount_action', '未提供')}",
        f"- 最大风险或0元主因：{decision.get('decisive_reason', '未提供')}",
        f"- 决策数据时点：{decision.get('data_as_of_beijing', '未提供')}",
    ])


def format_position_pnl(position: dict) -> str:
    """Exact broker P&L, explicit derived estimate, or unknown; never zero fallback."""
    def num(value):
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
    exact = next((position.get(k) for k in ("pnl", "holding_pnl") if position.get(k) not in (None, "")), None)
    exact_pct = next((position.get(k) for k in ("pnl_pct", "holding_pnl_pct") if position.get(k) not in (None, "")), None)
    estimated = False
    if exact is None:
        market_value, cost, quantity = (num(position.get(k)) for k in ("market_value", "cost", "quantity"))
        if market_value is not None and cost is not None and quantity is not None:
            exact = market_value - cost * quantity
            basis = cost * quantity
            exact_pct = exact / basis * 100 if basis else None
            estimated = True
    if exact is None:
        return "—"
    suffix = "（估算）" if estimated else ""
    pct = f"（{float(exact_pct):+.2f}%）" if exact_pct is not None else ""
    return f"{float(exact):,.2f}元{suffix}{pct}"


def latest_formal_risk() -> float | None:
    # Historical Known-net reviews are PIT compatibility evidence only.
    return None


def canonical_risk(equity: dict, formal_override: float | None = None) -> float | None:
    # Pure parser remains independently testable; production callers explicitly
    # supply the newest formal review value when one exists.
    if formal_override is not None:
        return float(formal_override)
    summary = equity.get("summary") or {}
    for key in ("current_strategy_return_pct_gross", "known_net_current_strategy_return_pct"):
        try:
            return float(summary[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def canonical_risk_identity(equity: dict) -> dict:
    return {
        "source": "data/state/etf_strategy_equity.json::summary.current_strategy_return_pct_gross",
        "replay_cutoff": str(equity.get("replay_cutoff") or ""),
        "trade_fact_count": (equity.get("summary") or {}).get("trade_fact_count"),
    }


def normalize_dashboard_projection(text: str, root: Path = ROOT, account: dict | None = None) -> str:
    """Keep Dashboard a current projection and repair legacy newline serialization."""
    replacement = "|ETF层当前结构|当前持仓与观察角色仅以上方‘云端实时状态（自动同步）’中的canonical account projection为准；本区块不再复制当前角色列表。|"
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    lines = text.splitlines()
    cleaned = []
    in_corrections = False
    for line in lines:
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->":
            in_corrections = True
            cleaned.append(line)
            continue
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->":
            in_corrections = False
            cleaned.append(line)
            continue
        if not in_corrections:
            cleaned.append(line)
    lines = cleaned
    for index, line in enumerate(lines):
        if "|ETF层当前结构|" in line:
            lines[index] = replacement
            break
    heading = "### 下一关键节点"
    if heading in lines:
        start = lines.index(heading)
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("## ") and lines[index] != heading:
                end = index
                break
        today = str((account or {}).get("updated_at") or "")[:10]
        next_day = "待由交易日历确定"
        try:
            calendar = load_json(root / "config/market/a_share_trading_calendar_2026.json")
            closed = set(calendar.get("closed_dates") or [])
            from datetime import date, timedelta
            cursor = date.fromisoformat(today) if today else date.today()
            for _ in range(370):
                cursor += timedelta(days=1)
                candidate = cursor.isoformat()
                if cursor.weekday() < 5 and candidate not in closed:
                    next_day = candidate
                    break
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        lines[start:end] = [
            heading, "",
            f"- 下一A股交易日：{next_day}；优先复核当前ACTIVE_TRIAL与正式行情/账户事实。",
            "- 已关闭Trial仅在下一可执行节点独立评估降低风险或退出；本投影不生成订单。",
            "- 当前无待处理结算现金约束。",
        ]
    result = "\n".join(lines)
    return result + ("\n" if text.endswith("\n") else "")


def preserve_decision_block(existing: str) -> str:
    if START_DASH not in existing or END_DASH not in existing:
        return ""
    block = existing[existing.index(START_DASH) + len(START_DASH):existing.index(END_DASH)]
    starts = [block.find("### 最近一次正式盘中决策"), block.find("### 最近一次正式收盘复盘")]
    start = min([x for x in starts if x >= 0], default=-1)
    if start < 0:
        return ""
    end = block.find("同步请求：", start)
    if end < 0:
        end = len(block)
    return block[start:end].strip()


def build_dashboard_block(account: dict, equity: dict, existing: str, root: Path = ROOT) -> str:
    positions = account.get("positions") or []
    membership = active_account_asset_codes(root, account)
    etfs = [p for p in positions if normalize_code(p.get("code")) in membership["etf"]]
    stocks = [p for p in positions if normalize_code(p.get("code")) in membership["stocks"]]
    total_asset = float(account.get("total_asset") or 0)
    exposure = float(account.get("stock_market_value") or 0) / total_asset * 100 if total_asset else 0
    risk = canonical_risk(equity, latest_formal_risk())
    risk_identity = canonical_risk_identity(equity)
    risk_cutoff = risk_identity["replay_cutoff"] or "UNKNOWN"
    risk_source = risk_identity["source"]
    summary = equity.get("summary") or {}
    pending_fee_trades = [
        t for t in (account.get("trades") or [])
        if str(t.get("fee_status", "")).upper() in {"PENDING", "PENDING_CONFIRMATION", "UNKNOWN"}
    ]
    pending = bool(summary.get("unknown_fee_flag", True) or pending_fee_trades)
    fee_fact = effective_confirmed_fee_fact(root, equity.get("trades") or [])
    known_fees = fee_fact["effective_confirmed_fee_sum"]
    overlay_fees = fee_fact["executed_event_confirmed_fee_sum"]
    confirmed_account_fees = sum(float(t.get("fee") or 0) for t in (account.get("trades") or []) if str(t.get("fee_status", "")).upper() == "CONFIRMED")
    lines = [
        "## 云端实时状态（自动同步）", "",
        f"> 账户事实更新时间：{account.get('updated_at', '')}  ",
        f"> 账户事实来源：{account.get('source', '')}  ",
        f"> ETF策略风险as-of：{risk_cutoff}  ",
        f"> ETF策略风险来源：{risk_source}  ",
        "> 场景：ACCOUNT_FACT_MAINTENANCE  ",
        "> 账户事实与ETF策略风险使用各自canonical provenance；本区块不推导交易权限或下单。",
        "", "|项目|最新事实|", "|-|-|",
        f"|总资产|{money(account.get('total_asset'))}|",
        f"|股票市值|{money(account.get('stock_market_value'))}|",
        f"|账面现金|{money(account.get('cash'))}|",
        f"|待结算锁定资金|{money(account.get('reserved_cash_for_settlement', 0))}|",
        f"|可部署现金|{money(account.get('deployable_cash'))}|",
        f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|",
        f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|",
        f"|账户总风险暴露率|约{exposure:.2f}%|",
        f"|ETF策略风险率|约{risk:.4f}%（Gross；as-of {risk_cutoff}）|" if risk is not None else "|ETF策略风险率|当前canonical策略权益状态缺失|",
        f"|累计已知ETF费用（有效事实）|{money(known_fees)}；已执行成交overlay {money(overlay_fees)}；待确认费用状态：{'存在' if pending else '无'}|",
        f"|账户事实内已确认费用记录合计|{money(confirmed_account_fees)}（仅统计account_fact中明确标记CONFIRMED的记录；不代表历史累计ETF费用）|",
        "", "### 当前持仓事实", "",
        "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|",
    ]
    for p in positions:
        lines.append(
            f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|"
            f"{float(p.get('cost') or 0):.3f}|{position_metric(p, 'current_price', 'last_price'):.3f}|"
            f"{money(p.get('market_value'))}|{format_position_pnl(p)}|"
        )
    etf_codes = {str(item.get("code")): str(item.get("name") or item.get("code")) for item in (load_json(root / "config/market/etf_monitor_universe.json").get("objects") or []) if item.get("code")}
    held_codes = membership["etf"]
    observed = [f"{name}（{code}）" for code, name in etf_codes.items() if code not in held_codes]
    lines += [
        "", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。",
        f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。",
        f"观察ETF：{'、'.join(observed) or '无'}。",
        "观察ETF说明：仅展示经正式决策准入/保留、当前无持仓且仍值得跨节点持续监测的ETF；本节点池外发现对象不会因被发现而自动进入本列表。",
    ]
    decision = latest_canonical_formal_decision(root)
    if decision:
        lines += ["", render_decision_projection(decision)]
    else:
        lines += ["", "最近一次正式决策未随本次账户维护请求提供；脚本不自行推断。"]
    return "\n".join(lines)


def update_experience(text: str, account: dict) -> tuple[str, int]:
    changed = 0
    for trade in account.get("trades") or []:
        if str(trade.get("fee_status", "")).upper() != "CONFIRMED":
            continue
        code = str(trade.get("code") or "")
        action = str(trade.get("action") or "")
        quantity = str(int(trade.get("quantity") or 0))
        price = str(trade.get("price") or "")
        date = str(trade.get("trade_time") or "")[:10]
        lines = text.splitlines()
        action_upper = action.upper()
        action_tokens = {action, "买入" if action_upper in {"BUY", "B"} else "", "卖出" if action_upper in {"SELL", "S"} else ""}
        normalized_quantity = quantity.replace(",", "")
        normalized_price = f"{float(trade.get('price')):.3f}" if trade.get('price') is not None else price
        for i, line in enumerate(lines):
            normalized_line = line.replace(",", "")
            action_match = any(token and token in line for token in action_tokens)
            price_match = price in line or normalized_price in line
            if not (line.startswith("|") and date in line and code in line and action_match and normalized_quantity in normalized_line and price_match):
                continue
            parts = line.split("|")
            if len(parts) < 10:
                continue
            fee = trade.get("fee")
            amount = trade.get("amount", trade.get("gross_amount"))
            parts[8] = f"{float(fee):.2f}" if fee is not None else parts[8]
            parts[9] = f"−{float(amount):,.2f}" if action.upper() in {"BUY", "买入"} else f"{float(amount):,.2f}"
            note = parts[10].strip()
            marker = "费用已确认" if str(fee) else "费用待确认"
            if marker not in note:
                note = (note + "；" if note else "") + marker
            parts[10] = note
            lines[i] = "|".join(parts)
            changed += 1
            break
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text, changed


def build_archive_fact_block(account: dict) -> str:
    trades = account.get("trades") or []
    trade_lines = []
    for trade in trades:
        if str(trade.get("fee_status", "")).upper() != "CONFIRMED":
            continue
        trade_lines.append(
            f"- {trade.get('object', trade.get('name', ''))}（{trade.get('code', '')}）"
            f"：{trade.get('action', '')}{int(trade.get('quantity') or 0)}份，"
            f"成交价{trade.get('price')}，成交本金{money(trade.get('gross_amount'))}，"
            f"已确认费用{money(trade.get('fee'))}，资金发生额{money(trade.get('amount'))}。"
        )
    if not trade_lines:
        trade_lines.append("- 本次账户事实未新增已确认费用的成交记录。")
    return "\n".join([
        "### 账户事实增量维护（仅客观事实）",
        f"- 账户事实确认时间：{account.get('updated_at', '')}",
        f"- 来源：{account.get('source', '')}",
        f"- 总资产：{money(account.get('total_asset'))}；可用资金：{money(account.get('cash'))}；持仓数量沿用最新确认账户事实。",
        *trade_lines,
        "- 本区块不生成交易权限、买卖建议或MASTER修改；未确认费用不写入已知费用。",
    ])


def sync_current_account_reference(root: Path, account: dict) -> bool:
    path = root / "data/state/CURRENT.json"
    if not path.exists():
        return False
    current = load_json(path)
    changed = False
    wanted = {
        "account_fact_path": "data/state/account_fact.json",
        "account_fact_updated_at": account.get("updated_at"),
        "account_fact_status": account.get("status"),
        "account_fact_source": account.get("source"),
    }
    for key, value in wanted.items():
        if current.get(key) != value:
            current[key] = value
            changed = True
    if changed:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def sync_formal_files(root: Path = ROOT, account: dict | None = None) -> dict:
    account = account or load_json(root / "data/state/account_fact.json")
    equity_path = root / "data/state/etf_strategy_equity.json"
    equity = load_json(equity_path) if equity_path.exists() else {"summary": {}}
    dash_path = root / "ETF当前状态_DASHBOARD.md"
    archive_path = root / "ETF市场行情档案_2026.md"
    experience_path = root / "ETF交易复盘与经验库_2026.md"

    existing_dash = dash_path.read_text(encoding="utf-8")
    new_dash = replace_block(existing_dash, START_DASH, END_DASH, build_dashboard_block(account, equity, existing_dash, root), after_heading=True)
    new_dash = normalize_dashboard_projection(new_dash, root, account)
    dash_changed = new_dash != existing_dash
    if dash_changed:
        write_formal_text_if_changed(root, dash_path.name, new_dash)

    existing_archive = archive_path.read_text(encoding="utf-8")
    new_archive = replace_block(existing_archive, START_ARCHIVE, END_ARCHIVE, build_archive_fact_block(account), before_heading="## 6. 历史Excel与专项数据来源")
    archive_changed = new_archive != existing_archive
    if archive_changed:
        write_formal_text_if_changed(root, archive_path.name, new_archive)

    existing_experience = experience_path.read_text(encoding="utf-8")
    new_experience, experience_updates = update_experience(existing_experience, account)
    experience_changed = new_experience != existing_experience
    if experience_changed:
        write_formal_text_if_changed(root, experience_path.name, new_experience)

    current_changed = sync_current_account_reference(root, account)
    research_archive_changed = archive_completed_research(root)
    return {
        "dashboard_changed": dash_changed,
        "archive_changed": archive_changed,
        "experience_changed": experience_changed,
        "experience_fee_rows_updated": experience_updates,
        "current_account_reference_changed": current_changed,
        "completed_research_archive_changed": research_archive_changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = sync_formal_files(root)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
