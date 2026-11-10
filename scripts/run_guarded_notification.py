from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from notification_materiality_guard import notification_evidence_error
from hyperliquid_contribution import build_contribution


REPORT_RETRY_DELAYS_SECONDS = (2.0, 8.0)


def _guarded_persist(original):
    def guarded(event: dict, *, policy: str):
        error = notification_evidence_error(event)
        if error:
            return {
                "status": "REJECTED_NO_COMPARABLE_EVIDENCE",
                "detail": error,
                "title": event.get("title"),
            }
        return original(event, policy=policy)

    return guarded


def _pulse_object_label(item: dict) -> str:
    asset = str(item.get("asset") or "").strip()
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    direction = str(item.get("direction") or "").strip()
    if asset:
        direction_text = {"negative": "偏弱", "positive": "偏强", "mixed": "分化"}.get(direction, direction)
        return f"{asset}（{direction_text}）" if direction_text else asset
    signature = str(item.get("candidate_signature") or "").strip()
    if not code and signature:
        code = signature.split("::", 1)[0]
    if code and not name:
        try:
            from notification_center import ROOT, read_json
            from pulse_history import read_pulse_projection
            discovery = read_pulse_projection(ROOT, "DISCOVERY")
            rows = []
            latest = discovery.get("latest") if isinstance(discovery, dict) else None
            if isinstance(latest, dict):
                rows.extend(latest.get("candidates") or [])
            days = discovery.get("days") if isinstance(discovery, dict) else None
            if isinstance(days, dict):
                for entries in days.values():
                    if isinstance(entries, list):
                        rows.extend(entries)
            name = next(
                (str(row.get("name") or "").strip() for row in rows
                 if str(row.get("code") or "") == code),
                "",
            )
            if not name:
                name = next(
                    (str(row.get("name") or "").strip() for row in rows
                     if str(row.get("candidate_signature") or "").split("::", 1)[0] == code),
                    "",
                )
            if not name:
                config = read_json(ROOT / "config" / "market" / "etf_monitor_universe.json", {})
                name = next(
                    (str(row.get("name") or "").strip() for row in (config.get("objects") or [])
                     if str(row.get("code") or "") == code),
                    "",
                )
        except Exception:
            name = ""
    if code and name:
        return f"{name}（{code}）"
    return code or name or signature


def _pulse_fact_display(fact: dict) -> str:
    objects = "、".join(str(x) for x in (fact.get("objects") or []) if str(x)) or "暂未列明"
    count = fact.get("candidate_count") or fact.get("observation_count") or 0
    return f"识别对象数：{count}；对象：{objects}。"


def _hyperliquid_signal_fingerprint(fact: dict) -> dict:
    """Keep comparable signal fields without exposing the full HL catalog."""
    result = {}
    for item in (fact.get("observations") or []):
        asset = str(item.get("asset") or "").strip()
        if not asset:
            continue
        result[asset] = {
            key: str(item.get(key) or "")
            for key in ("funding", "open_interest", "premium", "mark_px", "mid_px", "prev_day_px")
        }
    return result


def _hyperliquid_signal_changes(before: dict, after: dict) -> list[str]:
    old = _hyperliquid_signal_fingerprint(before)
    new = _hyperliquid_signal_fingerprint(after)
    changes = []
    for asset in sorted(set(old) | set(new)):
        if old.get(asset) == new.get(asset):
            continue
        fields = [
            label for key, label in (
                ("funding", "资金费率"),
                ("open_interest", "未平仓量"),
                ("premium", "溢价"),
                ("mark_px", "标记价格"),
                ("mid_px", "中间价格"),
                ("prev_day_px", "前日价格"),
            )
            if old.get(asset, {}).get(key) != new.get(asset, {}).get(key)
        ]
        if fields:
            changes.append(f"{asset}：" + "、".join(fields) + "发生变化")
    return changes


def _hyperliquid_asset_explanation(asset: str) -> str:
    asset = str(asset or "").strip()
    if not asset:
        return ""
    display_asset = asset.split(":", 1)[-1]
    family_labels = {
        "CHINA_TECH_RISK": "中国互联网与平台经济方向",
        "BIOTECH": "美国生物科技与创新药方向",
        "SEMICONDUCTOR": "半导体方向",
        "SEMICONDUCTOR_HIGH_BETA": "高波动半导体方向",
        "MEMORY": "存储与内存产业链方向",
        "CHINA_MEMORY_CHAIN": "中国存储产业链方向",
        "AI_COMPUTE": "人工智能与算力方向",
        "AI_NETWORKING": "人工智能网络与通信方向",
        "ENERGY": "能源、油气与化工方向",
        "GOLD": "黄金与资源方向",
        "INDUSTRIAL_METALS": "工业金属与资源方向",
        "KOREA_EQUITY": "韩国及亚洲股票方向",
        "JAPAN_EQUITY": "日本股票方向",
        "TAIWAN_EQUITY": "台湾及亚洲科技方向",
        "GLOBAL_RISK_APPETITE": "全球风险偏好与成长资产方向",
    }
    try:
        from notification_center import ROOT, read_json
        config = read_json(ROOT / "config" / "market" / "hyperliquid_etf_exposure_map.json", {})
        rows = config.get("asset_mappings") or []
        match = next(
            (row for row in rows
             if str(row.get("asset") or "").split(":", 1)[-1] == asset.split(":", 1)[-1]),
            None,
        )
        if match:
            family = str(match.get("family") or "")
            description = family_labels.get(family)
            if description:
                return f"{display_asset}（{description}）"
    except Exception:
        pass
    return f"{display_asset}（当前仅确认其为外部参照资产，具体经济暴露尚未完成确认）"


def _canonicalize_etf_labels(labels: list[str]) -> list[str]:
    """Keep one canonical user label per ETF code across all external assets."""
    chosen = {}
    order = []
    for label in labels:
        text = str(label or "").strip()
        if not text:
            continue
        match = re.search(r"[（(](\d{6})[）)]", text)
        code = match.group(1) if match else text
        if code not in chosen:
            chosen[code] = text
            order.append(code)
        else:
            current = chosen[code]
            # Prefer the fuller ETF display name over a shortened alias.
            if ("ETF" in text and "ETF" not in current) or len(text) > len(current):
                chosen[code] = text
    return [chosen[code] for code in order]

