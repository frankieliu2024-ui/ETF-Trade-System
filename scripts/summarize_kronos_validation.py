from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SOURCE_DIR = ROOT / "events/research/kronos_backfill"
OUT_PATH = ROOT / "research/backtests/kronos_validation_summary.json"


def round4(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 4) if math.isfinite(value) else None


def load_records() -> list[dict]:
    records = []
    for path in sorted(SOURCE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            forecast = payload.get("forecast") or {}
            outcome = payload.get("realized_forward_outcome") or {}
            records.append({
                "code": str(payload.get("code") or ""),
                "market_date": payload.get("market_date"),
                "bias": forecast.get("kronos_bias"),
                "forecast_terminal_pct": forecast.get("forecast_return_median_pct"),
                "up_probability": forecast.get("forecast_up_probability"),
                "realized_terminal_pct": outcome.get("terminal_return_pct"),
            })
        except Exception:
            continue
    return [r for r in records if r["code"] and r["realized_terminal_pct"] is not None]


def summarize_group(rows: list[dict]) -> dict:
    actual = [float(r["realized_terminal_pct"]) for r in rows]
    forecast = [float(r["forecast_terminal_pct"]) for r in rows if r["forecast_terminal_pct"] is not None]
    direction_hits = []
    for r in rows:
        f = r["forecast_terminal_pct"]
        a = r["realized_terminal_pct"]
        if f is None or a is None or float(f) == 0 or float(a) == 0:
            continue
        direction_hits.append((float(f) > 0) == (float(a) > 0))
    return {
        "n": len(rows),
        "realized_terminal_mean_pct": round4(mean(actual)) if actual else None,
        "realized_terminal_median_pct": round4(median(actual)) if actual else None,
        "forecast_terminal_mean_pct": round4(mean(forecast)) if forecast else None,
        "direction_accuracy": round4(sum(direction_hits) / len(direction_hits)) if direction_hits else None,
        "realized_positive_rate": round4(sum(x > 0 for x in actual) / len(actual)) if actual else None,
    }


def main() -> int:
    records = load_records()
    by_code = defaultdict(list)
    by_bias = defaultdict(list)
    for row in records:
        by_code[row["code"]].append(row)
        by_bias[str(row.get("bias") or "unknown")].append(row)

    bullish = by_bias.get("bullish", [])
    bearish = by_bias.get("bearish", [])
    bullish_mean = summarize_group(bullish).get("realized_terminal_mean_pct") if bullish else None
    bearish_mean = summarize_group(bearish).get("realized_terminal_mean_pct") if bearish else None
    spread = round4(float(bullish_mean) - float(bearish_mean)) if bullish_mean is not None and bearish_mean is not None else None

    payload = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "KRONOS_RESEARCH_VALIDATION_SUMMARY",
        "total_evaluations": len(records),
        "overall": summarize_group(records),
        "by_bias": {k: summarize_group(v) for k, v in sorted(by_bias.items())},
        "by_code": {k: summarize_group(v) for k, v in sorted(by_code.items())},
        "bullish_minus_bearish_realized_mean_spread_pct_points": spread,
        "decision_eligible": False,
        "trade_signal": None,
        "interpretation_boundary": "本汇总只回答Kronos是否具有研究层增量信息。不能由方向准确率、收益分组或单次预测直接生成Trial/Confirm、交易金额、持有、降低风险或退出。",
        "promotion_gate": {
            "status": "NOT_EVALUATED" if not records else "RESEARCH_REVIEW_REQUIRED",
            "requirements": [
                "样本量达到配置最低要求且覆盖多个ETF和不同市场阶段",
                "bullish/neutral/bearish分组的真实forward outcome呈稳定可解释排序",
                "样本外方向或机会排序指标优于无Kronos基线",
                "增量价值在不同ETF上不是由单一品种驱动",
                "通过后也只能先进入研究摘要，不直接修改MASTER交易规则"
            ]
        }
    }
    atomic_json_write(OUT_PATH, payload)
    print(json.dumps({"total_evaluations": len(records), "spread": spread}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
