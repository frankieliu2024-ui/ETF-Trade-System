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


def latest_formal_risk() -> float | None:
    review_dir = ROOT / "events" / "reviews"
    candidates = []
    for path in review_dir.glob("*.json") if review_dir.exists() else []:
        try:
            event = load_json(path)
            review = event.get("review") or event.get("formal_review") or {}
            fact = review.get("etf_strategy_known_net") or {}
            risk = float(fact.get("etf_strategy_risk_rate_pct"))
            float(fact.get("known_net_strategy_equity"))
            stamp = str(event.get("updated_at_beijing") or event.get("account_updated_at") or "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if stamp:
            candidates.append((stamp, risk))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def canonical_risk(equity: dict, formal_override: float | None = None) -> float | None:
    # Pure parser remains independently testable; production callers explicitly
    # supply the newest formal review value when one exists.
    if formal_override is not None:
        return float(formal_override)
    summary = equity.get("summary") or {}
    for key in ("known_net_current_strategy_return_pct", "current_strategy_return_pct_gross"):
        try:
            return float(summary[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def normalize_dashboard_projection(text: str, root: Path = ROOT, account: dict | None = None) -> str:
    """Keep Dashboard a current projection, excluding historical correction logs."""
    replacement = "|ETF层当前结构|当前持仓与观察角色仅以上方‘云端实时状态（自动同步）’中的canonical account projection为准；本区块不再复制当前角色列表。|"
    # Recover legacy Dashboard blobs that persisted the two-character\\n sequence.
    # The projection is human-readable text; normalization then emits real newlines.
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    lines = text.splitlines()
    cleaned = []
    in_corrections = False
    for line in lines:
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->":
            in_corrections = True
            continue
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->":
            in_corrections = False
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
    etfs = [p for p in positions if p.get("asset_type") == "ETF"]
    stocks = [p for p in positions if p.get("asset_type") == "STOCK"]
    total_asset = float(account.get("total_asset") or 0)
    exposure = float(account.get("stock_market_value") or 0) / total_asset * 100 if total_asset else 0
    risk = canonical_risk(equity, latest_formal_risk())
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
        f"> 更新时间：{account.get('updated_at', '')}  ",
        f"> 来源：{account.get('source', '')}  ",
        "> 场景：ACCOUNT_FACT_MAINTENANCE  ",
        "> 本区块只同步已确认账户事实与既有正式决策；自动程序不得自行推导交易权限或下单。",
        "", "|项目|最新事实|", "|-|-|",
        f"|总资产|{money(account.get('total_asset'))}|",
        f"|股票市值|{money(account.get('stock_market_value'))}|",
        f"|账面现金|{money(account.get('cash'))}|",
        f"|待结算锁定资金|{money(account.get('reserved_cash_for_settlement', 0))}|",
        f"|可部署现金|{money(account.get('deployable_cash'))}|",
        f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|",
        f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|",
        f"|账户总风险暴露率|约{exposure:.2f}%|",
        f"|ETF策略风险率|约{risk:.2f}%（Known-net）|" if risk is not None else "|ETF策略风险率|当前辅助权益状态缺失，保留最近有效值|",
        f"|累计已知ETF费用（有效事实）|{money(known_fees)}；已执行成交overlay {money(overlay_fees)}；待确认费用状态：{'存在' if pending else '无'}|",
        f"|账户事实内已确认费用记录合计|{money(confirmed_account_fees)}（仅统计account_fact中明确标记CONFIRMED的记录；不代表历史累计ETF费用）|",
        "", "### 当前持仓事实", "",
        "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|",
    ]
    for p in positions:
        lines.append(
            f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|"
            f"{float(p.get('cost') or 0):.3f}|{float(p.get('last_price') or 0):.3f}|"
            f"{money(p.get('market_value'))}|{money(p.get('holding_pnl'))}"
            f"（{float(p.get('holding_pnl_pct') or 0):+.2f}%）|"
        )
    lines += [
        "", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。",
        f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。",
    ]
    decision = preserve_decision_block(existing)
    if decision:
        lines += ["", decision]
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
        normalized_price = f"{float(trade.get('price')):.3f}" if trade.get("price") is not None else price
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
        f"- 总资产：{money(account.get('total_asset'))}；可用资金：{money(account.get('cash'))}；"
        f"持仓数量沿用最新确认账户事实。",
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
