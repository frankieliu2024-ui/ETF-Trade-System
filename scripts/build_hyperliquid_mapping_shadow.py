from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _asset_id(row):
    raw = row.get("asset")
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("id") or "").strip()
    return str(row.get("object_id") or raw or row.get("name") or "").strip()


def build_shadow_mapping(*, latest: dict, mapping_config: dict, universe: dict) -> dict:
    observations = latest.get("observations") or []
    configured = mapping_config.get("asset_mappings") or []
    etfs = universe.get("objects") or []
    etf_rows = [
        {"code": str(x.get("code") or "").strip(), "name": str(x.get("name") or "").strip()}
        for x in etfs if str(x.get("code") or "").strip()
    ]
    now = str(latest.get("fact_time_beijing") or latest.get("formed_at_beijing") or "")
    edges = []
    observed_ids = set()

    for item in observations:
        if not isinstance(item, dict):
            continue
        asset = _asset_id(item)
        if not asset:
            continue
        observed_ids.add(asset)
        configured_rows = [x for x in configured if str(x.get("asset") or "").strip() == asset]
        if not configured_rows:
            edges.append({
                "object_id": asset,
                "object_name": str(item.get("object_name") or asset),
                "economic_family": str(item.get("family") or ""),
                "etf_code": None,
                "mapping_type": "UNMAPPED_OBJECT",
                "source": "LIVE_OBJECT_CATALOG",
                "confidence": "NONE",
                "status": "SHADOW",
                "reason": "当前对象没有配置锚点，等待经济族群或独立证据生成候选边",
                "effective_as_of": now,
            })
            continue
        for cfg in configured_rows:
            family = str(cfg.get("family") or "").strip()
            explicit = [str(x).strip() for x in (cfg.get("etf_codes") or cfg.get("mapped_assets") or []) if str(x).strip()]
            markers = [str(x).strip() for x in (cfg.get("name_markers") or []) if str(x).strip()]
            excluded = [str(x).strip() for x in (cfg.get("exclude_name_markers") or []) if str(x).strip()]
            codes = explicit or [
                row["code"] for row in etf_rows
                if any(m in row["name"] for m in markers)
                and not any(m in row["name"] for m in excluded)
            ]
            if not codes:
                codes = [None]
            for code in codes:
                edges.append({
                    "object_id": asset,
                    "object_name": str(item.get("object_name") or asset),
                    "economic_family": family,
                    "etf_code": code,
                    "mapping_type": "DIRECT_CODE" if explicit else "NAME_MARKER_INFERRED",
                    "source": "CONFIG_SEED" if explicit else "CONFIG_FAMILY_MARKER",
                    "confidence": "HIGH" if explicit else "LOW",
                    "status": "ACTIVE_SEED" if explicit else "SHADOW",
                    "reason": "配置直接绑定" if explicit else "经济族群名称标记候选，未经过历史传导验证",
                    "effective_as_of": now,
                })

    return {
        "schema_version": "1.0",
        "projection_type": "HYPERLIQUID_MAPPING_SHADOW",
        "read_only": True,
        "production_eligible": False,
        "generated_at_beijing": now,
        "source_context_id": str(latest.get("context_id") or latest.get("identity") or ""),
        "observed_object_count": len(observed_ids),
        "edge_count": len(edges),
        "active_seed_edge_count": sum(x["status"] == "ACTIVE_SEED" for x in edges),
        "shadow_edge_count": sum(x["status"] == "SHADOW" for x in edges),
        "edges": edges,
        "upgrade_rule": "只有经过独立证据、历史传导和冲突检查后，SHADOW边才可申请进入正式映射；本文件不改变任何消费者或交易权限。",
    }


def main() -> None:
    latest = _load(ROOT / "data/state/hyperliquid_pulse_latest.json", {}).get("latest") or {}
    config = _load(ROOT / "config/market/hyperliquid_etf_exposure_map.json", {})
    universe = _load(ROOT / "config/market/etf_monitor_universe.json", {})
    result = build_shadow_mapping(latest=latest, mapping_config=config, universe=universe)
    target = ROOT / "data/state/hyperliquid_mapping_shadow.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "UPDATED", "path": str(target), "edge_count": result["edge_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
