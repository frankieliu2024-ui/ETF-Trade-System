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


POLICY = load_runtime_policy()
RETRY_LIMIT = int(os.environ.get("HITHINK_RETRY_LIMIT", POLICY["provider_retry_limit"]))
TIMEOUT_SECONDS = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", POLICY["provider_timeout_seconds"]))
MAX_WORKERS = int(os.environ.get("HITHINK_MAX_WORKERS", POLICY["provider_max_workers"]))
CLOSE_GRACE_SECONDS = int(POLICY.get("close_grace_seconds", 900))

ETF = [
    ("561980", "561980.SH"), ("588000", "588000.SH"),
    ("159781", "159781.SZ"), ("159941", "159941.SZ"),
    ("159561", "159561.SZ"), ("513520", "513520.SH"),
    ("513180", "513180.SH"), ("518880", "518880.SH"),
]
INDEX = [("000001", "000001.SH"), ("399006", "399006.SZ")]
NODES = {"0925", "1030", "1130", "1330", "1430", "close", "live", "manual", "scheduled"}
PLANNED_TIMES = {"0925": "09:25", "1030": "10:30", "1130": "11:30", "1330": "13:30", "1430": "14:30", "close": "15:00"}


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def in_a_share_capture_window(captured_dt: datetime) -> bool:
    if captured_dt.weekday() >= 5:
        return False
    minute = captured_dt.hour * 60 + captured_dt.minute
    morning = 9 * 60 + 30 <= minute <= 11 * 60 + 30
    afternoon = 13 * 60 <= minute <= 15 * 60
    close_grace_end = 15 * 60 + max(1, CLOSE_GRACE_SECONDS // 60)
    close_grace = 15 * 60 < minute <= close_grace_end
    return morning or afternoon or close_grace


def resolve_scheduled_node(captured_dt: datetime) -> str:
    return "close" if (captured_dt.hour * 60 + captured_dt.minute) >= 15 * 60 else "live"


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


def row(asset_class: str, code: str, thscode: str, item: dict, captured: str, provider_ts: object) -> dict:
    required = ["open_price", "high_price", "low_price", "last_price", "volume", "turnover"]
    missing = [key for key in required if item.get(key) is None]
    if missing:
        raise RuntimeError(f"{code} missing fields: {','.join(missing)}")
    if item["high_price"] < max(item["open_price"], item["last_price"]) or item["low_price"] > min(item["open_price"], item["last_price"]):
        raise RuntimeError(f"{code} failed OHLC relationship")
    return {
        "asset_class": asset_class, "symbol": code, "thscode": thscode,
        "open": item["open_price"], "high": item["high_price"],
        "low": item["low_price"], "close": item["last_price"],
        "volume": item["volume"], "amount": item["turnover"],
        "provider": "hithink-finance", "provider_timestamp_ms": provider_ts,
        "captured_at": captured, "timezone": "Asia/Shanghai",
        "quality_status": "PASS",
    }


def fetch_etf(cli: str, run_dir: Path, code: str, thscode: str, captured: str) -> dict:
    obj = run_json(cli, ["fund", "snapshot", "--thscode", thscode], run_dir / f"ETF_{code}.json")
    items = obj.get("data", {}).get("item") or []
    if len(items) != 1:
        raise RuntimeError(f"expected one ETF row for {code}, got {len(items)}")
    return row("ETF", code, thscode, items[0], captured, obj.get("data", {}).get("timestamp"))


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
    captured_dt = now_shanghai()
    captured = captured_dt.isoformat(timespec="seconds")
    market_date = captured_dt.date().isoformat()

    if args.node == "scheduled" and not args.probe_only and not in_a_share_capture_window(captured_dt):
        write_runtime_health({
            "status": "SKIPPED",
            "reason": "outside_a_share_capture_window",
            "market_date": market_date,
            "run_started_at": run_started_at,
            "captured_at": captured,
        })
        print(json.dumps({"ok": True, "skipped": True, "reason": "outside_a_share_capture_window", "market_date": market_date, "captured_at": captured}, ensure_ascii=False))
        return 0

    node = resolve_scheduled_node(captured_dt) if args.node == "scheduled" else args.node
    planned_time = PLANNED_TIMES.get(node, "")

    if args.node == "scheduled" and node == "close" and close_already_recorded(market_date):
        write_runtime_health({
            "status": "SKIPPED",
            "reason": "close_already_recorded",
            "market_date": market_date,
            "run_started_at": run_started_at,
            "captured_at": captured,
        })
        print(json.dumps({"ok": True, "skipped": True, "reason": "close_already_recorded", "market_date": market_date}, ensure_ascii=False))
        return 0

    if captured_dt.weekday() >= 5 and not args.probe_only:
        write_runtime_health({"status": "SKIPPED", "reason": "non_trading_weekend", "market_date": market_date, "run_started_at": run_started_at})
        print(json.dumps({"ok": True, "skipped": True, "reason": "non_trading_weekend", "market_date": market_date}, ensure_ascii=False))
        return 0

    cli = cli_path()
    run_dir = ROOT / "data" / "market" / "raw" / "hithink" / market_date / f"{captured_dt:%H%M%S}"
    snapshot_dir = ROOT / "data" / "market" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as pool:
        futures = {pool.submit(fetch_etf, cli, run_dir, code, thscode, captured): code for code, thscode in ETF}
        for future in as_completed(futures):
            rows.append(future.result())

    obj = run_json(cli, ["index", "snapshot", "--thscodes", ",".join(x[1] for x in INDEX)], run_dir / "INDEX_core.json")
    returned = {x.get("thscode"): x for x in (obj.get("data", {}).get("item") or [])}
    for code, thscode in INDEX:
        if thscode not in returned:
            raise RuntimeError(f"missing index row for {thscode}")
        rows.append(row("A_SHARE_INDEX", code, thscode, returned[thscode], captured, obj.get("data", {}).get("timestamp")))

    rows.sort(key=lambda x: (x["asset_class"], x["symbol"]))
    acquisition_seconds = round(time.monotonic() - run_started_monotonic, 3)

    if args.probe_only:
        write_runtime_health({
            "status": "PROBE_PASS", "market_date": market_date, "run_started_at": run_started_at,
            "captured_at": captured, "acquisition_seconds": acquisition_seconds, "count": len(rows),
        })
        print(json.dumps({"ok": True, "probe_only": True, "count": len(rows), "market_date": market_date, "quality_status": "PASS", "node_written": False, "acquisition_seconds": acquisition_seconds}, ensure_ascii=False))
        return 0

    if not is_newer_than_current(captured_dt):
        write_runtime_health({
            "status": "SUPERSEDED", "reason": "newer_current_already_exists", "market_date": market_date,
            "run_started_at": run_started_at, "captured_at": captured, "acquisition_seconds": acquisition_seconds,
        })
        print(json.dumps({"ok": True, "skipped": True, "reason": "newer_current_already_exists", "captured_at": captured}, ensure_ascii=False))
        return 0

    snapshot = {
        "market_date": market_date, "node": node, "planned_time": planned_time,
        "actual_run_time": captured, "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "captured_at": captured, "timezone": "Asia/Shanghai", "provider": "hithink-finance",
        "quality_status": "PASS", "count": len(rows),
        "runtime": {
            "acquisition_seconds": acquisition_seconds,
            "target_cadence_seconds": POLICY["target_cadence_seconds"],
            "provider_timeout_seconds": TIMEOUT_SECONDS,
            "provider_retry_limit": RETRY_LIMIT,
            "provider_max_workers": MAX_WORKERS,
            "close_grace_seconds": CLOSE_GRACE_SECONDS,
        },
        "rows": rows,
    }
    name = f"{captured_dt:%Y-%m-%d_%H%M%S}.json"
    temp = snapshot_dir / f".{name}.tmp"
    target = snapshot_dir / name
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)

    update_current(
        root=ROOT, market_date=market_date, node=node, captured_at=captured,
        latest_snapshot=str(target.relative_to(ROOT)).replace("\\", "/"),
        snapshot_commit=os.environ.get("GITHUB_SHA", ""), node_status="READY",
        data_freshness={
            "status": "FRESH", "provider": "hithink-finance", "count": len(rows),
            "capture_mode": "INTRADAY_PULSE" if node == "live" else "CLOSE",
            "captured_at": captured,
            "target_cadence_seconds": POLICY["target_cadence_seconds"],
            "fresh_max_age_seconds": POLICY["fresh_max_age_seconds"],
            "degraded_max_age_seconds": POLICY["degraded_max_age_seconds"],
            "acquisition_seconds": acquisition_seconds,
        },
    )
    write_runtime_health({
        "status": "PASS", "market_date": market_date, "node": node, "run_started_at": run_started_at,
        "captured_at": captured, "acquisition_seconds": acquisition_seconds, "count": len(rows),
        "latest_snapshot": str(target.relative_to(ROOT)).replace("\\", "/"),
    })
    print(json.dumps({"ok": True, "snapshot": str(target), "count": len(rows), "market_date": market_date, "node": node, "acquisition_seconds": acquisition_seconds}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            write_runtime_health({
                "status": "FAILED",
                "reason": "collection_error",
                "error": str(exc)[-1000:],
                "failed_at": now_utc_text(),
                "preserve_previous_current": True,
            })
        except Exception:
            pass
        print(f"cloud runner failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
