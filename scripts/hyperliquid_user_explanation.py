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



def _latest_binding_rows(text: str) -> list[dict[str, Any]]:
    """Read ETF bindings from the global latest Hyperliquid contribution projection."""
    payload = read_json(ROOT / "data/state/hyperliquid_pulse_latest.json", {})
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    rows = []
    upper = text.upper()
    configured = read_json(MAP_PATH, {}).get("asset_mappings") or []
    alias_assets = {}
    for mapping in configured:
        asset = str(mapping.get("asset") or "")
        for alias in mapping.get("aliases") or []:
            alias_assets[str(alias).upper()] = asset
    alias_asset = next((asset for alias, asset in alias_assets.items() if alias in upper), "")
    for row in (latest.get("etf_bindings") or []):
        if not isinstance(row, dict):
            continue
        object_id = str(row.get("object_id") or row.get("asset") or "")
        family = str(row.get("family") or "").upper()
        direct = object_id and object_id.split(":", 1)[-1].lower() in text.lower()
        related = (
            alias_asset and object_id.upper() == alias_asset.upper()
        ) or (
            alias_asset and object_id.split(":", 1)[-1].upper() == alias_asset.split(":", 1)[-1].upper()
        )
        if direct or related:
            rows.append(row)
    return rows


def _binding_labels(row: dict[str, Any]) -> list[str]:
    codes = [str(x) for x in (row.get("mapped_assets") or []) if str(x)]
    universe = read_json(ROOT / "config" / "market" / "etf_monitor_universe.json", {})
    names = {str(x.get("code") or ""): str(x.get("name") or "") for x in (universe.get("objects") or [])}
    return [f"{names.get(code, code)}（{code}）" for code in codes]


def _format_delta(value: Any) -> str:
    n = _number(value)
    return "不可比" if n is None else f"{n:+.4f}"


def explain_for_notification(*, text: str, target: str = "") -> str:
    """Render the global contribution package without rebuilding mappings."""
    rows = _latest_binding_rows(f"{text} {target}")
    if not rows:
        return "Hyperliquid：当前对象未在本次全局贡献包形成可消费绑定；不改变ETF配置，等待下一次贡献包刷新。该状态不是对象无映射，而是当前投影未提供可消费绑定。"
    statements = []
    for row in rows[:3]:
        labels = _binding_labels(row)
        target_text = "、".join(labels[:4]) if labels else "固定映射对象"
        direction = str(row.get("direction_signal") or "UNDETERMINED")
        effect = str(row.get("decision_effect") or "INSUFFICIENT")
        effect_text = {
            "SUPPORT": "支持",
            "CONFIRMATORY": "支持/确认",
            "CONTRADICT": "矛盾/压制",
            "CONTRADICTION": "矛盾/压制",
            "TIMING_FILTER": "时机过滤",
            "CROWDING_RISK": "拥挤风险",
            "NO_DIRECT_MAPPING": "无直接映射",
            "INSUFFICIENT": "保持观察，等待下一合法脉冲",
        }.get(effect, "当前无法判断")
        signal = row.get("signal_fields") if isinstance(row.get("signal_fields"), dict) else {}
        comparison = str(signal.get("pulse_comparison_status") or row.get("pulse_comparison_status") or "NOT_COMPARABLE")
        priority = row.get("priority_effect") or "保持观察优先级，等待传导"
        trigger = row.get("next_trigger") or "A股同步传导且本地承接正常时升级"
        detail = f"{row.get('object_id') or row.get('asset')}绑定{target_text}；方向{direction}，结论为{effect_text}；{priority}；升级条件：{trigger}"
        if comparison == "COMPARABLE":
            delta = signal.get("pulse_delta") if isinstance(signal.get("pulse_delta"), dict) else {}
            detail += f"；10分钟变化：资金费率{_format_delta(delta.get('funding'))}、未平仓量{_format_delta(delta.get('open_interest'))}、溢价{_format_delta(delta.get('premium'))}"
        else:
            reason = signal.get("comparison_reason") or "缺少前一合法脉冲比较基准"
            detail += f"；本次不升级优先级：{reason}"
        statements.append(detail)
    return "Hyperliquid：" + "；".join(statements) + "。"
