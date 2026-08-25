from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class MarketQuoteRoute:
    market: str
    phase: str
    source_priority: list[str]
    semantic_rule: str


MARKET_ZONES = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "US": "America/New_York",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
}


def market_phase(market: str, now: datetime | None = None) -> str:
    tz = ZoneInfo(MARKET_ZONES[market])
    local = (now or datetime.now(tz)).astimezone(tz)
    minute = local.hour * 60 + local.minute

    if market in {"CN", "HK", "TW", "JP", "KR"}:
        if market == "CN":
            if 9 * 60 + 15 <= minute < 9 * 60 + 30:
                return "OPENING_AUCTION"
            if (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute < 15 * 60):
                return "REGULAR"
            return "OFF_SESSION"
        return "REGULAR" if 9 * 60 <= minute < 16 * 60 else "OFF_SESSION"

    if market == "US":
        if 4 * 60 <= minute < 9 * 60 + 30:
            return "PRE_MARKET"
        if 9 * 60 + 30 <= minute < 16 * 60:
            return "REGULAR"
        if 16 * 60 <= minute < 20 * 60:
            return "POST_MARKET"
        return "OFF_SESSION"

    return "UNKNOWN"


def route(market: str, phase: str) -> MarketQuoteRoute:
    if market == "US":
        if phase == "REGULAR":
            return MarketQuoteRoute(market, phase, ["realtime_snapshot", "us_extended_hours_context"], "返回现金盘实时行情")
        if phase in {"PRE_MARKET", "POST_MARKET"}:
            return MarketQuoteRoute(market, phase, ["us_extended_hours_context", "overseas_context"], "返回扩展行情并附正式收盘基准")
        return MarketQuoteRoute(market, phase, ["overseas_context"], "返回最近正式收盘")

    return MarketQuoteRoute(market, phase, ["market_context", "snapshot", "archive"], "根据本地交易状态返回最新有效行情")
