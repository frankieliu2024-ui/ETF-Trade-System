from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from state_manager import atomic_json_write, read_current, update_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
UTC = timezone.utc


def load_runtime_policy() -> dict:
    path = ROOT / "config" / "runtime_policy.json"
    default = {
        "target_cadence_seconds": 600,
        "fresh_max_age_seconds": 900,
        "degraded_max_age_seconds": 1500,
        "stale_after_seconds": 1500,
        "close_grace_seconds": 900,
        "provider_timeout_seconds": 25,
        "provider_retry_limit": 2,
        "provider_max_workers": 3,
    }
    if path.exists():
        default.update(json.loads(path.read_text(encoding="utf-8")))
    return default


def load_etf_universe() -> list[tuple[str, str]]:
    path = ROOT / "config" / "market" / "etf_monitor_universe.json"
    if not path.exists():
        raise RuntimeError("missing canonical ETF universe: config/market/etf_monitor_universe.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    objects = data.get("objects") or []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in objects:
        code = str(item.get("code", "")).strip()
        thscode = str(item.get("thscode", "")).strip()
        if not code or not thscode:
            raise RuntimeError("ETF universe contains missing code/thscode")
        if code in seen:
            raise RuntimeError(f"duplicate ETF code in universe: {code}")
        seen.add(code)
        result.append((code, thscode))
    if not result:
        raise RuntimeError("canonical ETF universe is empty")
    return result


POLICY = load_runtime_policy()
ETF = load_etf_universe()
RETRY_LIMIT = int(os.environ.get("HITHINK_RETRY_LIMIT", POLICY["provider_retry_limit"]))
TIMEOUT_SECONDS = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", POLICY["provider_timeout_seconds"]))
MAX_WORKERS = int(os.environ.get("HITHINK_MAX_WORKERS", POLICY["provider_max_workers"]))
CLOSE_GRACE_SECONDS = int(POLICY.get("close_grace_seconds", 900))

INDEX = [("000001", "000001.SH"), ("399006", "399006.SZ")]
NODES = {"auction", "0925", "1030", "1130", "1330", "1430", "close", "live", "manual", "scheduled"}
PLANNED_TIMES = {"auction": "09:15-09:25", "0925": "09:25", "1030": "10:30", "1130": "11:30", "1330": "13:30", "1430": "14:30", "close": "15:00"}


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def provider_as_of_beijing(timestamp_ms: object) -> str:
    if timestamp_ms in (None, ""):
        return ""
    return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=SHANGHAI).isoformat(timespec="seconds")


