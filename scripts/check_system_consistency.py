from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "state" / "system_consistency.json"
DATA_STANDARD = "ETF与市场监测数据接口使用规范.md"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

FORMAL_FILES = ["ETF规则_MASTER.md", "ETF当前状态_DASHBOARD.md", "ETF交易复盘与经验库_2026.md", "ETF市场行情档案_2026.md"]
CORE_RUNTIME_FILES = [
    "data/state/CURRENT.json", "data/state/runtime_health.json", "data/state/account_fact.json",
    "config/runtime_policy.json", "config/market/market_monitor_config.json", "config/market/provider_priority.json",
    "config/market/etf_monitor_universe.json", "config/market/a_share_trading_calendar_2026.json",
]
CRITICAL_TRACKED_FILES = FORMAL_FILES + [
    DATA_STANDARD, "ETF_SYSTEM_INDEX.md", "scripts/check_system_consistency.py", "scripts/runtime_session_gate.py",
    "scripts/cloud_runner_snapshot.py", "scripts/build_overseas_context.py", "scripts/build_query_context.py",
    ".github/workflows/market-snapshot.yml", ".github/workflows/overseas-preopen-pulse.yml",
    ".github/workflows/system-consistency.yml",
] + CORE_RUNTIME_FILES
EXPECTED_INDICES = {"000001.SH", "399006.SZ", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}
REQUIRED_PROVIDERS = {"hithink_finance", "yahoo_chart_api"}


def read_text(path: str) -> str:
    p = ROOT / path
    if not p.exists():
        raise FileNotFoundError(path)
    return p.read_text(encoding="utf-8")


def read_json(path: str) -> dict:
    return json.loads(read_text(path))


