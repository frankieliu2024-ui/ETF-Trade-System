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
    from market_data_guard import classify_provider_failure, update_structural_health
except ModuleNotFoundError:
    from scripts.multi_source_market import _request_json
    from scripts.state_manager import atomic_json_write, now_utc
    from scripts.market_data_guard import classify_provider_failure, update_structural_health

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = ZoneInfo("Asia/Shanghai")

QUALITY_STATUS_CN = {
    "PASS": "数据可用",
    "DEGRADED": "数据受限",
    "FAILED": "当前不可用",
    "MISSING": "当前缺失",
}
FRESHNESS_STATUS_CN = {
    "FRESH": "时点正常",
    "DELAYED": "存在延迟",
    "STALE": "时点过旧",
    "SESSION_REFERENCE": "已结束交易时段参考",
    "PREVIOUS_SESSION_REFERENCE": "上一交易时段参考",
    "MISSING": "当前缺失",
}

OBJECTS = {
    "NDX": {"name": "纳斯达克100指数", "symbol": "^NDX", "timezone": "America/New_York", "role": "US_TECH"},
    "SOX": {"name": "费城半导体指数", "symbol": "^SOX", "timezone": "America/New_York", "role": "US_SEMICONDUCTOR"},
    "N225": {"name": "日经225指数", "symbol": "^N225", "timezone": "Asia/Tokyo", "role": "JAPAN_EQUITY"},
    "KOSPI": {"name": "韩国综合指数", "symbol": "^KS11", "timezone": "Asia/Seoul", "role": "KOREA_EQUITY"},
    "TWII": {"name": "台湾加权指数", "symbol": "^TWII", "timezone": "Asia/Taipei", "role": "TAIWAN_EQUITY"},
    "HSTECH": {"name": "恒生科技指数", "symbol": "HSTECH.HK", "timezone": "Asia/Hong_Kong", "role": "HK_TECH"},
}

EASTMONEY_DIRECT_FALLBACKS = {
    "NDX": "100.NDX100",
    "N225": "100.N225",
    "KOSPI": "100.KS11",
    "TWII": "100.TWII",
}

SESSIONS = {
    "America/New_York": [((9, 30), (16, 0))],
    "Asia/Tokyo": [((9, 0), (11, 30)), ((12, 30), (15, 30))],
    "Asia/Seoul": [((9, 0), (15, 30))],
    "Asia/Taipei": [((9, 0), (13, 30))],
    "Asia/Hong_Kong": [((9, 30), (12, 0)), ((13, 0), (16, 0))],
}


def load_provider_health() -> dict:
    path = ROOT / "data" / "state" / "overseas_runtime_health.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload.get("provider_health") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _minutes(value: tuple[int, int]) -> int:
    return value[0] * 60 + value[1]


def market_phase(timezone_name: str, now_utc: datetime) -> str:
    local = now_utc.astimezone(ZoneInfo(timezone_name))
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


