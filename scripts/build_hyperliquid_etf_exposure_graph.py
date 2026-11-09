from __future__ import annotations

from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parents[1]


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return None
    x, y = a[-n:], b[-n:]
    mx, my = sum(x) / n, sum(y) / n
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    den = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    return None if den == 0 else sum(i * j for i, j in zip(dx, dy)) / den


def _edge(object_id, etf_code, series, min_obs=20):
    hl = series.get("hyperliquid") or []
    etf = series.get("etf") or []
    if len(hl) < min_obs or len(etf) < min_obs:
        return {
            "object_id": object_id,
            "etf_code": etf_code,
            "edge_status": "INSUFFICIENT_HISTORY",
            "mapping_type": "DYNAMIC_STATISTICAL_EXPOSURE",
            "sample_count": min(len(hl), len(etf)),
            "reason": "缺少至少20个时间对齐样本",
            "evidence": {},
        }
    same = _corr(hl, etf)
    lagged = _corr(hl[:-1], etf[1:])
    edge_status = "SHADOW" if same is not None and abs(same) >= 0.30 else "NO_STABLE_EDGE"
    return {
        "object_id": object_id,
        "etf_code": etf_code,
        "edge_status": edge_status,
        "mapping_type": "DYNAMIC_STATISTICAL_EXPOSURE",
        "sample_count": min(len(hl), len(etf)),
        "direction": "POSITIVE" if (same or 0) > 0 else "NEGATIVE" if (same or 0) < 0 else "UNDETERMINED",
        "reason": "滚动统计候选边，仍需留出样本验证" if edge_status == "SHADOW" else "当前滚动样本未达到稳定边阈值",
        "evidence": {
            "same_window_correlation": round(same, 6) if same is not None else None,
            "hl_leads_etf_one_step_correlation": round(lagged, 6) if lagged is not None else None,
            "out_of_sample_status": "NOT_RUN",
        },
        "upgrade_eligible": False,
    }


def build_graph(*, latest: dict, universe: dict, aligned_series: dict | None = None) -> dict:
    observations = latest.get("observations") or []
    objects = sorted({
        str(x.get("object_id") or x.get("asset") or "").strip()
        for x in observations if str(x.get("object_id") or x.get("asset") or "").strip()
    })
    etfs = sorted({
        str(x.get("code") or "").strip()
        for x in (universe.get("objects") or []) if str(x.get("code") or "").strip()
    })
    aligned_series = aligned_series if isinstance(aligned_series, dict) else {}
    edges = []
    for object_id in objects:
        for etf_code in etfs:
            key = f"{object_id}|{etf_code}"
            edges.append(_edge(object_id, etf_code, aligned_series.get(key) or {}))
    return {
        "schema_version": "1.0",
        "projection_type": "HYPERLIQUID_ETF_DYNAMIC_EXPOSURE_GRAPH",
        "read_only": True,
        "production_eligible": False,
        "node_model": "HYPERLIQUID_OBJECT_TO_ETF_TERMINAL_NODE",
        "object_node_count": len(objects),
        "etf_node_count": len(etfs),
        "edge_count": len(edges),
        "shadow_edge_count": sum(x["edge_status"] == "SHADOW" for x in edges),
        "insufficient_history_edge_count": sum(x["edge_status"] == "INSUFFICIENT_HISTORY" for x in edges),
        "edges": edges,
        "upgrade_rule": "边必须经过滚动样本、留出样本、安慰剂检验和ETF自身反馈确认后才可进入正式贡献包；当前图不改变正式判断或交易权限。",
    }


def main():
    def load(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
    latest = load(ROOT / "data/state/hyperliquid_pulse_latest.json", {}).get("latest") or {}
    universe = load(ROOT / "config/market/etf_monitor_universe.json", {})
    result = build_graph(latest=latest, universe=universe)
    (ROOT / "data/state/hyperliquid_etf_dynamic_exposure_graph.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "UPDATED", "edge_count": result["edge_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
