from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from kronos_research_adapter import KronosResearchAdapter, round4
except ModuleNotFoundError:
    from scripts.kronos_research_adapter import KronosResearchAdapter, round4

try:
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
DAILY_DIR = ROOT / "events/research/daily_features"
OUT_DIR = ROOT / "events/research/kronos_backfill"
STATUS_PATH = ROOT / "data/state/kronos_research_status.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_daily_history() -> dict[str, list[dict]]:
    by_code: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(DAILY_DIR.glob("*.json")):
        payload = load_json(path)
        market_date = str(payload.get("market_date") or path.stem)
        for item in payload.get("features") or []:
            code = str(item.get("code") or "")
            if not code:
                continue
            row = {
                "date": market_date,
                "name": item.get("name"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
            }
            if all(row[k] is not None for k in ["open", "high", "low", "close"]):
                by_code[code].append(row)
    for code in by_code:
        by_code[code].sort(key=lambda x: x["date"])
    return by_code


def realized_outcome(rows: list[dict], idx: int, pred_len: int) -> dict:
    base = float(rows[idx]["close"])
    future = rows[idx + 1: idx + 1 + pred_len]
    terminal = float(future[-1]["close"])
    high = max(float(x["high"]) for x in future)
    low = min(float(x["low"]) for x in future)
    return {
        "terminal_return_pct": round4((terminal / base - 1.0) * 100.0),
        "max_upside_pct": round4((high / base - 1.0) * 100.0),
        "max_downside_pct": round4((low / base - 1.0) * 100.0),
        "future_dates": [x["date"] for x in future],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    args = parser.parse_args()
    request = load_json((ROOT / args.request_path).resolve())
    config_path = ROOT / str(request.get("config_path") or "config/research/kronos_poc.json")
    config = load_json(config_path)

    start_date = str(request.get("start_date") or "2025-01-01")
    end_date = str(request.get("end_date") or "2026-08-21")
    stride = max(1, int(request.get("stride_trading_days") or 20))
    max_per_code = max(1, int(request.get("max_evaluations_per_code") or 16))
    selected_codes = {str(x) for x in request.get("codes") or []}
    pred_len = int(config["forecast"]["pred_len"])
    min_history = int(config["validation"].get("minimum_history_rows", 120))

    history = load_daily_history()
    adapter = KronosResearchAdapter(config_path=config_path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0
    failures = []
    code_summary = {}

    for code, rows in sorted(history.items()):
        if selected_codes and code not in selected_codes:
            continue
        eligible = []
        for idx, row in enumerate(rows):
            if row["date"] < start_date or row["date"] > end_date:
                continue
            if idx + 1 < min_history or idx + pred_len >= len(rows):
                continue
            eligible.append(idx)
        eligible = eligible[::stride][:max_per_code]
        code_generated = 0

        for idx in eligible:
            as_of = rows[idx]["date"]
            target = OUT_DIR / f"{as_of}_{code}.json"
            if target.exists() and not bool(request.get("overwrite", False)):
                skipped += 1
                continue
            try:
                hist_rows = rows[: idx + 1]
                x_df = pd.DataFrame(hist_rows)[["open", "high", "low", "close", "volume", "amount"]].copy()
                x_df["volume"] = x_df["volume"].fillna(0.0)
                x_df["amount"] = x_df["amount"].fillna(0.0)
                x_ts = pd.Series(pd.to_datetime([x["date"] for x in hist_rows]))
                y_ts = pd.Series(pd.to_datetime([x["date"] for x in rows[idx + 1: idx + 1 + pred_len]]))
                forecast = adapter.predict_distribution(x_df, x_ts, y_ts, code=code, as_of=as_of)
                outcome = realized_outcome(rows, idx, pred_len)
                payload = {
                    "schema_version": "1.0",
                    "generated_at": now_utc(),
                    "mode": "KRONOS_POINT_IN_TIME_HISTORICAL_VALIDATION",
                    "market_date": as_of,
                    "code": code,
                    "name": rows[idx].get("name"),
                    "forecast": forecast,
                    "realized_forward_outcome": outcome,
                    "historical_decision_prohibited": True,
                    "decision_eligible": False,
                    "trade_signal": None,
                    "boundary": config["boundaries"]["description"],
                }
                atomic_json_write(target, payload)
                generated += 1
                code_generated += 1
            except Exception as exc:
                failures.append({"code": code, "as_of": as_of, "error": str(exc)})

        code_summary[code] = {
            "available_rows": len(rows),
            "eligible_evaluations": len(eligible),
            "generated": code_generated,
        }

    status = {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "mode": "KRONOS_RESEARCH_POC",
        "status": "PASS" if generated > 0 and not failures else ("DEGRADED" if generated > 0 else "FAIL"),
        "request_path": args.request_path,
        "upstream_commit": config["upstream"]["commit"],
        "model": config["model"]["predictor"],
        "tokenizer": config["model"]["tokenizer"],
        "generated_evaluations": generated,
        "existing_evaluations_preserved": skipped,
        "failure_count": len(failures),
        "failures": failures[:50],
        "by_code": code_summary,
        "decision_eligible": False,
        "trade_signal": None,
        "boundary": config["boundaries"]["description"],
        "next_gate": "先统计Kronos与真实forward outcome的样本外关系；只有出现稳定增量信息后，才允许讨论是否向正式研究摘要贡献权重。不得直接进入MASTER交易信号。",
    }
    atomic_json_write(STATUS_PATH, status)
    print(json.dumps({"status": status["status"], "generated": generated, "skipped": skipped, "failures": len(failures)}, ensure_ascii=False))
    return 0 if generated > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
