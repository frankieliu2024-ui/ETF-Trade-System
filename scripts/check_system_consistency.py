from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"

FORMAL_FILES = [
    "ETF规则_MASTER.md",
    "ETF当前状态_DASHBOARD.md",
    "ETF交易复盘与经验库_2026.md",
    "ETF市场行情档案_2026.md",
]
EXPECTED_INDICES = {"000001.SH", "399006.SZ", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}


def read_text(path: str) -> str:
    p = ROOT / path
    if not p.exists():
        raise FileNotFoundError(path)
    return p.read_text(encoding="utf-8")


def read_json(path: str) -> dict:
    return json.loads(read_text(path))


def codes_from_dashboard(text: str) -> set[str]:
    match = re.search(r"\|ETF层当前结构\|(.*?)\|\n", text)
    if not match:
        return set()
    return set(re.findall(r"（(\d{6})）", match.group(1)))


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, *, warning: bool = False) -> None:
        checks.append({"name": name, "status": "PASS" if ok else ("WARNING" if warning else "FAIL"), "detail": detail})
        if not ok:
            (warnings if warning else errors).append(f"{name}: {detail}")

    for path in FORMAL_FILES:
        check(f"formal_file:{path}", (ROOT / path).exists(), "exists" if (ROOT / path).exists() else "missing")

    index_text = read_text("ETF_SYSTEM_INDEX.md")
    for path in FORMAL_FILES:
        check(f"index_entry:{path}", f"`{path}`" in index_text, "canonical entry present" if f"`{path}`" in index_text else "canonical entry missing")

    market_cfg = read_json("config/market/market_monitor_config.json")
    formal_indices = set(market_cfg.get("formal_index_layer", {}).get("required_objects", []))
    monitor_indices = set(market_cfg.get("monitoring_layers", {}).get("index_monitor", {}).get("required_objects", []))
    check("index_layer:formal_vs_expected", formal_indices == EXPECTED_INDICES, f"actual={sorted(formal_indices)} expected={sorted(EXPECTED_INDICES)}")
    check("index_layer:monitor_vs_formal", monitor_indices == formal_indices, f"monitor={sorted(monitor_indices)} formal={sorted(formal_indices)}")

    universe = read_json("config/market/etf_monitor_universe.json")
    objects = universe.get("objects") or []
    universe_codes = {str(x.get("code", "")) for x in objects if x.get("code")}
    universe_thscodes = [str(x.get("thscode", "")) for x in objects]
    check("etf_universe:nonempty", bool(universe_codes), f"count={len(universe_codes)}")
    check("etf_universe:no_duplicates", len(universe_codes) == len(objects), f"objects={len(objects)} unique_codes={len(universe_codes)}")
    check("etf_universe:thscode_complete", all(universe_thscodes) and len(universe_thscodes) == len(objects), "all objects have thscode")

    dashboard = read_text("ETF当前状态_DASHBOARD.md")
    dashboard_codes = codes_from_dashboard(dashboard)
    check("etf_universe:dashboard_match", dashboard_codes == universe_codes, f"dashboard={sorted(dashboard_codes)} runtime={sorted(universe_codes)}")

    master = read_text("ETF规则_MASTER.md")
    check("master:three_layers", "云端市场监测固定为三层" in master, "three-layer rule present")
    check("master:holding_observation", "持仓ETF＋观察ETF" in master or "持仓ETF+观察ETF" in master, "holding/observation taxonomy present")
    stale_pool = "统一研究池固定为" in master or "八ETF" in master and "每个自动或人工决策节点先读取八ETF" in master
    check("master:no_parallel_fixed_pool", not stale_pool, "no stale fixed-pool taxonomy" if not stale_pool else "stale fixed research-pool wording remains")

    runner = read_text("scripts/cloud_runner_snapshot.py")
    check("runner:canonical_etf_universe", "etf_monitor_universe.json" in runner and "load_etf_universe" in runner, "runner loads canonical ETF universe")
    check("runner:no_hardcoded_etf_list", "ETF = [" not in runner, "no hardcoded ETF list")

    stock_cfg = market_cfg.get("monitoring_layers", {}).get("stock_monitor", {})
    check("stock_layer:no_fixed_default_codes", stock_cfg.get("fixed_default_codes") == [], f"fixed_default_codes={stock_cfg.get('fixed_default_codes')}")
    check("stock_layer:dynamic_mode", stock_cfg.get("mode") == "DYNAMIC_ACCOUNT_PLUS_QUERY_TIME_INDUSTRY", f"mode={stock_cfg.get('mode')}")

    query = read_text("scripts/build_query_context.py")
    check("query:etf_universe_read", "etf_monitor_universe" in query, "query context reads ETF universe")
    check("query:stock_market_read", "stock_market_context" in query, "query context reads stock market context")

    workflow = read_text(".github/workflows/market-snapshot.yml")
    check("workflow:consistency_preflight", "check_system_consistency.py" in workflow, "preflight consistency gate present")

    account_path = ROOT / "data" / "state" / "account_fact.json"
    if account_path.exists():
        account = json.loads(account_path.read_text(encoding="utf-8"))
        check("account_fact:current_availability", account.get("status") == "VALID", f"status={account.get('status', 'MISSING')} (state warning only)", warning=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
        "hard_error_count": len(errors),
        "warning_count": len(warnings),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "principle": "任何规则、Dashboard、监测对象、数据入口、运行脚本或workflow相关更新后，一致性检查是基础验收步骤；硬冲突不得进入生产，账户缺失等正常状态只告警。",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
