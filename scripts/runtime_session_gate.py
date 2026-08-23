from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def set_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main() -> int:
    now = datetime.now(SHANGHAI)
    date_text = now.date().isoformat()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    policy = load_json(ROOT / "config" / "runtime_policy.json")
    calendar = load_json(ROOT / "config" / "market" / "a_share_trading_calendar_2026.json")

    if event_name == "workflow_dispatch":
        set_output("should_capture", "true")
        set_output("reason", "manual_dispatch")
        set_output("market_date", date_text)
        print(json.dumps({"should_capture": True, "reason": "manual_dispatch", "market_date": date_text}, ensure_ascii=False))
        return 0

    start = calendar.get("coverage_start", "")
    end = calendar.get("coverage_end", "")
    if not start or not end or not (start <= date_text <= end):
        raise RuntimeError(f"A-share trading calendar does not cover {date_text}: {start}..{end}")

    if now.weekday() >= 5:
        should_capture, reason = False, "weekend"
    elif date_text in set(calendar.get("closed_dates") or []):
        should_capture, reason = False, "exchange_closed"
    else:
        minute = now.hour * 60 + now.minute
        close_grace_minutes = max(1, int(policy.get("close_grace_seconds", 900)) // 60)
        in_morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
        in_afternoon = 13 * 60 <= minute <= 15 * 60
        in_close_grace = 15 * 60 < minute <= 15 * 60 + close_grace_minutes
        should_capture = in_morning or in_afternoon or in_close_grace
        reason = "capture_window" if should_capture else "outside_capture_window"

    set_output("should_capture", "true" if should_capture else "false")
    set_output("reason", reason)
    set_output("market_date", date_text)
    print(json.dumps({"should_capture": should_capture, "reason": reason, "market_date": date_text, "captured_at": now.isoformat(timespec="seconds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
