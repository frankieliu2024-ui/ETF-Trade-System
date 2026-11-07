from __future__ import annotations

import argparse
import json
import os
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


def _discovery_candidates_changed(before: dict, after: dict) -> bool:
    """Ignore pulse metadata changes when the evaluated candidate set is unchanged."""
    before_ids = {
        _pulse_candidate_identity(item)
        for item in (before.get("candidates") or [])
        if isinstance(item, dict) and _pulse_candidate_identity(item)
    }
    after_ids = {
        _pulse_candidate_identity(item)
        for item in (after.get("candidates") or [])
        if isinstance(item, dict) and _pulse_candidate_identity(item)
    }
    return before_ids != after_ids


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
    """Render one decision-bearing HL change as a short Chinese sentence."""
    old = {str(k): v for k, v in (before or {}).items()}
    new = {str(k): v for k, v in (after or {}).items()}
    display = asset.split(":", 1)[-1]
    parts = []
    labels = {
        "funding": ("资金费率", "反映多空持仓成本；转正通常代表做多一方付费，转负通常代表做空一方付费"),
        "open_interest": ("未平仓量", "反映尚未平仓的合约规模；上升意味着杠杆参与和潜在波动增加"),
        "premium": ("溢价", "反映合约价格相对参考价格的偏离；扩大说明情绪或流动性偏离加大"),
    }
    for key, (label, meaning) in labels.items():
        if old.get(key) == new.get(key) or (old.get(key) in (None, "") and new.get(key) in (None, "")):
            continue
        before_text = str(old.get(key) or "缺失")
        after_text = str(new.get(key) or "缺失")
        parts.append(f"{label}{before_text}→{after_text}（{meaning}）")
    return f"{display}：" + "；".join(parts) if parts else ""


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


def _hyperliquid_signal_value(signal_row: str) -> str:
    """Translate changed fields into decision-relevant plain language."""
    meanings = []
    if "资金费率" in signal_row:
        meanings.append("多空持仓成本发生变化，提示杠杆拥挤方向需要复核")
    if "未平仓量" in signal_row:
        meanings.append("未平仓规模发生变化，提示杠杆参与和潜在波动风险需要复核")
    if "溢价" in signal_row:
        meanings.append("相对参考价格的偏离发生变化，提示情绪或流动性偏离需要复核")
    return "；".join(meanings) or "外部决策信号发生变化"


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
    bindings = []
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
                bindings.append(
                    f"{display_asset}（外部参照）→ {label}（{relation}）："
                    f"{impact}；当前价值是提高该ETF下一节点的复核优先级，"
                    "但尚不能单独证明A股方向已经改变，也不构成交易动作。"
                )
    deduped = []
    seen_codes = set()
    for label in labels:
        code = label.rsplit("（", 1)[-1].rstrip("）") if "（" in label else label
        if code in seen_codes:
            continue
        seen_codes.add(code)
        deduped.append(label)
    return deduped[:4], rows[:6], sorted(changed_assets), bindings[:8]


def _pulse_object_impact(source: str, related_text: str, added: list[str], removed: list[str]) -> str:
    objects = related_text or "相关ETF对象"
    if source == "discovery_pulse":
        changed = "、".join((added + removed)[:6]) or objects
        return f"对ETF系统的具体价值是：把{changed}这类候选变化提前交给正式决策，与现金、现有持仓和观察ETF比较；它改变的是下一节点的核对重点，不等于已经出现买入机会。"
    return f"对ETF系统的具体价值是：为{objects}提供A股交易时段之外的方向、时机或拥挤风险线索；下一次正式判断要先核对这些ETF自身是否同步，再决定维持观察、继续持有或调整机会排序，不把外部资产直接当成A股ETF。"



def _discovery_reason(item: dict) -> str:
    """Return only evidence carried by the Discovery candidate itself."""
    states = [str(x.get("state") or "") for x in (item.get("surfaced_states") or []) if isinstance(x, dict)]
    evidence = []
    for state in (item.get("surfaced_states") or []):
        if not isinstance(state, dict):
            continue
        detail = state.get("evidence") or {}
        if not isinstance(detail, dict):
            continue
        direction = str(detail.get("direction") or "").strip()
        returns = []
        # Accept both canonical return_5/return_20 fields and the
        # window_returns shape used by Discovery snapshots and fixtures.
        window_returns = detail.get("window_returns") or {}
        for key in ("return_5", "return_10", "return_20", "return_40", "return_60"):
            value = detail.get(key)
            if value is None:
                window = key.replace("return_", "")
                value = window_returns.get(window)
            if value is not None:
                try:
                    returns.append(f"{key.replace('return_', '')}日{float(value) * 100:+.1f}%")
                except (TypeError, ValueError):
                    pass
        if direction or returns:
            evidence.append("、".join(([direction] if direction else []) + returns))
    spot = item.get("discovery_spot") or {}
    if isinstance(spot, dict):
        try:
            change = float(spot.get("change_pct"))
            evidence.append(f"节点涨跌{change:+.2f}%")
        except (TypeError, ValueError):
            pass
        try:
            turnover = float(spot.get("turnover_pct"))
            if turnover > 0:
                evidence.append(f"换手率{turnover:.1f}%")
        except (TypeError, ValueError):
            pass
    parts = list(dict.fromkeys(x for x in evidence if x))
    if parts:
        return "；".join(parts[:2])
    if states:
        return "出现" + "、".join(dict.fromkeys(states)) + "信号"
    return "本次进入候选池；入选的具体强弱依据尚未完整确认"

