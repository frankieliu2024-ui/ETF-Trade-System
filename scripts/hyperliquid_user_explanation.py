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



def _fixed_etf_labels(mapping: dict[str, Any]) -> list[str]:
    """Return a conservative fixed ETF projection for notification text."""
    explicit = [str(x) for x in (mapping.get("etf_codes") or mapping.get("mapped_assets") or []) if str(x)]
    fixed = {
        "GLOBAL_RISK_APPETITE": ["159941", "588000", "159781"],
        "SEMICONDUCTOR": ["159781", "515880", "159326", "588000"],
        "SEMICONDUCTOR_HIGH_BETA": ["159781", "515880", "159326", "588000"],
        "MEMORY": ["159326", "159781", "515880", "588000"],
        "AI_COMPUTE": ["159781", "515880", "588000"],
        "AI_NETWORKING": ["515880", "159781", "588000"],
        "BIOTECH": ["159992", "159217"],
        "ENERGY": ["159981", "513350", "515220"],
        "GOLD": ["518880", "159315", "518860"],
    }
    codes = explicit or fixed.get(str(mapping.get("family") or ""), [])
    universe = read_json(ROOT / "config" / "market" / "etf_monitor_universe.json", {})
    names = {str(x.get("code") or ""): str(x.get("name") or "") for x in (universe.get("objects") or [])}
    return [f"{names.get(code, code)}（{code}）" for code in codes]


def _format_delta(value: Any) -> str:
    n = _number(value)
    return "不可比" if n is None else f"{n:+.4f}"

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
    """Render one concise, target-specific Hyperliquid contribution."""
    context = read_json(ROOT / "data/state/overseas_context.json", {})
    rows = _mapping_rows(f"{text} {target}", context)
    if not rows:
        return "Hyperliquid：当前对象无固定映射，无法用于判断。"
    statements = []
    for mapping, obs in rows[:3]:
        family = FAMILY_LABELS.get(str(mapping.get("family") or ""), "相关外部方向")
        labels = _fixed_etf_labels(mapping)
        target_text = "、".join(labels[:4]) if labels else "当前对象无固定ETF映射"
        ctx = obs.get("context") or obs
        direction = _direction(ctx.get("markPx") or ctx.get("mark_px"), ctx.get("prevDayPx") or ctx.get("prev_day_px"))
        comparison = str(obs.get("pulse_comparison_status") or "NOT_COMPARABLE")
        delta = obs.get("pulse_delta") if isinstance(obs.get("pulse_delta"), dict) else {}
        crowding = str(obs.get("crowding_signal") or "")
        if comparison != "COMPARABLE":
            effect = "无法判断，缺少前一合法脉冲比较基准"
        elif "CROWDING" in crowding:
            effect = "拥挤风险，降低追涨优先级"
        elif direction == "价格偏强":
            effect = "方向支持/时机过滤，仍需A股ETF自身同步确认"
        elif direction == "价格偏弱":
            effect = "方向矛盾/风险过滤，不支持提高新增优先级"
        else:
            effect = "当前无法用于方向判断"
        detail = f"{family}对象映射至{target_text}；{direction}，{effect}"
        if comparison == "COMPARABLE":
            detail += f"；10分钟变化：资金费率{_format_delta(delta.get('funding'))}、未平仓量{_format_delta(delta.get('open_interest'))}、溢价{_format_delta(delta.get('premium'))}"
        statements.append(detail)
    return "Hyperliquid：" + "；".join(statements) + "。"

