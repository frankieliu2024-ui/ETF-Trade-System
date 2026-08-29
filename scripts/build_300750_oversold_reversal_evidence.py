from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from state_manager import read_json
except ModuleNotFoundError:
    from scripts.state_manager import read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "data/state/300750_oversold_reversal_evidence.json"
CONCLUSION = ROOT / "research/backtests/ipo_base_stock_specific_signal_conclusion.json"
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
UA = "Mozilla/5.0 ETF-Trade-System formal research evidence"


def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _fetch_eastmoney() -> list[dict]:
    params = {
        "secid": "0.300750", "klt": "101", "fqt": "0", "beg": "0", "end": "20500101",
        "lmt": "80", "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    req = Request(EASTMONEY_URL + "?" + urlencode(params), headers={"User-Agent": UA, "Accept": "application/json"})
    payload = json.loads(urlopen(req, timeout=12).read().decode("utf-8"))
    rows = ((payload.get("data") or {}).get("klines") or [])
    out = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        close = _num(parts[2])
        if close is not None and close > 0:
            out.append({"date": parts[0], "close": close})
    out.sort(key=lambda x: x["date"])
    if not out:
        raise RuntimeError("eastmoney returned no daily bars")
    return out


def _fetch_tencent() -> list[dict]:
    sym = "sz300750"
    params = {"param": f"{sym},day,,,80,qfq"}
    req = Request(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode(params),
        headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
    )
    payload = json.loads(urlopen(req, timeout=15).read().decode("utf-8"))
    data = ((payload.get("data") or {}).get(sym) or {})
    rows = data.get("qfqday") or data.get("day") or []
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        close = _num(row[2])
        if close is not None and close > 0:
            out.append({"date": str(row[0]), "close": close})
    out.sort(key=lambda x: x["date"])
    if not out:
        raise RuntimeError("tencent returned no daily bars")
    return out


def _fetch_daily_bars() -> tuple[list[dict], str, list[str]]:
    errors = []
    try:
        return _fetch_eastmoney(), "EASTMONEY_PUSH2HIS", errors
    except Exception as exc:
        errors.append(f"eastmoney:{type(exc).__name__}:{exc}")
    try:
        return _fetch_tencent(), "TENCENT_IFZQ", errors
    except Exception as exc:
        errors.append(f"tencent:{type(exc).__name__}:{exc}")
    raise RuntimeError(" | ".join(errors))


def _latest_completed_date(root: Path, bars: list[dict]) -> str | None:
    current = read_json(root / "data/state/CURRENT.json", {})
    market_date = str(current.get("market_date") or "")
    phase = str(current.get("market_phase") or "").upper()
    if not market_date:
        return bars[-1]["date"] if bars else None
    same_day_complete = "POST_CLOSE" in phase or phase in {"CLOSE", "CLOSED"}
    eligible = [x["date"] for x in bars if x["date"] <= market_date] if same_day_complete else [x["date"] for x in bars if x["date"] < market_date]
    return eligible[-1] if eligible else None


def build(root: Path = ROOT) -> dict:
    conclusion = read_json(root / CONCLUSION.relative_to(ROOT), {})
    signal = None
    for stock in conclusion.get("stocks") or []:
        if str(stock.get("code") or "") != "300750":
            continue
        for item in stock.get("validated_signals") or []:
            if item.get("signal_id") == "OVERSOLD_REVERSAL" and item.get("decision_eligible") is True:
                signal = item
                break
    base = {
        "schema_version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "mode": "FORMAL_300750_OVERSOLD_REVERSAL_DYNAMIC_EVIDENCE",
        "evidence_id": "ipo_base_stock_oversold_reversal_300750",
        "display_name": "宁德时代（300750）超跌反转专项证据",
        "scope": ["300750"], "read_only": True, "use_as_decision_evidence": True,
        "can_generate_decision_independently": False, "automatic_trade": False, "trade_signal": None,
        "definition": "最新完整日线：过去5个交易日累计跌幅不高于-8%，且收盘价低于20日均线；参数来自已完成正式转化的固定研究定义，不在运行期重估。",
        "research_source": "research/backtests/ipo_base_stock_specific_signal_conclusion.json",
    }
    if not signal:
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "validated OVERSOLD_REVERSAL research contract not found"}
    try:
        bars, provider, provider_errors = _fetch_daily_bars()
    except Exception as exc:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "reason": f"daily history fetch failed: {type(exc).__name__}: {exc}"}
    cutoff = _latest_completed_date(root, bars)
    usable = [x for x in bars if cutoff and x["date"] <= cutoff]
    if len(usable) < 20:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "provider": provider, "provider_fallback_errors": provider_errors, "completed_bar_date": cutoff, "coverage": len(usable), "reason": "fewer than 20 completed daily closes"}

    latest = usable[-1]
    close_now = latest["close"]
    close_5_sessions_ago = usable[-6]["close"] if len(usable) >= 6 else None
    ma20 = sum(x["close"] for x in usable[-20:]) / 20.0
    ret5 = ((close_now / close_5_sessions_ago) - 1.0) * 100.0 if close_5_sessions_ago else None
    match = bool(ret5 is not None and ret5 <= -8.0 and close_now < ma20)
    return {
        **base, "status": "READY", "use_in_current_decision": True,
        "provider": provider, "provider_fallback_errors": provider_errors,
        "completed_bar_date": latest["date"], "close": round(close_now, 4),
        "close_5_sessions_ago": round(close_5_sessions_ago, 4) if close_5_sessions_ago else None,
        "return_5d_pct": round(ret5, 4) if ret5 is not None else None,
        "ma20": round(ma20, 4), "below_ma20": bool(close_now < ma20),
        "pattern_match": match, "direction": "POSITIVE" if match else "INACTIVE",
        "interpretation_rule": "pattern_match=true只表示已验证的超跌反转研究条件当前成立，可增强宁德时代持有价值/新增候选/资本比较；不得直接生成风险许可、Trial/Confirm、金额、卖出份额或订单。",
        "static_current_match_ignored": True,
    }


if __name__ == "__main__":
    result = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result.get("status"), "provider": result.get("provider"), "completed_bar_date": result.get("completed_bar_date"), "pattern_match": result.get("pattern_match"), "trade_signal": result.get("trade_signal")}, ensure_ascii=False))
