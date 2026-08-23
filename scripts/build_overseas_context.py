from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from multi_source_market import _request_json
    from state_manager import atomic_json_write, now_utc
except ModuleNotFoundError:
    from scripts.multi_source_market import _request_json
    from scripts.state_manager import atomic_json_write, now_utc

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")

# Formal overseas/Asia index layer used by ETF decisions. Data availability may
# degrade an object, but an object is not silently removed from the monitoring
# frame merely because one provider is temporarily unavailable.
OBJECTS = {
    "NDX": {"name": "纳斯达克100指数", "symbol": "^NDX", "timezone": "America/New_York", "role": "US_TECH"},
    "SOX": {"name": "费城半导体指数", "symbol": "^SOX", "timezone": "America/New_York", "role": "US_SEMICONDUCTOR"},
    "N225": {"name": "日经225指数", "symbol": "^N225", "timezone": "Asia/Tokyo", "role": "JAPAN_EQUITY"},
    "KOSPI": {"name": "韩国综合指数", "symbol": "^KS11", "timezone": "Asia/Seoul", "role": "KOREA_EQUITY"},
    "TWII": {"name": "台湾加权指数", "symbol": "^TWII", "timezone": "Asia/Taipei", "role": "TAIWAN_EQUITY"},
    "HSTECH": {"name": "恒生科技指数", "symbol": "^HSTECH", "timezone": "Asia/Hong_Kong", "role": "HK_TECH"},
}

SESSIONS = {
    "America/New_York": [((9, 30), (16, 0))],
    "Asia/Tokyo": [((9, 0), (11, 30)), ((12, 30), (15, 30))],
    "Asia/Seoul": [((9, 0), (15, 30))],
    "Asia/Taipei": [((9, 0), (13, 30))],
    "Asia/Hong_Kong": [((9, 30), (12, 0)), ((13, 0), (16, 0))],
}


def _minutes(hour_minute: tuple[int, int]) -> int:
    return hour_minute[0] * 60 + hour_minute[1]


def market_phase(timezone_name: str, now_utc: datetime) -> str:
    zone = ZoneInfo(timezone_name)
    local = now_utc.astimezone(zone)
    if local.weekday() >= 5:
        return "CLOSED_NON_TRADING_DAY"
    minute = local.hour * 60 + local.minute
    sessions = SESSIONS.get(timezone_name, [])
    if any(_minutes(start) <= minute < _minutes(end) for start, end in sessions):
        return "OPEN"
    first_open = min((_minutes(start) for start, _ in sessions), default=0)
    last_close = max((_minutes(end) for _, end in sessions), default=0)
    if minute < first_open:
        return "PRE_OPEN"
    if minute >= last_close:
        return "CLOSED"
    return "BREAK"


def latest_valid_row(payload: dict, symbol: str, timezone_name: str) -> dict:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    zone = ZoneInfo(timezone_name)
    for idx in range(len(timestamps) - 1, -1, -1):
        row = {}
        complete = True
        for field in ("open", "high", "low", "close"):
            values = quote.get(field) or []
            value = values[idx] if idx < len(values) else None
            row[field] = value
            complete = complete and value is not None
        if complete:
            volume_values = quote.get("volume") or []
            ts = int(timestamps[idx])
            dt_utc = datetime.fromtimestamp(ts, timezone.utc)
            dt_local = dt_utc.astimezone(zone)
            row.update({
                "volume": volume_values[idx] if idx < len(volume_values) else None,
                "timestamp": ts,
                "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "as_of_local": dt_local.isoformat(timespec="seconds"),
                "market_date_local": dt_local.date().isoformat(),
                "provider_timezone": result.get("meta", {}).get("timezone", timezone_name),
                "symbol": symbol,
            })
            return row
    raise RuntimeError("no complete latest row")


def validate_latest(latest: dict) -> str:
    low = latest.get("low")
    high = latest.get("high")
    open_ = latest.get("open")
    close = latest.get("close")
    if any(value is None for value in (low, high, open_, close)):
        return "FAILED"
    if not (low <= open_ <= high and low <= close <= high):
        return "FAILED"
    return "PASS"


def time_relation(object_id: str, latest: dict, phase: str, generated_utc: datetime) -> str:
    sh_date = generated_utc.astimezone(SHANGHAI).date().isoformat()
    source_date = latest.get("market_date_local", "")
    if object_id in {"NDX", "SOX"}:
        # During normal A-share trading hours the US cash market is not in the
        # same session. Treat the latest cash-index bar as prior-US-session
        # evidence, never as simultaneous A-share intraday evidence.
        return "PREVIOUS_US_SESSION_REFERENCE"
    if source_date == sh_date and phase == "OPEN":
        return "SAME_DAY_LIVE_OR_LATEST_PROVIDER_BAR"
    if source_date == sh_date and phase in {"BREAK", "CLOSED"}:
        return "SAME_DAY_SESSION_REFERENCE"
    return "PREVIOUS_SESSION_REFERENCE"


