from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
HISTORY_DIR = Path("events/research/evidence_snapshots")


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diff(new, old):
    a, b = safe_float(new), safe_float(old)
    return None if a is None or b is None else round(a - b, 4)


def classify_change(value, epsilon=0.05):
    if value is None:
        return "UNKNOWN"
    if value > epsilon:
        return "IMPROVED"
    if value < -epsilon:
        return "WEAKENED"
    return "UNCHANGED"


def evidence_semantics(relative: dict) -> str:
    phase = str(relative.get("market_phase") or "")
    as_of = str(relative.get("as_of_beijing") or "")
    today = datetime.now(SHANGHAI).date().isoformat()
    evidence_date = as_of[:10]
    if evidence_date == today and phase in {"CONTINUOUS_TRADING", "OPENING_CALL_AUCTION", "POST_CLOSE_GRACE"}:
        return "CURRENT_INTRADAY_OR_CLOSE"
    if evidence_date == today:
        return "CURRENT_DATE_LATEST"
    if evidence_date:
        return "PREVIOUS_OR_HISTORICAL"
    return "UNKNOWN"


def latest_prior(root: Path, market_date: str, current_as_of: str) -> dict:
    base = root / HISTORY_DIR / market_date
    if not base.exists():
        return {}
    candidates = []
    for path in base.glob("*.json"):
        obj = load_json(path, {})
        as_of = str(obj.get("as_of_beijing") or "")
        if as_of and as_of < current_as_of:
            candidates.append((as_of, obj))
    return max(candidates, key=lambda x: x[0])[1] if candidates else {}


def build(root: Path = ROOT) -> dict:
    relative = load_json(root / "data/state/relative_strength.json", {})
    market_date = str(relative.get("market_date") or "")
    as_of = str(relative.get("as_of_beijing") or "")
    prior = latest_prior(root, market_date, as_of) if market_date and as_of else {}
    prior_map = {str(x.get("code")): x for x in (prior.get("items") or []) if x.get("code")}
    items = []
    for row in relative.get("items") or []:
        code = str(row.get("code") or "")
        old = prior_map.get(code) or {}
        vs_med_delta = diff(row.get("vs_universe_median_pct_points"), old.get("vs_universe_median_pct_points"))
        vs_cyb_delta = diff(row.get("vs_chinext_pct_points"), old.get("vs_chinext_pct_points"))
        slope_delta = diff(row.get("recent_slope_pct_per_10m"), old.get("recent_slope_pct_per_10m"))
        rank_now = row.get("rank")
        rank_old = old.get("rank")
        rank_delta = (int(rank_old) - int(rank_now)) if rank_now is not None and rank_old is not None else None
        signals = [x for x in [vs_med_delta, vs_cyb_delta, slope_delta] if x is not None]
        aggregate = sum(signals) / len(signals) if signals else None
        items.append({
            "code": code,
            "name": row.get("name"),
            "display_name": f"{row.get('name')}（{code}）" if row.get("name") else code,
            "current": {
                "descriptive_daily_return_rank": rank_now,
                "vs_universe_median_pct_points": row.get("vs_universe_median_pct_points"),
                "vs_shanghai_pct_points": row.get("vs_shanghai_pct_points"),
                "vs_chinext_pct_points": row.get("vs_chinext_pct_points"),
                "recent_slope_pct_per_10m": row.get("recent_slope_pct_per_10m"),
            },
            "delta_from_prior_research_node": {
                "rank_improvement_places": rank_delta,
                "vs_universe_median_delta_pct_points": vs_med_delta,
                "vs_chinext_delta_pct_points": vs_cyb_delta,
                "recent_slope_delta_pct_per_10m": slope_delta,
                "direction": classify_change(aggregate),
            },
            "comparison_dimensions": [
                "相对ETF全集中位数", "相对上证指数", "相对创业板指", "近期路径斜率", "描述性当日收益排名"
            ],
            "capital_efficiency_score": None,
            "score_boundary": "不生成综合资本效率评分。不同ETF表达不同风险因子，统一比较使用多维证据和MASTER完整决策链。",
        })
    result = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "market_date": market_date,
        "as_of_beijing": as_of,
        "evidence_time_semantics": evidence_semantics(relative),
        "prior_as_of_beijing": prior.get("as_of_beijing", ""),
        "mode": "RESEARCH_EVIDENCE_DELTA",
        "read_only": True,
        "decision_boundary": "只描述研究证据相对上一研究节点的新增、增强、减弱或未变化；不创建交易状态、评分、阈值或动作。",
        "items": items,
    }
    if market_date and as_of:
        safe_name = as_of.replace(":", "").replace("+", "_").replace("-", "").replace("T", "_")
        hist_path = root / HISTORY_DIR / market_date / f"{safe_name}.json"
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(hist_path, {**relative, "market_phase": relative.get("market_phase"), "archived_for_delta": True})
    return result


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data/state/research_evidence_delta.json", result)
    print(json.dumps({"ok": True, "market_date": result.get("market_date"), "as_of_beijing": result.get("as_of_beijing"), "item_count": len(result.get("items") or [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