def parse_provider_datetime(raw_value, timezone_name: str) -> datetime:
    if raw_value in (None, "", 0, "0"):
        raise RuntimeError("provider timestamp missing")
    zone = ZoneInfo(timezone_name)
    if isinstance(raw_value, (int, float)) or str(raw_value).isdigit():
        ts = int(float(raw_value))
        if ts > 10_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts, timezone.utc)
    parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


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
        if not complete:
            continue
        ts = int(timestamps[idx])
        dt_utc = datetime.fromtimestamp(ts, timezone.utc)
        dt_local = dt_utc.astimezone(zone)
        previous_close = None
        close_values = quote.get("close") or []
        for j in range(idx - 1, -1, -1):
            candidate = close_values[j] if j < len(close_values) else None
            if candidate is not None:
                previous_close = candidate
                break
        row.update({
            "volume": ((quote.get("volume") or [None])[idx] if idx < len(quote.get("volume") or []) else None),
            "previous_close": previous_close,
            "timestamp": ts,
            "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "as_of_local": dt_local.isoformat(timespec="seconds"),
            "as_of_beijing": dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),
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
    return "PASS" if low <= open_ <= high and low <= close <= high else "FAILED"


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


def freshness_for_pulse(record: dict, generated_utc: datetime) -> tuple[str, bool, float | None]:
    latest = record.get("latest") or {}
    as_of = latest.get("as_of_utc")
    if not as_of:
        return "STALE", False, None
    observed = parse_provider_datetime(as_of, record.get("market_timezone", "Asia/Hong_Kong"))
    delay = max(0.0, (generated_utc - observed).total_seconds() / 60)
    phase = record.get("market_phase_at_generation", "")
    local_zone = ZoneInfo(record.get("market_timezone", "Asia/Hong_Kong"))
    same_local_date = latest.get("market_date_local") == generated_utc.astimezone(local_zone).date().isoformat()
    if phase == "OPEN":
        if same_local_date and delay <= 10:
            return "FRESH", True, round(delay, 2)
        if same_local_date and delay <= 20:
            return "DELAYED", False, round(delay, 2)
        return "STALE", False, round(delay, 2)
    if phase in {"BREAK", "CLOSED"} and same_local_date:
        return "SESSION_REFERENCE", True, round(delay, 2)
    return "PREVIOUS_SESSION_REFERENCE", False, round(delay, 2)


def provider_attempt(record: dict, provider_id: str, generated_utc: datetime) -> dict:
    freshness, stable, delay = freshness_for_pulse(record, generated_utc)
    latest = record.get("latest") or {}
    return {
        "provider": provider_id,
        "result": "PASS" if stable else "DEGRADED",
        "http_or_cli_result": record.get("provider_result", ""),
        "quality_status": record.get("quality_status", "MISSING"),
        "freshness_status": freshness,
        "stable_for_10m_pulse": stable,
        "symbol": record.get("symbol", ""),
        "latest_price": latest.get("close"),
        "open": latest.get("open"), "high": latest.get("high"), "low": latest.get("low"), "close": latest.get("close"),
        "provider_timestamp": latest.get("timestamp"),
        "provider_timestamp_field": record.get("provider_timestamp_field", ""),
        "as_of_beijing": latest.get("as_of_beijing", ""),
        "market_phase": record.get("market_phase_at_generation", ""),
        "delay_minutes": delay,
    }


def failed_attempt(provider_id: str, result: str, generated_utc: datetime, *, timezone_name: str = "Asia/Hong_Kong") -> dict:
    return {
        "provider": provider_id, "result": "FAILED", "http_or_cli_result": result,
        "quality_status": "FAILED", "freshness_status": "MISSING", "stable_for_10m_pulse": False,
        "as_of_beijing": "", "market_phase": market_phase(timezone_name, generated_utc), "delay_minutes": None,
    }


def compare_hstech_attempts(attempts: list[dict]) -> dict:
    usable = [x for x in attempts if x.get("latest_price") is not None and x.get("as_of_beijing")]
    if len(usable) < 2:
        return {"comparable": False, "reason": "fewer_than_two_timestamped_direct_sources"}
    times = [parse_provider_datetime(x["as_of_beijing"], "Asia/Hong_Kong") for x in usable]
    prices = [float(x["latest_price"]) for x in usable]
    gap = (max(times) - min(times)).total_seconds() / 60
    price_gap = (max(prices) - min(prices)) / min(prices) * 100 if min(prices) else None
    return {
        "comparable": gap <= 10,
        "time_gap_minutes": round(gap, 2),
        "price_gap_pct": round(price_gap, 4) if price_gap is not None else None,
        "reason": "timestamps_aligned" if gap <= 10 else "timestamps_not_aligned",
    }


def select_best_candidate(candidates: list[tuple[dict, dict]]) -> tuple[dict, dict] | None:
    eligible = [pair for pair in candidates if pair[0].get("quality_status") == "PASS" and pair[1].get("as_of_beijing") and pair[1].get("delay_minutes") is not None]
    if not eligible:
        return None
    stable = [pair for pair in eligible if pair[1].get("stable_for_10m_pulse") is True]
    pool = stable or eligible
    return min(pool, key=lambda pair: float(pair[1].get("delay_minutes", float("inf"))))


def select_hstech_candidate(candidates: list[tuple[dict, dict]]) -> tuple[dict, dict, str] | None:
    """Eastmoney 124.HSTECH is the formal primary after live-session validation.

    Fallbacks are only used when the primary is unavailable, invalid, or not fresh enough.
    """
    for record, summary in candidates:
        if summary.get("provider") == "eastmoney_push2:124.HSTECH" and record.get("quality_status") == "PASS" and summary.get("stable_for_10m_pulse") is True:
            return record, summary, "configured_primary_eastmoney_valid_and_fresh"
    selected = select_best_candidate(candidates)
    if selected:
        return selected[0], selected[1], "primary_unavailable_or_degraded_then_lowest_latency_valid_direct_fallback"
    return None


def fetch_yahoo(object_id: str, spec: dict, generated_utc: datetime) -> dict:
    payload = _request_json(spec["symbol"], period="5d")
    latest = latest_valid_row(payload, spec["symbol"], spec["timezone"])
    phase = market_phase(spec["timezone"], generated_utc)
    return {
        "object": object_id, "name": spec["name"], "reference_role": spec["role"],
        "provider": "yahoo_chart_api", "symbol": spec["symbol"], "market_timezone": spec["timezone"],
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation(object_id, latest, phase, generated_utc),
        "quality_status": validate_latest(latest), "latest": latest,
        "provider_result": "HTTP 200", "provider_timestamp_field": "chart.timestamp",
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）；同时保留本地市场时区和market_phase。",
        "decision_note": "不同市场非同一时点，不得把上一收盘、盘中和当日收盘混为同步信号。",
    }


def fetch_naver_kospi(generated_utc: datetime) -> dict:
    url = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI"
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16"})
    with urllib.request.urlopen(req, timeout=20) as response:
        status_code = int(response.status)
        payload = json.load(response)
    rows = payload.get("datas") or []
    if len(rows) != 1:
        raise RuntimeError(f"Naver KOSPI expected one row, got {len(rows)}")
    data = rows[0]
    def num(key):
        raw = data.get(key + "Raw", data.get(key))
        if raw in (None, "", "-"):
            return None
        return float(str(raw).replace(",", ""))
    required = {"open": num("openPrice"), "high": num("highPrice"), "low": num("lowPrice"), "close": num("closePrice")}
    if any(v is None for v in required.values()):
        raise RuntimeError("Naver KOSPI missing OHLC fields")
    raw_time = data.get("localTradedAt")
    if not raw_time:
        raise RuntimeError("Naver KOSPI missing localTradedAt")
    local = datetime.fromisoformat(str(raw_time))
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    dt_utc = local.astimezone(timezone.utc)
    latest = {
        **required,
        "volume": num("accumulatedTradingVolume"),
        "amount": num("accumulatedTradingValue"),
        "timestamp": int(dt_utc.timestamp()),
        "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "as_of_local": local.isoformat(timespec="seconds"),
        "as_of_beijing": dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),
        "market_date_local": local.date().isoformat(),
        "provider_timezone": "Asia/Seoul",
        "symbol": "KOSPI",
    }
    phase = market_phase("Asia/Seoul", generated_utc)
    return {
        "object": "KOSPI", "name": "韩国综合指数", "reference_role": "KOREA_EQUITY",
        "provider": "naver_finance", "provider_result": f"HTTP {status_code}",
        "provider_timestamp_field": "localTradedAt", "symbol": "KOSPI", "market_timezone": "Asia/Seoul",
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation("KOSPI", latest, phase, generated_utc),
        "quality_status": validate_latest(latest), "latest": latest,
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）；同时保留韩国本地交易时点。",
        "decision_note": "Naver Finance公开实时指数JSON；使用localTradedAt作为provider时点，作为韩国综合指数直连备源。",
    }


