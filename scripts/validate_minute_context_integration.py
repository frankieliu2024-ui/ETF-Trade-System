from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import now_utc
except ModuleNotFoundError:
    from scripts.state_manager import now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
POC_PATH = Path("data/state/minute_index_acceptance_poc.json")
REGIME_PATH = Path("data/state/market_regime_context.json")
STRUCTURE_PATH = Path("data/state/market_structure_context.json")
OUT_PATH = Path("data/state/minute_context_integration_validation.json")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round4(value):
    return None if value is None else round(float(value), 4)


def index_validation(poc: dict, regime: dict) -> dict:
    baseline = {str(x.get("code")): x for x in (regime.get("indices") or []) if x.get("code")}
    items = []
    for minute in ((poc.get("index_minute_poc") or {}).get("items") or []):
        code = str(minute.get("symbol") or "")
        base = baseline.get(code) or {}
        continuity = bool((minute.get("sampling") or {}).get("continuity_pass"))
        pit = bool((minute.get("point_in_time") or {}).get("pass"))
        alignment = minute.get("quote_alignment") or {}
        price_match = f(alignment.get("abs_diff_pct"))
        interface_pass = bool(
            minute.get("status") == "READY"
            and continuity
            and pit
            and price_match is not None
            and price_match <= 0.25
        )
        intraday_gate_pass = bool(minute.get("item_quality_pass") is True)
        baseline_coverage = base.get("sampling_coverage")
        minute_samples = int(minute.get("sample_count") or 0)
        incremental = []
        if minute_samples >= 200 and baseline_coverage != "HIGH":
            incremental.append("MINUTE_LEVEL_PATH_DENSITY")
        if minute.get("path_low_as_of_beijing") and minute.get("path_high_as_of_beijing"):
            incremental.append("EXACT_EXTREME_TIMING")
        if f(minute.get("recent_slope_pct_per_10m")) is not None:
            incremental.append("EXACT_RECENT_10M_SLOPE")
        base_path = f(base.get("path_change_pct"))
        minute_path = f(minute.get("path_change_pct"))
        items.append({
            "code": code,
            "display_name": minute.get("name"),
            "interface_data_pass": interface_pass,
            "intraday_quality_gate_pass": intraday_gate_pass,
            "intraday_gate_note": (
                "PASS，可进入盘中接入验证" if intraday_gate_pass
                else "本次未通过盘中完整时点门槛；不等同于接口或价格失败"
            ),
            "baseline_sampling_coverage": baseline_coverage,
            "minute_sample_count": minute_samples,
            "baseline_path_change_pct": round4(base_path),
            "minute_path_change_pct": round4(minute_path),
            "path_change_delta_pct_points": round4(minute_path - base_path) if minute_path is not None and base_path is not None else None,
            "minute_path_low_as_of_beijing": minute.get("path_low_as_of_beijing"),
            "minute_path_high_as_of_beijing": minute.get("path_high_as_of_beijing"),
            "minute_recent_slope_pct_per_10m": minute.get("recent_slope_pct_per_10m"),
            "quote_alignment": alignment,
            "incremental_evidence": incremental,
            "proposed_integration_role": "SUPPLEMENTAL_INDEX_PATH_VALIDATION_ONLY",
        })
    return {
        "target_count": len(items),
        "interface_data_pass_count": sum(1 for x in items if x.get("interface_data_pass")),
        "intraday_quality_pass_count": sum(1 for x in items if x.get("intraday_quality_gate_pass")),
        "all_interface_data_pass": bool(items) and all(x.get("interface_data_pass") for x in items),
        "all_intraday_quality_pass": bool(items) and all(x.get("intraday_quality_gate_pass") for x in items),
        "items": items,
        "promotion_rule": "只有真实盘中节点同时满足PIT、连续性、freshness和同源quote时点/价格门槛，且相对离散指数路径有稳定增量解释价值，才考虑进入market_regime_context正式路径字段。",
    }


