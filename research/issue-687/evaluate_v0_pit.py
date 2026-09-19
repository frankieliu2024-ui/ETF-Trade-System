from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from etf_opportunity_discovery import discover_etf


def forward_return(frame: pd.DataFrame, idx: int, sessions: int) -> float | None:
    if idx + sessions >= len(frame):
        return None
    start = float(frame.iloc[idx]["close"])
    end = float(frame.iloc[idx + sessions]["close"])
    return end / start - 1.0 if start > 0 else None


def evaluate_one(path: Path, code: str) -> dict:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)

    events = []
    previous_states: set[str] = set()
    eligible_days = 0
    surfaced_days = 0
    for idx in range(len(frame)):
        as_of = frame.iloc[idx]["date"].date().isoformat()
        result = discover_etf(frame, code=code, name=code, as_of=as_of)
        if result["status"] != "PASS":
            continue
        eligible_days += 1
        states = {x["state"] for x in result["surfaced_states"]}
        if states:
            surfaced_days += 1
        new_states = sorted(states - previous_states)
        if new_states:
            events.append({
                "as_of": as_of,
                "new_states": new_states,
                "forward_returns": {
                    str(h): None if (v := forward_return(frame, idx, h)) is None else round(v, 6)
                    for h in (5, 10, 20)
                },
            })
        previous_states = states

    return {
        "code": code,
        "rows": len(frame),
        "first_date": frame.iloc[0]["date"].date().isoformat(),
        "last_date": frame.iloc[-1]["date"].date().isoformat(),
        "eligible_days": eligible_days,
        "surfaced_days": surfaced_days,
        "surfaced_day_rate": round(surfaced_days / eligible_days, 6) if eligible_days else None,
        "state_entry_events": events,
        "event_count": len(events),
        "research_boundary": "descriptive PIT replay only; forward returns are evaluation labels and never discovery inputs",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--dataset", action="append", required=True, help="CODE=relative/path.csv")
    args = p.parse_args()

    rows = []
    for item in args.dataset:
        code, rel = item.split("=", 1)
        rows.append(evaluate_one(ROOT / rel, code))
    output = {
        "issue": 687,
        "method": "V0 daily PIT replay",
        "selection_bias_warning": "Eight cases were previously selected mandatory out-of-pool examples; results do not establish all-market production value.",
        "etfs": rows,
    }
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
