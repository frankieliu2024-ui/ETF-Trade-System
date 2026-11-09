"""Translate Hyperliquid machine evidence into one user-facing ETF explanation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from notification_center import read_json


ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "config" / "market" / "hyperliquid_etf_exposure_map.json"

FAMILY_LABELS = {
    "SEMICONDUCTOR": "美国半导体方向",
    "SEMICONDUCTOR_HIGH_BETA": "高波动半导体方向",
    "MEMORY": "全球存储方向",
    "CHINA_MEMORY_CHAIN": "中国存储与半导体产业链方向",
    "AI_COMPUTE": "AI计算与高性能芯片方向",
    "AI_NETWORKING": "AI网络与高速通信方向",
    "BIOTECH": "美国生物科技与创新药方向",
    "ENERGY": "能源、油气与化工方向",
    "GOLD": "黄金与贵金属方向",
    "GLOBAL_RISK_APPETITE": "全球风险偏好与成长方向",
    "KOREA_EQUITY": "韩国股票与亚洲科技方向",
    "JAPAN_EQUITY": "日本股票方向",
    "TAIWAN_EQUITY": "台湾科技与半导体方向",
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction(mark: Any, prev: Any) -> str:
    m, p = _number(mark), _number(prev)
    if m is None or p is None or p == 0:
        return "方向暂不明确"
    return "价格偏强" if m > p else "价格偏弱" if m < p else "价格基本持平"


def _funding_text(value: Any) -> str:
    n = _number(value)
    if n is None:
        return "资金成本暂不可判断"
    if n > 0:
        return "做多一侧向做空一侧支付持仓成本，市场做多押注相对更急"
    if n < 0:
        return "做空一侧向做多一侧支付持仓成本，市场做空押注相对更急"
    return "多空双方持仓成本接近中性"


def _crowding_text(item: dict[str, Any]) -> str:
    value = str(item.get("crowding") or item.get("crowding_status") or "").strip()
    if not value:
        return "未形成可确认的拥挤度结论"
    return value


def _open_interest_text(value: Any, direction: str) -> str:
    n = _number(value)
    if n is None:
        return "未平仓押注变化暂不可判断"
    if direction == "价格偏强":
        return "当前未平仓押注规模可读，但缺少前一时点可比值，不能判断新增押注是趋势确认还是拥挤"
    if direction == "价格偏弱":
        return "当前未平仓押注规模可读，但缺少前一时点可比值，不能判断是新增做空还是多头退出"
    return "当前未平仓押注规模可读，但暂不足以判断方向"


def _premium_text(value: Any) -> str:
    n = _number(value)
    if n is None:
        return "价格相对参考价的偏离暂不可判断"
    if abs(n) >= 0.003:
        return "价格相对参考价偏离较大，情绪和短线波动风险上升"
    if n > 0:
        return "价格略高于参考价，存在轻微情绪溢价"
    if n < 0:
        return "价格略低于参考价，外部买方溢价不足"
    return "价格基本贴近参考价"


def _latest_pulse_observations() -> dict[str, dict[str, Any]]:
    """Read the shared latest pulse projection, including cross-pulse fields."""
    payload = read_json(ROOT / "data/state/hyperliquid_pulse_latest.json", {})
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    rows = latest.get("observations") if isinstance(latest, dict) else []
    return {
        str(row.get("asset") or ""): row
        for row in (rows or [])
        if isinstance(row, dict) and str(row.get("asset") or "")
    }


def _mapping_rows(text: str, context: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    hl = context.get("hyperliquid") if isinstance(context, dict) else {}
    observations = hl.get("observations") if isinstance(hl, dict) else []
    mappings = hl.get("mapping") if isinstance(hl, dict) else []
    if not isinstance(observations, list) or not isinstance(mappings, list):
        return []
    pulse_by_asset = _latest_pulse_observations()
    by_asset = {}
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        asset = obs.get("asset")
        asset = asset.get("name") if isinstance(asset, dict) else asset
        if asset:
            by_asset[str(asset)] = obs
    rows = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        markers = [str(x) for x in mapping.get("name_markers") or []]
        excluded = [str(x) for x in mapping.get("exclude_name_markers") or []]
        relevant = str(mapping.get("asset") or "") in text or any(x and x in text for x in markers)
        if any(x and x in text for x in excluded):
            relevant = False
        if relevant:
            asset = str(mapping.get("asset") or "")
            current = by_asset.get(asset, {})
            pulse = pulse_by_asset.get(asset, {})
            # The pulse projection is authoritative for cross-pulse comparison.
            # Keep current context as a one-release fallback for raw values.
            merged = dict(current)
            merged.update({key: value for key, value in pulse.items() if value not in (None, "", {})})
            rows.append((mapping, merged))
    return rows


def explain_for_notification(*, text: str, target: str = "") -> str:
    """Return a compact contribution; never expose object-by-object rows."""
    context = read_json(ROOT / "data/state/overseas_context.json", {})
    rows = _mapping_rows(f"{text} {target}", context)
    if not rows:
        return "本轮Hyperliquid未形成与当前对象可确认的直接或行业级增量信号，不改变当前判断"

    directions = []
    families = []
    risk_flags = []
    for mapping, obs in rows:
        family = FAMILY_LABELS.get(str(mapping.get("family") or ""), "相关外部方向")
        if family not in families:
            families.append(family)
        ctx = obs.get("context") or obs
        direction = _direction(
            ctx.get("markPx") or ctx.get("mark_px"),
            ctx.get("prevDayPx") or ctx.get("prev_day_px"),
        )
        directions.append(direction)
        comparison = str(obs.get("pulse_comparison_status") or "SINGLE_PULSE_ONLY")
        delta = obs.get("pulse_delta") if isinstance(obs.get("pulse_delta"), dict) else {}
        funding = _funding_text(ctx.get("funding") or ctx.get("funding"))
        premium = _premium_text(ctx.get("premium") or ctx.get("premium"))
        oi_delta = _number(delta.get("open_interest"))
        funding_delta = _number(delta.get("funding"))
        premium_delta = _number(delta.get("premium"))
        crowding = str(obs.get("crowding_signal") or "")
        if comparison != "COMPARABLE":
            risk_flags.append("跨脉冲历史不足，无法确认拥挤变化")
        elif crowding and crowding not in {"NO_CLEAR_CROWDING"}:
            risk_flags.append(
                f"{crowding}（资金费率变化{funding_delta if funding_delta is not None else '不可用'}、"
                f"未平仓量变化{oi_delta if oi_delta is not None else '不可用'}、"
                f"溢价变化{premium_delta if premium_delta is not None else '不可用'}）"
            )
        elif premium not in risk_flags and premium != "价格基本贴近参考价":
            risk_flags.append(premium)
        if funding != "多空双方持仓成本接近中性" and funding not in risk_flags:
            risk_flags.append(funding)

    if directions and all(x == "价格偏强" for x in directions):
        relation = "相关外部方向整体偏强，形成辅助确认"
    elif directions and all(x == "价格偏弱" for x in directions):
        relation = "相关外部方向整体偏弱，形成压制或风险过滤"
    elif any(x in {"价格偏强", "价格偏弱"} for x in directions):
        relation = "相关外部方向出现分化，只形成部分确认"
    else:
        relation = "相关外部方向暂不明确，主要用于时机过滤"

    family_text = "、".join(families[:3])
    risk_text = f"同时提示{risk_flags[0]}" if risk_flags else "暂未形成明确拥挤或溢价风险"
    return f"Hyperliquid显示{family_text}出现变化，{relation}；{risk_text}。对当前对象仅作方向确认和风险/时机过滤，不单独生成交易动作"