def acceptance_validation(poc: dict, structure: dict) -> dict:
    baselines = {str(x.get("code")): x for x in (structure.get("items") or []) if x.get("code")}
    items = []
    for row in ((poc.get("ten_minute_acceptance_poc") or {}).get("items") or []):
        code = str(row.get("symbol") or "")
        evidence = row.get("evidence") or {}
        base = baselines.get(code) or {}
        turnover = base.get("turnover_acceptance_context") or {}
        comparison = evidence.get("comparison") or {}
        recent = evidence.get("recent_10m") or {}
        ready = evidence.get("status") == "READY"
        amount_relation = comparison.get("amount_relation")
        price_direction = comparison.get("recent_price_direction")
        pace_state = turnover.get("pace_state")
        if not ready:
            relation = "INSUFFICIENT"
        elif pace_state == "ABOVE_NORMAL_PACE" and amount_relation == "EXPANDED_VS_PRIOR_10M":
            relation = "BROAD_AND_RECENT_ACTIVITY_BOTH_ELEVATED"
        elif pace_state == "BELOW_NORMAL_PACE" and amount_relation == "CONTRACTED_VS_PRIOR_10M":
            relation = "BROAD_AND_RECENT_ACTIVITY_BOTH_SUBDUED"
        else:
            relation = "DIFFERENT_HORIZON_INFORMATION"
        items.append({
            "code": code,
            "display_name": row.get("name"),
            "ready": ready,
            "baseline_acceptance_behavior": turnover.get("acceptance_behavior"),
            "baseline_time_normalized_amount_pace_ratio": turnover.get("time_normalized_amount_pace_ratio"),
            "baseline_pace_state": pace_state,
            "recent_10m_price_change_pct": recent.get("price_change_pct"),
            "recent_10m_amount_delta": recent.get("amount_delta"),
            "recent_10m_volume_delta": recent.get("volume_delta"),
            "amount_expansion_ratio_vs_prior_10m": comparison.get("amount_expansion_ratio"),
            "volume_expansion_ratio_vs_prior_10m": comparison.get("volume_expansion_ratio"),
            "amount_relation": amount_relation,
            "recent_price_direction": price_direction,
            "baseline_vs_recent_relation": relation,
            "incremental_evidence": [
                "RECENT_10M_PRICE_DIRECTION",
                "RECENT_VS_PRIOR_10M_AMOUNT_EXPANSION",
                "RECENT_VS_PRIOR_10M_VOLUME_EXPANSION",
            ] if ready else [],
            "proposed_integration_role": "SUPPLEMENTAL_TURNOVER_ACCEPTANCE_VALIDATION_ONLY",
        })
    return {
        "target_count": len(items),
        "ready_count": sum(1 for x in items if x.get("ready")),
        "all_ready": bool(items) and all(x.get("ready") for x in items),
        "items": items,
        "promotion_rule": "先验证最近10分钟证据是否在多个自然盘中节点稳定补充或修正现有20日时间归一化成交承接；正式接入只增加事实维度，不建立总分、不设机械买卖阈值。",
    }


def build(root: Path = ROOT) -> dict:
    poc = load_json(root / POC_PATH, {})
    regime = load_json(root / REGIME_PATH, {})
    structure = load_json(root / STRUCTURE_PATH, {})
    index = index_validation(poc, regime)
    acceptance = acceptance_validation(poc, structure)
    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "MINUTE_CONTEXT_SHADOW_INTEGRATION_VALIDATION",
        "read_only": True,
        "source_poc": str(POC_PATH).replace("\\", "/"),
        "baseline_contexts": [str(REGIME_PATH).replace("\\", "/"), str(STRUCTURE_PATH).replace("\\", "/")],
        "index_minute_integration": index,
        "ten_minute_acceptance_integration": acceptance,
        "status": "READY" if index.get("all_interface_data_pass") and acceptance.get("all_ready") else "DEGRADED",
        "decision_boundary": "本文件只验证分钟证据接入现有market_regime_context与turnover_acceptance_context后的增量信息价值和冲突情况；不写正式context、不生成风险许可、机会状态、金额或交易动作。",
        "promotion_boundary": "接入验证PASS不自动等于正式生产启用；正式启用仍需独立生产变更并保留原有fallback。",
    }


def main() -> None:
    payload = build(ROOT)
    target = ROOT / OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "status": payload.get("status"),
        "index_interface_data_pass_count": (payload.get("index_minute_integration") or {}).get("interface_data_pass_count"),
        "index_intraday_quality_pass_count": (payload.get("index_minute_integration") or {}).get("intraday_quality_pass_count"),
        "acceptance_ready_count": (payload.get("ten_minute_acceptance_integration") or {}).get("ready_count"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
