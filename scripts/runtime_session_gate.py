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


def market_phase(minute: int, close_grace_minutes: int = 15) -> str:
    # 09:25-09:29 is the post-auction/pre-continuous static window. A delayed
    # GitHub Actions runner may still capture the final opening-auction result
    # there, but it must not be interpreted as continuous-trading execution.
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "OPENING_CALL_AUCTION"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "CONTINUOUS_MORNING"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "CONTINUOUS_AFTERNOON"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "CLOSING_CALL_AUCTION"
    if 15 * 60 < minute <= 15 * 60 + max(1, close_grace_minutes):
        return "POST_CLOSE_GRACE"
    return "OUTSIDE_SESSION"


def main() -> int:
    now = datetime.now(SHANGHAI)
    date_text = now.date().isoformat()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    policy = load_json(ROOT / "config" / "runtime_policy.json")
    calendar = load_json(ROOT / "config" / "market" / "a_share_trading_calendar_2026.json"))

    start = calendar.get("coverage_start", "")
    end = calendar.get("coverage_end", "")
    if not start or not end or not (start <= date_text <= end):
        raise RuntimeError(f"A-share trading calendar does not cover {date_text}: {start}..{end}")

    close_grace_minutes = max(1, int(policy.get("close_grace_seconds", 900)) // 60)
    minute = now.hour * 60 + now.minute
    phase = market_phase(minute, close_grace_minutes)
    if now.weekday() >= 5:
        should_capture, reason = False, "weekend"
    elif date_text in set(calendar.get("closed_dates") or []):
        should_capture, reason = False, "exchange_closed"
    else:
        in_opening_auction = 9 * 60 + 15 <= minute < 9 * 60 + 30
        in_morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
        in_afternoon = 13 * 60 <= minute <= 15 * 60
        in_close_grace = 15 * 60 < minute <= 15 * 60 + close_grace_minutes
        should_capture = in_opening_auction or in_morning or in_afternoon or in_close_grace
        reason = "capture_window" if should_capture else "outside_capture_window"

    if event_name == "workflow_dispatch":
        reason = "manual_dispatch_capture_window" if should_capture else f"manual_dispatch_{reason}"

    set_output("should_capture", "true" if should_capture else "false")
    set_output("reason", reason)
    set_output("market_date", date_text)
    set_output("market_phase", phase)
    print(json.dumps({"should_capture": should_capture, "reason": reason, "market_date": date_text, "market_phase": phase, "captured_at_beijing": now.isoformat(timespec="seconds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
