from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
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
BEIJING = ZoneInfo("Asia/Shanghai")

OBJECTS = {
    "NDX": {"name": "纳斯达克100指数", "symbol": "^NDX", "timezone": "America/New_York", "role": "US_TECH"},
    "SOX": {"name": "费城半导体指数", "symbol": "^SOX", "timezone": "America/New_York", "role": "US_SEMICONDUCTOR"},
    "N225": {"name": "日经225指数", "symbol": "^N225", "timezone": "Asia/Tokyo", "role": "JAPAN_EQUITY"},
    "KOSPI": {"name": "韩国综合指数", "symbol": "^KS11", "timezone": "Asia/Seoul", "role": "KOREA_EQUITY"},
    "TWII": {"name": "台湾加权指数", "symbol": "^TWII", "timezone": "Asia/Taipei", "role": "TAIWAN_EQUITY"},
    "HSTECH": {"name": "恒生科技指数", "symbol": "HSTECH.HK", "timezone": "Asia/Hong_Kong", "role": "HK_TECH"},
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
            dt_beijing = dt_utc.astimezone(BEIJING)
            row.update({
                "volume": volume_values[idx] if idx < len(volume_values) else None,
                "timestamp": ts,
                "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "as_of_local": dt_local.isoformat(timespec="seconds"),
                "as_of_beijing": dt_beijing.isoformat(timespec="seconds"),
                "market_date_local": dt_local.date().isoformat(),
                "provider_timezone": result.get("meta", {}).get("timezone", timezone_name),
                "symbol": symbol,
            })
            return row
    raise RuntimeError("no complete latest row")


def validate_latest(latest: dict) -> str:
    low, high, open_, close = latest.get("low"), latest.get("high"), latest.get("open"), latest.get("close")
    if any(value is None for value in (low, high, open_, close)):
        return "FAILED"
    if not (low <= open_ <= high and low <= close <= high):
        return "FAILED"
    return "PASS"


def time_relation(object_id: str, latest: dict, phase: str, generated_utc: datetime) -> str:
    sh_date = generated_utc.astimezone(BEIJING).date().isoformat()
    source_date = latest.get("market_date_local", "")
    if object_id in {"NDX", "SOX"}:
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
        "object": object_id, "name": spec["name"], "reference_role": spec["role"],
        "provider": "yahoo_chart_api", "symbol": spec["symbol"], "market_timezone": spec["timezone"],
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation(object_id, latest, phase, generated_utc),
        "quality_status": quality, "latest": latest,
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）；同时保留本地市场时区和market_phase用于跨市场解释。",
        "decision_note": "必须按北京时间数据时点与market_phase解释；不同市场非同一时点，不得把上一收盘、盘中和当日收盘混为同步信号。",
    }


def fetch_hstech_eastmoney(generated_utc: datetime) -> dict:
    params = {
        "secid": "124.HSTECH",
        "fltt": "2",
        "invt": "2",
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f124",
    }
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.15"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    data = payload.get("data") or {}
    required = {"open": data.get("f46"), "high": data.get("f44"), "low": data.get("f45"), "close": data.get("f43")}
    if any(value in (None, "-") for value in required.values()):
        raise RuntimeError("eastmoney 124.HSTECH missing OHLC fields")
    raw_ts = data.get("f124")
    if raw_ts in (None, "", 0):
        raise RuntimeError("eastmoney 124.HSTECH missing provider timestamp f124")
    ts = int(raw_ts)
    if ts > 10_000_000_000:
        ts //= 1000
    dt_utc = datetime.fromtimestamp(ts, timezone.utc)
    local = dt_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
    beijing = dt_utc.astimezone(BEIJING)
    latest = {
        **required,
        "volume": data.get("f47"),
        "amount": data.get("f48"),
        "previous_close": data.get("f60"),
        "timestamp": ts,
        "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "as_of_local": local.isoformat(timespec="seconds"),
        "as_of_beijing": beijing.isoformat(timespec="seconds"),
        "market_date_local": local.date().isoformat(),
        "provider_timezone": "Asia/Hong_Kong",
        "symbol": "124.HSTECH",
    }
    phase = market_phase("Asia/Hong_Kong", generated_utc)
    return {
        "object": "HSTECH",
        "name": "恒生科技指数",
        "reference_role": "HK_TECH",
        "provider": "eastmoney_push2",
        "symbol": "124.HSTECH",
        "market_timezone": "Asia/Hong_Kong",
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation("HSTECH", latest, phase, generated_utc),
        "quality_status": validate_latest(latest),
        "latest": latest,
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）。",
        "decision_note": "东方财富为恒生科技指数直接行情备源；必须使用provider时间戳f124，不得用workflow执行时间冒充行情时间。",
    }