def _hyperliquid_relevant_asset_objects() -> dict[str, list[str]]:
    """Return mapped Hyperliquid assets with current ETF decision objects."""
    try:
        from notification_center import ROOT, read_json
        mapping = read_json(ROOT / "config/market/hyperliquid_etf_exposure_map.json", {})
        universe = read_json(ROOT / "config/market/etf_monitor_universe.json", {})
        account = read_json(ROOT / "data/state/account_fact.json", {})
        opportunity = read_json(ROOT / "data/state/formal_opportunity_current.json", {})
        discovery = read_json(ROOT / "data/state/formal_discovery_current.json", {})
    except Exception:
        return {}
    objects = []
    objects.extend(item for item in (universe.get("objects") or []) if isinstance(item, dict))
    objects.extend(item for item in (account.get("positions") or []) if isinstance(item, dict))
    objects.extend(item for item in (opportunity.get("opportunities") or []) if isinstance(item, dict))
    discovery_rows = discovery.get("candidates") or (discovery.get("discovery") or {}).get("candidates") or []
    objects.extend(item for item in discovery_rows if isinstance(item, dict))
    normalized = []
    for item in objects:
        code = str(item.get("code") or item.get("security_code") or item.get("symbol") or "").strip()
        name = str(item.get("name") or item.get("security_name") or item.get("display_name") or "").strip()
        if name or code:
            normalized.append((code, name))
    result = {}
    preferred = {
        "CHINA_TECH_RISK": ("科技", "互联网", "平台", "恒生", "港股宽基"),
        "BIOTECH": ("创新药", "医药", "医疗", "生物", "器械"),
        "SEMICONDUCTOR": ("半导体", "芯片", "科创", "通信", "电子", "设备"),
        "SEMICONDUCTOR_HIGH_BETA": ("半导体", "芯片", "科创", "通信", "电子", "设备"),
        "MEMORY": ("半导体", "芯片", "存储", "科创", "通信", "电子", "设备"),
        "AI_COMPUTE": ("人工智能", "算力", "半导体", "芯片", "通信", "电子", "科创", "纳指"),
        "AI_NETWORKING": ("人工智能", "算力", "半导体", "芯片", "通信", "电子"),
        "ENERGY": ("能源", "油气", "煤炭", "化工", "石化"),
        "GOLD": ("黄金", "有色", "资源"),
        "INDUSTRIAL_METALS": ("有色", "工业金属", "资源", "电网", "新能源"),
        "KOREA_EQUITY": ("韩国", "亚洲", "恒生", "科技"),
        "JAPAN_EQUITY": ("日本", "日经", "亚洲"),
        "TAIWAN_EQUITY": ("台湾", "亚洲", "半导体", "科技"),
        "GLOBAL_RISK_APPETITE": ("宽基", "成长", "科创", "创业", "标普", "纳指"),
    }
    excluded = {
        "CHINA_TECH_RISK": ("创新药", "医药", "医疗", "器械", "红利", "低波", "央企"),
        "BIOTECH": ("红利", "低波", "央企", "煤炭", "能源", "科技"),
    }
    for row in (mapping.get("asset_mappings") or []):
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset") or "").strip()
        markers = [str(marker).strip().lower() for marker in (row.get("name_markers") or []) if str(marker).strip()]
        excluded_row_markers = [str(marker).strip().lower() for marker in (row.get("exclude_name_markers") or []) if str(marker).strip()]
        if not asset or not markers:
            continue
        labels = []
        for code, name in normalized:
            if any(marker in name.lower() for marker in markers):
                labels.append(f"{name}（{code}）" if name and code else (name or code))
        if excluded_row_markers:
            labels = [
                label for label in labels
                if not any(marker in label.lower() for marker in excluded_row_markers)
            ]
        family = str(row.get("family") or "")
        preferred_markers = preferred.get(family) or ()
        excluded_markers = excluded.get(family) or ()
        if preferred_markers:
            focused = [label for label in labels if any(marker in label.lower() for marker in preferred_markers)]
            if focused:
                labels = focused
        if excluded_markers:
            labels = [label for label in labels if not any(marker in label.lower() for marker in excluded_markers)]
        if labels:
            result[asset] = list(dict.fromkeys(labels))[:4]
    all_labels = [label for labels in result.values() for label in labels]
    canonical = _canonicalize_etf_labels(all_labels)
    by_code = {re.search(r"[（(](\d{6})[）)]", label).group(1): label for label in canonical if re.search(r"[（(](\d{6})[）)]", label)}
    result = {
        asset: [by_code.get(re.search(r"[（(](\d{6})[）)]", label).group(1), label) if re.search(r"[（(](\d{6})[）)]", label) else label for label in labels]
        for asset, labels in result.items()
    }
    return result


def _pulse_candidate_identity(item: dict) -> str:
    """Return the stable business identity used for Discovery comparison."""
    return str(
        item.get("candidate_signature")
        or item.get("code")
        or item.get("security_code")
        or item.get("symbol")
        or item.get("name")
        or ""
    ).strip()


def _discovery_candidate_fingerprint(item: dict) -> tuple:
    """Compare business state, not only candidate membership."""
    states = tuple(sorted(
        str(x.get("state") or "").strip().upper()
        for x in (item.get("surfaced_states") or [])
        if isinstance(x, dict)
    ))
    spot = item.get("discovery_spot") or {}
    spot_classes = tuple(sorted(
        key for key in ("change_pct", "turnover_pct")
        if isinstance(spot, dict) and spot.get(key) not in (None, "")
    ))
    evidence = []
    for state_item in (item.get("surfaced_states") or []):
        if not isinstance(state_item, dict):
            continue
        ev = state_item.get("evidence") or {}
        evidence.append((str(state_item.get("state") or "").upper(), tuple(
            (key, str(ev.get(key))) for key in sorted(ev) if ev.get(key) not in (None, "")
        )))
    spot_values = tuple(
        (key, str(spot.get(key))) for key in sorted(spot)
        if spot.get(key) not in (None, "")
    ) if isinstance(spot, dict) else ()
    return (
        _pulse_candidate_identity(item),
        str(item.get("object_role") or item.get("role") or "").strip().upper(),
        str(item.get("state") or item.get("status") or item.get("lifecycle") or "").strip().upper(),
        states,
        spot_classes,
        tuple(evidence),
        spot_values,
        tuple(sorted((str(k), str(v)) for k, v in (item.get("historical_context") or {}).items() if v not in (None, ""))),
    )


def _discovery_candidates_changed(before: dict, after: dict) -> bool:
    """Detect additions, removals and meaningful state changes for all ETF roles."""
    def index(payload: dict) -> dict:
        return {
            _pulse_candidate_identity(item): _discovery_candidate_fingerprint(item)
            for item in (payload.get("candidates") or [])
            if isinstance(item, dict) and _pulse_candidate_identity(item)
        }
    return index(before) != index(after)

def _hyperliquid_decision_signal_changed(before: dict, after: dict) -> bool:
    """Only decision-bearing HL fields can qualify for a user pulse.

    Price snapshots remain machine evidence; funding, open interest and premium
    are the fields that can explain timing, crowding or liquidity implications.
    """
    old = _hyperliquid_signal_fingerprint(before)
    new = _hyperliquid_signal_fingerprint(after)
    for asset in set(old) | set(new):
        before_item = old.get(asset, {})
        after_item = new.get(asset, {})
        if any(before_item.get(key) != after_item.get(key)
               for key in ("funding", "open_interest", "premium")):
            return True
    return False


def _pulse_signal_explanation(signal_changes: list[str]) -> str:
    """Translate machine signal names into concise user-facing meaning."""
    explanations = []
    joined = "、".join(signal_changes)
    if "资金费率" in joined:
        explanations.append("资金费率反映多空持仓的周期性资金成本，变化说明做多或做空的一方正在变得更拥挤")
    if "未平仓量" in joined:
        explanations.append("未平仓量反映市场中尚未平仓的合约规模，变化说明参与资金和风险暴露正在增加或减少")
    if "溢价" in joined:
        explanations.append("溢价反映合约价格相对参考现货的偏离，扩大通常意味着情绪或流动性出现变化")
    return "；".join(explanations)


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _human_signal(asset: str, before: dict, after: dict) -> str:
    """Translate HL changes into direct user value, not metric narration."""
    old = {str(k): v for k, v in (before or {}).items()}
    new = {str(k): v for k, v in (after or {}).items()}
    display = asset.split(":", 1)[-1]
    parts = []
    old_direction = str(old.get("direction") or "").lower()
    new_direction = str(new.get("direction") or "").lower()
    direction_names = {"positive": "偏强", "negative": "偏弱", "mixed": "分化"}
    if new_direction and new_direction != old_direction:
        if old_direction:
            parts.append(f"外部方向由{direction_names.get(old_direction, '此前状态')}转为{direction_names.get(new_direction, '新状态')}")
        else:
            parts.append(f"外部方向转为{direction_names.get(new_direction, '新状态')}")

    old_funding, new_funding = _number(old.get("funding")), _number(new.get("funding"))
    if old_funding is not None and new_funding is not None and old_funding * new_funding < 0:
        parts.append("做多与做空的拥挤方向发生反转")
    elif old.get("funding") != new.get("funding") and new_funding is not None:
        if new_funding > 0:
            parts.append("做多一侧拥挤加深，继续上涨的波动风险上升")
        elif new_funding < 0:
            parts.append("做空一侧拥挤加深，继续下跌或反弹挤压的风险上升")
        else:
            parts.append("多空拥挤方向趋于中性")

    old_oi, new_oi = _number(old.get("open_interest")), _number(new.get("open_interest"))
    if old_oi is not None and new_oi is not None and abs(old_oi) > 0:
        ratio = (new_oi - old_oi) / abs(old_oi)
        if ratio >= 0.05:
            parts.append("新增仓位增加，短线波动风险上升")
        elif ratio <= -0.05:
            parts.append("部分仓位退出，短线波动压力缓和")
    elif old.get("open_interest") != new.get("open_interest"):
        parts.append("市场参与度变化，短线波动风险随之变化")

    old_premium, new_premium = _number(old.get("premium")), _number(new.get("premium"))
    if old_premium is not None and new_premium is not None:
        if abs(new_premium) > abs(old_premium) and abs(new_premium - old_premium) >= 0.001:
            parts.append("价格偏离扩大，市场情绪波动加大")
        elif abs(new_premium) < abs(old_premium) and abs(new_premium - old_premium) >= 0.001:
            parts.append("价格偏离收窄，市场情绪趋于稳定")
    elif old.get("premium") != new.get("premium"):
        parts.append("价格偏离发生变化，短线情绪需要重新确认")

    return f"{display}：" + "；".join(dict.fromkeys(parts)) if parts else ""


