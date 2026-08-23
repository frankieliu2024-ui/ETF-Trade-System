from __future__ import annotations

import json
import os
from pathlib import Path

from state_manager import atomic_json_write, build_dashboard_candidate, build_decision_context


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "dashboard_update_candidate.json", candidate)
    atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)
    print(json.dumps({"ok": True, "account_fact_status": context["account_fact_status"], "trade_decision_generated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
