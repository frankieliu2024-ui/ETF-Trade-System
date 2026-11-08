#!/usr/bin/env python3
"""Research-only analysis for aligned Hyperliquid/A-share overnight observations.

Input CSV is intentionally explicit and PIT-auditable. This module computes
descriptive statistics only; it does not select trades or write production state.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

REQUIRED = {
    "object_id", "trade_date", "hl_overnight_change",
    "a_share_open_gap", "a_share_open_to_close_return",
}
OPTIONAL = {"a_share_prev_return", "hl_adjusted_change", "hl_funding", "hl_open_interest_change"}

def number(row: dict, key: str):
    value = row.get(key, "")
    try:
        return float(value) if value not in ("", None) else None
    except (TypeError, ValueError):
        return None

def corr(xs: list[float], ys: list[float]):
    if len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    den = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    return sum(x * y for x, y in zip(dx, dy)) / den if den else None

def summarize(rows: list[dict]) -> dict:
    valid = [
        row for row in rows
        if number(row, "hl_overnight_change") is not None
        and number(row, "a_share_open_gap") is not None
    ]
    pairs = [(number(r, "hl_overnight_change"), number(r, "a_share_open_gap")) for r in valid]
    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
    nonzero = [(x, y) for x, y in pairs if x != 0 and y != 0]
    hit = sum((x > 0) == (y > 0) for x, y in nonzero) / len(nonzero) if nonzero else None
    prev_pairs = [
        (number(r, "a_share_prev_return"), number(r, "a_share_open_gap"))
        for r in valid
        if number(r, "a_share_prev_return") is not None
    ]
    post = [
        (number(r, "hl_overnight_change"), number(r, "a_share_open_to_close_return"))
        for r in rows
        if number(r, "hl_overnight_change") is not None
        and number(r, "a_share_open_to_close_return") is not None
    ]
    reversals = sum((x > 0) != (y > 0) for x, y in post if x != 0 and y != 0)
    post_nonzero = sum(x != 0 and y != 0 for x, y in post)
    return {
        "sample_count": len(rows),
        "valid_gap_pairs": len(pairs),
        "missing_gap_rows": len(rows) - len(pairs),
        "gap_correlation": corr(xs, ys),
        "gap_direction_hit_rate": hit,
        "gap_direction_nonzero_count": len(nonzero),
        "prior_day_gap_correlation": corr([x for x, _ in prev_pairs], [y for _, y in prev_pairs]),
        "post_open_correlation": corr([x for x, _ in post], [y for _, y in post]),
        "post_open_reversal_rate": reversals / post_nonzero if post_nonzero else None,
        "post_open_nonzero_count": post_nonzero,
    }

def run(path: Path) -> dict:
    grouped = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = sorted(REQUIRED - fields)
        if missing:
            raise ValueError("missing required columns: " + ", ".join(missing))
        for row in reader:
            grouped[row.get("object_id", "").strip()].append(row)
    results = {key: summarize(value) for key, value in sorted(grouped.items()) if key}
    return {
        "schema_version": "research-hl-overnight-analysis-v1",
        "mode": "RESEARCH_ONLY_EXPLORATORY",
        "read_only": True,
        "input": str(path),
        "objects": results,
        "conclusion_boundary": "Descriptive statistics only. No production state, formal decision, trading permission, amount or order is generated.",
        "quality_notes": [
            "correlation is not proof of causality or profitability",
            "a-share prior-day return is a control, not a trading rule",
            "costs, slippage, funding and PIT validity require separate audit",
            "small or unstable samples must remain INSUFFICIENT_EVIDENCE",
        ],
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = json.dumps(run(Path(args.csv_path)), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(result + "\n", encoding="utf-8")
    print(result)
