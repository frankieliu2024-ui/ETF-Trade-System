from __future__ import annotations

from datetime import datetime, timezone, timedelta


BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _phase(as_of: str) -> str:
    try:
        value = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        local = value.astimezone(BEIJING)
        minute = local.hour * 60 + local.minute
    except (TypeError, ValueError):
        return "UNKNOWN"
    if minute < 8 * 60:
        return "PREOPEN_OVERNIGHT_AGGREGATION"
    if minute < 16 * 60:
        return "A_SHARE_ACTIVE_WINDOW"
    return "POST_WINDOW_REFERENCE"


def build_contribution(*, context: dict | None, node: str = "", task: str = "") -> dict:
    """Create one read-only contribution packet shared by decision and review consumers."""
    context = context if isinstance(context, dict) else {}
    latest = context.get("latest") if isinstance(context.get("latest"), dict) else {}
    as_of = str(
        latest.get("fact_time_beijing")
        or latest.get("formed_at_beijing")
        or context.get("latest_observation_collected_at_beijing")
        or context.get("generated_at_beijing")
        or ""
    )
    phase = _phase(as_of)
    observations = latest.get("observations") or context.get("observations") or []
    mapped = [x for x in observations if isinstance(x, dict) and (x.get("family") or x.get("asset"))]
    return {
        "status": "READY" if mapped else "DEGRADED",
        "task": str(task or ""),
        "node": str(node or ""),
        "phase": phase,
        "as_of_beijing": as_of,
        "mapped_object_count": len(mapped),
        "mapped_objects": [
            {
                "asset": x.get("asset") or x.get("name") or "",
                "family": x.get("family") or "",
                "direction": x.get("direction") or "",
                "funding": x.get("funding") or "",
                "open_interest": x.get("open_interest") or x.get("openInterest") or "",
                "premium": x.get("premium") or "",
                "decision_effect": x.get("decision_effect") or "",
            }
            for x in mapped
        ],
        "node_roles": {
            "preopen": "隔夜聚合用于次日开盘预期、开盘风险和是否等待确认",
            "intraday": "盘中用于外部确认、背离、强化和反转识别",
            "post_close": "盘后用于信号归因和下一交易日研究输入",
        },
        "consumer_contract": "同一只读贡献包供正式决策和交易复盘消费；不独立生成交易权限、金额或订单",
    }