def _hyperliquid_mapping_metadata(asset: str, label: str) -> tuple[str, str]:
    """Classify the mapping and translate its decision value for users."""
    family = ""
    try:
        from notification_center import ROOT, read_json
        mapping = read_json(ROOT / "config" / "market" / "hyperliquid_etf_exposure_map.json", {})
        for row in (mapping.get("asset_mappings") or []):
            if str(row.get("asset") or "").strip() == str(asset).strip():
                family = str(row.get("family") or "")
                break
    except Exception:
        pass
    label_name = label.split("（", 1)[0]
    direct_markers = {
        "BIOTECH": ("创新药", "医药", "医疗", "生物"),
        "SEMICONDUCTOR": ("半导体", "芯片"),
        "SEMICONDUCTOR_HIGH_BETA": ("半导体", "芯片"),
        "MEMORY": ("存储", "内存", "半导体", "芯片"),
        "AI_COMPUTE": ("人工智能", "算力", "半导体", "芯片"),
        "AI_NETWORKING": ("人工智能", "算力", "通信"),
        "ENERGY": ("能源", "油气", "煤炭", "化工", "石化"),
    }
    if any(marker in label_name for marker in (direct_markers.get(family) or ())):
        relation = "直接/产业链关系"
    elif family in {"KOREA_EQUITY", "JAPAN_EQUITY", "TAIWAN_EQUITY", "GLOBAL_RISK_APPETITE"}:
        relation = "间接市场/风险偏好关系"
    else:
        relation = "间接风格或共同风险因子关系"
    return family, relation




def _hyperliquid_plain_signal_basis(signal_changes: list[str]) -> str:
    """Return a user-facing value statement, never a list of raw metric categories."""
    return "外部资金与价格行为出现足以改变该ETF复核优先级的变化"

