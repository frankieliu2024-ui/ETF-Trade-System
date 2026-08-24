from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from statistics import median

from hithink_etf_data import HithinkETFClient

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY_DIR = ROOT / "events/research/daily_features"
RAW_DIR = ROOT / "data/market/audit/research_backfill"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def round4(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    request = load_json((ROOT / args.request_path).resolve())
    start = date.fromisoformat(str(request.get("start_date") or "2024-01-01"))
    end = date.fromisoformat(str(request.get("end_date") or "2026-08-21"))
    universe = load_json(ROOT / "config/market/etf_monitor_universe.json")
    objects = universe.get("objects") or []
    client = HithinkETFClient(timeout=25, max_attempts=2)

    by_date: dict[str, list[dict]] = {}
    sources = []
    failures = []
    for obj in objects:
        code = str(obj.get("code") or "")
        name = str(obj.get("name") or "")
        thscode = str(obj.get("thscode") or "")
        if not code or not thscode:
            continue
        try:
            frame, meta = client.history(thscode, start, end, RAW_DIR / code)
            sources.append({"code": code, "name": name, "thscode": thscode, "rows": len(frame), "meta": meta})
            for _, row in frame.iterrows():
                d = row["date"].strftime("%Y-%m-%d")
                by_date.setdefault(d, []).append({
                    "code": code, "name": name,
                    "open": round4(row.get("open")), "high": round4(row.get("high")), "low": round4(row.get("low")), "close": round4(row.get("close")),
                    "volume": round4(row.get("volume")), "amount": round4(row.get("amount")), "close_return_pct": round4(row.get("change_pct")),
                    "provider": "hithink-finance", "quality_status": "PASS",
                })
        except Exception as exc:
            failures.append({"code": code, "name": name, "thscode": thscode, "error": str(exc)})

    written = 0
    skipped_existing = 0
    for market_date, features in sorted(by_date.items()):
        target = DAILY_DIR / f"{market_date}.json"
        if target.exists():
            skipped_existing += 1
            continue
        changes = [x["close_return_pct"] for x in features if x.get("close_return_pct") is not None]
        med = median(changes) if changes else None
        ranked = sorted([x for x in features if x.get("close_return_pct") is not None], key=lambda x: x["close_return_pct"], reverse=True)
        rank_map = {x["code"]: i + 1 for i, x in enumerate(ranked)}
        for item in features:
            change = item.get("close_return_pct")
            item["relative_to_etf_universe_median_pct_points"] = round4(change - med) if change is not None and med is not None else None
            item["etf_universe_close_return_rank"] = rank_map.get(item["code"])
            item["relative_to_indices_pct_points"] = {}
            item["intraday_path"] = {"sampling_coverage": "NOT_AVAILABLE_DAILY_BACKFILL", "structure_candidates": []}
        payload = {
            "schema_version": "1.0-backfill",
            "generated_at": now_utc(),
            "market_date": market_date,
            "as_of_beijing": f"{market_date}T15:00:00+08:00",
            "market_phase": "CLOSED",
            "source_snapshot": "HISTORICAL_BACKFILL_HITHINK_1D",
            "quality_status": "PASS",
            "mode": "OBJECTIVE_RESEARCH_FEATURES_HISTORICAL_BACKFILL",
            "read_only": True,
            "historical_backfill": True,
            "historical_decision_prohibited": True,
            "decision_boundary": "只回补当时客观ETF日线事实和横截面描述，不伪造历史ChatGPT决策、Trial/Confirm或盘中路径。",
            "etf_universe_count": len(objects),
            "observed_etf_count": len(features),
            "etf_universe_median_close_return_pct": round4(med),
            "features": features,
        }
        atomic_json_write(target, payload)
        written += 1

    summary = {
        "schema_version": "1.0", "generated_at": now_utc(), "mode": "HISTORICAL_RESEARCH_BACKFILL",
        "start_date": start.isoformat(), "end_date": end.isoformat(), "requested_etf_count": len(objects), "successful_etf_count": len(sources),
        "failure_count": len(failures), "daily_feature_days_written": written, "existing_days_preserved": skipped_existing,
        "sources": sources, "failures": failures,
        "boundary": "历史回补只扩充客观研究事实。不得据此声称历史上系统会作出某个交易决策；若需验证历史决策逻辑，必须另做严格point-in-time回放。",
        "optimization_principle": "优先用低成本历史事实缩短学习周期，但不为扩大样本而降低数据质量或制造伪历史决策。",
    }
    atomic_json_write(ROOT / "data/state/historical_backfill_status.json", summary)
    print(json.dumps({k: summary[k] for k in ["successful_etf_count", "failure_count", "daily_feature_days_written", "existing_days_preserved"]}, ensure_ascii=False))
    return 0 if len(sources) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
