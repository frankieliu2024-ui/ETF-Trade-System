from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from notification_materiality_guard import notification_evidence_error


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
            discovery = read_json(ROOT / "data" / "state" / "discovery_pulse_index.json", {})
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
    return (
        _pulse_candidate_identity(item),
        str(item.get("object_role") or item.get("role") or "").strip().upper(),
        str(item.get("state") or item.get("status") or item.get("lifecycle") or "").strip().upper(),
        states,
        spot_classes,
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
    """Translate HL field changes into direct decision-bearing language without metrics."""
    old = {str(k): v for k, v in (before or {}).items()}
    new = {str(k): v for k, v in (after or {}).items()}
    display = asset.split(":", 1)[-1]
    parts = []

    old_funding, new_funding = _number(old.get("funding")), _number(new.get("funding"))
    if old_funding is not None and new_funding is not None and old_funding * new_funding < 0:
        parts.append("多空拥挤方向发生反转")
    elif old.get("funding") != new.get("funding") and new_funding is not None:
        parts.append("多空拥挤程度发生变化")

    old_oi, new_oi = _number(old.get("open_interest")), _number(new.get("open_interest"))
    if old_oi is not None and new_oi is not None and abs(old_oi) > 0:
        ratio = (new_oi - old_oi) / abs(old_oi)
        if ratio >= 0.05:
            parts.append("潜在波动风险上升")
        elif ratio <= -0.05:
            parts.append("潜在波动压力缓和")
    elif old.get("open_interest") != new.get("open_interest"):
        parts.append("潜在波动风险发生变化")

    old_premium, new_premium = _number(old.get("premium")), _number(new.get("premium"))
    if old_premium is not None and new_premium is not None:
        if abs(new_premium) > abs(old_premium) and abs(new_premium - old_premium) >= 0.001:
            parts.append("合约偏离参考价格加大，市场情绪更容易出现过热或过冷")
        elif abs(new_premium) < abs(old_premium) and abs(new_premium - old_premium) >= 0.001:
            parts.append("合约偏离参考价格收窄，市场情绪趋于稳定")
    elif old.get("premium") != new.get("premium"):
        parts.append("合约相对参考价格的偏离发生变化")

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
        return f"对ETF系统的具体价值是：把{changed}这类候选变化提前交给正式决策，与现金、现有持仓和观察ETF比较；它改变的是下一节点的核对重点，不等于已经出现买入机会。"
    return f"对ETF系统的具体价值是：为{objects}提供A股交易时段之外的方向、时机或拥挤风险线索；下一次正式判断要先核对这些ETF自身是否同步，再决定维持观察、继续持有或调整机会排序，不把外部资产直接当成A股ETF。"



def _discovery_reason(item: dict) -> str:
    """Translate Discovery evidence into a concise user-facing reason."""
    states = {
        str(x.get("state") or "").strip().upper()
        for x in (item.get("surfaced_states") or [])
        if isinstance(x, dict)
    }
    spot = item.get("discovery_spot") or {}
    has_trend = bool(states & {"PERSISTENT_TREND", "TREND", "MOMENTUM"})
    has_node = isinstance(spot, dict) and any(
        spot.get(key) not in (None, "") for key in ("change_pct", "turnover_pct")
    )
    role = str(item.get("object_role") or item.get("role") or "").strip()
    state = str(item.get("state") or item.get("status") or item.get("lifecycle") or "").strip()
    returns = ((spot.get("window_returns") if isinstance(spot, dict) else None) or
               ((item.get("surfaced_states") or [{}])[0].get("evidence") or {}).get("window_returns") or {})
    def pct_text(value):
        try:
            return f"{float(value) * 100:+.1f}%"
        except (TypeError, ValueError):
            return "数据缺失"
    trend_detail = []
    if returns.get("5") is not None:
        trend_detail.append(f"5日{pct_text(returns.get('5'))}")
    if returns.get("20") is not None:
        trend_detail.append(f"20日{pct_text(returns.get('20'))}")
    node_detail = []
    if isinstance(spot, dict) and spot.get("change_pct") not in (None, ""):
        node_detail.append(f"节点涨跌{float(spot['change_pct']):+.1f}%")
    if isinstance(spot, dict) and spot.get("turnover_pct") not in (None, ""):
        node_detail.append(f"成交承接{float(spot['turnover_pct']):+.1f}%")
    if has_trend and has_node:
        return ("、".join(trend_detail + node_detail)
                + "；5日和20日均为正，说明不是单日波动，且本节点上涨得到成交承接配合")
    if has_trend:
        return "、".join(trend_detail) + "，中短期趋势出现持续性变化"
    if has_node:
        return "、".join(node_detail) + "，节点表现与成交承接出现同步变化"
    if role or state:
        return "ETF在Discovery中的评估角色或状态发生变化"
    return "ETF进入了新的Discovery评估范围"


def _hyperliquid_user_change(asset: str, before: dict, after: dict) -> str:
    """State the economic direction of a HL change without raw metric labels."""
    parts = []
    old_funding, new_funding = _number(before.get("funding")), _number(after.get("funding"))
    if old_funding is not None and new_funding is not None:
        if new_funding < old_funding and new_funding < 0:
            parts.append("看空一侧拥挤加深")
        elif new_funding > old_funding and new_funding > 0:
            parts.append("看多一侧拥挤加深")
        elif old_funding * new_funding < 0:
            parts.append("多空拥挤方向发生反转")
        else:
            parts.append("多空拥挤程度发生变化")
    old_oi, new_oi = _number(before.get("open_interest")), _number(after.get("open_interest"))
    if old_oi and new_oi and (new_oi - old_oi) / abs(old_oi) >= 0.05:
        parts.append("参与资金增加，潜在波动风险上升")
    elif old_oi and new_oi and (new_oi - old_oi) / abs(old_oi) <= -0.05:
        parts.append("参与资金减少，潜在波动压力缓和")
    old_premium, new_premium = _number(before.get("premium")), _number(after.get("premium"))
    if old_premium is not None and new_premium is not None and abs(new_premium) > abs(old_premium):
        parts.append("价格偏离扩大，情绪风险上升")
    elif old_premium is not None and new_premium is not None and abs(new_premium) < abs(old_premium):
        parts.append("价格偏离收窄，情绪趋于稳定")
    return "；".join(dict.fromkeys(parts)) or "外部方向和参与程度发生了足以影响判断的变化"


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
    """Require a meaningful decision-bearing change, not machine jitter."""
    old = _hyperliquid_signal_fingerprint(before)
    new = _hyperliquid_signal_fingerprint(after)
    for asset in set(old) | set(new):
        before_item, after_item = old.get(asset, {}), new.get(asset, {})
        funding_old = _number_value(before_item.get("funding"))
        funding_new = _number_value(after_item.get("funding"))
        if funding_old is not None and funding_new is not None and abs(funding_new - funding_old) >= 0.00005:
            return True
        oi_old = _number_value(before_item.get("open_interest"))
        oi_new = _number_value(after_item.get("open_interest"))
        if oi_old is not None and oi_new is not None:
            base = max(abs(oi_old), 1.0)
            if abs(oi_new - oi_old) / base >= 0.05:
                return True
        premium_old = _number_value(before_item.get("premium"))
        premium_new = _number_value(after_item.get("premium"))
        if premium_old is not None and premium_new is not None and abs(premium_new - premium_old) >= 0.001:
            return True
    return False

def _pulse_user_window(source: str, after: dict) -> bool:
    """Discovery and Hyperliquid share one user-interruption window."""
    if source not in {"discovery_pulse", "hyperliquid_pulse", "dual_engine_pulse"}:
        return False
    stamp = str(after.get("formed_at_beijing") or after.get("fact_time_beijing") or "").strip()
    match = re.search(r"T(\d{2}):\d{2}", stamp)
    if not match:
        return False
    return 8 <= int(match.group(1)) < 16


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
    discovery_changed = []
    for identity in sorted(set(before_rows) | set(after_rows)):
        old = before_rows.get(identity) or {}
        new = after_rows.get(identity) or {}
        if _discovery_candidate_fingerprint(old) == _discovery_candidate_fingerprint(new):
            continue
        row = new or old
        if _discovery_candidate_has_user_value(row):
            discovery_changed.append(row)

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
    all_labels = _canonicalize_etf_labels(discovery_labels + hl_labels)
    if not all_labels:
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
    # User-visible pulses require both engines to identify the same ETF.
    # Different-object evidence remains machine/audit state and is silent.
    if not overlap:
        return None
    # The overlap is the complete user-visible scope.  Discovery and
    # Hyperliquid may contain other valid machine/audit objects, but those
    # objects are not evidence for this notification and must never leak into
    # the title or body.
    discovery_changed = [
        row for row in discovery_changed
        if (re.search(r"[（(](\d{6})[）)]", label(row))
            and re.search(r"[（(](\d{6})[）)]", label(row)).group(1) in overlap)
    ]
    hl_labels = [
        text for text in hl_labels
        if (re.search(r"[（(](\d{6})[）)]", text)
            and re.search(r"[（(](\d{6})[）)]", text).group(1) in overlap)
    ]
    bindings = {
        asset: [
            label for label in labels
            if (re.search(r"[（(](\d{6})[）)]", label)
                and re.search(r"[（(](\d{6})[）)]", label).group(1) in overlap)
        ]
        for asset, labels in bindings.items()
    }
    bindings = {asset: labels for asset, labels in bindings.items() if labels}
    primary = next(
        (text for text in all_labels if re.search(r"[（(](\d{6})[）)]", text)
         and re.search(r"[（(](\d{6})[）)]", text).group(1) in overlap),
        all_labels[0],
    )
    if discovery_changed and hl_labels and overlap:
        title = f"【市场异动】{primary}趋势改善，但海外生物科技风险上升"
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
            f"{label(row)}：{_discovery_reason(row)}"
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
            f"{asset.split(':', 1)[-1]}对应{', '.join(labels)}，代表美国生物科技与创新药方向；"
            f"{_hyperliquid_user_change(asset, before_by_asset.get(asset, {}), after_by_asset.get(asset, {}))}；"
            f"对{', '.join(labels)}的具体含义是：外部环境风险上升，不能作为该ETF上涨依据，"
            f"只有该ETF自身继续走强时，才可视为外部风险未形成压制"
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
        core_conclusion = (
            f"【核心结论】{primary}自身趋势和成交承接改善，但海外生物科技方向风险上升；"
            f"当前不能据此提高交易判断。"
        )
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

    content = (
        f"{core_conclusion}\n\n"
        f"【Discovery依据】{discovery_text}\n\n"
        f"【Hyperliquid依据】{hl_text}\n\n"
        f"【下一步核对】只核对{primary}自身走势是否继续走强并保持成交承接；若不能同步，外部信号按风险提示处理。\n\n"
        f"【当前边界】不自动下单、不自动调仓。"
    )
    evidence_time = str(
        discovery_after.get("formed_at_beijing")
        or hyper_after.get("formed_at_beijing")
        or discovery_after.get("fact_time_beijing")
        or hyper_after.get("fact_time_beijing")
        or ""
    )
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
            "before_fact": "Discovery与Hyperliquid已有可比前序事实",
            "after_fact": "Discovery与Hyperliquid本次窗口事实已完成ETF级合并",
        },
        "delivery_mode": "COMPACT",
    }