def a_share_market_phase(captured_dt: datetime) -> str:
    minute = captured_dt.hour * 60 + captured_dt.minute
    # 09:25-09:29 is still pre-continuous trading. A delayed scheduled runner
    # may capture the final opening-auction result here; keep auction semantics.
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "OPENING_CALL_AUCTION"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "CONTINUOUS_MORNING"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "CONTINUOUS_AFTERNOON"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "CLOSING_CALL_AUCTION"
    if 15 * 60 < minute <= 15 * 60 + max(1, CLOSE_GRACE_SECONDS // 60):
        return "POST_CLOSE_GRACE"
    return "OUTSIDE_SESSION"


def in_a_share_capture_window(captured_dt: datetime) -> bool:
    if captured_dt.weekday() >= 5:
        return False
    minute = captured_dt.hour * 60 + captured_dt.minute
    opening_auction = 9 * 60 + 15 <= minute < 9 * 60 + 30
    morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
    afternoon = 13 * 60 <= minute <= 15 * 60
    close_grace_end = 15 * 60 + max(1, CLOSE_GRACE_SECONDS // 60)
    close_grace = 15 * 60 < minute <= close_grace_end
    return opening_auction or morning or afternoon or close_grace


def resolve_scheduled_node(captured_dt: datetime) -> str:
    minute = captured_dt.hour * 60 + captured_dt.minute
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "auction"
    return "close" if minute >= 15 * 60 else "live"


def close_already_recorded(market_date: str) -> bool:
    current = read_current(ROOT)
    return current.get("market_date") == market_date and current.get("latest_valid_node") == "close" and current.get("node_status") == "READY"


def cli_path() -> str:
    found = shutil.which("hithink-finance")
    if found:
        return found
    raise RuntimeError("hithink-finance CLI not found on PATH")


def write_runtime_health(payload: dict) -> None:
    base = {
        "generated_at": now_utc_text(),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "target_cadence_seconds": POLICY["target_cadence_seconds"],
        "fresh_max_age_seconds": POLICY["fresh_max_age_seconds"],
        "degraded_max_age_seconds": POLICY["degraded_max_age_seconds"],
        "close_grace_seconds": CLOSE_GRACE_SECONDS,
        "provider_timeout_seconds": TIMEOUT_SECONDS,
        "provider_retry_limit": RETRY_LIMIT,
        "provider_max_workers": MAX_WORKERS,
        "etf_universe_count": len(ETF),
    }
    base.update(payload)
    atomic_json_write(ROOT / "data" / "state" / "runtime_health.json", base)


def run_json(cli: str, args: list[str], raw_path: Path) -> dict:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            completed = subprocess.run(
                [cli, *args, "--output", str(raw_path), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            last_error = f"timeout after {TIMEOUT_SECONDS}s"
        else:
            if completed.returncode == 0 and raw_path.exists():
                obj = json.loads(raw_path.read_text(encoding="utf-8"))
                if obj.get("ok") and obj.get("meta", {}).get("source") == "remote":
                    return obj
                last_error = f"business failure meta={obj.get('meta')}"
            else:
                last_error = (completed.stderr or "CLI failed")[-500:]
        if attempt < RETRY_LIMIT:
            time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"request failed after {RETRY_LIMIT} attempts: {args}; {last_error}")


def row(asset_class: str, code: str, thscode: str, item: dict, captured: str, provider_ts: object, market_phase: str) -> dict:
    required = ["open_price", "high_price", "low_price", "last_price", "volume", "turnover"]
    missing = [key for key in required if item.get(key) is None]
    auction_partial = (
        market_phase == "OPENING_CALL_AUCTION"
        and set(missing).issubset({"open_price", "high_price", "low_price"})
        and item.get("last_price") is not None
        and item.get("prev_price") is not None
        and provider_ts not in (None, "")
    )
    if missing and not auction_partial:
        raise RuntimeError(f"{code} missing fields: {','.join(missing)}")
    if not auction_partial and (
        item["high_price"] < max(item["open_price"], item["last_price"])
        or item["low_price"] > min(item["open_price"], item["last_price"])
    ):
        raise RuntimeError(f"{code} failed OHLC relationship")
    quality_status = "DEGRADED" if auction_partial else "PASS"
    semantic_note = (
        "OPENING_CALL_AUCTION阶段provider尚未形成完整日内OHLC；保留最新价、昨收、成交量、成交额和provider时点，禁止用旧OHLC补齐。"
        if auction_partial
        else ("OPENING_CALL_AUCTION阶段仅按集合竞价时点快照解释，不与连续竞价最新成交语义混用。" if market_phase == "OPENING_CALL_AUCTION" else "")
    )
    return {
        "asset_class": asset_class, "symbol": code, "thscode": thscode,
        "open": item.get("open_price"), "high": item.get("high_price"),
        "low": item.get("low_price"), "close": item.get("last_price"),
        "prev_close": item.get("prev_price"),
        "change_pct": item.get("price_change_ratio_pct"),
        "volume": item["volume"], "amount": item["turnover"],
        "provider": "hithink-finance", "provider_timestamp_ms": provider_ts,
        "as_of_beijing": provider_as_of_beijing(provider_ts),
        "captured_at": captured, "captured_at_beijing": captured, "timezone": "Asia/Shanghai",
        "market_phase": market_phase,
        "quality_status": quality_status,
        "semantic_note": semantic_note,
    }


def fetch_etf(cli: str, run_dir: Path, code: str, thscode: str) -> dict:
    obj = run_json(cli, ["fund", "snapshot", "--thscode", thscode], run_dir / f"ETF_{code}.json")
    items = obj.get("data", {}).get("item") or []
    if len(items) != 1:
        raise RuntimeError(f"expected one ETF row for {code}, got {len(items)}")
    received = now_shanghai()
    return row("ETF", code, thscode, items[0], received.isoformat(timespec="seconds"), obj.get("data", {}).get("timestamp"), a_share_market_phase(received))


def failed_etf_row(code: str, thscode: str, error: str, market_phase: str) -> dict:
    captured = now_shanghai().isoformat(timespec="seconds")
    return {
        "asset_class": "ETF",
        "symbol": code,
        "thscode": thscode,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "prev_close": None,
        "change_pct": None,
        "volume": None,
        "amount": None,
        "provider": "hithink-finance",
        "provider_timestamp_ms": None,
        "as_of_beijing": "",
        "captured_at": captured,
        "captured_at_beijing": captured,
        "timezone": "Asia/Shanghai",
        "market_phase": market_phase,
        "quality_status": "FAILED",
        "error": error[-500:],
        "semantic_note": "该对象provider请求失败；未使用旧行情、代理或伪造OHLC补齐。"
    }


def is_newer_than_current(captured_dt: datetime) -> bool:
    current = read_current(ROOT)
    text = current.get("captured_at", "")
    if not text:
        return True
    try:
        previous = datetime.fromisoformat(text)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=SHANGHAI)
    return captured_dt > previous.astimezone(SHANGHAI)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="manual", choices=sorted(NODES))
    parser.add_argument("--probe-only", action="store_true", help="Fetch and validate real data without creating a market node")
    args = parser.parse_args()

    run_started_monotonic = time.monotonic()
    run_started_at = now_utc_text()
    run_started_dt = now_shanghai()
    capture_started_at = run_started_dt.isoformat(timespec="seconds")
    market_date = run_started_dt.date().isoformat()
    market_phase = a_share_market_phase(run_started_dt)

    if args.node == "scheduled" and not args.probe_only and not in_a_share_capture_window(run_started_dt):
        write_runtime_health({"status": "SKIPPED", "reason": "outside_a_share_capture_window", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "market_phase": market_phase})
        print(json.dumps({"ok": True, "skipped": True, "reason": "outside_a_share_capture_window", "market_date": market_date, "capture_started_at_beijing": capture_started_at}, ensure_ascii=False))
        return 0

    node = resolve_scheduled_node(run_started_dt) if args.node == "scheduled" else args.node
    planned_time = PLANNED_TIMES.get(node, "")

    if args.node == "scheduled" and node == "close" and close_already_recorded(market_date):
        write_runtime_health({"status": "SKIPPED", "reason": "close_already_recorded", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "market_phase": market_phase})
        print(json.dumps({"ok": True, "skipped": True, "reason": "close_already_recorded", "market_date": market_date}, ensure_ascii=False))
        return 0

    if run_started_dt.weekday() >= 5 and not args.probe_only:
        write_runtime_health({"status": "SKIPPED", "reason": "non_trading_weekend", "market_date": market_date, "run_started_at": run_started_at})
        print(json.dumps({"ok": True, "skipped": True, "reason": "non_trading_weekend", "market_date": market_date}, ensure_ascii=False))
        return 0

    cli = cli_path()
    run_dir = ROOT / "data" / "market" / "raw" / "hithink" / market_date / f"{run_started_dt:%H%M%S}"
    snapshot_dir = ROOT / "data" / "market" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as pool:
        futures = {pool.submit(fetch_etf, cli, run_dir, code, thscode): (code, thscode) for code, thscode in ETF}
        for future in as_completed(futures):
            code, thscode = futures[future]
            try:
                rows.append(future.result())
            except Exception as error:  # noqa: BLE001 - preserve object-level provider failure
                failed = failed_etf_row(code, thscode, str(error), a_share_market_phase(now_shanghai()))
                rows.append(failed)
                print(json.dumps({"object_failure": failed}, ensure_ascii=False))

    obj = run_json(cli, ["index", "snapshot", "--thscodes", ",".join(x[1] for x in INDEX)], run_dir / "INDEX_core.json")
    returned = {x.get("thscode"): x for x in (obj.get("data", {}).get("item") or [])}
    for code, thscode in INDEX:
        if thscode not in returned:
            raise RuntimeError(f"missing index row for {thscode}")
        received = now_shanghai()
        rows.append(row("A_SHARE_INDEX", code, thscode, returned[thscode], received.isoformat(timespec="seconds"), obj.get("data", {}).get("timestamp"), a_share_market_phase(received)))

    rows.sort(key=lambda x: (x["asset_class"], x["symbol"]))
    overall_quality = "PASS" if all(item.get("quality_status") == "PASS" for item in rows) else "DEGRADED"
    acquisition_seconds = round(time.monotonic() - run_started_monotonic, 3)
    captured_dt = now_shanghai()
    captured = captured_dt.isoformat(timespec="seconds")
    market_phase = a_share_market_phase(captured_dt)

    if args.probe_only:
        write_runtime_health({"status": "PROBE_PASS", "market_date": market_date, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "captured_at": captured, "captured_at_beijing": captured, "market_phase": market_phase, "acquisition_seconds": acquisition_seconds, "count": len(rows)})
        print(json.dumps({"ok": True, "probe_only": True, "count": len(rows), "market_date": market_date, "quality_status": overall_quality, "node_written": False, "market_phase": market_phase, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "etf_universe_count": len(ETF)}, ensure_ascii=False))
        return 0

    if not is_newer_than_current(captured_dt):
        write_runtime_health({"status": "SUPERSEDED", "reason": "newer_current_already_exists", "market_date": market_date, "run_started_at": run_started_at, "captured_at": captured, "captured_at_beijing": captured, "market_phase": market_phase, "acquisition_seconds": acquisition_seconds})
        print(json.dumps({"ok": True, "skipped": True, "reason": "newer_current_already_exists", "captured_at_beijing": captured}, ensure_ascii=False))
        return 0

    snapshot = {
        "market_date": market_date, "node": node, "planned_time": planned_time,
        "market_phase": market_phase,
        "actual_run_time": captured, "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "capture_started_at_beijing": capture_started_at,
        "captured_at": captured, "captured_at_beijing": captured, "timezone": "Asia/Shanghai", "provider": "hithink-finance",
        "quality_status": overall_quality, "count": len(rows), "etf_universe_count": len(ETF),
        "semantic_scope": "集合竞价脉冲只按集合竞价信息解释；连续竞价脉冲才按盘中成交语义解释。",
        "runtime": {"acquisition_seconds": acquisition_seconds, "target_cadence_seconds": POLICY["target_cadence_seconds"], "provider_timeout_seconds": TIMEOUT_SECONDS, "provider_retry_limit": RETRY_LIMIT, "provider_max_workers": MAX_WORKERS, "close_grace_seconds": CLOSE_GRACE_SECONDS},
        "rows": rows,
    }
    name = f"{captured_dt:%Y-%m-%d_%H%M%S}.json"
    temp = snapshot_dir / f".{name}.tmp"
    target = snapshot_dir / name
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)

    capture_mode = "OPENING_AUCTION_PULSE" if market_phase == "OPENING_CALL_AUCTION" else ("CLOSE" if node == "close" else "INTRADAY_PULSE")
    update_current(root=ROOT, market_date=market_date, node=node, captured_at=captured, latest_snapshot=str(target.relative_to(ROOT)).replace("\\", "/"), snapshot_commit=os.environ.get("GITHUB_SHA", ""), node_status=("READY" if overall_quality == "PASS" else "DEGRADED"), data_freshness={"status": overall_quality, "provider": "hithink-finance", "count": len(rows), "etf_universe_count": len(ETF), "capture_mode": capture_mode, "market_phase": market_phase, "captured_at": captured, "captured_at_beijing": captured, "target_cadence_seconds": POLICY["target_cadence_seconds"], "fresh_max_age_seconds": POLICY["fresh_max_age_seconds"], "degraded_max_age_seconds": POLICY["degraded_max_age_seconds"], "acquisition_seconds": acquisition_seconds})
    write_runtime_health({"status": ("PASS" if overall_quality == "PASS" else "DEGRADED"), "quality_status": overall_quality, "market_date": market_date, "node": node, "market_phase": market_phase, "run_started_at": run_started_at, "capture_started_at_beijing": capture_started_at, "captured_at": captured, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "count": len(rows), "latest_snapshot": str(target.relative_to(ROOT)).replace("\\", "/")})
    print(json.dumps({"ok": True, "snapshot": str(target), "count": len(rows), "market_date": market_date, "node": node, "market_phase": market_phase, "captured_at_beijing": captured, "acquisition_seconds": acquisition_seconds, "etf_universe_count": len(ETF)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            write_runtime_health({"status": "FAILED", "reason": "collection_error", "error": str(exc)[-1000:], "failed_at": now_utc_text(), "preserve_previous_current": True})
        except Exception:
            pass
        print(f"cloud runner failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
