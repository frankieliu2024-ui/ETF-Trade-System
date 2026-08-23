from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, build_decision_context, now_utc, read_account_fact, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    account = read_account_fact(root)
    decision = build_decision_context(root)
    return {
        "generated_at": now_utc(), "market_date": current.get("market_date", ""),
        "latest_valid_node": current.get("latest_valid_node", ""), "current": current,
        "decision_context": decision, "data_status": current.get("data_freshness", {}),
        "account_fact_status": account["status"], "needs_account_screenshot": account["status"] != "VALID",
        "read_only": True, "interaction_boundary": "用户主动查询读取最新状态；正式交易判断仍需人工确认，不生成自动交易输出。",
    }


def main() -> None:
    context = build(ROOT)
    atomic_json_write(ROOT / "data" / "state" / "query_context.json", context)
    print(json.dumps({"ok": True, "market_date": context["market_date"], "latest_valid_node": context["latest_valid_node"], "account_fact_status": context["account_fact_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
