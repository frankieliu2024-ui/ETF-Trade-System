from __future__ import annotations

"""Minimal idempotent maintenance for confirmed account facts.

This module updates only the human-readable fact documents. It never writes
ETF规则_MASTER.md and never derives permissions, orders, or lifecycle actions.
It is invoked after an accepted state-sync request, so account_fact remains the
machine source of truth.
"""

import argparse
import json
import os
from datetime import timezone, timedelta
from pathlib import Path

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


def replace_block(text: str, start: str, end: str, block: str, after_heading: bool = False) -> str:
    managed = f"{start}\n{block.rstrip()}\n{end}"
    if start in text and end in text:
        a = text.index(start)
        b = text.index(end, a) + len(end)
        return text[:a] + managed + text[b:]
    if after_heading:
        lines = text.splitlines()
        pos = 1 if lines and lines[0].startswith("#") else 0
        lines[pos:pos] = ["", managed, ""]
        return "\n".join(lines).rstrip() + "\n"
    return text.rstrip() + "\n\n" + managed + "\n"


def display_name(position: dict) -> str:
    return f"{position.get('name', '')}（{position.get('code', '')}）"


def canonical_risk(equity: dict) -> float | None:
    summary = equity.get("summary") or {}
    for key in ("known_net_current_strategy_return_pct", "current_strategy_return_pct_gross"):
        try:
            return float(summary[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


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


def build_dashboard_block(account: dict, equity: dict, existing: str) -> str:
    positions = account.get("positions") or []
    etfs = [p for p in positions if p.get("asset_type") == "ETF"]
    stocks = [p for p in positions if p.get("asset_type") == "STOCK"]
    total_asset = float(account.get("total_asset") or 0)
    exposure = float(account.get("stock_market_value") or 0) / total_asset * 100 if total_asset else 0
    risk = canonical_risk(equity)
    summary = equity.get("summary") or {}
    pending = summary.get("unknown_fee_flag", True)
    known_fees = summary.get("known_fees")
    lines = [
        "## 云端实时状态（自动同步）", "",
        f"> 更新时间：{account.get('updated_at', '')}  ",
        f"> 来源：{account.get('source', '')}  ",
        "> 场景：ACCOUNT_FACT_MAINTENANCE  ",
        "> 本区块只同步已确认账户事实与既有正式决策；自动程序不得自行推导交易权限或下单。",
        "", "|项目|最新事实|", "|-|-|",
        f"|总资产|{money(account.get('total_asset'))}|",
        f"|股票市值|{money(account.get('stock_market_value'))}|",
        f"|可用资金|{money(account.get('cash'))}|",
        f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|",
        f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|",
        f"|账户总风险暴露率|约{exposure:.2f}%|",
        f"|ETF策略风险率|约{risk:.2f}%（Known-net；唯一决定风险区间）|" if risk is not None else "|ETF策略风险率|当前辅助权益状态缺失，保留最近有效值|",
        f"|ETF已确认费用|{money(known_fees)}；待确认费用状态：{'存在' if pending else '无'}|",
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
        for i, line in enumerate(lines):
            if not (line.startswith("|") and date in line and code in line and action in line and quantity in line and price in line):
                continue
            parts = line.split("|")
            if len(parts) < 10:
                continue
            fee = trade.get("fee")
            amount = trade.get("amount", trade.get("gross_amount"))
            parts[7] = f"{float(fee):.2f}" if fee is not None else parts[7]
            parts[8] = f"−{float(amount):,.2f}" if action.upper() in {"BUY", "买入"} else f"{float(amount):,.2f}"
            note = parts[9].strip()
            marker = "费用已确认" if str(fee) else "费用待确认"
            if marker not in note:
                note = (note + "；" if note else "") + marker
            parts[9] = note
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


def sync_formal_files(root: Path = ROOT, account: dict | None = None) -> dict:
    dashboard_path = root / "ETF当前状态_DASHBOARD.md"
    experience_path = root / "ETF交易复盘与经验库_2026.md"
    archive_path = root / "ETF市场行情档案_2026.md"
    account = account or load_json(root / "data/state/account_fact.json")
    equity_path = root / "data/state/etf_strategy_equity.json"
    equity = load_json(equity_path) if equity_path.exists() else {}
    dashboard = dashboard_path.read_text(encoding="utf-8")
    dashboard = replace_block(dashboard, START_DASH, END_DASH, build_dashboard_block(account, equity, dashboard), after_heading=True)
    dashboard_path.write_text(dashboard, encoding="utf-8")
    experience = experience_path.read_text(encoding="utf-8")
    experience, experience_updates = update_experience(experience, account)
    experience_path.write_text(experience, encoding="utf-8")
    archive = archive_path.read_text(encoding="utf-8")
    archive = replace_block(archive, START_ARCHIVE, END_ARCHIVE, build_archive_fact_block(account))
    archive_path.write_text(archive, encoding="utf-8")
    return {"dashboard_updated": True, "experience_trade_rows_updated": experience_updates, "archive_updated": True, "master_touched": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync confirmed account facts into formal fact documents.")
    parser.add_argument("--check", action="store_true", help="validate inputs and markers without writing")
    args = parser.parse_args()
    root = ROOT
    account = load_json(root / "data/state/account_fact.json")
    required = [root / "ETF当前状态_DASHBOARD.md", root / "ETF交易复盘与经验库_2026.md", root / "ETF市场行情档案_2026.md"]
    if not all(path.exists() for path in required):
        raise SystemExit("formal fact document missing")
    if args.check:
        print(json.dumps({"ok": True, "master_touched": False, "account_updated_at": account.get("updated_at")}, ensure_ascii=False))
        return 0
    print(json.dumps(sync_formal_files(root, account), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
