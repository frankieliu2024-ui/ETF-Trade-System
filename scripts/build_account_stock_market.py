from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STOCK_CONTEXT = ROOT / "data" / "state" / "stock_context.json"
OUTPUT = ROOT / "data" / "state" / "stock_market_context.json"
TIMEOUT = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", "25"))


def thscode(code: str) -> str:
    code = code.strip()
    if code.startswith(("60", "68", "51", "56", "58")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "15")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return code


def fetch_one(cli: str, code: str) -> dict:
    raw_dir = ROOT / "data" / "market" / "raw" / "account_stocks"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{code}.json"
    completed = subprocess.run(
        [cli, "stock", "snapshot", "--thscode", thscode(code), "--output", str(path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, check=False,
    )
    if completed.returncode != 0 or not path.exists():
        raise RuntimeError((completed.stderr or "stock snapshot failed")[-400:])
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("data", {}).get("item") or []
    if not payload.get("ok") or len(items) != 1:
        raise RuntimeError("invalid stock snapshot payload")
    item = items[0]
    return {
        "code": code,
        "thscode": thscode(code),
        "open": item.get("open_price"),
        "high": item.get("high_price"),
        "low": item.get("low_price"),
        "close": item.get("last_price"),
        "volume": item.get("volume"),
        "amount": item.get("turnover"),
        "provider_timestamp_ms": payload.get("data", {}).get("timestamp"),
        "provider": "hithink-finance",
        "quality_status": "PASS",
    }


def build() -> dict:
    stock_context = read_json(STOCK_CONTEXT, {})
    stocks = stock_context.get("default_stock_layer", {}).get("ipo_base_stocks", []) or []
    result = {
        "generated_at": now_utc(),
        "source_stock_context": "data/state/stock_context.json",
        "objects": {},
        "quality_status": "EMPTY" if not stocks else "PASS",
        "decision_boundary": "仅监测当前账户已确认打新底仓个股的行情事实；不把个股行情单独转化为ETF交易动作。",
    }
    if not stocks:
        return result
    cli = shutil.which("hithink-finance")
    if not cli:
        result["quality_status"] = "FAILED"
        result["error"] = "hithink-finance CLI not found"
        return result
    failures = 0
    for stock in stocks:
        code = str(stock.get("code", ""))
        if not code:
            continue
        try:
            quote = fetch_one(cli, code)
            quote["name"] = stock.get("name", "")
            quote["quantity"] = stock.get("quantity")
            quote["market_value_from_account"] = stock.get("market_value")
            result["objects"][code] = quote
        except Exception as exc:
            failures += 1
            result["objects"][code] = {
                "code": code,
                "name": stock.get("name", ""),
                "quality_status": "FAILED",
                "error": str(exc)[-400:],
            }
    if failures:
        result["quality_status"] = "DEGRADED" if failures < len(stocks) else "FAILED"
    return result


def main() -> None:
    # This collector is deliberately non-fatal: a third-layer quote failure must not
    # block the core ETF/index pulse. The failure is explicit in the output instead.
    try:
        context = build()
    except Exception as exc:
        context = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "quality_status": "FAILED",
            "objects": {},
            "error": str(exc)[-500:],
            "decision_boundary": "第三层个股行情失败不阻断ETF/指数主链；失败对象不得冒充当前行情。",
        }
    atomic_json_write(OUTPUT, context)
    print(json.dumps({"ok": True, "quality_status": context.get("quality_status"), "count": len(context.get("objects", {}))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
