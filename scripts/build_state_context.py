from __future__ import annotations

import json
import os
from pathlib import Path

from build_intraday_path_features import build as build_intraday_path_features
from state_manager import atomic_json_write, build_dashboard_candidate, build_decision_context


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def main() -> None:
    path_features = build_intraday_path_features(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "intraday_path_features.json", path_features)
    candidate = build_dashboard_candidate(ROOT)
    context = build_decision_context(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "dashboard_update_candidate.json", candidate)
    atomic_json_write(ROOT / "data" / "state" / "decision_context.json", context)
    print(json.dumps({
        "ok": True,
        "account_fact_status": context["account_fact_status"],
        "intraday_path_status": path_features.get("status"),
        "intraday_path_feature_count": len(path_features.get("features") or []),
        "trade_decision_generated": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