def fetch_yahoo(object_id: str, spec: dict, generated_utc: datetime) -> dict:
    payload = _request_json(spec["symbol"], period="5d")
    latest = latest_valid_row(payload, spec["symbol"], spec["timezone"])
    phase = market_phase(spec["timezone"], generated_utc)
    quality = validate_latest(latest)
    return {
        "object": object_id,
        "name": spec["name"],
        "reference_role": spec["role"],
        "provider": "yahoo_chart_api",
        "symbol": spec["symbol"],
        "market_timezone": spec["timezone"],
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation(object_id, latest, phase, generated_utc),
        "quality_status": quality,
        "latest": latest,
        "decision_note": "必须按as_of与market_phase解释；不同市场非同一时点，不得把上一收盘、盘中和当日收盘混为同步信号。",
    }


def fetch_hstech_hithink(generated_utc: datetime) -> dict:
    cli = shutil.which("hithink-finance")
    if not cli:
        raise RuntimeError("hithink-finance CLI not found")
    thscode = os.environ.get("HSTECH_HITHINK_CODE", "HS2083")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "hstech.json"
        completed = subprocess.run(
            [cli, "index", "snapshot", "--thscodes", thscode, "--output", str(out), "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, check=False,
        )
        if completed.returncode != 0 or not out.exists():
            raise RuntimeError((completed.stderr or "hithink HS2083 request failed")[-500:])
        obj = json.loads(out.read_text(encoding="utf-8"))
        items = obj.get("data", {}).get("item") or []
        if len(items) != 1:
            raise RuntimeError(f"HS2083 expected one row, got {len(items)}")
        item = items[0]
        required = ("open_price", "high_price", "low_price", "last_price")
        if any(item.get(k) is None for k in required):
            raise RuntimeError("HS2083 missing OHLC fields")
        captured = generated_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
        latest = {
            "open": item["open_price"], "high": item["high_price"], "low": item["low_price"], "close": item["last_price"],
            "volume": item.get("volume"), "amount": item.get("turnover"),
            "as_of_utc": generated_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "as_of_local": captured.isoformat(timespec="seconds"),
            "market_date_local": captured.date().isoformat(),
            "provider_timezone": "Asia/Hong_Kong", "symbol": thscode,
        }
        phase = market_phase("Asia/Hong_Kong", generated_utc)
        return {
            "object": "HSTECH", "name": "恒生科技指数", "reference_role": "HK_TECH",
            "provider": "hithink-finance", "symbol": thscode, "market_timezone": "Asia/Hong_Kong",
            "market_phase_at_generation": phase,
            "time_relation_to_a_share": time_relation("HSTECH", latest, phase, generated_utc),
            "quality_status": validate_latest(latest), "latest": latest,
            "decision_note": "恒生科技与A股交易时段部分重合但收盘时点不同；A股15:00时香港市场通常仍未完成当日收盘，必须按盘中时点解释。",
        }


def build() -> dict:
    generated_utc = datetime.now(timezone.utc)
    objects = {}
    pass_count = 0
    for object_id, spec in OBJECTS.items():
        try:
            if object_id == "HSTECH":
                try:
                    record = fetch_hstech_hithink(generated_utc)
                except Exception as hithink_error:
                    record = fetch_yahoo(object_id, spec, generated_utc)
                    record["fallback_from"] = "hithink-finance:HS2083"
                    record["fallback_reason"] = str(hithink_error)[-300:]
            else:
                record = fetch_yahoo(object_id, spec, generated_utc)
            if record["quality_status"] == "PASS":
                pass_count += 1
            objects[object_id] = record
        except Exception as exc:
            objects[object_id] = {
                "object": object_id,
                "name": spec["name"],
                "reference_role": spec["role"],
                "provider": "hithink-finance/yahoo_chart_api" if object_id == "HSTECH" else "yahoo_chart_api",
                "symbol": "HS2083/^HSTECH" if object_id == "HSTECH" else spec["symbol"],
                "market_timezone": spec["timezone"],
                "market_phase_at_generation": market_phase(spec["timezone"], generated_utc),
                "quality_status": "FAILED",
                "error": str(exc)[-500:],
                "decision_note": "监测对象保留但当前数据不可用；不得用旧值冒充当前状态，也不得因单一对象失败删除该监测职责。",
            }
    overall = "PASS" if pass_count == len(OBJECTS) else ("DEGRADED" if pass_count else "FAILED")
    return {
        "generated_at": now_utc(),
        "a_share_reference_timezone": "Asia/Shanghai",
        "scope": "FORMAL_OVERSEAS_AND_ASIA_INDEX_LAYER",
        "quality_status": overall,
        "required_objects": list(OBJECTS.keys()),
        "objects": objects,
        "time_alignment_rule": "跨市场证据必须同时读取市场本地时区、as_of、market_phase和time_relation_to_a_share。美国现金指数在A股交易时段通常代表上一美股交易时段；亚洲市场按同日盘中/已收盘/上一交易日分别解释；不得把不同市场非同步价格当作同一时点共振。",
        "decision_boundary": "海外与亚洲指数是正式市场监测层的重要组成，但只作风险背景、增强或反向证据；必须继续经过本地传导与目标ETF自身反馈，不能单独生成ETF买卖动作。",
    }


def main() -> None:
    context = build()
    atomic_json_write(ROOT / "data" / "state" / "overseas_context.json", context)
    print(json.dumps({
        "ok": True,
        "quality_status": context["quality_status"],
        "objects": {k: v["quality_status"] for k, v in context["objects"].items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