def fetch_hstech_hithink(generated_utc: datetime) -> dict:
    cli = shutil.which("hithink-finance")
    if not cli:
        raise RuntimeError("hithink-finance CLI not found")
    thscode = os.environ.get("HSTECH_HITHINK_CODE", "HS2083")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "hstech.json"
        completed = subprocess.run([cli, "index", "snapshot", "--thscodes", thscode, "--output", str(out), "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, check=False)
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
        local = generated_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
        beijing = generated_utc.astimezone(BEIJING)
        latest = {"open": item["open_price"], "high": item["high_price"], "low": item["low_price"], "close": item["last_price"], "volume": item.get("volume"), "amount": item.get("turnover"), "as_of_utc": generated_utc.isoformat(timespec="seconds").replace("+00:00", "Z"), "as_of_local": local.isoformat(timespec="seconds"), "as_of_beijing": beijing.isoformat(timespec="seconds"), "market_date_local": local.date().isoformat(), "provider_timezone": "Asia/Hong_Kong", "symbol": thscode}
        phase = market_phase("Asia/Hong_Kong", generated_utc)
        return {"object": "HSTECH", "name": "恒生科技指数", "reference_role": "HK_TECH", "provider": "hithink-finance", "symbol": thscode, "market_timezone": "Asia/Hong_Kong", "market_phase_at_generation": phase, "time_relation_to_a_share": time_relation("HSTECH", latest, phase, generated_utc), "quality_status": validate_latest(latest), "latest": latest, "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）。", "decision_note": "恒生科技与A股交易时段部分重合但收盘时点不同；必须按北京时间数据时点和香港market_phase解释。"}


def build() -> dict:
    generated_utc = datetime.now(timezone.utc)
    generated_beijing = generated_utc.astimezone(BEIJING)
    objects = {}
    pass_count = 0
    for object_id, spec in OBJECTS.items():
        try:
            if object_id == "HSTECH":
                try:
                    record = fetch_hstech_hithink(generated_utc)
                except Exception as hithink_error:
                    try:
                        record = fetch_yahoo(object_id, spec, generated_utc)
                        record["fallback_from"] = "hithink-finance:HS2083"
                        record["fallback_reason"] = str(hithink_error)[-300:]
                    except Exception as yahoo_error:
                        record = fetch_hstech_eastmoney(generated_utc)
                        record["fallback_from"] = "hithink-finance:HS2083 -> yahoo_chart_api:HSTECH.HK"
                        record["fallback_reason"] = f"hithink={str(hithink_error)[-180:]}; yahoo={str(yahoo_error)[-180:]}"
            else:
                record = fetch_yahoo(object_id, spec, generated_utc)
            if record["quality_status"] == "PASS":
                pass_count += 1
            objects[object_id] = record
        except Exception as exc:
            objects[object_id] = {"object": object_id, "name": spec["name"], "reference_role": spec["role"], "provider": "hithink-finance/yahoo_chart_api/eastmoney_push2" if object_id == "HSTECH" else "yahoo_chart_api", "symbol": "HS2083/HSTECH.HK/124.HSTECH" if object_id == "HSTECH" else spec["symbol"], "market_timezone": spec["timezone"], "market_phase_at_generation": market_phase(spec["timezone"], generated_utc), "generated_at_beijing": generated_beijing.isoformat(timespec="seconds"), "quality_status": "FAILED", "error": str(exc)[-500:], "decision_note": "监测对象保留但当前数据不可用；不得用旧值冒充当前状态。"}
    overall = "PASS" if pass_count == len(OBJECTS) else ("DEGRADED" if pass_count else "FAILED")
    return {
        "generated_at": now_utc(),
        "generated_at_beijing": generated_beijing.isoformat(timespec="seconds"),
        "display_timezone": "Asia/Shanghai",
        "a_share_reference_timezone": "Asia/Shanghai",
        "scope": "FORMAL_OVERSEAS_AND_ASIA_INDEX_LAYER",
        "quality_status": overall,
        "required_objects": list(OBJECTS.keys()),
        "objects": objects,
        "output_time_rule": "任何正式行情输出必须明确标注数据时点，并统一优先转换为北京时间；海外对象同时保留market_timezone和market_phase，禁止只显示自然日期。",
        "time_alignment_rule": "跨市场证据必须同时读取北京时间数据时点、市场本地时区、as_of_local、market_phase和time_relation_to_a_share。美国现金指数在A股交易时段通常代表上一美股交易时段；亚洲市场按同日盘中/已收盘/上一交易日分别解释。",
        "decision_boundary": "海外与亚洲指数是正式市场监测层的重要组成，但只作风险背景、增强或反向证据；必须继续经过本地传导与目标ETF自身反馈，不能单独生成ETF买卖动作。",
    }


def main() -> None:
    context = build()
    atomic_json_write(ROOT / "data" / "state" / "overseas_context.json", context)
    print(json.dumps({"ok": True, "generated_at_beijing": context["generated_at_beijing"], "quality_status": context["quality_status"], "objects": {k: v["quality_status"] for k, v in context["objects"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
