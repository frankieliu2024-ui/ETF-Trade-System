from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STOCK_CONTEXT = ROOT / "data" / "state" / "stock_context.json"
OUTPUT = ROOT / "data" / "state" / "stock_market_context.json"
TIMEOUT = int(os.environ.get("HITHINK_TIMEOUT_SECONDS", "25"))
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def thscode(code: str) -> str:
    code = code.strip()
    if code.startswith(("60", "68", "51", "56", "58")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "15")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return code


def as_beijing(timestamp_ms: object) -> str:
    if timestamp_ms in (None, ""):
        return ""
    return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=SHANGHAI).isoformat(timespec="seconds")


def market_phase(timestamp_ms: object) -> str:
    dt = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=SHANGHAI) if timestamp_ms not in (None, "") else datetime.now(SHANGHAI)
    minute = dt.hour * 60 + dt.minute
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return "OPENING_CALL_AUCTION"
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        return "CONTINUOUS_MORNING"
    if 13 * 60 <= minute < 14 * 60 + 57:
        return "CONTINUOUS_AFTERNOON"
    if 14 * 60 + 57 <= minute <= 15 * 60:
        return "CLOSING_CALL_AUCTION"
    if 15 * 60 < minute <= 15 * 60 + 15:
        return "POST_CLOSE_GRACE"
    return "OUTSIDE_SESSION"


def validate_item(code: str, item: dict) -> None:
    required = ("open_price", "high_price", "low_price", "last_price", "volume", "turnover")
    missing = [key for key in required if item.get(key) is None]
    if missing:
        raise RuntimeError(f"{code} missing fields: {','.join(missing)}")
    o, h, low, close = (float(item[key]) for key in ("open_price", "high_price", "low_price", "last_price"))
    if h < max(o, low, close) or low > min(o, h, close):
        raise RuntimeError(f"{code} failed OHLC relationship")


def fetch_many(cli: str, stocks: list[dict]) -> dict[str, dict]:
    raw_dir = ROOT / "data" / "market" / "raw" / "account_stocks"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "current_account_stocks.json"
    requested = {thscode(str(stock.get("code", ""))): stock for stock in stocks if stock.get("code")}
    completed = subprocess.run(
        [cli, "market", "snapshot", "--thscodes", ",".join(requested), "--output", str(path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, check=False,
    )
    if completed.returncode != 0 or not path.exists():
        raise RuntimeError((completed.stderr or completed.stdout or "market snapshot failed")[-800:])
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("data", {}).get("item") or []
    if not payload.get("ok") or payload.get("meta", {}).get("source") != "remote":
        raise RuntimeError("invalid or non-remote market snapshot payload")
    timestamp_ms = payload.get("data", {}).get("timestamp")
    returned = {str(item.get("thscode", "")): item for item in items}
    result: dict[str, dict] = {}
    for requested_thscode, stock in requested.items():
        code = str(stock.get("code", ""))
        item = returned.get(requested_thscode)
        if item is None:
            result[code] = {"code": code, "name": stock.get("name", ""), "thscode": requested_thscode, "quality_status": "FAILED", "error": "provider returned no exact item"}
            continue
        try:
            validate_item(code, item)
        except Exception as exc:
            result[code] = {"code": code, "name": stock.get("name", ""), "thscode": requested_thscode, "quality_status": "FAILED", "error": str(exc)}
            continue
        result[code] = {
            "code": code,
            "name": stock.get("name", ""),
            "thscode": requested_thscode,
            "open": item.get("open_price"),
            "high": item.get("high_price"),
            "low": item.get("low_price"),
            "close": item.get("last_price"),
            "prev_close": item.get("prev_price"),
            "change_pct": item.get("price_change_ratio_pct"),
            "volume": item.get("volume"),
            "amount": item.get("turnover"),
            "provider_timestamp_ms": timestamp_ms,
            "as_of_beijing": as_beijing(timestamp_ms),
            "market_phase": market_phase(timestamp_ms),
            "provider": "hithink-finance",
            "provider_request_id": payload.get("meta", {}).get("request_id", ""),
            "quality_status": "PASS",
            "quantity": stock.get("quantity"),
            "market_value_from_account": stock.get("market_value"),
        }
    return result


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
    try:
        result["objects"] = fetch_many(cli, stocks)
    except Exception as exc:
        result["objects"] = {
            str(stock.get("code", "")): {
                "code": str(stock.get("code", "")),
                "name": stock.get("name", ""),
                "quality_status": "FAILED",
                "error": str(exc)[-800:],
            }
            for stock in stocks if stock.get("code")
        }
    failures = sum(1 for item in result["objects"].values() if item.get("quality_status") != "PASS")
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
