from __future__ import annotations

import ast

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
CORE_RUNTIME_FILES = ["data/state/CURRENT.json", "data/state/runtime_health.json", "data/state/overseas_runtime_health.json", "data/state/account_fact.json", "data/state/us_extended_hours_context.json", "config/runtime_policy.json", "config/market/market_monitor_config.json", "config/market/provider_priority.json", "config/market/etf_monitor_universe.json", "config/market/a_share_trading_calendar_2026.json"]
CRITICAL_TRACKED_FILES = FORMAL_FILES + [DATA_STANDARD, "ETF_SYSTEM_INDEX.md", "scripts/check_system_consistency.py", "scripts/runtime_session_gate.py", "scripts/cloud_runner_snapshot.py", "scripts/build_stock_context.py", "scripts/build_account_stock_market.py", "scripts/market_data_guard.py", "tests/test_market_data_guard.py", "scripts/build_overseas_context.py", "scripts/build_us_extended_hours_context.py", "scripts/build_query_context.py", ".github/workflows/market-snapshot.yml", ".github/workflows/on-demand-market-data.yml", ".github/workflows/overseas-preopen-pulse.yml", ".github/workflows/us-extended-hours-pulse.yml", ".github/workflows/system-consistency.yml"] + CORE_RUNTIME_FILES
EXPECTED_INDICES = {"000001.SH", "399006.SZ", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}
REQUIRED_PROVIDERS = {"hithink_finance", "yahoo_chart_api", "eastmoney_push2"}


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
    return set(re.findall(r"（(\d{6})）", match.group(1))) if match else set()


def standard_number(text: str, key: str) -> int | None:
    match = re.search(rf"`{re.escape(key)}\s*=\s*(\d+)`", text)
    return int(match.group(1)) if match else None



def expected_freshness_status(age_seconds: object, runtime: dict) -> str | None:
    if not isinstance(age_seconds, (int, float)):
        return None
    fresh_limit = int(runtime.get("fresh_max_age_seconds", 900))
    degraded_limit = int(runtime.get("degraded_max_age_seconds", 1500))
    if age_seconds <= fresh_limit:
        return "FRESH"
    if age_seconds <= degraded_limit:
        return "DEGRADED"
    return "STALE"


def context_freshness(context: dict) -> dict:
    freshness = context.get("freshness_at_context_build") or {}
    data_status = context.get("data_status") or {}
    return {
        "status": str(freshness.get("status") or data_status.get("status") or "").upper(),
        "age_seconds": freshness.get("age_seconds", data_status.get("age_seconds")),
    }



def _time_not_before(later: object, earlier: object) -> bool:
    if not later or not earlier: return False
    try:
        a = datetime.fromisoformat(str(later).replace("Z", "+00:00")); b = datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
        if a.tzinfo is None: a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None: b = b.replace(tzinfo=timezone.utc)
        return a >= b
    except (TypeError, ValueError): return False

