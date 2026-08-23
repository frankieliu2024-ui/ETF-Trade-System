from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
RETRY_LIMIT = int(os.environ.get("HITHINK_RETRY_LIMIT", "3"))
TIMEOUT_SECONDS = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", "60"))

ETF = [
    ("561980", "561980.SH"), ("588000", "588000.SH"),
    ("159781", "159781.SZ"), ("159941", "159941.SZ"),
    ("159561", "159561.SZ"), ("513520", "513520.SH"),
    ("513180", "513180.SH"), ("518880", "518880.SH"),
]
INDEX = [("000001", "000001.SH"), ("399006", "399006.SZ")]
NODES = {"0925", "1030", "1130", "1330", "1430", "close", "manual"}


def cli_path() -> str:
    found = shutil.which("hithink-finance")
    if found:
        return found
    raise RuntimeError("hithink-finance CLI not found on PATH")


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
            time.sleep(2 ** (attempt - 1))
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="manual", choices=sorted(NODES))
    parser.add_argument("--probe-only", action="store_true", help="Fetch and validate real data without creating a market node")
    args = parser.parse_args()
    captured_dt = datetime.now(SHANGHAI)
    captured = captured_dt.isoformat(timespec="seconds")
    market_date = captured_dt.date().isoformat()
    if captured_dt.weekday() >= 5 and not args.probe_only:
        print(json.dumps({"ok": False, "reason": "non_trading_weekend", "market_date": market_date}, ensure_ascii=False))
        return 2
    cli = cli_path()
    run_dir = ROOT / "data" / "market" / "raw" / "hithink" / market_date
    snapshot_dir = ROOT / "data" / "market" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for code, thscode in ETF:
        obj = run_json(cli, ["fund", "snapshot", "--thscode", thscode], run_dir / f"ETF_{code}.json")
        items = obj.get("data", {}).get("item") or []
        if len(items) != 1:
            raise RuntimeError(f"expected one ETF row for {code}, got {len(items)}")
        rows.append(row("ETF", code, thscode, items[0], captured, obj.get("data", {}).get("timestamp")))
    obj = run_json(cli, ["index", "snapshot", "--thscodes", ",".join(x[1] for x in INDEX)], run_dir / "INDEX_core.json")
    returned = {x.get("thscode"): x for x in (obj.get("data", {}).get("item") or [])}
    for code, thscode in INDEX:
        if thscode not in returned:
            raise RuntimeError(f"missing index row for {thscode}")
        rows.append(row("A_SHARE_INDEX", code, thscode, returned[thscode], captured, obj.get("data", {}).get("timestamp")))
    if args.probe_only:
        print(json.dumps({"ok": True, "probe_only": True, "count": len(rows), "market_date": market_date, "quality_status": "PASS", "node_written": False}, ensure_ascii=False))
        return 0
    snapshot = {
        "market_date": market_date, "node": args.node, "captured_at": captured,
        "timezone": "Asia/Shanghai", "provider": "hithink-finance",
        "quality_status": "PASS", "count": len(rows), "rows": rows,
    }
    name = f"{captured_dt:%Y-%m-%d_%H%M}.json"
    temp = snapshot_dir / f".{name}.tmp"
    target = snapshot_dir / name
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    current = {
        "market_date": market_date, "latest_valid_node": args.node,
        "captured_at": captured, "node_status": "VALID",
        "snapshot_commit": os.environ.get("GITHUB_SHA", ""),
        "data_freshness": "fresh", "rules_version": "V2.2.15",
        "generated_at": captured,
    }
    (ROOT / "data" / "state" / "CURRENT.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "snapshot": str(target), "count": len(rows), "market_date": market_date, "node": args.node}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"cloud runner failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