def fetch_twse_taiex(generated_utc: datetime) -> dict:
    params = {"ex_ch": "tse_t00.tw", "json": "1", "delay": "0", "_": str(int(generated_utc.timestamp() * 1000))}
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16", "Referer": "https://mis.twse.com.tw/stock/fibest.jsp?stock=t00"})
    with urllib.request.urlopen(req, timeout=20) as response:
        status_code = int(response.status); payload = json.load(response)
    rows = payload.get("msgArray") or []
    if len(rows) != 1: raise RuntimeError(f"TWSE MIS TAIEX expected one row, got {len(rows)}")
    data = rows[0]
    def num(key):
        raw=data.get(key)
        if raw in (None,"","-"): return None
        return float(str(raw).replace(",",""))
    required={"open":num("o"),"high":num("h"),"low":num("l"),"close":num("z")}
    if any(v is None for v in required.values()): raise RuntimeError("TWSE MIS TAIEX missing OHLC fields")
    raw_time=data.get("tlong")
    if not raw_time: raise RuntimeError("TWSE MIS TAIEX missing tlong provider timestamp")
    dt_utc=parse_provider_datetime(raw_time,"Asia/Taipei"); local=dt_utc.astimezone(ZoneInfo("Asia/Taipei"))
    latest={**required,"volume":num("v"),"previous_close":num("y"),"timestamp":int(dt_utc.timestamp()),"as_of_utc":dt_utc.isoformat(timespec="seconds").replace("+00:00","Z"),"as_of_local":local.isoformat(timespec="seconds"),"as_of_beijing":dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),"market_date_local":local.date().isoformat(),"provider_timezone":"Asia/Taipei","symbol":"tse_t00.tw"}
    phase=market_phase("Asia/Taipei",generated_utc)
    return {"object":"TWII","name":"台湾加权指数","reference_role":"TAIWAN_EQUITY","provider":"twse_mis","provider_result":f"HTTP {status_code}","provider_timestamp_field":"tlong","symbol":"tse_t00.tw","market_timezone":"Asia/Taipei","market_phase_at_generation":phase,"time_relation_to_a_share":time_relation("TWII",latest,phase,generated_utc),"quality_status":validate_latest(latest),"latest":latest,"display_time_rule":"正式输出优先显示latest.as_of_beijing（北京时间）；同时保留台湾本地交易时点。","decision_note":"台湾证券交易所官方MIS为台湾加权指数正式实时主源；使用tlong作为provider时点，不得用请求时间代替行情时间。"}


