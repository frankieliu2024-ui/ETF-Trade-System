from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from formal_entry_contract import build_identity

ROOT = Path(__file__).resolve().parents[1]
REQUEST_DIR = ROOT / "requests" / "live_snapshot"
BEIJING = timezone(timedelta(hours=8))
SLOTS = {
    "0 2 * * 1-5": (10, 0),
    "0 3 * * 1-5": (11, 0),
    "30 5 * * 1-5": (13, 30),
    "30 6 * * 1-5": (14, 30),
}


def main() -> int:
    cron = os.environ.get("SCHEDULED_CRON", "").strip()
    slot = SLOTS.get(cron)
    if slot is None:
        print("FORMAL_SCHEDULE_INGRESS=SKIP")
        return 0

    now = datetime.now(BEIJING)
    hour, minute = slot
    market_date = now.date().isoformat()
    occurrence = f"{market_date}T{hour:02d}:{minute:02d}:00+08:00"

    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in REQUEST_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("source") == "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
            and payload.get("occurrence_id") == occurrence
        ):
            print(f"FORMAL_SCHEDULE_INGRESS=EXISTS request_id={payload.get('request_id', '')}")
            return 0

    request_id = f"fd_{now:%Y%m%d}_{hour:02d}{minute:02d}00_github_scheduled_formal_analysis"
    path = REQUEST_DIR / f"{request_id}.json"
    payload = {
        "request_id": request_id,
        "requested_at_beijing": now.isoformat(),
        "request_type": "MARKET_QUOTE_REFRESH",
        "source": "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
        "entry_mode": "AUTOMATIC_SCHEDULED_TASK",
        "task_id": "ETF正式决策",
        "task_run_id": f"ETF_FORMAL_DECISION_{market_date.replace('-', '')}_{hour:02d}{minute:02d}",
        "occurrence_id": occurrence,
        "market_date": market_date,
        "triggered_at_beijing": now.isoformat(),
        "intent": {"query_intent": "FORMAL_INTRADAY_ANALYSIS"},
        "force_refresh": True,
        "require_post_request_snapshot": True,
        "reuse_inflight_refresh": True,
        "duplicate_equivalent_refresh_forbidden": True,
        "ingress_owner": "GITHUB_SCHEDULED_FORMAL_DECISION",
    }
    build_identity(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FORMAL_SCHEDULE_INGRESS=CREATED request_id={request_id} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