def _hyperliquid_user_scope(
    before: dict,
    after: dict,
    changed_assets: set[str],
    relevant_objects: dict[str, list[str]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Build a user scope while preserving classified asset-to-ETF bindings."""
    old_by_asset = {
        str(x.get("asset") or "").strip(): x
        for x in (before.get("observations") or [])
        if isinstance(x, dict)
    }
    new_by_asset = {
        str(x.get("asset") or "").strip(): x
        for x in (after.get("observations") or [])
        if isinstance(x, dict)
    }
    labels = []
    rows = []
    bindings = {}
    for asset in sorted(changed_assets):
        mapped = (relevant_objects.get(asset) or [])[:2]
        labels.extend(mapped)
        row = _human_signal(asset, old_by_asset.get(asset, {}), new_by_asset.get(asset, {}))
        if not row:
            continue
        rows.append(row)
        display_asset = asset.split(":", 1)[-1]
        signal_text = row.split("：", 1)[-1]
        if mapped:
            for label in mapped:
                _, relation = _hyperliquid_mapping_metadata(asset, label)
                if relation == "直接/产业链关系":
                    impact = "直接影响该ETF对应产业方向的强弱判断"
                elif relation == "间接市场/风险偏好关系":
                    impact = "间接影响该ETF的市场风险偏好和时机判断"
                else:
                    impact = "间接影响该ETF的风格、拥挤风险和时机判断"
                bindings.setdefault(asset, []).append(label)
    deduped = []
    seen_codes = set()
    for label in labels:
        code = label.rsplit("（", 1)[-1].rstrip("）") if "（" in label else label
        if code in seen_codes:
            continue
        seen_codes.add(code)
        deduped.append(label)
    return deduped[:4], rows[:6], sorted(changed_assets), bindings


def _pulse_object_impact(source: str, related_text: str, added: list[str], removed: list[str]) -> str:
    objects = related_text or "相关ETF对象"
    if source == "discovery_pulse":
        changed = "、".join((added + removed)[:6]) or objects
        return f"对ETF系统的直接建议是：将{changed}默认列为优先买入建议对象，立即与现金、现有持仓和观察ETF比较；只有明确反证才降级或否决。该建议需要用户确认，不自动下单。"
    return f"对ETF系统的具体价值是：为{objects}提供A股交易时段之外的方向、时机或拥挤风险线索；下一次正式判断要先核对这些ETF自身是否同步，再决定维持观察、继续持有或调整机会排序，不把外部资产直接当成A股ETF。"



def _discovery_reason(item: dict) -> str:
    """Translate all populated, decision-relevant Discovery evidence."""
    states = {
        str(x.get("state") or "").strip().upper()
        for x in (item.get("surfaced_states") or [])
        if isinstance(x, dict)
    }
    spot = item.get("discovery_spot") or {}
    class_names = {
        "PERSISTENT_STRUCTURE": "中短期趋势持续",
        "PERSISTENT_TREND": "中短期趋势持续",
        "TREND_CHANGE": "趋势正在转强",
        "SHORT_TERM_CHANGE": "短期走势明显改善",
        "NEW_RELATIVE_DIVERGENCE": "相对同类或基准出现分化",
        "STRUCTURAL_CHANGE": "走势结构发生变化",
        "SHORT_HISTORY_CURRENT_CHANGE": "上市时间较短但近期出现明显变化",
        "EXTERNAL_DRIVER_TRIGGER": "出现外部驱动线索",
    }
    info_classes = [class_names.get(str(x).upper(), str(x)) for x in (item.get("information_classes") or [])]
    state_names = {
        "PERSISTENT_TREND": "中短期趋势持续",
        "TREND_CHANGE": "趋势转强",
        "SHORT_TERM_CHANGE": "短期变化",
        "RECOVERY_BREAKOUT": "修复并接近前高",
    }
    states_text = [state_names.get(x, x) for x in sorted(states)]
    evidence_parts = []
    if info_classes:
        evidence_parts.append("发现类型：" + "、".join(dict.fromkeys(info_classes)))
    if states_text:
        evidence_parts.append("状态证据：" + "、".join(dict.fromkeys(states_text)))

    def pct_text(value):
        try:
            return f"{float(value) * 100:+.1f}%"
        except (TypeError, ValueError):
            return "数据缺失"
    returns = {}
    state_detail = []
    for state_item in (item.get("surfaced_states") or []):
        if isinstance(state_item, dict):
            evidence = state_item.get("evidence") or {}
            returns.update(evidence.get("window_returns") or {})
            for key, label in (("return_10", "10日"), ("return_40", "40日")):
                if evidence.get(key) is not None:
                    state_detail.append(f"{label}{pct_text(evidence[key])}")
            if evidence.get("max_drawdown_60") is not None:
                state_detail.append(f"60日最大回撤{float(evidence['max_drawdown_60']) * 100:.1f}%")
            if evidence.get("short_vs_long_normalized_gap") is not None:
                state_detail.append(f"短长趋势差{pct_text(evidence['short_vs_long_normalized_gap'])}")
            if evidence.get("short_vs_medium_normalized_gap") is not None:
                state_detail.append(f"短中趋势差{pct_text(evidence['short_vs_medium_normalized_gap'])}")
    returns.update((spot.get("window_returns") if isinstance(spot, dict) else None) or {})
    price_context = item.get("price_context") or {}
    if isinstance(price_context, dict):
        for key in ("return_5d", "return_10d", "return_20d", "return_40d", "return_60d", "return_ytd"):
            if price_context.get(key) is not None:
                window = key.removeprefix("return_").removesuffix("d").replace("_", "")
                try:
                    numeric = float(price_context[key])
                    returns.setdefault(window, numeric / 100 if abs(numeric) > 1 else numeric)
                except (TypeError, ValueError):
                    continue
    trend_detail = [f"{w}日{pct_text(returns[w])}" for w in ("5", "10", "20", "40", "60") if returns.get(w) is not None]
    if trend_detail:
        evidence_parts.append("区间表现：" + "、".join(trend_detail))
    if state_detail:
        evidence_parts.append("状态细节：" + "、".join(dict.fromkeys(state_detail)))
    if isinstance(spot, dict):
        node = []
        labels = (("change_pct", "本节点涨跌", True), ("amplitude_pct", "日内振幅", True),
                  ("turnover_pct", "换手承接", True), ("volume_ratio", "量比", False),
                  ("return_60d_pct", "60日", True), ("return_ytd_pct", "年初至今", True))
        for key, label, percent in labels:
            value = spot.get(key)
            if value not in (None, ""):
                try:
                    number = float(value)
                    node.append(f"{label}{number:+.1f}{'%' if percent else '倍'}")
                except (TypeError, ValueError):
                    pass
        if spot.get("amount") not in (None, ""):
            try:
                node.append(f"成交额{float(spot['amount']) / 100000000:.2f}亿元")
            except (TypeError, ValueError):
                pass
        if node:
            evidence_parts.append("本节点：" + "、".join(node))
        path = [spot.get(k) for k in ("open", "high", "low", "price")]
        if all(v not in (None, "") for v in path):
            evidence_parts.append("价格路径：开盘→最高→最低→现价 " + "→".join(f"{float(v):.3f}" for v in path))
    volume_context = item.get("volume_context") or {}
    if isinstance(volume_context, dict):
        volume_parts = []
        for key, label in (("turnover_ratio", "换手承接"), ("volume_ratio", "量比"), ("amount", "成交额")):
            value = volume_context.get(key)
            if value in (None, ""):
                continue
            try:
                if key == "amount":
                    volume_parts.append(f"{label}{float(value) / 100000000:.2f}亿元")
                elif key == "turnover_ratio":
                    volume_parts.append(f"{label}{float(value):+.1f}%")
                else:
                    volume_parts.append(f"{label}{float(value):.1f}倍")
            except (TypeError, ValueError):
                continue
        if volume_parts:
            evidence_parts.append("成交证据：" + "、".join(volume_parts))
    for key, label in (("reason", "入选理由"), ("selection_reason", "入选理由"), ("impact", "当前影响")):
        value = item.get(key)
        if value not in (None, ""):
            evidence_parts.append(f"{label}：{value}")
    history = item.get("historical_context") or {}
    if history.get("sample_count") not in (None, ""):
        evidence_parts.append(f"历史样本：已完成{history['sample_count']}个交易日")
    if history.get("status") == "SHORT_HISTORY_LIMITED":
        evidence_parts.append("历史限制：样本不足，不能据此推导完整长期趋势")
    drivers = item.get("external_driver_evidence") or []
    if drivers:
        evidence_parts.append("外部线索：" + "；".join(str(x) for x in drivers[:3]))
    return "；".join(evidence_parts) or "Discovery发现该ETF出现了需要正式判断核对的变化"


def _hyperliquid_user_change(asset: str, before: dict, after: dict) -> str:
    """Backward-compatible wrapper for the single user-value translator."""
    return _human_signal(asset, before, after).split("：", 1)[-1]


def _hyperliquid_evidence_text(asset: str, labels: list[str], before: dict, after: dict) -> str:
    """Render complete HL evidence for one asset/ETF binding without raw values."""
    _, relation = _hyperliquid_mapping_metadata(asset, labels[0] if labels else "")
    relation_text = {
        "直接/产业链关系": "直接对应产业方向",
        "间接市场/风险偏好关系": "间接反映市场风险偏好",
        "间接风格或共同风险因子关系": "间接反映共同风格和风险偏好",
    }.get(relation, relation)
    family_text = {
        "BIOTECH": "美国生物科技与创新药方向",
        "CHINA_TECH_RISK": "中国互联网与平台经济方向",
        "SEMICONDUCTOR": "半导体方向",
        "SEMICONDUCTOR_HIGH_BETA": "高波动半导体方向",
        "MEMORY": "存储与内存产业链方向",
        "AI_COMPUTE": "人工智能与算力方向",
        "AI_NETWORKING": "人工智能网络与通信方向",
        "ENERGY": "能源与化工方向",
        "GLOBAL_RISK_APPETITE": "全球风险偏好方向",
    }
    family, _ = _hyperliquid_mapping_metadata(asset, labels[0] if labels else "")
    direction = str(after.get("direction") or "").lower()
    direction_text = {"positive": "外部方向偏强", "negative": "外部方向偏弱", "mixed": "外部方向分化"}.get(direction, "外部方向发生变化")
    old_direction = str(before.get("direction") or "").lower()
    if old_direction and old_direction != direction:
        direction_text = f"外部方向由{ {'positive':'偏强','negative':'偏弱','mixed':'分化'}.get(old_direction, '此前未明确') }转为{ {'positive':'偏强','negative':'偏弱','mixed':'分化'}.get(direction, '新的状态') }"
    change = _hyperliquid_user_change(asset, before, after)
    risk_words = ("拥挤加深", "风险上升", "偏离扩大", "压力")
    if direction == "negative" or any(word in change for word in risk_words):
        effect = "对上述ETF属于压制或风险过滤，不支持直接提高看多判断"
        use = "当前只用于风险过滤"
        recommendation = "建议维持观察或降低新增优先级"
    elif direction == "positive":
        effect = "对上述ETF属于方向支持，但仍需结合ETF自身走势确认"
        use = "当前用于方向确认和时机判断"
        recommendation = "建议提高买入评估优先级，但仍需核对ETF自身承接"
    else:
        effect = "对上述ETF暂不能归类为支持或压制，只能作为观察线索"
        use = "当前只用于观察"
        recommendation = "建议暂不据此升级买入，等待可比方向或价格信号"
    target = "、".join(labels)
    return (
        f"{asset.split(':', 1)[-1]}代表{family_text.get(family, '相关外部市场方向')}，与{target}{relation_text}；"
        f"本次变化为{direction_text}，同时{change}；"
        f"对{target}的直接含义是：{effect}，{use}；直接建议：{recommendation}。"
    )


def _discovery_candidate_has_user_value(item: dict) -> bool:
    """Allow valuable changes for new, observed, opportunity and held ETFs."""
    states = {
        str(x.get("state") or "").strip().upper()
        for x in (item.get("surfaced_states") or [])
        if isinstance(x, dict)
    }
    spot = item.get("discovery_spot") or {}
    role = str(item.get("object_role") or item.get("role") or "").strip().upper()
    lifecycle = str(item.get("state") or item.get("status") or item.get("lifecycle") or "").strip().upper()
    return bool(
        states
        or (isinstance(spot, dict) and any(spot.get(key) not in (None, "") for key in ("change_pct", "turnover_pct")))
        or role in {"HELD", "HOLDING", "OBSERVED", "OBSERVATION", "OPPORTUNITY", "NEW", "CANDIDATE"}
        or lifecycle
    )

def _number_value(value: object) -> float | None:
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _hyperliquid_signal_materially_changed(before: dict, after: dict) -> bool:
    """Accept any auditable decision-bearing change; metrics raise priority.

    The old implementation required one of three narrow numeric deltas.  That
    made direction changes, new/removed observations and qualitative state
    changes disappear even when the mapped ETF impact was clear.
    """
    if _hyperliquid_decision_signal_changed(before, after):
        return True
    old = _hyperliquid_signal_fingerprint(before)
    new = _hyperliquid_signal_fingerprint(after)
    for asset in set(old) | set(new):
        before_item, after_item = old.get(asset, {}), new.get(asset, {})
        funding_old = _number_value(before_item.get("funding"))
        funding_new = _number_value(after_item.get("funding"))
        if funding_old is not None and funding_new is not None and funding_new != funding_old:
            return True
        oi_old = _number_value(before_item.get("open_interest"))
        oi_new = _number_value(after_item.get("open_interest"))
        if oi_old is not None and oi_new is not None:
            base = max(abs(oi_old), 1.0)
            if oi_new != oi_old:
                return True
        premium_old = _number_value(before_item.get("premium"))
        premium_new = _number_value(after_item.get("premium"))
        if premium_old is not None and premium_new is not None and premium_new != premium_old:
            return True
    return False

def _pulse_user_window(source: str, after: dict) -> bool:
    """Validate a timestamp without dropping valid overnight evidence.

    Discovery and Hyperliquid use the same pulse cadence.  The user-visible
    window is no longer a hard 08:00-16:00 filter; outside A-share hours the
    fact remains eligible and is still subject to same-ETF mapping, evidence,
    deduplication and content validation.
    """
    if source not in {"discovery_pulse", "hyperliquid_pulse", "dual_engine_pulse"}:
        return False
    stamp = str(after.get("formed_at_beijing") or after.get("fact_time_beijing") or "").strip()
    match = re.search(r"T(\d{2}):\d{2}", stamp)
    if not match:
        return False
    hour = int(match.group(1))
    return 0 <= hour <= 23


def _integrated_pulse_event(discovery_before: dict, discovery_after: dict,
                            hyper_before: dict, hyper_after: dict) -> dict | None:
    """Aggregate both engines into one ETF-level user event for one pulse window."""
    if not _pulse_user_window("discovery_pulse", discovery_after):
        return None

    before_rows = {
        _pulse_candidate_identity(x): x
        for x in (discovery_before.get("candidates") or [])
        if isinstance(x, dict) and _pulse_candidate_identity(x)
    }
    after_rows = {
        _pulse_candidate_identity(x): x
        for x in (discovery_after.get("candidates") or [])
        if isinstance(x, dict) and _pulse_candidate_identity(x)
    }
    from continuous_pulse_state import transition

    discovery_changed = []
    discovery_transitions = {}
    for identity in sorted(set(before_rows) | set(after_rows)):
        old = before_rows.get(identity) or {}
        new = after_rows.get(identity) or {}
        if _discovery_candidate_fingerprint(old) == _discovery_candidate_fingerprint(new):
            continue
        row = new or old
        if _discovery_candidate_has_user_value(row):
            discovery_changed.append(row)
            discovery_transitions[_pulse_candidate_identity(row)] = transition(old, new)

    signal_changes = _hyperliquid_signal_changes(hyper_before, hyper_after)
    changed_assets = {
        item.split("：", 1)[0] for item in signal_changes
        if item.split("：", 1)[0]
    }
    hl_changed = bool(
        hyper_before.get("observations")
        and _hyperliquid_decision_signal_changed(hyper_before, hyper_after)
        and _hyperliquid_signal_materially_changed(hyper_before, hyper_after)
    )
    relevant_objects = _hyperliquid_relevant_asset_objects() if hl_changed else {}
    changed_assets &= set(relevant_objects)
    hl_labels, human_changes, _, bindings = _hyperliquid_user_scope(
        hyper_before, hyper_after, changed_assets, relevant_objects
    ) if changed_assets else ([], [], [], {})
    if not discovery_changed and not hl_labels:
        return None

    def label(row: dict) -> str:
        return _pulse_object_label(row)

    discovery_labels = [label(row) for row in discovery_changed]
    if not discovery_labels and not hl_labels:
        return None
    discovery_codes = {
        re.search(r"[（(](\d{6})[）)]", text).group(1)
        for text in discovery_labels
        if re.search(r"[（(](\d{6})[）)]", text)
    }
    hl_codes = {
        re.search(r"[（(](\d{6})[）)]", text).group(1)
        for text in hl_labels
        if re.search(r"[（(](\d{6})[）)]", text)
    }
    overlap = discovery_codes & hl_codes
    # Discovery and Hyperliquid are independent user-value channels.  A
    # Discovery candidate must remain notifyable when Hyperliquid has no
    # same-ETF mapping; Hyperliquid is enrichment, never a prerequisite.
    # Retain each channel's own affected objects and only describe overlap
    # when it actually exists.
    bindings = {
        asset: [
            label for label in labels
            if (re.search(r"[（(](\d{6})[）)]", label)
                and re.search(r"[（(](\d{6})[）)]", label).group(1) in overlap)
        ]
        for asset, labels in bindings.items()
    }
    bindings = {asset: labels for asset, labels in bindings.items() if labels}
    discovery_labels = [label(row) for row in discovery_changed]
    transition_text = {
        "INITIAL": "首次形成",
        "STRENGTHENING": "较上一脉冲继续强化",
        "WEAKENING": "较上一脉冲出现衰减",
        "REVERSING": "较上一脉冲出现方向反转",
        "PERSISTING": "较上一脉冲延续",
        "CHANGED": "较上一脉冲发生变化",
    }
    all_labels = _canonicalize_etf_labels(discovery_labels + hl_labels)
    if not all_labels:
        return None
    primary = next(
        (text for text in all_labels if re.search(r"[（(](\d{6})[）)]", text)
         and re.search(r"[（(](\d{6})[）)]", text).group(1) in overlap),
        all_labels[0],
    )
    if discovery_changed and hl_labels and overlap:
        mapped_assets = list(bindings)
        asset = mapped_assets[0] if mapped_assets else "外部方向"
        after_item = next(
            (item for item in (hyper_after.get("observations") or [])
             if str(item.get("asset") or "").strip() == asset),
            {},
        )
        direction = {"positive": "偏强", "negative": "偏弱", "mixed": "分化"}.get(
            str(after_item.get("direction") or "").lower(), "发生变化"
        )
        title = f"【市场异动】{primary}本地趋势变化，{asset.split(':', 1)[-1]}外部方向{direction}"
    elif discovery_changed and hl_labels:
        discovery_title = "、".join(discovery_labels[:3])
        hl_title = "、".join(hl_labels[:3])
        title = f"【市场异动】{discovery_title}进入复核；{hl_title}受外部信号影响"
    elif discovery_changed:
        title = f"【市场异动】{primary}进入Discovery复核范围"
    else:
        title = f"【市场异动】{primary}受外部信号影响"

    if discovery_changed:
        discovery_text = "；".join(
            f"{label(row)}：{transition_text.get(discovery_transitions.get(_pulse_candidate_identity(row), 'CHANGED'), '发生变化')}；{_discovery_reason(row)}"
            for row in discovery_changed[:4]
        )
    else:
        discovery_text = "本次未发现新的Discovery状态变化，但相关ETF仍在当前评估范围内。"
    if hl_labels:
        basis = "外部信号方向发生变化"
        before_by_asset = {
            str(item.get("asset") or "").strip(): item
            for item in (hyper_before.get("observations") or [])
            if isinstance(item, dict)
        }
        after_by_asset = {
            str(item.get("asset") or "").strip(): item
            for item in (hyper_after.get("observations") or [])
            if isinstance(item, dict)
        }
        change_by_asset = {
            row.split("：", 1)[0]: row
            for row in human_changes
            if "：" in row
        }
        hl_text = "；".join(
            _hyperliquid_evidence_text(
                asset, labels, before_by_asset.get(asset, {}), after_by_asset.get(asset, {})
            )
            for asset, labels in list(bindings.items())[:4]
            if labels
        ) or f"相关外部参照出现{basis}。"
    else:
        hl_text = "本次未形成可比的Hyperliquid确认，不能单独扩大结论。"

    if overlap:
        impact = "方向确认、入场时机或风险过滤"
        relation = "两类证据都指向该ETF，下一次正式判断核对其自身走势是否继续同步"
    elif discovery_changed and hl_labels:
        impact = "分别提高两组ETF的复核优先级"
        relation = "Discovery与Hyperliquid涉及不同ETF，外部信号不代表Discovery对象方向已改变"
    elif discovery_changed:
        impact = "下一次正式判断的比较优先级"
        relation = "当前只有Discovery证据，Hyperliquid未形成可比确认"
    else:
        impact = "方向确认、入场时机或风险过滤"
        relation = "当前只有外部证据，仍需结合ETF自身走势确认"

    if overlap:
        transition_states = set(discovery_transitions.values())
        if "STRENGTHENING" in transition_states:
            local_state = "本地趋势较上一脉冲继续强化"
        elif "WEAKENING" in transition_states:
            local_state = "本地趋势较上一脉冲出现衰减"
        elif "REVERSING" in transition_states:
            local_state = "本地趋势较上一脉冲出现反转"
        elif "INITIAL" in transition_states:
            local_state = "本地趋势首次形成有效变化"
        else:
            local_state = "本地趋势较上一脉冲延续"
        hl_has_support = "外部方向偏强" in hl_text
        hl_has_risk = any(word in hl_text for word in ("风险上升", "风险过滤", "压制", "拥挤加深", "偏离扩大"))
        if hl_has_support and hl_has_risk:
            effect_text = f"{local_state}，但外部方向偏强与拥挤风险同时存在，当前属于方向支持与风险过滤并存"
        elif hl_has_risk:
            effect_text = f"{local_state}，但外部信号主要用于风险过滤，不能提高看多判断"
        elif hl_has_support:
            effect_text = f"{local_state}，外部信号可用于方向确认，但仍需本地走势继续同步"
        else:
            effect_text = f"{local_state}，外部信号暂只能作为观察线索，尚不能提高结论强度"
        core_conclusion = f"【核心结论】{primary}{effect_text}。"
    elif discovery_changed and hl_labels:
        core_conclusion = (
            f"【核心结论】Discovery发现{discovery_title}进入复核，"
            f"Hyperliquid显示{hl_title}受外部信号影响；{relation}，"
            f"当前价值是分别提高两组ETF的复核优先级，不能把外部信号归因于Discovery对象，"
            f"也不代表自动交易。"
        )
    elif discovery_changed:
        core_conclusion = (
            f"【核心结论】{primary}进入Discovery复核范围，{relation}；"
            f"当前价值是提高下一次正式判断中的比较优先级，不代表自动交易。"
        )
    else:
        core_conclusion = (
            f"【核心结论】{primary}受外部信号影响，{relation}；"
            f"当前价值是提高下一次正式判断中的复核优先级，不代表自动交易。"
        )

    if overlap and "WEAKENING" in set(discovery_transitions.values()):
        next_check = f"重点核对{primary}自身走弱是否延续，以及成交承接是否同步减弱；若本地继续走弱，外部强势不能替代ETF自身确认。"
    elif overlap and "REVERSING" in set(discovery_transitions.values()):
        next_check = f"重点核对{primary}是否确认方向反转；在反转未被自身走势和成交承接确认前，不提高结论强度。"
    elif overlap:
        next_check = f"重点核对{primary}自身强势能否延续，以及成交承接是否保持；若本地不再同步，外部信号只保留为风险过滤。"
    else:
        next_check = f"重点核对{primary}自身走势和成交承接，再决定外部证据用于方向确认、时机判断还是风险过滤。"
    hyperliquid_time = str(
        hyper_after.get("collected_at_beijing")
        or hyper_after.get("fact_time_beijing")
        or hyper_after.get("formed_at_beijing")
        or ""
    )
    hyperliquid_time_display = (
        hyperliquid_time.replace("T", " ").replace("+08:00", "")
        if hyperliquid_time else "尚未确认"
    )
    hyperliquid_time_note = f"Hyperliquid采集时间（北京时间）：{hyperliquid_time_display}"
    core_conclusion = f"{core_conclusion} Hyperliquid具体依据：{hl_text}（{hyperliquid_time_note}）"
    impact_text = (
        f"{relation}。Hyperliquid在本轮的作用是：{impact}；"
        f"具体依据为{hl_text}。"
    )
    content = (
        f"{core_conclusion}\n\n"
        f"【Discovery依据】{discovery_text}\n\n"
        f"【对ETF系统的影响】{impact_text}\n\n"
        f"【下一步核对】{next_check}\n\n"
        f"【数据与边界】{hyperliquid_time_note}\n\n"
        f"【当前边界】不自动下单、不自动调仓。"
    )
    evidence_time = str(
        discovery_after.get("formed_at_beijing")
        or hyper_after.get("formed_at_beijing")
        or discovery_after.get("fact_time_beijing")
        or hyper_after.get("fact_time_beijing")
        or ""
    )
    hyperliquid_contribution = build_contribution(context={"latest": hyper_after, "observations": hyper_after.get("observations") or []}, task="PULSE_NOTIFICATION")
    return {
        "event_type": "PULSE_FACT_UPDATE",
        "source": "dual_engine_pulse",
        "key": f"pulse:dual_engine:{discovery_after.get('identity')}:{hyper_after.get('identity')}",
        "title": title,
        "content": content,
        "market": "A_SHARE_ETF",
        "evidence_time_beijing": evidence_time,
        "before_fact": "Discovery与Hyperliquid已有可比前序事实",
        "after_fact": "Discovery与Hyperliquid本次窗口事实已完成ETF级合并",
        "confirmation_context": {
            "discovery_changed": discovery_labels,
            "hyperliquid_changed_assets": sorted(changed_assets),
            "affected_etfs": all_labels,
            "bindings": bindings,
            "discovery_identity": discovery_after.get("identity"),
            "hyperliquid_identity": hyper_after.get("identity"),
            "evidence_time_beijing": evidence_time,
            "hyperliquid_observation_time_beijing": hyperliquid_time,
            "before_fact": "Discovery与Hyperliquid已有可比前序事实",
            "after_fact": "Discovery与Hyperliquid本次窗口事实已完成ETF级合并",
            "discovery_state_transitions": {
                _pulse_candidate_identity(row): discovery_transitions.get(_pulse_candidate_identity(row), "CHANGED")
                for row in discovery_changed
            },
            "continuous_state_contract": "OBJECT_LEVEL_PREVIOUS_CURRENT_TRANSITION",
            "hyperliquid_contribution": hyperliquid_contribution,
        },
        "hyperliquid_contribution": hyperliquid_contribution,
        "delivery_mode": "COMPACT",
    }


def _pulse_candidates_from_payloads(payloads: dict[str, dict]) -> list[dict]:
    """Return at most one integrated user event for the shared pulse window."""
    def pulse_time(item: dict) -> datetime | None:
        value = item.get("formed_at_beijing") or item.get("fact_time_beijing")
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def latest_window(payload: dict) -> tuple[dict, dict]:
        history = []
        days = payload.get("days") if isinstance(payload, dict) else None
        if isinstance(days, dict):
            for market_date, entries in days.items():
                if isinstance(entries, list):
                    history.extend(
                        ({**x, "_pulse_market_date": str(market_date)} for x in entries if isinstance(x, dict))
                    )
        latest = payload.get("latest") if isinstance(payload, dict) else None
        if isinstance(latest, dict) and latest.get("identity"):
            latest_day = str(payload.get("latest_market_date") or "").strip()
            history.append({**latest, "_pulse_market_date": latest_day})
        unique = {}
        for item in history:
            identity = str(item.get("identity") or "").strip()
            if identity:
                unique[identity] = item
        ordered_all = sorted(unique.values(), key=lambda x: str(x.get("formed_at_beijing") or ""))
        if not ordered_all:
            return ({}, {})
        # Continuous pulses are comparable within one market date only.  If
        # today's index has just one record, do not pair it with yesterday's
        # final pulse and manufacture a cross-session change.
        latest_day = str(ordered_all[-1].get("_pulse_market_date") or "").strip()
        ordered = [
            item for item in ordered_all
            if str(item.get("_pulse_market_date") or "").strip() == latest_day
        ] if latest_day else ordered_all
        if len(ordered) < 2:
            return ({}, {})
        # Compare the latest pulse with a bounded rolling baseline instead of
        # only the immediately preceding pulse.  This captures persistent
        # strengthening/weakening and reversal after a noisy intermediate tick.
        baseline = ordered[max(0, len(ordered) - 4)]
        latest = ordered[-1]
        return (baseline, latest)
    discovery_before, discovery_after = latest_window(payloads.get("discovery_pulse") or {})
    hyper_before, hyper_after = latest_window(payloads.get("hyperliquid_pulse") or {})
    # Use the latest comparable same-day fact from each engine. A runner delay
    # or one missed pulse must not erase a valid same-ETF combination. Exact
    # ETF overlap remains enforced below.
    discovery_time = pulse_time(discovery_after)
    hyper_time = pulse_time(hyper_after)
    if not discovery_time or not hyper_time or discovery_time.date() != hyper_time.date():
        return []
    event = _integrated_pulse_event(
        discovery_before, discovery_after, hyper_before, hyper_after
    )
    return [event] if event else []

def _pulse_candidates() -> list[dict]:
    """Build comparable Discovery/Hyperliquid pulse events from canonical indexes."""
    from notification_center import ROOT
    from pulse_history import read_pulse_projection

    return _pulse_candidates_from_payloads({
        "discovery_pulse": read_pulse_projection(ROOT, "DISCOVERY"),
        "hyperliquid_pulse": read_pulse_projection(ROOT, "HYPERLIQUID"),
    })


def _annotate_market_continuous_state(event: dict) -> dict:
    """Apply the shared previous/current transition at the candidate boundary."""
    if not event:
        return event
    from continuous_pulse_state import transition
    from notification_center import ROOT, read_json

    event = dict(event)
    context = dict(event.get("confirmation_context") or {})
    event_type = str(event.get("event_type") or event.get("type") or "")
    market_date = str(context.get("market_date") or context.get("us_market_date") or event.get("market_date") or "")
    session = str(context.get("session_node") or context.get("session") or context.get("market_phase") or "")
    object_codes = set(str(x) for x in (context.get("object_codes") or []) if str(x))
    code = str(event.get("security_code") or context.get("security_code") or "")
    if code:
        object_codes.add(code)
    state = read_json(ROOT / "data" / "state" / "notification_center.json", {})
    prior = None
    for item in reversed(state.get("notifications") or []):
        if str(item.get("event_type") or "") != event_type:
            continue
        old = item.get("confirmation_context") or {}
        old_date = str(old.get("market_date") or old.get("us_market_date") or "")
        old_session = str(old.get("session_node") or old.get("session") or old.get("market_phase") or "")
        old_codes = set(str(x) for x in (old.get("object_codes") or []) if str(x))
        old_code = str(item.get("security_code") or old.get("security_code") or "")
        if old_code:
            old_codes.add(old_code)
        if market_date and old_date != market_date:
            continue
        if session and old_session and old_session != session:
            continue
        if object_codes and old_codes and not (object_codes & old_codes):
            continue
        if item.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED"}:
            prior = item
            break

    magnitude_keys = ("lead_change_pct", "day_change_pct", "phase_metric_change_pct", "hstech_change_pct", "tech_change_pct", "semi_change_pct")
    def magnitude(ctx: dict) -> float | None:
        values = []
        for key in magnitude_keys:
            try:
                value = float(ctx.get(key))
            except (TypeError, ValueError):
                continue
            values.append(abs(value))
        return max(values) if values else None

    if prior is None:
        state_name = "INITIAL"
    else:
        old_ctx = prior.get("confirmation_context") or {}
        state_name = transition(
            {"direction": old_ctx.get("direction"), "day_change_pct": magnitude(old_ctx)},
            {"direction": context.get("direction"), "day_change_pct": magnitude(context)},
        )
    context["continuous_state_transition"] = state_name
    context["continuous_state_contract"] = "OBJECT_LEVEL_PREVIOUS_CURRENT_TRANSITION"
    if prior is not None:
        context["previous_notification_id"] = prior.get("notification_id")
    event["confirmation_context"] = context
    return event


def _pulse_test_candidates() -> list[dict]:
    """Build synthetic pulses without reading or writing production pulse indexes.

    Each manual workflow run gets a unique synthetic identity so the test entry
    can exercise the same send path repeatedly without weakening production
    idempotency for real pulse facts.
    """
    run_id = str(os.environ.get("PULSE_TEST_RUN_ID") or "manual").strip()
    suffix = f"_{run_id}"
    base = {
        "status": "READY",
        "coverage_status": "COMPLETE",
        "candidate_count": 2,
        "observation_count": 1,
        "fact_time_beijing": "2026-11-06T15:20:00+08:00",
    }
    return _pulse_candidates_from_payloads({
        "discovery_pulse": {
            "latest": {**base, "identity": f"PULSE_TEST_DISCOVERY_AFTER{suffix}", "formed_at_beijing": "2026-11-06T15:20:00+08:00",
                       "candidate_count": 4, "candidates": [{"code": "513630", "name": "港股低波红利ETF摩根", "candidate_signature": "513630::OBSERVATION_FULL_EVALUATION"},
                                                            {"code": "159567", "name": "港股创新药ETF银华", "candidate_signature": "159567::OBSERVATION_FULL_EVALUATION"},
                                                            {"code": "159992", "name": "创新药ETF银华", "candidate_signature": "159992::OBSERVATION_FULL_EVALUATION",
                                                             "information_classes": ["PERSISTENT_TREND", "TREND_CHANGE", "NEW_RELATIVE_DIVERGENCE", "STRUCTURAL_CHANGE"],
                                                             "surfaced_states": [{"state": "PERSISTENT_TREND", "evidence": {"window_returns": {"5": 0.025, "20": 0.035}}}],
                                                             "discovery_spot": {"change_pct": 1.1, "amplitude_pct": 2.2, "turnover_pct": 7.4, "volume_ratio": 1.6, "amount": 45000000, "return_60d_pct": 8.4, "return_ytd_pct": 12.6, "open": 0.780, "high": 0.792, "low": 0.776, "price": 0.790},
                                                             "historical_context": {"status": "READY", "sample_count": 90}},
                                                            {"code": "520550", "name": "港股红利低波ETF招商", "candidate_signature": "520550::OBSERVATION_FULL_EVALUATION",
                                                             "surfaced_states": [{"state": "PERSISTENT_TREND", "evidence": {"window_returns": {"5": 0.03, "20": 0.04}}}],
                                                             "discovery_spot": {"change_pct": 1.2, "turnover_pct": 8.1}}]},
            "days": {"test": [{**base, "identity": f"PULSE_TEST_DISCOVERY_BEFORE{suffix}", "formed_at_beijing": "2026-11-06T15:19:00+08:00",
                               "candidates": [{"code": "513630", "name": "港股低波红利ETF摩根", "candidate_signature": "513630::OBSERVATION_FULL_EVALUATION"},
                                              {"code": "159567", "name": "港股创新药ETF银华", "candidate_signature": "159567::OBSERVATION_FULL_EVALUATION"}]}]},
        },
        "hyperliquid_pulse": {
            "latest": {**base, "identity": f"PULSE_TEST_HYPERLIQUID_AFTER{suffix}", "formed_at_beijing": "2026-11-06T15:20:30+08:00",
                       "candidate_count": 2, "observations": [{"asset": "xyz:BABA", "direction": "negative", "funding": "0.030%", "open_interest": "120", "premium": "0.40%"},
                                                              {"asset": "xyz:XBI", "direction": "positive", "funding": "-0.020%", "open_interest": "80", "premium": "-0.15%"}]},
            "days": {"test": [{**base, "identity": f"PULSE_TEST_HYPERLIQUID_BEFORE{suffix}", "formed_at_beijing": "2026-11-06T15:19:30+08:00",
                               "observations": [{"asset": "xyz:BABA", "direction": "negative", "funding": "0.010%", "open_interest": "100", "premium": "0.10%"},
                                                  {"asset": "xyz:XBI", "direction": "positive", "funding": "-0.010%", "open_interest": "70", "premium": "-0.10%"}]}]},
        },
    })




def _collect_candidates(market: str) -> list[dict]:
    if market == "pulse":
        return _pulse_candidates()
    if market == "pulse-test":
        return _pulse_test_candidates()
    if market in {"a-share", "apac"}:
        import send_regional_session_summary as regional
        import send_market_shock_notification as shock
        return [_annotate_market_continuous_state(event) for event in (
            regional._a_share_event() if market == "a-share" else regional._apac_event(),
            shock._legacy._a_share_candidate() if market == "a-share" else shock._legacy._context_candidate("asia"),
        ) if event]
    import send_us_session_summary as summary
    import send_market_shock_notification as shock
    return [_annotate_market_continuous_state(event) for event in (summary.build_event(), shock._legacy._context_candidate("us")) if event]


def _run_batch(market: str) -> int:
    import market_notification_common as common
    events = common.aggregate_candidate_events(_collect_candidates(market))
    if not events:
        print(json.dumps({"status": "NO_NOTIFICATION_NEEDED"}, ensure_ascii=False))
        return 0
    results = []
    for event in events:
        error = notification_evidence_error(event)
        if error:
            results.append({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error})
            continue
        results.append(common.persist_and_send(event, policy="canonical bounded market notification batch"))
    print(json.dumps({"status": "BATCHED", "results": results}, ensure_ascii=False))
    # A batch is a delivery boundary, not merely an event-construction step.
    # Never report success when any candidate reached the sender but failed to
    # persist or deliver.  Previously the workflow could finish green while
    # PushPlus was unavailable because nested FAILED results were only printed.
    failed_statuses = {
        "FAILED",
        "SEND_FAILED",
        "PERSIST_FAILED",
        "DELIVERY_FAILED",
    }
    if any(str(result.get("status") or "").upper() in failed_statuses for result in results):
        return 1
    return 0


def _latest_failed_report_response(center, event: dict) -> dict:
    source = str(event.get("source_event_id") or event.get("key") or "")
    if not source:
        return {}
    state = center.read_json(center.STATE / "notification_center.json", {})
    candidates = [
        item for item in (state.get("notifications") or [])
        if str(item.get("source_event_id") or "") == source
        and str(item.get("event_type") or "") == "REPORT_DELIVERY_REQUEST"
        and str(item.get("lifecycle_status") or "").upper() == "FAILED"
    ]
    if not candidates:
        return {}
    candidates.sort(key=lambda item: str(item.get("last_attempted_at") or item.get("created_at") or ""))
    return dict(candidates[-1].get("response") or {})


def _report_retryable_transport_failure(center, event: dict) -> bool:
    response = _latest_failed_report_response(center, event)
    error = str(response.get("error") or "")
    message = str(response.get("message") or "")
    lowered = message.lower()
    if error == "URLError" and "handshake operation timed out" in lowered:
        return True
    if error == "HTTPError" and any(f"http error {status}:" in lowered for status in (502, 503, 504)):
        return True
    return False


def _run_center(mode: str, formal_content_file: str | None = None) -> int:
    import notification_center as center

    event = None
    if mode != "channel-test":
        event = center.choose_event(mode)
        error = notification_evidence_error(event)
        if error:
            print(json.dumps({"status": "REJECTED_NO_COMPARABLE_EVIDENCE", "detail": error, "title": (event or {}).get("title")}, ensure_ascii=False))
            return 0
        original_choose = center.choose_event
        center.choose_event = lambda requested: event if requested == mode else original_choose(requested)
    sys.argv = ["notification_center.py", "--mode", mode]
    result = center.main()
    if (
        result == 0
        or mode != "event"
        or not event
        or str(event.get("event_type") or "") != "REPORT_DELIVERY_REQUEST"
    ):
        return result

    for retry_number, delay in enumerate(REPORT_RETRY_DELAYS_SECONDS, start=1):
        if not _report_retryable_transport_failure(center, event):
            break
        print(json.dumps({
            "status": "RETRYING_REPORT_DELIVERY",
            "retry_number": retry_number,
            "delay_seconds": delay,
            "source_event_id": event.get("source_event_id") or event.get("key"),
        }, ensure_ascii=False))
        time.sleep(delay)
        result = center.main()
        if result == 0:
            break
    return result


def _run_regional(market: str) -> int:
    import market_notification_common as common
    import send_regional_session_summary as producer

    producer.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_regional_session_summary.py", "--market", market]
    return producer.main()


def _run_shock(market: str) -> int:
    import market_notification_common as common
    import send_market_shock_notification as producer

    producer._legacy.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_market_shock_notification.py", "--market", market]
    return producer.main()


def _run_us() -> int:
    import market_notification_common as common
    import send_us_session_summary as producer

    producer.persist_and_send = _guarded_persist(common.persist_and_send)
    sys.argv = ["send_us_session_summary.py"]
    return producer.main()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("family", choices=["center", "regional", "shock", "us", "batch"])
    parser.add_argument("--mode", choices=["event", "close", "close-test", "channel-test"], default="event")
    parser.add_argument("--formal-content-file")
    parser.add_argument("--market", choices=["a-share", "apac", "asia", "us", "pulse", "pulse-test"])
    args = parser.parse_args()

    if args.family == "center":
        return _run_center(args.mode, args.formal_content_file)
    if args.family == "batch":
        if args.market not in {"a-share", "apac", "us", "pulse", "pulse-test"}:
            parser.error("batch requires --market a-share|apac|us|pulse")
        return _run_batch(args.market)
    if args.family == "regional":
        if args.market not in {"a-share", "apac"}:
            parser.error("regional requires --market a-share|apac")
        return _run_regional(args.market)
    if args.family == "shock":
        if args.market not in {"a-share", "asia", "us"}:
            parser.error("shock requires --market a-share|asia|us")
        return _run_shock(args.market)
    return _run_us()


if __name__ == "__main__":
    raise SystemExit(main())