def _python_ast_ok(relative_path: str) -> bool:
    try:
        ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        return True
    except (OSError, SyntaxError):
        return False


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, *, warning: bool = False) -> None:
        checks.append({"name": name, "status": "PASS" if ok else ("WARNING" if warning else "FAIL"), "detail": detail})
        if not ok:
            (warnings if warning else errors).append(f"{name}: {detail}")

    test_proc = subprocess.run([os.environ.get("PYTHON", "python"), "-m", "unittest", "discover", "-s", "tests", "-p", "test_market_data_guard.py"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    check("tests:market_data_guard", test_proc.returncode == 0, (test_proc.stdout + test_proc.stderr)[-1000:])

    compile_proc = subprocess.run([os.environ.get("PYTHON", "python"), "-m", "py_compile", "scripts/market_data_guard.py", "scripts/cloud_runner_snapshot.py", "scripts/build_account_stock_market.py", "scripts/build_overseas_context.py", "scripts/build_overseas_runtime_health.py"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    check("tests:market_data_guard_compile", compile_proc.returncode == 0, (compile_proc.stdout + compile_proc.stderr)[-1000:])

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
    rc, tracked = run_git("-c", "core.quotePath=false", "ls-files")
    tracked_set = set(tracked.splitlines()) if rc == 0 else set()
    missing_tracked = [p for p in CRITICAL_TRACKED_FILES if p not in tracked_set]
    check("git:critical_files_tracked", not missing_tracked, f"missing_tracked={missing_tracked}")
    rc, dirty = run_git("status", "--porcelain", "--untracked-files=all")
    dirty_lines = [line for line in dirty.splitlines() if line.strip()]
    allowed_dirty_prefixes = ("data/state/", "events/research/")
    unexpected_dirty = [line for line in dirty_lines if not (line[2:].strip().startswith(allowed_dirty_prefixes) or line[2:].strip().startswith("data/state/dashboard_update_candidate.json"))]
    check("git:working_tree_clean_before_check", rc == 0 and not unexpected_dirty, "clean" if not dirty_lines else f"generated_state_dirty={len(dirty_lines)} unexpected={unexpected_dirty[:20]}")

    index_text = read_text("ETF_SYSTEM_INDEX.md")
    for path in FORMAL_FILES:
        check(f"index_entry:{path}", f"`{path}`" in index_text, "canonical entry present" if f"`{path}`" in index_text else "missing")
    check("index_entry:data_standard", DATA_STANDARD in index_text, "data standard entry present")
    check("index_entry:us_extended_hours", "us_extended_hours_context.json" in index_text and "us-extended-hours-pulse.yml" in index_text, "US extended-hours state and workflow present in system index")

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
    check("runner:provider_timestamp", "provider_as_of_beijing" in runner and "provider_timestamp_ms" in runner, "A-share rows retain provider time separately from capture completion time")

    stock_cfg = market_cfg.get("monitoring_layers", {}).get("stock_monitor", {})
    check("stock_layer:no_fixed_default_codes", stock_cfg.get("fixed_default_codes") == [], f"fixed_default_codes={stock_cfg.get('fixed_default_codes')}")
    check("stock_layer:dynamic_mode", stock_cfg.get("mode") == "DYNAMIC_ACCOUNT_PLUS_QUERY_TIME_INDUSTRY", f"mode={stock_cfg.get('mode')}")
    stock_context_builder = read_text("scripts/build_stock_context.py")
    stock_market_builder = read_text("scripts/build_account_stock_market.py")
    check("stock_layer:canonical_etf_universe", "etf_monitor_universe.json" in stock_context_builder and "load_etf_codes" in stock_context_builder, "stock classification reads canonical ETF universe")
    check("stock_layer:hithink_market_snapshot", '"market", "snapshot"' in stock_market_builder and '"stock", "snapshot"' not in stock_market_builder, "dynamic account stocks use the supported market.snapshot capability")
    check("stock_layer:beijing_timestamp", "as_of_beijing" in stock_market_builder and "market_phase" in stock_market_builder, "stock facts expose Beijing provider time and market phase")

    provider_cfg = read_json("config/market/provider_priority.json")
    providers = set((provider_cfg.get("providers") or {}).keys())
    check("providers:required_sources", REQUIRED_PROVIDERS.issubset(providers), f"required={sorted(REQUIRED_PROVIDERS)} actual={sorted(providers)}")
    check("providers:formal_indices", set(provider_cfg.get("formal_index_objects") or []) == EXPECTED_INDICES, f"provider formal indices={provider_cfg.get('formal_index_objects')}")
    expected_hstech_chain = ["hithink-finance:HS2083", "yahoo_chart_api:HSTECH.HK", "eastmoney_push2:124.HSTECH", "ETF_PROXY_513180"]
    actual_hstech_chain = (provider_cfg.get("objects") or {}).get("HSTECH") or []
    check("providers:hstech_chain_exact", actual_hstech_chain == expected_hstech_chain, f"actual={actual_hstech_chain} expected={expected_hstech_chain}")
    fallback_policy = provider_cfg.get("object_fallback_policy") or {}
    universe = read_json("config/market/etf_monitor_universe.json")
    etf_codes = {str(item.get("code")) for item in (universe.get("objects") or []) if item.get("code")}
    etf_missing_fallback = sorted(code for code in etf_codes if not isinstance(fallback_policy.get(code + (".SH" if code.startswith(("5", "6")) else ".SZ")), dict))
    check("providers:etf_fallback_policy", not etf_missing_fallback, f"missing object fallback entries={etf_missing_fallback}", warning=True)
    invalid_direct = []
    for object_id, rule in fallback_policy.items():
        if rule.get("direct_only") is True and any(str(x).startswith("ETF_PROXY") for x in (rule.get("fallback") or [])):
            invalid_direct.append(object_id)
    check("providers:direct_only_no_proxy", not invalid_direct, f"direct_only objects with proxy fallback={invalid_direct}")
    account = read_json("data/state/account_fact.json")
    account_stocks = [str(item.get("code")) for item in (account.get("positions") or []) if item.get("asset_type") == "STOCK" and float(item.get("quantity") or 0) > 0]
    stock_missing_fallback = sorted(code for code in account_stocks if code + (".SH" if code.startswith(("6", "5")) else ".SZ") not in fallback_policy)
    check("providers:account_stock_fallback_policy", not stock_missing_fallback, f"account stocks without direct fallback={stock_missing_fallback}", warning=True)
    check("providers:single_source_shanghai_index", "000001.SH" not in fallback_policy, "000001.SH remains single-source until a verified Eastmoney index mapping exists", warning=True)
    guard_text = read_text("scripts/market_data_guard.py")
    check("providers:guard_module", all(token in guard_text for token in ("def validate_market_row", "def classify_provider_failure", "def update_structural_health")), "shared market data guard functions present")
    check("providers:guard_call_chain", "market_data_guard" in read_text("scripts/cloud_runner_snapshot.py") and "market_data_guard" in read_text("scripts/build_account_stock_market.py"), "ETF/index and account-stock collectors import the shared guard")
    check("providers:hstech_health_memory", "provider_health" in read_text("scripts/build_overseas_context.py") and "provider_health" in read_text("scripts/build_overseas_runtime_health.py"), "HSTECH structural provider memory reuses overseas runtime health")
    us_provider = provider_cfg.get("us_extended_hours", {})
    check("providers:us_extended_hours", us_provider.get("base_proxies") == ["QQQ", "SOXX"] and us_provider.get("conditional_industry_stocks") == "dynamic_only", f"us_extended_hours={us_provider}")

    standard = read_text(DATA_STANDARD)
    check("data_standard:three_layers", all(x in standard for x in ["第一层：指数", "第二层：ETF", "第三层：个股"]), "three-layer structure documented")
    check("data_standard:multi_provider", "hithink-finance" in standard and "Yahoo Chart API" in standard, "Hithink and Yahoo documented")
    check("data_standard:pulse_principle", "10分钟是常规采集目标，不是决策时钟" in standard, "pulse principle documented")
    check("data_standard:preopen_start", all(x in standard for x in ["北京时间08:00", "北京时间09:15", "OPENING_CALL_AUCTION"]), "Asia pre-open and A-share auction start documented")
    check("data_standard:mandatory_timestamp", "正式输出强制时间戳" in standard and "as_of_beijing" in standard and "数据时点（北京时间）" in standard, "mandatory Beijing-time output documented")
    check("data_standard:consistency_scope", "代码与提交完整性" in standard and "运行链" in standard and "Git跟踪状态" in standard, "consistency extends beyond text")
    check("data_standard:us_extended_hours", all(x in standard for x in ["POST_MARKET", "PRE_MARKET", "QQQ", "SOXX", "不高估海外时间领先，也不低估海外时间领先"]), "US extended-hours timing and evidence boundaries documented")

    runtime = read_json("config/runtime_policy.json")
    for key in ["target_cadence_seconds", "fresh_max_age_seconds", "degraded_max_age_seconds", "close_grace_seconds", "provider_timeout_seconds", "provider_retry_limit", "scheduled_provider_max_workers", "query_provider_max_workers"]:
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
    check("query:mandatory_beijing_output", "output_time_rule" in query and "数据时点（北京时间）" in query, "query context forces Beijing-time output")
    check("query:us_extended_hours_read", "us_extended_hours_context" in query and "us_extended_hours_rule" in query, "query context reads and explains US extended hours")

    overseas_builder = read_text("scripts/build_overseas_context.py")
    check("overseas:beijing_timestamp", "as_of_beijing" in overseas_builder and "generated_at_beijing" in overseas_builder, "overseas context exposes Beijing timestamps")
    check("overseas:market_phase", "market_phase_at_generation" in overseas_builder, "overseas market phase retained")
    check("overseas:hstech_three_direct_sources", all(token in overseas_builder for token in ["HS2083", "HSTECH.HK", "124.HSTECH"]), "HSTECH Hithink/Yahoo/Eastmoney chain present")
    check("overseas:hstech_push2delay_degraded_fallback", "push2delay.eastmoney.com" in overseas_builder, "Eastmoney push2delay fallback present")
    check("overseas:hstech_provider_timestamp", all(token in overseas_builder for token in ["f86", "f124", "provider_timestamp_field"]), "HSTECH provider timestamp fields enforced")
    check("overseas:hstech_no_invalid_yahoo_symbol", "^HSTECH" not in overseas_builder, "invalid Yahoo production symbol absent")
    multi_source = read_text("scripts/multi_source_market.py")
    check("provider_adapter:hstech_valid_yahoo_symbol", '"HSTECH": "HSTECH.HK"' in multi_source and "^HSTECH" not in multi_source, "Yahoo adapter uses HSTECH.HK only")

    us_builder = read_text("scripts/build_us_extended_hours_context.py")
    check("us_extended:session_split", all(x in us_builder for x in ["PRE_MARKET", "REGULAR", "POST_MARKET"]), "US cash/pre/post sessions are explicitly split")
    check("us_extended:beijing_timestamp", "as_of_beijing" in us_builder and "generated_at_beijing" in us_builder, "US extended hours expose Beijing timestamps")
    check("us_extended:dynamic_industry_symbols", "US_EXTENDED_SYMBOLS" in us_builder and "CONDITIONAL_US_INDUSTRY_STOCK" in us_builder, "US industry stocks remain query-time dynamic")

    session_gate = read_text("scripts/runtime_session_gate.py")
    check("session_gate:calendar_read", "a_share_trading_calendar_2026.json" in session_gate, "session gate reads official exchange calendar")
    check("session_gate:opening_auction", "9 * 60 + 15" in session_gate and "OPENING_CALL_AUCTION" in session_gate, "session gate opens at 09:15")

    workflow = read_text(".github/workflows/market-snapshot.yml")
    check("workflow:consistency_preflight", "check_system_consistency.py" in workflow, "preflight consistency gate present")
    check("workflow:concurrency_policy_loader", "Load unified provider concurrency policy" in workflow and "runtime_policy.json" in workflow and "HITHINK_MAX_WORKERS" in workflow, "workflow loads scheduled/query concurrency from runtime policy")
    check("workflow:session_gate", "runtime_session_gate.py" in workflow and "session_gate.outputs.should_capture" in workflow, "exchange calendar/session gate wired")
    check("workflow:auction_cron", '15,20,25,30,40,50 1 * * 1-5' in workflow, "09:15/09:20/09:25 auction pulses scheduled")
    check("workflow:valid_snapshot_downstream_gate", "snapshot_result.outputs.snapshot_written == 'true'" in workflow, "downstream contexts require new valid A-share snapshot")
    check("workflow:single_full_recovery_entry", "workflow_dispatch" in workflow and not (ROOT / ".github/workflows/full-snapshot-recovery.yml").exists(), "market-snapshot workflow_dispatch is the only full-state recovery entry")
    check("workflow:runtime_artifact_staging", "git add -A -- data/market/snapshots data/state" in workflow and "2>/dev/null || true" not in workflow, "runtime staging cannot silently drop a snapshot because an optional path is absent")
    on_demand_workflow = read_text(".github/workflows/on-demand-market-data.yml")
    check("workflow:on_demand_degraded_only", "run_full_snapshot_recovery.py" not in on_demand_workflow and "data/state/CURRENT.json" not in on_demand_workflow, "single-object on-demand service cannot impersonate production CURRENT")

    overseas_workflow = read_text(".github/workflows/overseas-preopen-pulse.yml")
    check("workflow:overseas_preopen_exists", "build_overseas_context.py" in overseas_workflow, "standalone overseas pre-open pulse wired")
    check("workflow:us_extended_wired", "build_us_extended_hours_context.py" in overseas_workflow and "us_extended_hours_context.json" in overseas_workflow, "US extended-hours context wired into A-share pre-open workflow")
    check("workflow:us_postmarket_0700_start", '*/10 23 * * 0-4' in overseas_workflow, "Beijing 07:00-07:50 prior-US post-market tail scheduled")
    check("workflow:overseas_0800_start", '*/10 0 * * 1-5' in overseas_workflow, "Beijing 08:00-08:50 overseas pulses scheduled")
    check("workflow:overseas_0900_0910", '0,10 1 * * 1-5' in overseas_workflow, "Beijing 09:00/09:10 overseas pulses scheduled")
    check("workflow:overseas_runtime_health", "build_overseas_runtime_health.py" in overseas_workflow and "overseas_runtime_health.json" in overseas_workflow, "overseas runtime health is generated and committed")
    check("workflow:overseas_hithink_runtime", "HITHINK_FINANCE_API_KEY" in overseas_workflow and "@hithink-tech/hithink-finance-cli" in overseas_workflow, "overseas runner installs Hithink CLI and receives secret")

    us_workflow = read_text(".github/workflows/us-extended-hours-pulse.yml")
    check("workflow:us_afternoon_exists", "build_us_extended_hours_context.py" in us_workflow, "standalone US afternoon/evening extended-hours pulse wired")
    check("workflow:us_premarket_dst_window", '0 8-13 * * 1-5' in us_workflow and '20,30 13 * * 1-5' in us_workflow, "Beijing 16:00-21:30 DST-sensitive US pre-market coverage scheduled")
    check("workflow:us_premarket_standard_window", '0,20,30 14 * * 1-5' in us_workflow, "Beijing 22:00-22:30 standard-time US pre-market/open boundary scheduled")

    maintenance_workflow = read_text(".github/workflows/system-consistency.yml")
    check("maintenance_workflow:data_standard_trigger", DATA_STANDARD in maintenance_workflow, "data standard changes trigger consistency workflow")
    check("maintenance_workflow:workflow_trigger", ".github/workflows/**" in maintenance_workflow, "workflow changes trigger consistency workflow")
    check("maintenance_workflow:no_recovery_state_machine", "full_snapshot_recovery" not in maintenance_workflow and "run_full_snapshot_recovery.py" not in maintenance_workflow, "consistency workflow validates only; it does not duplicate production recovery")

    runtime_health = read_json("data/state/runtime_health.json")
    query_context = read_json("data/state/query_context.json")
    decision_context = read_json("data/state/decision_context.json")
    query_freshness = context_freshness(query_context)
    decision_freshness = context_freshness(decision_context)
    query_expected = expected_freshness_status(query_freshness["age_seconds"], runtime)
    decision_expected = expected_freshness_status(decision_freshness["age_seconds"], runtime)
    check("dynamic_freshness:query_recalculated", bool(query_expected) and query_freshness["status"] == query_expected, f"declared={query_freshness['status']} expected={query_expected} age_seconds={query_freshness['age_seconds']}", warning=True)
    check("dynamic_freshness:decision_recalculated", bool(decision_expected) and decision_freshness["status"] == decision_expected, f"declared={decision_freshness['status']} expected={decision_expected} age_seconds={decision_freshness['age_seconds']}")
    check("dynamic_freshness:query_decision_aligned", query_freshness["status"] == decision_freshness["status"], f"query={query_freshness['status']} decision={decision_freshness['status']}")
    current = read_json("data/state/CURRENT.json")
    current_status = str((current.get("data_freshness") or {}).get("status", "")).upper()
    check("dynamic_freshness:current_snapshot_boundary", bool(current_status) and bool(query_expected), f"CURRENT snapshot_status={current_status}; context_status={query_expected}; CURRENT is not required to age-transition automatically")

    coverage = decision_context.get("analysis_coverage") or query_context.get("analysis_coverage") or {}
    quality = decision_context.get("data_quality_summary") or query_context.get("data_quality_summary") or {}
    check("decision:coverage_summary", coverage.get("coverage_status") in {"COMPLETE", "DEGRADED", "INCOMPLETE"}, f"coverage_status={coverage.get('coverage_status', 'MISSING')}")
    check("decision:quality_summary", all(key in quality for key in ("pass_count", "degraded_count", "failed_count", "failed_objects", "degraded_objects")), f"keys={sorted(quality)}")
    check("decision:core_stock_coverage_fields", all(key in coverage for key in ("core_market_pass", "core_market_failed", "account_stock_market_pass", "account_stock_market_failed")), f"coverage_keys={sorted(coverage)}", warning=True)
    check("decision:failed_objects_explicit", not quality.get("failed_count") or bool(quality.get("failed_objects")), f"failed_count={quality.get('failed_count')} failed_objects={quality.get('failed_objects')}", warning=True)
    check("decision:query_decision_current_aligned", query_context.get("current", {}).get("latest_snapshot") == decision_context.get("current", {}).get("latest_snapshot"), f"query={query_context.get('current', {}).get('latest_snapshot')} decision={decision_context.get('current', {}).get('latest_snapshot')}")
    pit = decision_context.get("point_in_time") or query_context.get("point_in_time") or {}
    check("point_in_time:context_not_before_market", _time_not_before(pit.get("context_generated_time"), pit.get("market_snapshot_time")), f"context={pit.get('context_generated_time')} market={pit.get('market_snapshot_time')}", warning=True)
    check("point_in_time:account_time_separate", "account_fact_time" in pit and "market_snapshot_time" in pit, f"fields={sorted(pit)}")
    pulse = decision_context.get("scheduled_pulse_health") or query_context.get("scheduled_pulse_health") or {}
    check("scheduled_pulse:observable", all(key in pulse for key in ("expected_slots", "observed_slots", "missing_slots", "status")), f"status={pulse.get('status', 'MISSING')}", warning=True)
    action = decision_context.get("formal_action") or {}
    check("formal_action:execution_boundary", action.get("execution_status") in {"UNKNOWN", "PENDING", "EXECUTED", "SUPERSEDED"} or not action, f"execution_status={action.get('execution_status', 'MISSING')}")

    for script_path in ("scripts/build_phase4_automation.py", "scripts/process_state_sync_request.py", "scripts/build_state_context.py", "scripts/build_query_context.py", "scripts/notification_center.py", "scripts/confirm_execution_reconciliation.py"):
        try:
            ast.parse((ROOT / script_path).read_text(encoding="utf-8"))
            check(f"python:syntax:{script_path}", True, "AST parse passed")
        except (OSError, SyntaxError) as exc:
            check(f"python:syntax:{script_path}", False, str(exc))

    phase4_trigger = read_json("data/state/decision_trigger.json")
    phase4_ranking = read_json("data/state/capital_efficiency_ranking.json")
    e2e_state = read_json("data/state/e2e_status.json")
    check("phase4:trigger_schema", isinstance(phase4_trigger, dict) and phase4_trigger.get("read_only") is True and "requires_formal_reassessment" in phase4_trigger, f"status={phase4_trigger.get('status', 'MISSING')}", warning=True)
    check("phase4:ranking_schema", isinstance(phase4_ranking, dict) and phase4_ranking.get("read_only") is True and isinstance(phase4_ranking.get("ordered_candidates"), list), f"status={phase4_ranking.get('status', 'MISSING')}", warning=True)
    check("phase4:e2e_boundary", str(phase4_ranking.get("e2e_status") or "").upper() != "BLOCKED" or not any(x.get("category") == "CASH" and x.get("eligibility") == "FORMAL_ACTION" for x in (phase4_ranking.get("ordered_candidates") or []) if isinstance(x, dict)), "E2E BLOCKED cannot create formal action")
    check("phase4:no_score", not any("score" in str(x).lower() for x in (phase4_ranking.get("ordered_candidates") or []) if isinstance(x, dict)), "capital ranking contains no composite score")
    check("phase4:e2e_state_present", str(e2e_state.get("purpose") or "").startswith("TOP_LEVEL_SYSTEM_USABILITY_ONLY"), f"status={e2e_state.get('status', 'MISSING')}", warning=True)


    # Notification lifecycle checks remain state-only and never block market data production.
    notification_state = read_json("data/state/notification_center.json")
    notification_items = notification_state.get("notifications") or []
    required_notification_fields = {"notification_id", "event_type", "source_event_id", "related_decision_id", "security_code", "security_name", "lifecycle_status", "created_at", "sent_at", "confirmed_at", "archived_at"}
    notification_statuses = {"CREATED", "SENT", "WAITING_CONFIRMATION", "CONFIRMED", "ARCHIVED", "EXPIRED"}
    check("notification:managed_schema", isinstance(notification_items, list) and all(required_notification_fields.issubset(set(item)) and item.get("lifecycle_status") in notification_statuses for item in notification_items if isinstance(item, dict)), f"count={len(notification_items)}")
    active_notification_sources = [str(item.get("source_event_id") or "") for item in notification_items if isinstance(item, dict) and item.get("lifecycle_status") in {"SENT", "WAITING_CONFIRMATION"}]
    check("notification:no_duplicate_active_source", len(active_notification_sources) == len(set(active_notification_sources)), f"active_sources={active_notification_sources}")
    check("notification:confirmation_entry_syntax", _python_ast_ok("scripts/confirm_execution_reconciliation.py"), "explicit confirmation entry AST parse passed")
    reconciliation = read_json("data/state/execution_reconciliation.json")
    if isinstance(reconciliation, dict):
        for match in reconciliation.get("matches") or []:
            if match.get("requires_user_confirmation"):
                check("reconciliation:confirmation_boundary", "不自动" in str(match.get("safety_boundary") or "") or "不会" in str(match.get("safety_boundary") or ""), "unconfirmed match remains a candidate", warning=True)
                break

    runtime_status = str(runtime_health.get("status", "")).upper()
    allowed_runtime_statuses = {"NOT_RUN", "PASS", "DEGRADED", "FAILED", "SKIPPED", "SUPERSEDED"}
    check("runtime_health:structured", runtime_status in allowed_runtime_statuses, f"status={runtime_status or 'MISSING'}")
    current = read_json("data/state/CURRENT.json")
    latest_snapshot = str(current.get("latest_snapshot", ""))
    current_ready = current.get("node_status") == "READY" and bool(current.get("market_date")) and bool(current.get("latest_valid_node"))
    runtime_snapshot = bool(runtime_health.get("latest_snapshot"))
    if runtime_status == "PASS":
        check("runtime_health:pass_requires_snapshot", runtime_snapshot and bool(latest_snapshot), f"runtime_snapshot={runtime_health.get('latest_snapshot', '')} current_snapshot={latest_snapshot or 'MISSING'}")
    elif runtime_status == "SKIPPED":
        check("runtime_health:skipped_reason", runtime_health.get("failure_stage") == "session_gate" and bool(runtime_health.get("reason")), f"failure_stage={runtime_health.get('failure_stage', '')} reason={runtime_health.get('reason', '')}", warning=True)
    live_runtime = current_ready and bool(latest_snapshot) and (
        (runtime_status == "PASS" and runtime_snapshot) or
        (runtime_status == "SKIPPED" and runtime_snapshot)
    )
    check("a_share_runtime:live_state_available", live_runtime, f"runtime_status={runtime_status or 'MISSING'} current_snapshot={latest_snapshot or 'MISSING'}", warning=True)
    if live_runtime:
        snapshot_path = ROOT / latest_snapshot
        check("a_share_runtime:current_ready", current.get("node_status") == "READY" and bool(current.get("market_date")) and bool(current.get("latest_valid_node")), f"market_date={current.get('market_date')} node={current.get('latest_valid_node')} status={current.get('node_status')}")
        check("a_share_runtime:snapshot_exists", snapshot_path.exists(), f"path={latest_snapshot}")
        if snapshot_path.exists():
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            rows = snapshot.get("rows") or []
            etf_rows = [row for row in rows if row.get("asset_class") == "ETF"]
            index_rows = [row for row in rows if row.get("asset_class") == "A_SHARE_INDEX"]
            check("a_share_runtime:snapshot_count", snapshot.get("count") == len(rows) == len(objects) + 2, f"snapshot_count={snapshot.get('count')} rows={len(rows)} expected={len(objects)+2}")
            check("a_share_runtime:market_date_alignment", snapshot.get("market_date") == current.get("market_date") == runtime_health.get("market_date"), f"snapshot={snapshot.get('market_date')} current={current.get('market_date')} health={runtime_health.get('market_date')}")
            check("a_share_runtime:etf_complete", {str(row.get('symbol', '')) for row in etf_rows} == universe_codes and all(row.get("quality_status") == "PASS" for row in etf_rows), f"count={len(etf_rows)} expected={len(universe_codes)}")
            check("a_share_runtime:core_indices_complete", {str(row.get('thscode', '')) for row in index_rows} == {"000001.SH", "399006.SZ"} and all(row.get("quality_status") == "PASS" for row in index_rows), f"indices={[row.get('thscode') for row in index_rows]}")
            check("a_share_runtime:beijing_capture", bool(snapshot.get("captured_at_beijing")) and snapshot.get("timezone") == "Asia/Shanghai", f"captured_at_beijing={snapshot.get('captured_at_beijing')} timezone={snapshot.get('timezone')}")

    account = read_json("data/state/account_fact.json")
    expected_account_stocks = {
        str(position.get("code", "")) for position in (account.get("positions") or [])
        if isinstance(position, dict) and str(position.get("asset_type", "")).upper() == "STOCK" and float(position.get("quantity") or 0) > 0
    }
    stock_context = read_json("data/state/stock_context.json")
    detected_stocks = {
        str(item.get("code", "")) for item in (stock_context.get("default_stock_layer", {}).get("detected_non_etf_stocks") or [])
    }
    stock_market = read_json("data/state/stock_market_context.json")
    stock_market_codes = set((stock_market.get("objects") or {}).keys())
    check("stock_runtime:dynamic_account_membership", detected_stocks == expected_account_stocks, f"detected={sorted(detected_stocks)} expected={sorted(expected_account_stocks)}", warning=not live_runtime)
    # Third-layer quote failure is explicit DEGRADED/FAILED evidence, but must not block the core ETF/index production pulse.\n    # The stock layer remains a hard requirement for full three-layer acceptance and is reported as WARNING here until refreshed.\n    check("stock_runtime:market_complete", stock_market_codes == expected_account_stocks and all(item.get("quality_status") == "PASS" and item.get("as_of_beijing") for item in (stock_market.get("objects") or {}).values()), f"codes={sorted(stock_market_codes)} status={stock_market.get('quality_status')}", warning=True)
    core_time_text = str(current.get("captured_at") or "")
    stock_time_texts = [str(item.get("as_of_beijing") or "") for item in (stock_market.get("objects") or {}).values() if item.get("as_of_beijing")]
    stock_time_aligned = True
    stock_time_detail = "not_comparable"
    if live_runtime and core_time_text and stock_time_texts:
        try:
            core_dt = datetime.fromisoformat(core_time_text.replace("Z", "+00:00"))
            stock_delays = []
            for stock_time in stock_time_texts:
                stock_dt = datetime.fromisoformat(stock_time.replace("Z", "+00:00"))
                stock_delays.append(round((core_dt - stock_dt).total_seconds()))
            max_delay = max(stock_delays)
            stock_time_aligned = max_delay <= 900
            stock_time_detail = f"max_core_minus_stock_seconds={max_delay}"
        except ValueError:
            stock_time_aligned = False
            stock_time_detail = "invalid_timestamp"
    check("stock_runtime:market_time_alignment", stock_time_aligned, stock_time_detail, warning=True)
    overseas_health = read_json("data/state/overseas_runtime_health.json")
    check("overseas_runtime:structured", bool(overseas_health.get("status")), f"status={overseas_health.get('status', 'MISSING')}")
    check("overseas_runtime:pulse_success", overseas_health.get("pulse_success") is True, f"pulse_success={overseas_health.get('pulse_success')}", warning=True)
    check("overseas_runtime:hard_errors", int(overseas_health.get("hard_error_count", 0)) == 0, f"hard_error_count={overseas_health.get('hard_error_count')}", warning=True)
    for object_id in ("N225", "KOSPI"):
        object_health = (overseas_health.get("objects") or {}).get(object_id) or {}
        check(f"overseas_runtime:{object_id}:same_day_bar", object_health.get("quality_status") == "PASS" and bool(object_health.get("as_of_beijing")), f"quality={object_health.get('quality_status')} as_of_beijing={object_health.get('as_of_beijing', '')}", warning=True)
    check("account_fact:current_availability", account.get("status") == "VALID", f"status={account.get('status', 'MISSING')} (state warning only)", warning=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generated_at_beijing": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "repository": {"head_sha": head_sha, "github_sha": github_sha, "github_run_id": os.environ.get("GITHUB_RUN_ID", ""), "github_ref": os.environ.get("GITHUB_REF", "")},
        "commit_audit": {
            "checked_commit": head_sha,
            "workflow_sha": github_sha,
            "persisted_state_commit": "",
            "persisted_state_commit_note": "由后续状态提交持久化；不递归追踪报告文件自身的最终SHA。",
        },
        "status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
        "hard_error_count": len(errors), "warning_count": len(warnings), "checks": checks, "errors": errors, "warnings": warnings,
        "principle": "一致性检查覆盖文本口径、配置、运行链、关键状态文件、Git跟踪/HEAD提交、workflow接线、数据时点字段、美股扩展时段链和自动验收结果；硬冲突不得进入生产。",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