def fetch_eastmoney_index(object_id: str, spec: dict, generated_utc: datetime, secid: str, *, host: str = "push2.eastmoney.com") -> dict:
    params = {"secid": secid, "fltt": "2", "invt": "2", "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f124"}
    url = f"https://{host}/api/qt/stock/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16"})
    with urllib.request.urlopen(req, timeout=20) as response:
        status_code = int(response.status)
        payload = json.load(response)
    data = payload.get("data") or {}
    required = {"open": data.get("f46"), "high": data.get("f44"), "low": data.get("f45"), "close": data.get("f43")}
    if any(value in (None, "-") for value in required.values()):
        raise RuntimeError(f"{host} {secid} missing OHLC fields")
    timestamp_field = "f124" if data.get("f124") not in (None, "", 0, "0") else "f86"
    dt_utc = parse_provider_datetime(data.get(timestamp_field), spec["timezone"])
    local = dt_utc.astimezone(ZoneInfo(spec["timezone"]))
    latest = {
        **required,
        "volume": data.get("f47"), "amount": data.get("f48"), "previous_close": data.get("f60"),
        "timestamp": int(dt_utc.timestamp()),
        "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "as_of_local": local.isoformat(timespec="seconds"),
        "as_of_beijing": dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),
        "market_date_local": local.date().isoformat(), "provider_timezone": spec["timezone"], "symbol": secid,
    }
    phase = market_phase(spec["timezone"], generated_utc)
    return {
        "object": object_id, "name": spec["name"], "reference_role": spec["role"],
        "provider": "eastmoney_push2", "provider_result": f"HTTP {status_code}", "provider_timestamp_field": timestamp_field,
        "provider_endpoint": host, "symbol": secid, "market_timezone": spec["timezone"],
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation(object_id, latest, phase, generated_utc),
        "quality_status": validate_latest(latest), "latest": latest,
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）；同时保留本地市场时区和market_phase。",
        "decision_note": "东方财富对象级直接行情；按当前对象级正式主备策略使用，并始终以provider自身时间戳通过质量门禁。",
    }


