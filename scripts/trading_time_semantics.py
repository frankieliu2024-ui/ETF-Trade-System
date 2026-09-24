"""Shared A-share user-visible temporal semantics.

This module is presentation-only. It reads the canonical A-share trading
calendar and resolves future executable wording; it never creates trading
permission, a decision, or an order.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


CALENDAR_PATH = Path("config/market/a_share_trading_calendar_2026.json")


def load_a_share_calendar(root: Path) -> dict[str, Any]:
    return json.loads((root / CALENDAR_PATH).read_text(encoding="utf-8"))


def is_a_share_trading_day(day: date, calendar: dict[str, Any]) -> bool:
    start = str(calendar.get("coverage_start") or "")
    end = str(calendar.get("coverage_end") or "")
    value = day.isoformat()
    covered = bool(start and end and start <= value <= end)
    return covered and day.weekday() < 5 and value not in set(calendar.get("closed_dates") or [])


def next_a_share_trading_day(after: date, calendar: dict[str, Any]) -> date:
    day = after
    coverage_end = str(calendar.get("coverage_end") or "")
    while True:
        day += timedelta(days=1)
        if coverage_end and day.isoformat() > coverage_end:
            raise ValueError("next A-share trading day is outside calendar coverage")
        if is_a_share_trading_day(day, calendar):
            return day


def user_visible_trading_time_anchor(as_of: date, calendar: dict[str, Any]) -> dict[str, Any]:
    next_day = next_a_share_trading_day(as_of, calendar)
    tomorrow = as_of + timedelta(days=1)
    return {
        "as_of_date": as_of.isoformat(),
        "next_a_share_trading_date": next_day.isoformat(),
        "tomorrow_is_a_share_trading_day": is_a_share_trading_day(tomorrow, calendar),
        "preferred_future_action_wording": "下一交易日",
        "preferred_future_node_wording": "下一可交易节点",
        "explicit_date_wording": f"下一A股交易日（{next_day.isoformat()}）",
        "rule": (
            "涉及买入、卖出、Trial、Confirm、盘中复核等未来可执行A股动作时，"
            "默认使用“下一交易日”或“下一可交易节点”；需要消除歧义时使用明确日期。"
            "不得仅因次日为工作日而写成“明日/明天”。“明日/明天”仅用于纯自然日语义，"
            "或已核验交易日历且确实意在描述该自然日。"
        ),
    }
