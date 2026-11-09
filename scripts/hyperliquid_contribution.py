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
    """Create the shared read-only Hyperliquid contribution packet.

    Formal Decision, Trade Review and pulse notifications consume the same
    object-level interpretation. Notification eligibility is recorded here but
    remains subject to the independent notification materiality/comparability
    gate.
    """
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

    def effect(item: dict) -> str:
        explicit = str(item.get("decision_effect") or "").strip().upper()
        if explicit:
            return explicit
        if item.get("crowding") or item.get("crowding_status"):
            return "CROWDING_RISK"
        if item.get("etf_codes") or item.get("mapped_assets"):
            return "INSUFFICIENT"
        if item.get("family"):
            return "NO_DIRECT_MAPPING"
        return "INSUFFICIENT"

    def signal(item: dict) -> dict:
        return {
            "direction": item.get("direction") or "",
            "funding": item.get("funding") or item.get("funding_rate") or "",
            "open_interest": item.get("open_interest") or item.get("openInterest") or "",
            "premium": item.get("premium") or "",
            "crowding": item.get("crowding") or item.get("crowding_status") or "",
            "mark_px": item.get("mark_px") or "",
            "mid_px": item.get("mid_px") or "",
            "prev_day_px": item.get("prev_day_px") or "",
        }

    mapped_objects = []
    for item in mapped:
        assets = list(item.get("etf_codes") or item.get("mapped_assets") or [])
        current_effect = effect(item)
        mapped_objects.append({
            "object_type": "HYPERLIQUID_ASSET",
            "object_id": item.get("asset") or item.get("name") or "",
            "object_name": item.get("name") or item.get("asset") or "",
            "asset": item.get("asset") or item.get("name") or "",
            "family": item.get("family") or "",
            "mapped_assets": assets,
            "mapping_reason": item.get("mapping_reason") or (
                "CONFIGURED_DIRECT_OR_ECONOMIC_FAMILY_BINDING"
                if assets or item.get("family") else "NO_DIRECT_MAPPING"
            ),
            "observation_as_of": item.get("observation_as_of") or as_of,
            "evidence_status": item.get("evidence_status") or "READY",
            "consumption_status": item.get("consumption_status") or (
                "CONSUMED" if assets or item.get("family") else "CONSUMED_AS_LIMITATION"
            ),
            "decision_effect": current_effect,
            "signal_fields": signal(item),
            "evidence_summary": item.get("evidence_summary") or signal(item),
            "decision_use": item.get("decision_use") or (
                "已映射对象用于方向确认、跨市场背离、入场时机过滤或拥挤风险识别；不独立授予交易权限"
                if assets or item.get("family") else
                "当前仅作为外部背景记录，不能替代具体ETF自身事实"
            ),
            "notification_eligibility": (
                "ELIGIBLE_IF_MATERIAL_AND_COMPARABLE" if assets
                else "MACHINE_LEDGER_ONLY_UNLESS_DECISION_RELEVANT"
            ),
            "notification_use": (
                "若相对前一脉冲发生可比且足以改变ETF关注重点的变化，可进入主动通知候选"
                if assets else
                "仅当该经济族群变化能够改变下一次正式判断的风险传导或时机，才升级为主动通知候选"
            ),
        })

    return {
        "schema_version": "2.1",
        "status": "READY" if mapped else "DEGRADED",
        "task": str(task or ""),
        "node": str(node or ""),
        "phase": phase,
        "as_of_beijing": as_of,
        "mapped_object_count": len(mapped_objects),
        "mapped_objects": mapped_objects,
        "notification_contract": {
            "shared_with_formal_decision": True,
            "materiality_gate_required": True,
            "comparable_before_after_required": True,
            "no_push_for_catalog_or_provider_only_change": True,
            "no_push_for_unmapped_asset_without_decision_relevance": True,
            "allowed_effects": [
                "SUPPORT", "CONFIRMATORY", "CONTRADICTION",
                "TIMING_FILTER", "CROWDING_RISK", "NO_INCREMENTAL_VALUE",
                "NO_DIRECT_MAPPING", "INSUFFICIENT"
            ],
        },
        "node_roles": {
            "preopen": "隔夜聚合用于次日开盘预期、开盘风险和是否等待确认",
            "intraday": "盘中用于外部确认、背离、强化、反转和拥挤风险识别",
            "post_close": "盘后用于信号归因和下一交易日研究输入",
        },
        "consumer_contract": "同一只读贡献包供正式决策、交易复盘和Hyperliquid脉冲主动通知消费；不独立生成交易权限、金额或订单",
    }
