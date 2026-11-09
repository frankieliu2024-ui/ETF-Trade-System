from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def score_edges(*, shadow: dict, latest: dict) -> dict:
    observations = {
        str(x.get("object_id") or x.get("asset") or ""): x
        for x in (latest.get("observations") or [])
        if isinstance(x, dict)
    }
    scored = []
    for edge in shadow.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        object_id = str(edge.get("object_id") or "")
        obs = observations.get(object_id, {})
        comparable = obs.get("pulse_comparison_status") == "COMPARABLE"
        components = {
            "configuration_anchor": 1.0 if edge.get("source") == "CONFIG_SEED" else 0.0,
            "live_object_observed": 1.0 if obs else 0.0,
            "cross_pulse_comparable": 1.0 if comparable else 0.0,
            "ashare_exposure_evidence": 0.0,
            "historical_transmission_evidence": 0.0,
            "independent_source_confirmation": 0.0,
        }
        gaps = []
        if not components["ashare_exposure_evidence"]:
            gaps.append("缺少ETF成分/经济暴露独立证据")
        if not components["historical_transmission_evidence"]:
            gaps.append("缺少历史同步率与领先性验证")
        if not components["independent_source_confirmation"]:
            gaps.append("缺少独立来源交叉确认")
        score = round(sum(components.values()) / len(components), 4)
        eligible = (
            edge.get("status") == "ACTIVE_SEED"
            or (
                score >= 0.75
                and not gaps
                and edge.get("mapping_type") != "UNMAPPED_OBJECT"
            )
        )
        scored.append({
            **edge,
            "evidence_components": components,
            "evidence_score": score,
            "evidence_gaps": gaps,
            "upgrade_eligible": bool(eligible),
            "upgrade_status": "ACTIVE_CANDIDATE" if eligible else "REQUIRES_VALIDATION",
        })
    return {
        "schema_version": "1.0",
        "projection_type": "HYPERLIQUID_MAPPING_EVIDENCE_SCORE",
        "read_only": True,
        "production_eligible": False,
        "source_context_id": shadow.get("source_context_id") or latest.get("context_id") or "",
        "generated_at_beijing": shadow.get("generated_at_beijing") or "",
        "edge_count": len(scored),
        "upgrade_candidate_count": sum(x["upgrade_eligible"] for x in scored),
        "validation_required_count": sum(not x["upgrade_eligible"] for x in scored),
        "edges": scored,
        "rule": "评分层只记录证据构成和缺口；未取得ETF暴露、历史传导和独立来源证据前，SHADOW边不得自动升级。",
    }


def main() -> None:
    shadow = _load(ROOT / "data/state/hyperliquid_mapping_shadow.json", {})
    latest = _load(ROOT / "data/state/hyperliquid_pulse_latest.json", {}).get("latest") or {}
    result = score_edges(shadow=shadow, latest=latest)
    target = ROOT / "data/state/hyperliquid_mapping_evidence_score.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "UPDATED", "edge_count": result["edge_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
