#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAP_DIR = ROOT / "data" / "market" / "snapshots"
FEATURE_DIR = ROOT / "events" / "research" / "daily_features"
OUT = ROOT / "research" / "reports" / "generated" / "section6_revalidation"


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def hm(text: str):
    try:
        dt = datetime.fromisoformat(str(text))
        return dt.hour * 60 + dt.minute
    except Exception:
        return None


def main():
    snapshots = defaultdict(list)
    for p in sorted(SNAP_DIR.glob("20*.json")):
        obj = load(p)
        if not obj:
            continue
        d = str(obj.get("market_date") or p.name[:10])
        phase = str(obj.get("market_phase") or "")
        if phase == "OPENING_CALL_AUCTION":
            continue
        ts = str(obj.get("captured_at_beijing") or obj.get("captured_at") or "")
        minute = hm(ts)
        if minute is None:
            continue
        snapshots[d].append({"path": str(p.relative_to(ROOT)), "minute": minute, "timestamp": ts, "quality": obj.get("quality_status")})

    snapshot_days = {}
    for d, xs in sorted(snapshots.items()):
        mins = sorted(x["minute"] for x in xs)
        snapshot_days[d] = {
            "snapshot_count": len(xs),
            "first_minute": mins[0] if mins else None,
            "last_minute": mins[-1] if mins else None,
            "has_morning": any(570 <= m <= 690 for m in mins),
            "has_afternoon": any(780 <= m <= 900 for m in mins),
            "has_opening_30m": any(570 <= m <= 600 for m in mins),
            "has_pre_lunch": any(660 <= m <= 690 for m in mins),
            "has_post_1400": any(840 <= m <= 900 for m in mins),
            "has_tail_1450_plus": any(890 <= m <= 900 for m in mins),
            "am_pm_split_eligible": any(570 <= m <= 690 for m in mins) and any(780 <= m <= 900 for m in mins),
            "tail_path_eligible": any(840 <= m < 890 for m in mins) and any(890 <= m <= 900 for m in mins),
            "discrete_path_eligible": len(xs) >= 6,
        }

    feature_days = {}
    for p in sorted(FEATURE_DIR.glob("20*.json")):
        obj = load(p)
        if not obj:
            continue
        d = str(obj.get("market_date") or p.stem)
        feats = obj.get("features") or []
        with_path = [x for x in feats if isinstance(x.get("intraday_path"), dict) and (x.get("intraday_path") or {}).get("sample_count")]
        coverage = defaultdict(int)
        for x in with_path:
            coverage[str((x.get("intraday_path") or {}).get("sampling_coverage") or "UNKNOWN")] += 1
        feature_days[d] = {
            "etf_count": len(feats),
            "with_intraday_path_count": len(with_path),
            "coverage": dict(sorted(coverage.items())),
        }

    path_feature_days = [d for d, z in feature_days.items() if z["with_intraday_path_count"] > 0]
    ampm_days = [d for d, z in snapshot_days.items() if z["am_pm_split_eligible"]]
    tail_days = [d for d, z in snapshot_days.items() if z["tail_path_eligible"]]
    discrete_days = [d for d, z in snapshot_days.items() if z["discrete_path_eligible"]]

    # Minute source is intentionally live/shadow and artifact-only; no persistent
    # multi-day minute history is expected in main under current governance.
    minute_workflow = ROOT / ".github" / "workflows" / "minute-path-shadow.yml"
    minute_builder = ROOT / "scripts" / "build_minute_path_features.py"
    minute_history_files = list(ROOT.glob("data/**/minute_path_features*.json"))

    payload = {
        "schema_version": "1.0",
        "mode": "RESEARCH_ONLY_ASHARE_ANCESTRAL_INTRADAY_COVERAGE_AUDIT",
        "issue": 107,
        "snapshot_date_count": len(snapshot_days),
        "snapshot_first_date": min(snapshot_days) if snapshot_days else None,
        "snapshot_last_date": max(snapshot_days) if snapshot_days else None,
        "am_pm_split_eligible_dates": ampm_days,
        "tail_path_eligible_dates": tail_days,
        "discrete_path_eligible_dates": discrete_days,
        "daily_feature_intraday_path_dates": path_feature_days,
        "snapshot_days": snapshot_days,
        "feature_days_with_path": {d: feature_days[d] for d in path_feature_days},
        "minute_capability": {
            "workflow_exists": minute_workflow.exists(),
            "builder_exists": minute_builder.exists(),
            "persistent_minute_history_file_count": len(minute_history_files),
            "persistent_minute_history_files": [str(p.relative_to(ROOT)) for p in minute_history_files[:20]],
            "governance_interpretation": "Minute path is live/shadow or object-level current evidence; current main does not preserve a multi-day minute warehouse for historical backtest. Do not create one solely for this research.",
        },
        "eligibility": {
            "daily_ancestral_rules": "READY_LONG_HISTORY",
            "discrete_intraday_path_rules": "FORWARD_SMALL_SAMPLE_ONLY" if discrete_days else "INSUFFICIENT",
            "am_pm_rules": "FORWARD_SMALL_SAMPLE_ONLY" if ampm_days else "INSUFFICIENT",
            "tail_rules": "FORWARD_SMALL_SAMPLE_ONLY" if tail_days else "INSUFFICIENT",
            "minute_15_30_60_hold_rules": "NO_PERSISTED_HISTORICAL_SAMPLE_WAIT_FORWARD",
        },
        "decision_boundary": "Coverage audit only. No new provider, warehouse, cache, schedule, trading rule or execution permission.",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ashare_ancestral_intraday_coverage_audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# A股祖训云端Stage3：日内/分钟覆盖审计（自动生成）",
        "",
        f"- 正式快照历史日期数：{len(snapshot_days)}；{payload['snapshot_first_date']} 至 {payload['snapshot_last_date']}。",
        f"- 可做上午/下午离散路径比较的日期：{len(ampm_days)}（{', '.join(ampm_days) if ampm_days else '无'}）。",
        f"- 可做14:00后至14:50+尾盘离散路径比较的日期：{len(tail_days)}（{', '.join(tail_days) if tail_days else '无'}）。",
        f"- 至少6个正式快照、可做粗日内路径的日期：{len(discrete_days)}（{', '.join(discrete_days) if discrete_days else '无'}）。",
        f"- daily_features中实际带intraday_path的日期：{len(path_feature_days)}（{', '.join(path_feature_days) if path_feature_days else '无'}）。",
        f"- main内持久化多日minute-path历史文件：{len(minute_history_files)}。",
        "",
        "## 结论",
        "",
        "1. 日线祖训有2024年至今的现成PIT大样本，可继续做条件化验证。",
        "2. 日内快照只适合forward小样本祖训验证；若日期数很少，不应把它扩展成历史统计结论。",
        "3. 15/30/60分钟守住率、二次上攻、精细尾盘结构当前没有持久化多日分钟仓库。按现行治理，应等待自然forward样本，不为祖训研究新建第二套分钟数据库或长期缓存。",
        "4. 当前分钟能力仍可在未来真实交易日作为Point-in-Time高分辨率证据采样，用于验证Stage1/2已筛出的少数高价值候选。",
    ]
    (OUT / "ashare_ancestral_intraday_coverage_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "snapshot_dates": len(snapshot_days), "ampm": len(ampm_days), "tail": len(tail_days), "discrete": len(discrete_days), "path_feature_days": len(path_feature_days), "minute_history": len(minute_history_files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
