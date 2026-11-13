from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json


BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def _economic_etf_matches(mapping: dict) -> list[str]:
    """Resolve an industry/family mapping to the current ETF universe.

    Explicit etf_codes remain the strongest binding. When a mapping only has
    name_markers, resolve those markers against the canonical monitored ETF
    universe so an economic mapping is consumable by all downstream tasks.
    """
    scope = str(mapping.get("mapping_scope") or "CONTEXT_ONLY").strip().upper()
    if scope == "DIRECT_ETF":
        explicit = [str(x) for x in (mapping.get("etf_codes") or mapping.get("mapped_assets") or []) if str(x)]
        if explicit:
            return explicit
    elif scope == "INDUSTRY_CANDIDATE":
        validated = [str(x) for x in (mapping.get("validated_etf_codes") or []) if str(x)]
        if validated:
            return validated
    # Regional, macro and unvalidated industry relationships are evidence context only.
    return []
    try:
        universe = json.loads((ROOT / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        universe = {}
    objects = list(universe.get("objects") or [])
    try:
        discovery = json.loads((ROOT / "data/state/formal_discovery_current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        discovery = {}
    # Resolve both the persistent monitored universe and the current legal
    # Discovery candidates.  Discovery is an independent consumer class, not
    # a permission grant; excluding it here made a valid economic-family
    # signal disappear precisely when a new candidate entered the decision.
    markers = [str(x).strip() for x in (mapping.get("name_markers") or []) if str(x).strip()]
    excluded = [str(x).strip() for x in (mapping.get("exclude_name_markers") or []) if str(x).strip()]
    if scope != "INDUSTRY_CANDIDATE" or not mapping.get("validated_etf_codes"):
        return []
    if not markers:
        return []
    matches = []
    for item in objects:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "")
        if code and any(marker in name for marker in markers) and not any(marker in name for marker in excluded):
            matches.append(code)
    discovery_rows = discovery.get("discovery", {}).get("candidates", []) if isinstance(discovery.get("discovery"), dict) else []
    for item in discovery_rows:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or item.get("display_name") or "")
        if code and any(marker in name for marker in markers) and not any(marker in name for marker in excluded):
            matches.append(code)
    return sorted(set(matches))


def _normalize_observations(context: dict, observations: list) -> list[dict]:
    """Attach configured economic mappings to raw Hyperliquid observations.

    The collector returns nested ``asset.name`` objects while older discovery
    projections only carried the raw list.  Normalizing here keeps Discovery,
    Formal Decision, Trade Review and pulse notifications on the same mapping
    contract instead of allowing an old projection to become an empty packet.
    """
    configured = context.get("mapping") if isinstance(context.get("mapping"), list) and context.get("mapping") else None
    if configured is None:
        try:
            payload = json.loads((ROOT / "config/market/hyperliquid_etf_exposure_map.json").read_text(encoding="utf-8"))
            configured = payload.get("asset_mappings") or []
        except (OSError, ValueError):
            configured = []
    by_asset = {str(x.get("asset") or ""): x for x in configured if isinstance(x, dict)}
    normalized = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        raw_asset = item.get("asset")
        asset = raw_asset.get("name") if isinstance(raw_asset, dict) else raw_asset
        mapping = by_asset.get(str(asset or ""), {})
        row = dict(item)
        row["asset"] = str(asset or "")
        for key in ("family", "economic_family", "etf_codes", "mapped_assets", "mapping_reason", "name_markers", "exclude_name_markers", "aliases"):
            if mapping.get(key) is not None and row.get(key) in (None, "", []):
                row[key] = mapping.get(key)
        if not row.get("family") and row.get("economic_family"):
            row["family"] = row.get("economic_family")
        if not row.get("etf_codes") and not row.get("mapped_assets") and mapping:
            family_matches = _economic_etf_matches(mapping)
            if family_matches:
                row["mapped_assets"] = family_matches
                row["mapping_reason"] = "ECONOMIC_FAMILY_NAME_MARKER_MAPPING"
        ctx = row.get("context") if isinstance(row.get("context"), dict) else row
        row.setdefault("funding", ctx.get("funding"))
        row.setdefault("open_interest", ctx.get("openInterest"))
        row.setdefault("premium", ctx.get("premium"))
        row.setdefault("mark_px", ctx.get("markPx"))
        row.setdefault("mid_px", ctx.get("midPx"))
        row.setdefault("prev_day_px", ctx.get("prevDayPx"))
        if row.get("family") or row.get("etf_codes") or row.get("mapped_assets"):
            normalized.append(row)
    return normalized


def _mapping_candidates(configured: list[dict], observed_assets: set[str]) -> list[dict]:
    """Expose configured economic mappings even when no live row matched.

    A catalog entry is not a live signal. Keeping it explicit prevents the
    consumer from collapsing "mapping exists, live signal absent" into
    "there is no mapping", while preserving the fail-closed decision boundary.
    """
    candidates = []
    for mapping in configured:
        if not isinstance(mapping, dict):
            continue
        asset = str(mapping.get("asset") or "").strip()
        if not asset or asset in observed_assets:
            continue
        candidates.append({
            "object_type": "HYPERLIQUID_MAPPING_CANDIDATE",
            "object_id": asset,
            "asset": asset,
            "family": mapping.get("family") or "",
            "mapped_assets": list(mapping.get("etf_codes") or mapping.get("mapped_assets") or []) if str(mapping.get("mapping_scope") or "CONTEXT_ONLY").upper() == "DIRECT_ETF" else list(mapping.get("validated_etf_codes") or []),
            "name_markers": list(mapping.get("name_markers") or []),
            "mapping_reason": "CONFIGURED_ECONOMIC_FAMILY_CANDIDATE_NO_LIVE_SIGNAL",
            "evidence_status": "NO_LIVE_SIGNAL",
            "consumption_status": "CATALOG_ONLY",
            "decision_effect": "TIMING_FILTER",
            "signal_fields": {},
            "evidence_summary": "配置映射存在，但本轮快照没有匹配到该对象的实时观测信号",
            "decision_use": "保持相关ETF观察优先级，等待下一合法脉冲，不升级即时配置",
            "notification_eligibility": "NOT_ELIGIBLE_WITHOUT_LIVE_SIGNAL",
        })
    return candidates


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
    pulse_sequence = context.get("pulse_sequence") if isinstance(context.get("pulse_sequence"), list) else []
    pulse_sequence_summary = context.get("pulse_sequence_summary") if isinstance(context.get("pulse_sequence_summary"), dict) else {}
    observations = latest.get("observations") or context.get("observations") or []
    raw_observations = observations
    observations = _normalize_observations(context, raw_observations)
    mapped = [x for x in observations if isinstance(x, dict) and x.get("asset") and (x.get("family") or x.get("mapped_assets") or x.get("etf_codes"))]
    configured = context.get("mapping") if isinstance(context.get("mapping"), list) and context.get("mapping") else None
    if configured is None:
        try:
            payload = json.loads((ROOT / "config/market/hyperliquid_etf_exposure_map.json").read_text(encoding="utf-8"))
            configured = payload.get("asset_mappings") or []
        except (OSError, ValueError):
            configured = []
    observed_assets = set()
    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        raw_asset = item.get("asset")
        observed_assets.add(str(raw_asset.get("name") if isinstance(raw_asset, dict) else raw_asset or ""))
    mapping_candidates = _mapping_candidates(configured, observed_assets)

    def _number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _derived_direction(item: dict) -> tuple[str, str, str]:
        """Derive a transparent price direction when the provider omits direction."""
        explicit = str(item.get("direction") or "").strip().upper()
        if explicit:
            aliases = {
                "POSITIVE": "UP", "SUPPORTIVE": "UP", "BULLISH": "UP",
                "NEGATIVE": "DOWN", "BEARISH": "DOWN",
                "NEUTRAL": "FLAT", "UNCHANGED": "FLAT",
            }
            return aliases.get(explicit, explicit), "PROVIDER_DIRECTION", ""
        mark = _number(item.get("mark_px"))
        previous = _number(item.get("prev_day_px"))
        if mark is None or previous in (None, 0):
            return "", "UNAVAILABLE", "缺少当前价格或前日参考价格"
        change = mark / previous - 1
        if change > 0.0005:
            return "UP", "MARK_VS_PREV_DAY", f"{change:.4%}"
        if change < -0.0005:
            return "DOWN", "MARK_VS_PREV_DAY", f"{change:.4%}"
        return "FLAT", "MARK_VS_PREV_DAY", f"{change:.4%}"

    def _priority_effect(effect_name: str) -> str:
        return {
            "SUPPORT": "提高对应ETF的外部确认优先级；若A股同步上涨且承接正常，进入更高配置优先级比较",
            "CONFIRMATORY": "提高对应ETF的配置与持有优先级；外部与A股已形成相互确认",
            "CONTRADICT": "降低对应ETF的新增与持有优先级；若A股同步走弱，进入减持或释放比较",
            "CROWDING_RISK": "降低即时追涨优先级；保留方向支持，但先等待拥挤缓解或A股承接确认",
            "TIMING_FILTER": "方向支持成立但当前不直接追涨；将该ETF列入下一节点优先复核",
            "NO_DIRECT_MAPPING": "不改变该ETF排序；仅作为所属族群背景",
            "NO_INCREMENTAL_VALUE": "本次不改变该ETF排序；外部证据未增加有效边际信息",
            "INSUFFICIENT": "当前不改变该ETF排序；等待下一合法可比脉冲",
        }.get(effect_name, "当前不改变该ETF排序；等待下一合法可比脉冲")

    def _next_trigger(effect_name: str) -> str:
        return {
            "SUPPORT": "A股同步上涨且本地承接正常时，提高配置优先级",
            "CONFIRMATORY": "A股与外部方向继续同步时，维持较高配置与持有优先级",
            "CONTRADICT": "A股同步走弱或外部方向继续恶化时，提高释放优先级",
            "CROWDING_RISK": "拥挤度回落且A股承接保持时，恢复配置评估",
            "TIMING_FILTER": "下一合法脉冲确认方向延续，并核对A股承接",
            "NO_DIRECT_MAPPING": "继续使用ETF自身行情和所属族群事实判断",
            "NO_INCREMENTAL_VALUE": "继续使用ETF自身行情和资本比较判断",
            "INSUFFICIENT": "下一合法可比脉冲形成后重新比较",
        }.get(effect_name, "下一合法可比脉冲形成后重新比较")

    def effect(item: dict, direction: str) -> str:
        explicit = str(item.get("decision_effect") or "").strip().upper()
        if explicit:
            return explicit
        if item.get("crowding") or item.get("crowding_status") or "CROWDING_RISK" in str(item.get("crowding_signal") or ""):
            return "CROWDING_RISK"
        if item.get("etf_codes") or item.get("mapped_assets"):
            return "TIMING_FILTER" if direction else "INSUFFICIENT"
        if item.get("family"):
            return "NO_DIRECT_MAPPING"
        return "INSUFFICIENT"

    def signal(item: dict) -> dict:
        direction, direction_source, price_change_pct = _derived_direction(item)
        return {
            "direction": direction,
            "direction_source": direction_source,
            "price_change_pct": price_change_pct,
            "funding": item.get("funding") or item.get("funding_rate") or "",
            "open_interest": item.get("open_interest") or item.get("openInterest") or "",
            "premium": item.get("premium") or "",
            "crowding": item.get("crowding") or item.get("crowding_status") or "",
            "mark_px": item.get("mark_px") or "",
            "mid_px": item.get("mid_px") or "",
            "prev_day_px": item.get("prev_day_px") or "",
            "previous_pulse_as_of": item.get("previous_pulse_as_of") or None,
            "pulse_comparison_status": item.get("pulse_comparison_status") or "NOT_COMPARABLE",
            "comparison_reason": (item.get("comparison_reason") or "") if item.get("pulse_comparison_status") == "COMPARABLE" else (item.get("comparison_reason") or "未提供可验证的上一合法脉冲及原因"),
            "comparison_age_seconds": item.get("comparison_age_seconds"),
            "comparison_horizon": item.get("comparison_horizon") or "UNKNOWN",
            "pulse_delta": item.get("pulse_delta") or {},
            "pulse_delta_pct": item.get("pulse_delta_pct") or {},
            "positioning_signal": item.get("positioning_signal") or "UNDETERMINED",
            "funding_signal": item.get("funding_signal") or ("UNDETERMINED:缺少资金费率" if item.get("funding") in (None, "") else "AVAILABLE"),
            "pricing_signal": item.get("pricing_signal") or ("UNDETERMINED:缺少溢价/折价" if item.get("premium") in (None, "") else "AVAILABLE"),
            "crowding_signal": item.get("crowding_signal") or ("UNDETERMINED:" + (item.get("comparison_reason") or "缺少可比基准") if item.get("pulse_comparison_status") != "COMPARABLE" else "NO_CLEAR_CROWDING"),
        }

    mapped_objects = []
    for item in mapped:
        assets = list(item.get("etf_codes") or item.get("mapped_assets") or [])
        current_signal = signal(item)
        current_effect = effect(item, current_signal.get("direction"))
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
            "observation_as_of": (
                item.get("observation_as_of")
                or item.get("collected_at_beijing")
                or as_of
            ),
            "evidence_status": item.get("evidence_status") or "READY",
            "consumption_status": item.get("consumption_status") or (
                "CONSUMED" if assets or item.get("family") else "CONSUMED_AS_LIMITATION"
            ),
            "direction_signal": current_signal.get("direction") or "UNDETERMINED",
            "direction_signal_reason": current_signal.get("direction_source") or "无法判断：缺少当前价格或前日参考价格",
            "positioning_signal": current_signal.get("positioning_signal") or "UNDETERMINED",
            "pricing_signal": current_signal.get("pricing_signal") or "UNDETERMINED",
            "crowding_signal": current_signal.get("crowding_signal") or "UNDETERMINED",
            "cross_market_relation": item.get("cross_market_relation") or "UNDETERMINED：缺少A股同步事实",
            "decision_effect": current_effect,
            "priority_effect": _priority_effect(current_effect),
            "next_trigger": _next_trigger(current_effect),
            "signal_fields": current_signal,
            "evidence_summary": item.get("evidence_summary") or current_signal,
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

    # Materialize one consumer result per A-share ETF binding. Consumers
    # read this ledger; they do not reconstruct object-to-ETF conclusions.
    etf_contributions = []
    seen_bindings = set()
    for obj in mapped_objects:
        direction = (obj.get("signal_fields") or {}).get("direction")
        relation = "UNDETERMINED"
        if direction in {"UP", "DOWN", "FLAT"}:
            relation = "EXTERNAL_DIRECTION_AVAILABLE; A_SHARE_TRANSMISSION_REQUIRES_A_SHARE_FACT"
        for code in obj.get("mapped_assets") or []:
            code = str(code)
            if not code or code in seen_bindings:
                continue
            seen_bindings.add(code)
            etf_contributions.append({
                "ashare_code": code,
                "hyperliquid_object_ids": [obj.get("object_id")],
                "mapping_status": "MAPPED",
                "cross_market_relation": relation,
                "decision_effect": obj.get("decision_effect") or "TIMING_FILTER",
                "priority_effect": obj.get("priority_effect") or _priority_effect(str(obj.get("decision_effect") or "INSUFFICIENT")),
                "next_trigger": obj.get("next_trigger") or _next_trigger(str(obj.get("decision_effect") or "INSUFFICIENT")),
                "decision_use": "用于外部确认、背离识别和时机/拥挤过滤；不得单独生成交易权限、金额或订单",
                "source_context_id": context.get("context_id") or latest.get("context_id") or "",
                "source_observation_as_of": obj.get("observation_as_of") or None,
            })
    packet = {
        "schema_version": "2.1",
        "status": "READY" if mapped else ("DEGRADED" if mapping_candidates else "MISSING"),
        "quality_status": str(latest.get("quality_status") or context.get("quality_status") or latest.get("status") or "UNKNOWN"),
        "task": str(task or ""),
        "node": str(node or ""),
        "phase": phase,
        "as_of_beijing": as_of,
        "pulse_sequence": pulse_sequence,
        "pulse_sequence_summary": pulse_sequence_summary,
        "mapped_object_count": len(mapped_objects),
        "mapped_objects": mapped_objects,
        "etf_contribution_count": len(etf_contributions),
        "etf_contributions": etf_contributions,
        "live_signal_count": len(mapped_objects),
        "mapping_candidate_count": len(mapping_candidates),
        "mapping_candidates": mapping_candidates,
        "mapping_coverage_status": (
            "LIVE_SIGNAL_MATCHED" if mapped_objects else
            "CONFIGURED_CANDIDATES_NO_LIVE_SIGNAL" if mapping_candidates else
            "NO_CONFIGURED_MAPPING"
        ),
        "contribution_summary": (
            "本轮存在可检验的Hyperliquid经济族群映射，但没有匹配到可消费的实时信号；不能把映射候选当作方向证据。"
            if mapping_candidates and not mapped_objects else
            "本轮已匹配Hyperliquid实时对象信号，可按对象消费。"
            if mapped_objects else
            "本轮没有可用的Hyperliquid映射或实时对象信号。"
        ),
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
    try:
        from scripts.validate_hyperliquid_consumer_output import validate_contribution_packet
    except ModuleNotFoundError:
        # Direct script execution puts scripts/ on sys.path, not the repository root.
        from validate_hyperliquid_consumer_output import validate_contribution_packet
    packet["validation"] = validate_contribution_packet(packet)
    return packet