def run_git(*args: str) -> tuple[int, str]:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


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
    check(f"data_standard:{DATA_STANDARD}", (ROOT / DATA_STANDARD).exists(), "root-level standard exists" if (ROOT / DATA_STANDARD).exists() else "missing")
    for path in CORE_RUNTIME_FILES:
        check(f"runtime_file:{path}", (ROOT / path).exists(), "exists" if (ROOT / path).exists() else "missing core runtime dependency")

    rc, head_sha = run_git("rev-parse", "HEAD")
    check("git:head_commit", rc == 0 and bool(re.fullmatch(r"[0-9a-f]{40}", head_sha)), f"HEAD={head_sha or 'UNAVAILABLE'}")
    github_sha = os.environ.get("GITHUB_SHA", "")
    if github_sha:
        check("git:workflow_sha_matches_head", head_sha == github_sha, f"HEAD={head_sha} GITHUB_SHA={github_sha}")
    rc, tracked = run_git("ls-files")
    tracked_set = set(tracked.splitlines()) if rc == 0 else set()
    missing_tracked = [p for p in CRITICAL_TRACKED_FILES if p not in tracked_set]
    check("git:critical_files_tracked", not missing_tracked, f"missing_tracked={missing_tracked}")
    rc, dirty = run_git("status", "--porcelain", "--untracked-files=all")
    check("git:working_tree_clean_before_check", rc == 0 and not dirty, "clean" if not dirty else f"dirty={dirty[:500]}")

    index_text = read_text("ETF_SYSTEM_INDEX.md")
    for path in FORMAL_FILES:
        check(f"index_entry:{path}", f"`{path}`" in index_text, "canonical entry present" if f"`{path}`" in index_text else "missing")
    check("index_entry:data_standard", DATA_STANDARD in index_text, "data standard entry present")

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
    dashboard_codes = codes_from_dashboard(read_text("ETF当前状态_DASHBOARD.md"))
    check("etf_universe:dashboard_match", dashboard_codes == universe_codes, f"dashboard={sorted(dashboard_codes)} runtime={sorted(universe_codes)}")

    master = read_text("ETF规则_MASTER.md")
    check("master:three_layers", "云端市场监测固定为三层" in master, "three-layer rule present")
    check("master:holding_observation", "持仓ETF＋观察ETF" in master or "持仓ETF+观察ETF" in master, "holding/observation taxonomy present")
    stale_pool = "统一研究池固定为" in master or ("八ETF" in master and "每个自动或人工决策节点先读取八ETF" in master)
    check("master:no_parallel_fixed_pool", not stale_pool, "no stale fixed-pool taxonomy")

    runner = read_text("scripts/cloud_runner_snapshot.py")
    check("runner:canonical_etf_universe", "etf_monitor_universe.json" in runner and "load_etf_universe" in runner, "runner loads canonical ETF universe")
    check("runner:no_hardcoded_etf_list", "ETF = [" not in runner, "no hardcoded ETF list")
    check("runner:opening_auction_window", "9 * 60 + 15" in runner and "OPENING_CALL_AUCTION" in runner, "09:15 opening-auction capture is implemented")
    check("runner:beijing_timestamp", "captured_at_beijing" in runner, "A-share snapshots expose Beijing timestamp")

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
    check("data_standard:pulse_principle", "10分钟是常规采集目标，不是决策时钟" in standard, "pulse principle documented")
    check("data_standard:preopen_start", all(x in standard for x in ["北京时间08:00", "北京时间09:15", "OPENING_CALL_AUCTION"]), "Asia pre-open and A-share auction start documented")
    check("data_standard:mandatory_timestamp", "正式输出强制时间戳" in standard and "as_of_beijing" in standard and "数据时点（北京时间）" in standard, "mandatory Beijing-time output documented")
    check("data_standard:consistency_scope", "代码与提交完整性" in standard and "运行链" in standard and "Git跟踪状态" in standard, "consistency extends beyond text")

    runtime = read_json("config/runtime_policy.json")
    for key in ["target_cadence_seconds", "fresh_max_age_seconds", "degraded_max_age_seconds", "close_grace_seconds", "provider_timeout_seconds", "provider_retry_limit", "provider_max_workers"]:
        check(f"pulse_policy:{key}", standard_number(standard, key) == runtime.get(key), f"standard={standard_number(standard, key)} runtime={runtime.get(key)}")
    check("pulse_policy:workflow_timeout", "workflow单次运行最长6分钟" in standard and int(runtime.get("workflow_timeout_minutes", 0)) == 6, f"runtime={runtime.get('workflow_timeout_minutes')}")

    calendar = read_json("config/market/a_share_trading_calendar_2026.json")
    today_sh = datetime.now(SHANGHAI).date().isoformat()
    start, end = str(calendar.get("coverage_start", "")), str(calendar.get("coverage_end", ""))
    check("trading_calendar:coverage", bool(start and end and start <= today_sh <= end), f"today={today_sh} coverage={start}..{end}")
    check("trading_calendar:official_source", calendar.get("source", {}).get("authority") == "Shanghai Stock Exchange", f"authority={calendar.get('source', {}).get('authority')}")

    query = read_text("scripts/build_query_context.py")
    check("query:etf_universe_read", "etf_monitor_universe" in query, "query context reads ETF universe")
    check("query:stock_market_read", "stock_market_context" in query, "query context reads stock market context")
    check("query:data_standard_read", DATA_STANDARD in query, "query context references data standard")

    overseas_builder = read_text("scripts/build_overseas_context.py")
    check("overseas:beijing_timestamp", "as_of_beijing" in overseas_builder and "generated_at_beijing" in overseas_builder, "overseas context exposes Beijing timestamps")
    check("overseas:market_phase", "market_phase_at_generation" in overseas_builder, "overseas market phase retained")

    session_gate = read_text("scripts/runtime_session_gate.py")
    check("session_gate:calendar_read", "a_share_trading_calendar_2026.json" in session_gate, "session gate reads official exchange calendar")
    check("session_gate:opening_auction", "9 * 60 + 15" in session_gate and "OPENING_CALL_AUCTION" in session_gate, "session gate opens at 09:15")

    workflow = read_text(".github/workflows/market-snapshot.yml")
    check("workflow:consistency_preflight", "check_system_consistency.py" in workflow, "preflight consistency gate present")
    check("workflow:session_gate", "runtime_session_gate.py" in workflow and "session_gate.outputs.should_capture" in workflow, "exchange calendar/session gate wired")
    check("workflow:auction_cron", '15,20,25,30,40,50 1 * * 1-5' in workflow, "09:15/09:20/09:25 auction pulses scheduled")
    check("workflow:valid_snapshot_downstream_gate", "snapshot_result.outputs.snapshot_written == 'true'" in workflow, "downstream contexts require new valid A-share snapshot")

    overseas_workflow = read_text(".github/workflows/overseas-preopen-pulse.yml")
    check("workflow:overseas_preopen_exists", "build_overseas_context.py" in overseas_workflow, "standalone overseas pre-open pulse wired")
    check("workflow:overseas_0800_start", '*/10 0 * * 1-5' in overseas_workflow, "Beijing 08:00-08:50 overseas pulses scheduled")
    check("workflow:overseas_0900_0910", '0,10 1 * * 1-5' in overseas_workflow, "Beijing 09:00/09:10 overseas pulses scheduled")

    maintenance_workflow = read_text(".github/workflows/system-consistency.yml")
    check("maintenance_workflow:data_standard_trigger", DATA_STANDARD in maintenance_workflow, "data standard changes trigger consistency workflow")
    check("maintenance_workflow:workflow_trigger", ".github/workflows/**" in maintenance_workflow, "workflow changes trigger consistency workflow")

    runtime_health = read_json("data/state/runtime_health.json")
    check("runtime_health:structured", bool(runtime_health.get("status")), f"status={runtime_health.get('status', 'MISSING')}")

    account = read_json("data/state/account_fact.json")
    check("account_fact:current_availability", account.get("status") == "VALID", f"status={account.get('status', 'MISSING')} (state warning only)", warning=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "repository": {"head_sha": head_sha, "github_sha": github_sha, "github_run_id": os.environ.get("GITHUB_RUN_ID", ""), "github_ref": os.environ.get("GITHUB_REF", "")},
        "status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
        "hard_error_count": len(errors), "warning_count": len(warnings),
        "checks": checks, "errors": errors, "warnings": warnings,
        "principle": "一致性检查覆盖文本口径、配置、运行链、关键状态文件、Git跟踪/HEAD提交、workflow接线、数据时点字段和自动验收结果；硬冲突不得进入生产。",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