def _pulse_candidates_from_payloads(payloads: dict[str, dict]) -> list[dict]:
    """Return at most one integrated user event for the shared pulse window."""
    def latest_pair(payload: dict) -> tuple[dict, dict]:
        history = []
        days = payload.get("days") if isinstance(payload, dict) else None
        if isinstance(days, dict):
            for entries in days.values():
                if isinstance(entries, list):
                    history.extend(x for x in entries if isinstance(x, dict))
        latest = payload.get("latest") if isinstance(payload, dict) else None
        if isinstance(latest, dict) and latest.get("identity"):
            history.append(latest)
        unique = {}
        for item in history:
            identity = str(item.get("identity") or "").strip()
            if identity:
                unique[identity] = item
        ordered = sorted(unique.values(), key=lambda x: str(x.get("formed_at_beijing") or ""))
        return (ordered[-2], ordered[-1]) if len(ordered) >= 2 else ({}, {})
    discovery_before, discovery_after = latest_pair(payloads.get("discovery_pulse") or {})
    hyper_before, hyper_after = latest_pair(payloads.get("hyperliquid_pulse") or {})
    event = _integrated_pulse_event(
        discovery_before, discovery_after, hyper_before, hyper_after
    )
    return [event] if event else []

def _pulse_candidates() -> list[dict]:
    """Build comparable Discovery/Hyperliquid pulse events from canonical indexes."""
    from notification_center import ROOT, read_json

    return _pulse_candidates_from_payloads({
        "discovery_pulse": read_json(ROOT / "data" / "state" / "discovery_pulse_index.json", {}),
        "hyperliquid_pulse": read_json(ROOT / "data" / "state" / "hyperliquid_pulse_index.json", {}),
    })


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
                                                             "surfaced_states": [{"state": "PERSISTENT_TREND", "evidence": {"window_returns": {"5": 0.025, "20": 0.035}}}],
                                                             "discovery_spot": {"change_pct": 1.1, "turnover_pct": 7.4}},
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
        return [event for event in (
            regional._a_share_event() if market == "a-share" else regional._apac_event(),
            shock._legacy._a_share_candidate() if market == "a-share" else shock._legacy._context_candidate("asia"),
        ) if event]
    import send_us_session_summary as summary
    import send_market_shock_notification as shock
    return [event for event in (summary.build_event(), shock._legacy._context_candidate("us")) if event]


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
    return 1 if any(result.get("status") == "CREATED" for result in results) else 0


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

    if formal_content_file:
        print(json.dumps({
            "status": "FAILED",
            "error": "Formal Decision direct content projection is disabled; use canonical completion event",
        }, ensure_ascii=False))
        return 1

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


