from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"
DATA_STANDARD = "ETF与市场监测数据接口使用规范.md"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

FORMAL_FILES = [
    "ETF规则_MASTER.md",
    "ETF当前状态_DASHBOARD.md",
    "ETF交易复盘与经验库_2026.md",
    "ETF市场行情档案_2026.md",
]
CORE_RUNTIME_FILES = [
    "data/state/CURRENT.json",
    "data/state/runtime_health.json",
    "data/state/account_fact.json",
    "config/runtime_policy.json",
    "config/market/market_monitor_config.json",
    "config/market/provider_priority.json",
    "config/market/etf_monitor_universe.json",
    "config/market/a_share_trading_calendar_2026.json",
]
EXPECTED_INDICES = {"000001.SH", "399006.SZ", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}
REQUIRED_PROVIDERS = {"hithink_finance", "yahoo_chart_api"}


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


def standard_number(text: str, key: str) -> int | None:
    match = re.search(rf"`{re.escape(key)}\s*=\s*(\d+)`", text)
    return int(match.group(1)) if match else None


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
    check(f"data_standard:{DATA_STANDARD}", (ROOT / DATA_STANDARD).exists(), "root-level standard exists" if (ROOT / DATA_STANDARD).exists() else "missing root-level data standard")
    for path in CORE_RUNTIME_FILES:
        check(f"runtime_file:{path}", (ROOT / path).exists(), "exists" if (ROOT / path).exists() else "missing core runtime dependency")

    index_text = read_text("ETF_SYSTEM_INDEX.md")
    for path in FORMAL_FILES:
        check(f"index_entry:{path}", f"`{path}`" in index_text, "canonical entry present" if f"`{path}`" in index_text else "canonical entry missing")
    check("index_entry:data_standard", DATA_STANDARD in index_text, "data standard entry present" if DATA_STANDARD in index_text else "data standard missing from system index")

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

    provider_cfg = read_json("config/market/provider_priority.json")
    providers = set((provider_cfg.get("providers") or {}).keys())
    check("providers:required_sources", REQUIRED_PROVIDERS.issubset(providers), f"required={sorted(REQUIRED_PROVIDERS)} actual={sorted(providers)}")
    check("providers:formal_indices", set(provider_cfg.get("formal_index_objects") or []) == EXPECTED_INDICES, f"provider formal indices={provider_cfg.get('formal_index_objects')}")

    standard = read_text(DATA_STANDARD)
    check("data_standard:three_layers", all(x in standard for x in ["第一层：指数", "第二层：ETF", "第三层：个股"]), "three-layer structure documented")
    check("data_standard:multi_provider", "hithink-finance" in standard and "Yahoo Chart API" in standard, "Hithink and Yahoo documented")
    check("data_standard:pulse_principle", "10分钟是采集目标，不是决策时钟" in standard, "pulse principle documented")
    check("data_standard:time_alignment", all(x in standard for x in ["market_timezone", "as_of", "market_phase", "time_relation_to_a_share"]), "cross-market time fields documented")
    check("data_standard:consistency_gate", "check_system_consistency.py" in standard and "一致性检查是基础验收步骤" in standard, "maintenance consistency gate documented")
    check("data_standard:exchange_calendar", "a_share_trading_calendar_2026.json" in standard and "交易所官方休市" in standard, "exchange-calendar gate documented")
    check("data_standard:valid_snapshot_gate", "只有新有效快照" in standard, "downstream context build requires a new valid snapshot")

    runtime = read_json("config/runtime_policy.json")
    pulse_keys = [
        "target_cadence_seconds",
        "fresh_max_age_seconds",
        "degraded_max_age_seconds",
        "close_grace_seconds",
        "provider_timeout_seconds",
        "provider_retry_limit",
        "provider_max_workers",
    ]
    for key in pulse_keys:
        documented = standard_number(standard, key)
        actual = runtime.get(key)
        check(f"pulse_policy:{key}", documented == actual, f"standard={documented} runtime={actual}")
    check("pulse_policy:workflow_timeout", "workflow单次运行最长6分钟" in standard and int(runtime.get("workflow_timeout_minutes", 0)) == 6, f"runtime={runtime.get('workflow_timeout_minutes')}")

    calendar = read_json("config/market/a_share_trading_calendar_2026.json")
    today_sh = datetime.now(SHANGHAI).date().isoformat()
    coverage_start = str(calendar.get("coverage_start", ""))
    coverage_end = str(calendar.get("coverage_end", ""))
    check("trading_calendar:coverage", bool(coverage_start and coverage_end and coverage_start <= today_sh <= coverage_end), f"today={today_sh} coverage={coverage_start}..{coverage_end}")
    check("trading_calendar:official_source", calendar.get("source", {}).get("authority") == "Shanghai Stock Exchange", f"authority={calendar.get('source', {}).get('authority')}")
    check("trading_calendar:closed_dates", isinstance(calendar.get("closed_dates"), list) and len(calendar.get("closed_dates") or []) > 0, f"closed_date_count={len(calendar.get('closed_dates') or [])}")

    query = read_text("scripts/build_query_context.py")
    check("query:etf_universe_read", "etf_monitor_universe" in query, "query context reads ETF universe")
    check("query:stock_market_read", "stock_market_context" in query, "query context reads stock market context")
    check("query:data_standard_read", DATA_STANDARD in query, "query context references data standard")

    session_gate = read_text("scripts/runtime_session_gate.py")
    check("session_gate:calendar_read", "a_share_trading_calendar_2026.json" in session_gate, "session gate reads official exchange calendar")
    check("session_gate:close_grace", "close_grace_seconds" in session_gate, "session gate honors close grace")

    workflow = read_text(".github/workflows/market-snapshot.yml")
    check("workflow:consistency_preflight", "check_system_consistency.py" in workflow, "preflight consistency gate present")
    check("workflow:session_gate", "runtime_session_gate.py" in workflow and "session_gate.outputs.should_capture" in workflow, "exchange calendar/session gate wired")
    check("workflow:no_broad_outside_session_cron", '*/10 1-7 * * 1-5' not in workflow, "broad 09:00-15:50 trigger removed")
    check("workflow:valid_snapshot_downstream_gate", "snapshot_result.outputs.snapshot_written == 'true'" in workflow, "downstream context builds only after a new valid snapshot")

    maintenance_workflow = read_text(".github/workflows/system-consistency.yml")
    check("maintenance_workflow:data_standard_trigger", DATA_STANDARD in maintenance_workflow, "data standard changes trigger consistency workflow")
    check("maintenance_workflow:calendar_trigger", "config/market/**" in maintenance_workflow, "calendar/config changes trigger consistency workflow")

    runtime_health = read_json("data/state/runtime_health.json")
    check("runtime_health:structured", bool(runtime_health.get("status")), f"status={runtime_health.get('status', 'MISSING')}")

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
        "principle": "任何规则、Dashboard、数据接口规范、监测对象、数据源、交易日历、脉冲参数、数据入口、运行脚本或workflow相关更新后，一致性检查是基础验收步骤；硬冲突不得进入生产，账户缺失等正常状态只告警。",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