def _discovery_candidate_has_user_value(item: dict) -> bool:
    """Require quantitative, candidate-specific evidence before interrupting."""
    reason = _discovery_reason(item)
    return "尚未完整确认" not in reason and bool(reason.strip())


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

def _pulse_event(source: str, label: str, before: dict, after: dict) -> dict | None:
    # A first Hyperliquid snapshot establishes the comparison baseline. With
    # no prior observations, every asset would otherwise look "new" and the
    # full venue catalogue would leak into a user notification.
    if source == "hyperliquid_pulse" and not (before.get("observations") or []):
        return None
    before_objects = sorted(
        _pulse_object_label(x)
        for x in (before.get("candidates") or before.get("observations") or [])
        if isinstance(x, dict)
    )
    after_objects = sorted(
        _pulse_object_label(x)
        for x in (after.get("candidates") or after.get("observations") or [])
        if isinstance(x, dict)
    )
    before_fact = {
        "identity": before.get("identity"),
        "fact_time_beijing": before.get("fact_time_beijing"),
        "status": before.get("status"),
        "coverage_status": before.get("coverage_status"),
        "candidate_count": before.get("candidate_count"),
        "observation_count": before.get("observation_count"),
        "objects": before_objects,
    }
    after_fact = {
        "identity": after.get("identity"),
        "fact_time_beijing": after.get("fact_time_beijing"),
        "status": after.get("status"),
        "coverage_status": after.get("coverage_status"),
        "candidate_count": after.get("candidate_count"),
        "observation_count": after.get("observation_count"),
        "objects": after_objects,
    }
    if source == "hyperliquid_pulse":
        before_fact["signal_fingerprint"] = _hyperliquid_signal_fingerprint(before)
        after_fact["signal_fingerprint"] = _hyperliquid_signal_fingerprint(after)
    # Identity and timestamp change on every pulse; they are audit metadata,
    # not user-visible market change. Suppress no-op pulses such as 0 -> 0.
    comparable_before = {key: value for key, value in before_fact.items() if key not in {"identity", "fact_time_beijing"}}
    comparable_after = {key: value for key, value in after_fact.items() if key not in {"identity", "fact_time_beijing"}}
    if comparable_before == comparable_after:
        return None
    if source == "discovery_pulse" and not _discovery_candidates_changed(before, after):
        return None
    added = sorted(set(after_objects) - set(before_objects))
    removed = sorted(set(before_objects) - set(after_objects))
    changed = sorted(set(before_objects) & set(after_objects))
    evidence_time = str(after.get("formed_at_beijing") or after.get("fact_time_beijing") or "")
    change_lines = []
    if added:
        change_lines.append("新增对象：" + "、".join(added))
    if removed:
        change_lines.append("消失对象：" + "、".join(removed))
    if changed:
        change_lines.append("延续对象：" + "、".join(changed))
    if not change_lines:
        change_lines.append("对象集合未变，但状态、覆盖或数量发生变化。")
    signal_changes = []
    if source == "hyperliquid_pulse":
        if not _hyperliquid_decision_signal_changed(before, after):
            return None
        if not _hyperliquid_signal_materially_changed(before, after):
            return None
        signal_changes = _hyperliquid_signal_changes(before, after)
        # The machine comparison may cover hundreds of assets, but the
        # user-facing pulse must contain only assets whose decision-bearing
        # change maps to a currently managed ETF object.
        changed_assets = {item.split("：", 1)[0] for item in signal_changes}
        relevant_objects = _hyperliquid_relevant_asset_objects()
        changed_assets &= set(relevant_objects)
        if not changed_assets:
            return None
        signal_changes = [item for item in signal_changes if item.split("：", 1)[0] in changed_assets]
        related_labels, human_signal_changes, _, object_bindings = _hyperliquid_user_scope(
            before, after, changed_assets, relevant_objects
        )
        # The notification is an ETF decision aid, not a venue catalogue.
        # Replace raw external-asset object lists with the concrete ETF objects
        # that can actually be checked at the next decision node.
        before_objects = related_labels
        after_objects = related_labels
        before_fact["objects"] = before_objects
        after_fact["objects"] = after_objects
        before_fact["observation_count"] = len(related_labels)
        after_fact["observation_count"] = len(related_labels)
        added = sorted(set(after_objects) - set(before_objects))
        removed = sorted(set(before_objects) - set(after_objects))
        changed = sorted(set(before_objects) & set(after_objects))
        # Rebuild all user-visible change lines after filtering. The previous
        # implementation appended the full venue comparison before filtering,
        # which leaked unrelated assets into the notification body.
        change_lines = []
        if added:
            change_lines.append("新增对象：" + "、".join(added))
        if removed:
            change_lines.append("消失对象：" + "、".join(removed))
        if changed:
            change_lines.append("延续对象：" + "、".join(changed))
        if human_signal_changes:
            change_lines.append("具体信号：" + "\n".join(human_signal_changes))
        if not change_lines:
            change_lines.append("对象集合未变，但相关信号发生变化。")
    if source == "discovery_pulse":
        before_ids = {_pulse_candidate_identity(x) for x in (before.get("candidates") or []) if isinstance(x, dict)}
        added_items = [
            item for item in (after.get("candidates") or [])
            if isinstance(item, dict) and _pulse_candidate_identity(item) not in before_ids
        ]
        added_items = [item for item in added_items if _discovery_candidate_has_user_value(item)]
        if not added_items:
            return None
        reasons = []
        for item in added_items[:2]:
            name = str(item.get("name") or item.get("display_name") or _pulse_candidate_identity(item))
            reasons.append(f"{name}：{_discovery_reason(item)}")
        reason_text = "；".join(reasons) if reasons else "候选集合发生变化，但具体入选理由尚未完整确认"
        headline = "、".join((added + removed)[:3]) or "候选池"
        content = (
            f"【核心结论】新增候选：{headline}；入选依据是{reason_text}。"
            "当前只提升下一次正式判断的比较优先级，不代表A股方向改变，也不是买入信号。\n\n"
            f"【发生了什么】Discovery候选池新增{headline}，其趋势、节点表现或成交证据达到入选条件。\n\n"
            "【下一步核对】核对趋势能否持续、成交是否有承接，并与现金、现有持仓和其他观察对象比较。\n\n"
            "【当前边界】不授予Trial、Confirm、买入、卖出或调仓权限，不代表已经发生实际成交。"
        )
        title = f"【市场异动】新候选需复核：{headline}"
    else:
        short_names = "、".join(label.split("（", 1)[0] for label in related_labels[:3]) or "相关ETF"
        impact_lines = []
        seen_labels = set()
        for asset in sorted(changed_assets):
            for label in (relevant_objects.get(asset) or []):
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                _, relation = _hyperliquid_mapping_metadata(asset, label)
                if relation == "直接/产业链关系":
                    impact = "直接影响对应产业方向强弱判断"
                elif relation == "间接市场/风险偏好关系":
                    impact = "影响市场风险偏好和入场时机判断"
                else:
                    impact = "影响风格、拥挤风险和入场时机判断"
                impact_lines.append(f"{label}：外部参照变化，{impact}")
        impact_text = "；".join(impact_lines[:4]) or "相关ETF对象映射尚未完整确认"
        content = (
            f"【核心结论】{short_names}需要提高下一节点复核优先级，"
            "外部信号只影响方向确认、时机或风险过滤，不代表A股方向已经改变。\n\n"
            f"【发生了什么】{impact_text}。\n\n"
            "【下一步核对】逐只核对ETF自身走势、成交承接和相对强弱；只有A股反馈同步时，"
            "外部信号才可作为支持或时机依据，若背离则仅保留为风险提示。\n\n"
            "【当前边界】外部资产不是A股ETF，不能直接等同或据此买卖；不自动交易。"
        )
        title = f"【市场异动】需复核：{short_names}"
    return {
        "key": f"pulse:{source}:{after.get('identity')}",
        "event_type": "PULSE_FACT_UPDATE",
        "source": source,
        "title": title,
        "content": content,
        "user_action": "无需自动交易；下一正式判断逐对象核对变化及其对持仓、观察、Discovery和现金竞争的影响。",
        "confirmation_context": {
            "evidence_time_beijing": evidence_time,
            "before_fact": _pulse_fact_display(before_fact),
            "after_fact": _pulse_fact_display(after_fact),
            "before_fact_machine": json.dumps(before_fact, ensure_ascii=False, sort_keys=True),
            "after_fact_machine": json.dumps(after_fact, ensure_ascii=False, sort_keys=True),
            "applicable_object": label,
        },
    }


def _pulse_candidates_from_payloads(payloads: dict[str, dict]) -> list[dict]:
    candidates = []
    for source, label in (("discovery_pulse", "Discovery"), ("hyperliquid_pulse", "Hyperliquid")):
        payload = payloads.get(source) or {}
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
        if len(ordered) >= 2:
            event = _pulse_event(source, label, ordered[-2], ordered[-1])
            if event:
                candidates.append(event)
    return candidates


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
                       "candidate_count": 3, "candidates": [{"code": "513630", "name": "港股低波红利ETF摩根", "candidate_signature": "513630::OBSERVATION_FULL_EVALUATION"},
                                                            {"code": "159567", "name": "港股创新药ETF银华", "candidate_signature": "159567::OBSERVATION_FULL_EVALUATION"},
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
                               "observations": [{"asset": "xyz:BABA", "direction": "negative", "funding": "0.010%", "open_interest": "100", "premium": "0.10%"}]}]},
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