def fetch_hstech_eastmoney(generated_utc: datetime, *, host: str = "push2.eastmoney.com") -> dict:
    params = {"secid": "124.HSTECH", "fltt": "2", "invt": "2", "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f124"}
    url = f"https://{host}/api/qt/stock/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ETF-Trade-System/2.2.16"})
    with urllib.request.urlopen(req, timeout=20) as response:
        status_code = int(response.status)
        payload = json.load(response)
    data = payload.get("data") or {}
    required = {"open": data.get("f46"), "high": data.get("f44"), "low": data.get("f45"), "close": data.get("f43")}
    if any(value in (None, "-") for value in required.values()):
        raise RuntimeError(f"{host} 124.HSTECH missing OHLC fields")
    timestamp_field = "f124" if data.get("f124") not in (None, "", 0, "0") else "f86"
    dt_utc = parse_provider_datetime(data.get(timestamp_field), "Asia/Hong_Kong")
    local = dt_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
    latest = {
        **required,
        "volume": data.get("f47"), "amount": data.get("f48"), "previous_close": data.get("f60"),
        "timestamp": int(dt_utc.timestamp()),
        "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "as_of_local": local.isoformat(timespec="seconds"),
        "as_of_beijing": dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),
        "market_date_local": local.date().isoformat(), "provider_timezone": "Asia/Hong_Kong", "symbol": "124.HSTECH",
    }
    phase = market_phase("Asia/Hong_Kong", generated_utc)
    provider_name = "eastmoney_push2delay" if host.startswith("push2delay") else "eastmoney_push2"
    return {
        "object": "HSTECH", "name": "恒生科技指数", "reference_role": "HK_TECH",
        "provider": provider_name, "provider_result": f"HTTP {status_code}", "provider_timestamp_field": timestamp_field,
        "provider_endpoint": host, "symbol": "124.HSTECH", "market_timezone": "Asia/Hong_Kong",
        "market_phase_at_generation": phase,
        "time_relation_to_a_share": time_relation("HSTECH", latest, phase, generated_utc),
        "quality_status": validate_latest(latest), "latest": latest,
        "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）。",
        "decision_note": "东方财富124.HSTECH为恒生科技指数正式直接主源；必须使用provider自身时间戳，不得用workflow时间冒充行情时间。",
    }


