from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/market/hyperliquid_etf_session_policy.json"
NODES = {
    "ETF系统复核": {"08:30", "19:30"},
    "ETF正式决策": {"09:00", "10:00", "11:00", "13:30", "14:30"},
    "ETF交易复盘": {"12:30", "20:30"},
}


def _phase(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    beijing = parsed.astimezone(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8)))
    minute = beijing.hour * 60 + beijing.minute
    if 8 * 60 <= minute < 16 * 60:
        return "A_SHARE_ACTIVE_WINDOW"
    if minute < 8 * 60:
        return "PREOPEN_OVERNIGHT_AGGREGATION"
    return "OUTSIDE_CONTINUOUS_PULSE_WINDOW"


def validate_context(payload: dict) -> list[str]:
    errors = []
    policy = payload.get("session_policy") or {}
    if policy.get("continuous_pulse_window_beijing") != "08:00-16:00":
        errors.append("continuous pulse window is not bounded to 08:00-16:00")
    if policy.get("overnight_aggregation_boundary_beijing") != "08:00":
        errors.append("overnight aggregation boundary is not 08:00")
    for task, nodes in NODES.items():
        if set((policy.get("task_consumption") or {}).get(task) or []) != nodes:
            errors.append(f"task node coverage mismatch: {task}")
    if payload.get("decision_boundary", "").find("cannot independently") >= 0:
        errors.append("decision boundary text is malformed")
    return errors


def main() -> int:
    if not POLICY.exists():
        raise SystemExit("missing session policy")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if policy.get("continuous_pulse_window_beijing") != "08:00-16:00":
        raise SystemExit("invalid policy window")
    if set(policy["task_nodes"]["ETF正式决策"]) != NODES["ETF正式决策"]:
        raise SystemExit("formal decision node contract mismatch")
    for stamp in ("2026-11-07T23:55:00+00:00", "2026-11-08T00:00:00+00:00", "2026-11-08T07:55:00+00:00", "2026-11-08T08:00:00+00:00"):
        if stamp.endswith("23:55:00+00:00") and _phase(stamp) != "PREOPEN_OVERNIGHT_AGGREGATION":
            raise SystemExit(f"unexpected preopen phase for {stamp}")
        if not stamp.endswith("23:55:00+00:00") and stamp.endswith("08:00:00+00:00") and _phase(stamp) != "OUTSIDE_CONTINUOUS_PULSE_WINDOW":
            raise SystemExit(f"unexpected outside-window phase for {stamp}")
        if not stamp.endswith("23:55:00+00:00") and not stamp.endswith("08:00:00+00:00") and _phase(stamp) != "A_SHARE_ACTIVE_WINDOW":
            raise SystemExit(f"unexpected phase for {stamp}")
    print(json.dumps({"ok": True, "policy": policy["continuous_pulse_window_beijing"], "nodes": NODES}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