def fetch_hstech_hithink(generated_utc: datetime) -> dict:
    cli = shutil.which("hithink-finance")
    if not cli:
        raise RuntimeError("CLI unavailable: hithink-finance not installed")
    thscode = os.environ.get("HSTECH_HITHINK_CODE", "HS2083")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "hstech.json"
        completed = subprocess.run([cli, "index", "snapshot", "--thscodes", thscode, "--output", str(out), "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, check=False)
        if completed.returncode != 0 or not out.exists():
            detail = (completed.stdout or completed.stderr or "hithink request failed")[-500:]
            raise RuntimeError(f"CLI exit={completed.returncode}: {detail}")
        obj = json.loads(out.read_text(encoding="utf-8"))
        items = obj.get("data", {}).get("item") or []
        if len(items) != 1:
            raise RuntimeError(f"CLI exit=0: HS2083 expected one row, got {len(items)}")
        item = items[0]
        required = ("open_price", "high_price", "low_price", "last_price")
        if any(item.get(k) is None for k in required):
            raise RuntimeError("CLI exit=0: HS2083 missing OHLC fields")
        timestamp_fields = ("timestamp", "quote_timestamp", "update_timestamp", "trade_timestamp", "update_time", "trade_time", "datetime")
        timestamp_field = next((field for field in timestamp_fields if item.get(field) not in (None, "", 0, "0")), "")
        raw_timestamp = item.get(timestamp_field) if timestamp_field else None
        if raw_timestamp is None:
            raise RuntimeError("CLI exit=0: HS2083 missing provider timestamp")
        dt_utc = parse_provider_datetime(raw_timestamp, "Asia/Hong_Kong")
        local = dt_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
        latest = {
            "open": item["open_price"], "high": item["high_price"], "low": item["low_price"], "close": item["last_price"],
            "volume": item.get("volume"), "amount": item.get("turnover"), "timestamp": int(dt_utc.timestamp()),
            "as_of_utc": dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "as_of_local": local.isoformat(timespec="seconds"), "as_of_beijing": dt_utc.astimezone(BEIJING).isoformat(timespec="seconds"),
            "market_date_local": local.date().isoformat(), "provider_timezone": "Asia/Hong_Kong", "symbol": thscode,
        }
        phase = market_phase("Asia/Hong_Kong", generated_utc)
        return {
            "object": "HSTECH", "name": "恒生科技指数", "reference_role": "HK_TECH",
            "provider": "hithink-finance", "provider_result": "CLI exit=0", "provider_timestamp_field": timestamp_field,
            "symbol": thscode, "market_timezone": "Asia/Hong_Kong", "market_phase_at_generation": phase,
            "time_relation_to_a_share": time_relation("HSTECH", latest, phase, generated_utc),
            "quality_status": validate_latest(latest), "latest": latest,
            "display_time_rule": "正式输出优先显示latest.as_of_beijing（北京时间）。",
            "decision_note": "同花顺HS2083为恒生科技审计性直接备源。",
        }


def build() -> dict:
    generated_utc = datetime.now(timezone.utc)
    generated_beijing = generated_utc.astimezone(BEIJING)
    objects: dict[str, dict] = {}
    pass_count = 0
    provider_health = load_provider_health()

    for object_id, spec in OBJECTS.items():
        try:
            if object_id != "HSTECH":
                attempts: list[dict] = []
                record: dict | None = None
                selected_provider_id = ""

                # N225/KOSPI/TWII priorities were promoted after live-session parallel validation on
                # 2026-08-27. Production selection uses the first fresh, valid direct source.
                if object_id in {"N225", "KOSPI", "TWII"}:
                    secid = EASTMONEY_DIRECT_FALLBACKS.get(object_id)
                    if object_id == "N225":
                        direct_chain = [(f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),(f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),("yahoo_chart_api", lambda: fetch_yahoo(object_id, spec, generated_utc))]
                        configured_primary = f"eastmoney_push2delay:{secid}"
                    elif object_id == "KOSPI":
                        direct_chain = [("naver_finance:KOSPI", lambda: fetch_naver_kospi(generated_utc)),(f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),(f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),("yahoo_chart_api", lambda: fetch_yahoo(object_id, spec, generated_utc))]
                        configured_primary = "naver_finance:KOSPI"
                    else:
                        direct_chain = [("twse_mis:tse_t00.tw", lambda: fetch_twse_taiex(generated_utc)),(f"eastmoney_push2delay:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2delay.eastmoney.com")),(f"eastmoney_push2:{secid}", lambda: fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")),("yahoo_chart_api", lambda: fetch_yahoo(object_id, spec, generated_utc))]
                        configured_primary = "twse_mis:tse_t00.tw"

                    last_candidate: dict | None = None
                    for provider_id, loader in direct_chain:
                        try:
                            candidate = loader()
                            summary = provider_attempt(candidate, provider_id, generated_utc)
                            attempts.append(summary)
                            last_candidate = candidate
                            if candidate.get("quality_status") == "PASS" and summary.get("stable_for_10m_pulse") is True:
                                record = candidate
                                selected_provider_id = provider_id
                                record["selected_provider_id"] = provider_id
                                record["selection_basis"] = "validated_live_priority_first_usable_direct_source"
                                break
                        except Exception as provider_exc:
                            attempts.append(failed_attempt(provider_id, str(provider_exc)[-500:], generated_utc, timezone_name=spec["timezone"]))
                    if record is None and last_candidate is not None:
                        record = last_candidate
                    if record is None:
                        raise RuntimeError("no usable direct source in validated JP/KR chain")
                    record["provider_attempts"] = attempts
                    record["direct_source_chain"] = [provider_id for provider_id, _ in direct_chain]
                    record["configured_primary"] = configured_primary
                else:
                    primary_error = ""
                    try:
                        primary = fetch_yahoo(object_id, spec, generated_utc)
                        primary_summary = provider_attempt(primary, "yahoo_chart_api", generated_utc)
                        attempts.append(primary_summary)
                        primary_freshness = primary_summary.get("freshness_status")
                        primary_open_stale = primary.get("market_phase_at_generation") == "OPEN" and primary_freshness in {"DELAYED", "STALE"}
                        if primary.get("quality_status") == "PASS" and not primary_open_stale:
                            record = primary
                            record["selection_basis"] = "configured_primary_yahoo_valid_for_current_market_phase"
                        else:
                            record = primary
                            primary_error = f"primary quality={primary.get('quality_status')} freshness={primary_freshness}"
                    except Exception as primary_exc:
                        primary_error = str(primary_exc)[-500:]
                        attempts.append(failed_attempt("yahoo_chart_api", primary_error, generated_utc, timezone_name=spec["timezone"]))

                    secid = EASTMONEY_DIRECT_FALLBACKS.get(object_id)
                    if secid and (record is None or primary_error):
                        provider_id = f"eastmoney_push2:{secid}"
                        try:
                            fallback = fetch_eastmoney_index(object_id, spec, generated_utc, secid, host="push2.eastmoney.com")
                            fallback_summary = provider_attempt(fallback, provider_id, generated_utc)
                            attempts.append(fallback_summary)
                            if fallback.get("quality_status") == "PASS" and fallback_summary.get("stable_for_10m_pulse") is True:
                                record = fallback
                                record["selected_provider_id"] = provider_id
                                record["selection_basis"] = "yahoo_primary_delayed_then_verified_eastmoney_direct_fallback"
                            elif record is None:
                                record = fallback
                        except Exception as fallback_exc:
                            attempts.append(failed_attempt(provider_id, str(fallback_exc)[-500:], generated_utc, timezone_name=spec["timezone"]))

                    if record is None:
                        raise RuntimeError(primary_error or "no usable direct source")
                    if secid:
                        record["provider_attempts"] = attempts
                        record["direct_source_chain"] = ["yahoo_chart_api", f"eastmoney_push2:{secid}"]
                        record["configured_primary"] = "yahoo_chart_api"
            else:
                attempts: list[dict] = []
                candidates: list[tuple[dict, dict]] = []
                direct_chain = [
                    ("eastmoney_push2:124.HSTECH", lambda: fetch_hstech_eastmoney(generated_utc)),
                    ("yahoo_chart_api:HSTECH.HK", lambda: fetch_yahoo(object_id, spec, generated_utc)),
                    ("hithink-finance:HS2083", lambda: fetch_hstech_hithink(generated_utc)),
                ]
                for provider_id, loader in direct_chain:
                    health_entry = provider_health.get(provider_id) or {}
                    if provider_id == "hithink-finance:HS2083" and health_entry.get("status") == "SESSION_BYPASS" and health_entry.get("bypass_date") == generated_beijing.date().isoformat():
                        attempts.append(failed_attempt(provider_id, "SESSION_BYPASS: structural provider failure memory", generated_utc))
                        continue
                    try:
                        candidate = loader()
                        summary = provider_attempt(candidate, provider_id, generated_utc)
                        attempts.append(summary)
                        candidates.append((candidate, summary))
                        provider_health[provider_id] = {**health_entry, "status": "ACTIVE", "consecutive_failures": 0, "last_success_at": generated_utc.isoformat(timespec="seconds")}
                    except Exception as provider_error:
                        reason = str(provider_error)[-500:]
                        state = {"provider_health": provider_health}
                        update_structural_health(state, provider_id, failure_class=classify_provider_failure(reason), reason=reason, now=generated_utc, threshold=2)
                        provider_health = state["provider_health"]
                        provider_health[provider_id]["bypass_date"] = generated_beijing.date().isoformat()
                        attempts.append(failed_attempt(provider_id, reason, generated_utc))

                selected = select_hstech_candidate(candidates)
                if not selected:
                    try:
                        delayed = fetch_hstech_eastmoney(generated_utc, host="push2delay.eastmoney.com")
                        delayed_summary = provider_attempt(delayed, "eastmoney_push2delay:124.HSTECH", generated_utc)
                        attempts.append(delayed_summary)
                        candidates.append((delayed, delayed_summary))
                    except Exception as delayed_error:
                        attempts.append(failed_attempt("eastmoney_push2delay:124.HSTECH", str(delayed_error)[-500:], generated_utc))
                    selected = select_hstech_candidate(candidates)

                if selected:
                    record, selected_summary, basis = selected
                    record["quality_status"] = "PASS"
                    record["selected_provider_id"] = selected_summary["provider"]
                    record["selection_basis"] = basis
                else:
                    record = {
                        "object": "HSTECH", "name": spec["name"], "reference_role": spec["role"],
                        "provider": "DIRECT_SOURCES_UNAVAILABLE", "symbol": "124.HSTECH/HSTECH.HK/HS2083",
                        "market_timezone": spec["timezone"], "market_phase_at_generation": market_phase(spec["timezone"], generated_utc),
                        "quality_status": "DEGRADED", "proxy_eligible": "ETF_PROXY_513180",
                        "error": "no direct HSTECH source met timestamp/freshness requirements",
                    }
                record["provider_attempts"] = attempts
                record["source_comparison"] = compare_hstech_attempts(attempts)
                record["direct_source_chain"] = ["eastmoney_push2:124.HSTECH", "yahoo_chart_api:HSTECH.HK", "hithink-finance:HS2083"]
                record["configured_primary"] = "eastmoney_push2:124.HSTECH"

            freshness, stable_for_pulse, delay_minutes = freshness_for_pulse(record, generated_utc)
            record["freshness_status"] = freshness
            record["delay_minutes"] = delay_minutes
            record["stable_for_pulse"] = stable_for_pulse
            if record.get("quality_status") == "PASS" and freshness in {"DELAYED", "STALE"}:
                record["quality_status"] = "DEGRADED"
                record["quality_reason"] = "provider请求成功但当前开放交易阶段数据延迟，不按实时PASS解释"
            if record.get("quality_status") == "PASS":
                pass_count += 1
            objects[object_id] = record
        except Exception as exc:
            objects[object_id] = {
                "object": object_id, "name": spec["name"], "reference_role": spec["role"],
                "provider": "eastmoney_push2/yahoo_chart_api/hithink-finance" if object_id == "HSTECH" else "yahoo_chart_api",
                "symbol": "124.HSTECH/HSTECH.HK/HS2083" if object_id == "HSTECH" else spec["symbol"],
                "market_timezone": spec["timezone"], "market_phase_at_generation": market_phase(spec["timezone"], generated_utc),
                "generated_at_beijing": generated_beijing.isoformat(timespec="seconds"), "quality_status": "FAILED",
                "error": str(exc)[-500:], "decision_note": "监测对象保留但当前数据不可用；不得用旧值冒充当前状态。",
            }

    for record in objects.values():
        raw_quality = str(record.get("quality_status") or "MISSING").upper()
        raw_freshness = str(record.get("freshness_status") or "MISSING").upper()
        record["quality_status_cn"] = QUALITY_STATUS_CN.get(raw_quality, "状态待确认")
        record["freshness_status_cn"] = FRESHNESS_STATUS_CN.get(raw_freshness, "时点待确认")
    overall = "PASS" if pass_count == len(OBJECTS) else ("DEGRADED" if pass_count else "FAILED")
    return {
        "generated_at": now_utc(),
        "generated_at_beijing": generated_beijing.isoformat(timespec="seconds"),
        "display_timezone": "Asia/Shanghai", "a_share_reference_timezone": "Asia/Shanghai",
        "scope": "FORMAL_OVERSEAS_AND_ASIA_INDEX_LAYER", "quality_status": overall,
        "quality_status_cn": QUALITY_STATUS_CN.get(overall, "状态待确认"),
        "required_objects": list(OBJECTS.keys()), "objects": objects,
        "output_time_rule": "任何正式行情输出必须明确标注数据时点，并统一优先转换为北京时间；海外对象同时保留market_timezone和market_phase。",
        "time_alignment_rule": "美国现金指数在A股交易时段通常代表上一美股交易时段；亚洲市场按同日盘中/已收盘/上一交易日分别解释。",
        "decision_boundary": "海外与亚洲指数只作风险背景、增强或反向证据；必须继续经过本地传导与目标ETF自身反馈，不能单独生成ETF买卖动作。",
        "provider_health": provider_health,
    }


def main() -> None:
    context = build()
    atomic_json_write(ROOT / "data" / "state" / "overseas_context.json", context)
    print(json.dumps({"ok": True, "generated_at_beijing": context["generated_at_beijing"], "quality_status": context["quality_status"], "objects": {k: v["quality_status"] for k, v in context["objects"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
