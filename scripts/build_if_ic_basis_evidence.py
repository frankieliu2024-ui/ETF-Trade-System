from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import zipfile
from calendar import FRIDAY, monthcalendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from state_manager import read_json
except ModuleNotFoundError:
    from scripts.state_manager import read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "data/state/if_ic_basis_5d_evidence.json"
CONCLUSION = ROOT / "research/backtests/index_futures_if_ic_basis_formal_conclusion.json"
CALENDAR = ROOT / "config/market/a_share_trading_calendar_2026.json"
PRODUCTS = {"IF": "000300", "IC": "000905"}
HEADERS = {"User-Agent": "Mozilla/5.0 ETF-Trade-System formal evidence", "Accept": "*/*"}
BJ = ZoneInfo("Asia/Shanghai")


def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _is_trading_day(d: date, calendar: dict) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(calendar.get("closed_dates") or [])


def _decision_market_date(root: Path) -> date:
    current = read_json(root / "data/state/CURRENT.json", {})
    calendar = read_json(root / CALENDAR.relative_to(ROOT), {})
    today = datetime.now(timezone.utc).astimezone(BJ).date()
    if _is_trading_day(today, calendar):
        return today
    try:
        return date.fromisoformat(str(current.get("market_date") or ""))
    except ValueError:
        d = today
        while not _is_trading_day(d, calendar):
            d -= timedelta(days=1)
        return d


def _month_keys(start: date, end: date) -> list[str]:
    y, m = start.year, start.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _third_friday(year: int, month: int) -> date:
    fridays = [week[FRIDAY] for week in monthcalendar(year, month) if week[FRIDAY]]
    return date(year, month, fridays[2])


def _contract_expiry(contract: str) -> date | None:
    match = re.fullmatch(r"(?:IF|IC)(\d{4})", contract.upper())
    if not match:
        return None
    yymm = match.group(1)
    return _third_friday(2000 + int(yymm[:2]), int(yymm[2:]))


def _read_url(url: str, timeout: int = 8) -> bytes:
    return urlopen(Request(url, headers=HEADERS), timeout=timeout).read()


def _fetch_month(ym: str, start: date, end: date) -> list[dict]:
    errors = []
    for scheme in ("https", "http"):
        url = f"{scheme}://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"
        try:
            raw = _read_url(url)
            rows = []
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name in archive.namelist():
                    match = re.fullmatch(r"(\d{8})_1\.csv", name.split("/")[-1])
                    if not match:
                        continue
                    d = datetime.strptime(match.group(1), "%Y%m%d").date()
                    if not start <= d <= end:
                        continue
                    reader = csv.reader(io.StringIO(archive.read(name).decode("gb2312", errors="ignore")))
                    next(reader, None)
                    for parts in reader:
                        if len(parts) < 10:
                            continue
                        contract = str(parts[0]).strip().upper()
                        if not re.fullmatch(r"(?:IF|IC)\d{4}", contract):
                            continue
                        close, volume, oi = _num(parts[8]), _num(parts[4]), _num(parts[6])
                        if None in (close, volume, oi):
                            continue
                        rows.append({"trade_date": d, "product": contract[:2], "contract": contract, "close": close, "volume": volume, "open_interest": oi})
            return rows
        except Exception as exc:
            errors.append(f"{scheme}:{type(exc).__name__}:{exc}")
    raise RuntimeError(f"CFFEX {ym} unavailable: {' | '.join(errors)}")


def _fetch_futures(start: date, end: date) -> list[dict]:
    rows, errors = [], []
    for ym in _month_keys(start, end):
        try:
            rows.extend(_fetch_month(ym, start, end))
        except Exception as exc:
            errors.append(str(exc))
    unique = {(x["trade_date"], x["product"], x["contract"]): x for x in rows}
    if not unique:
        raise RuntimeError("; ".join(errors) or "no CFFEX rows")
    return sorted(unique.values(), key=lambda x: (x["trade_date"], x["product"], x["contract"]))


def _fetch_spot(code: str, start: date, end: date) -> dict[date, float]:
    params = {"secid": f"1.{code}", "klt": "101", "fqt": "0", "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "lmt": "100", "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
    payload = json.loads(_read_url(url).decode("utf-8"))
    output = {}
    for line in ((payload.get("data") or {}).get("klines") or []):
        parts = str(line).split(",")
        if len(parts) < 3:
            continue
        try:
            d = date.fromisoformat(parts[0])
        except ValueError:
            continue
        close = _num(parts[2])
        if close is not None and close > 0:
            output[d] = close
    if not output:
        raise RuntimeError(f"spot history empty for {code}")
    return output


def _basis_history(rows: list[dict], spot: dict[date, float]) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = {}
    for row in rows:
        s = spot.get(row["trade_date"])
        if not s:
            continue
        output.setdefault(row["contract"], []).append({**row, "spot_close": s, "basis_close_pct": (row["close"] / s - 1.0) * 100.0})
    for series in output.values():
        series.sort(key=lambda x: x["trade_date"])
        for i, point in enumerate(series):
            point["basis_change_5d_pct_points"] = point["basis_close_pct"] - series[i - 5]["basis_close_pct"] if i >= 5 else None
    return output


def _select(product: str, mode: str, fact_date: date, rows: list[dict], history: dict[str, list[dict]]) -> dict:
    candidates = [x for x in rows if x["product"] == product and x["trade_date"] == fact_date and (_contract_expiry(x["contract"]) or fact_date) >= fact_date]
    if not candidates:
        return {"status": "DEGRADED", "selection_mode": mode, "reason": "no unexpired contract on fact date"}
    if mode == "NEAR":
        chosen = min(candidates, key=lambda x: (_contract_expiry(x["contract"]) or date.max, x["contract"]))
    else:
        chosen = max(candidates, key=lambda x: (x["open_interest"], x["volume"], x["contract"]))
    point = next((x for x in (history.get(chosen["contract"]) or []) if x["trade_date"] == fact_date), None)
    if not point or point.get("basis_change_5d_pct_points") is None:
        return {"status": "DEGRADED", "selection_mode": mode, "contract": chosen["contract"], "reason": "selected contract lacks five prior same-contract observations"}
    delta = point["basis_change_5d_pct_points"]
    return {"status": "READY", "selection_mode": mode, "contract": chosen["contract"], "expiry_date": (_contract_expiry(chosen["contract"]) or fact_date).isoformat(), "trade_date": fact_date.isoformat(), "futures_close": round(point["close"], 4), "spot_close": round(point["spot_close"], 4), "basis_close_pct": round(point["basis_close_pct"], 4), "basis_change_5d_pct_points": round(delta, 4), "direction": "STRENGTHENING" if delta > 0 else ("WEAKENING" if delta < 0 else "FLAT"), "open_interest": point["open_interest"], "volume": point["volume"]}


def build(root: Path = ROOT) -> dict:
    conclusion = read_json(root / CONCLUSION.relative_to(ROOT), {})
    base = {"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "mode": "FORMAL_IF_IC_5D_BASIS_DYNAMIC_EVIDENCE", "evidence_id": "if_ic_basis_5d", "display_name": "IF／IC 5日基差变化证据", "read_only": True, "use_as_decision_evidence": True, "can_generate_decision_independently": False, "automatic_trade": False, "trade_signal": None, "applies_to_etf_codes": ["561980", "588000", "159781", "159992", "515880", "159326"], "research_source": "research/backtests/index_futures_if_ic_basis_formal_conclusion.json", "point_in_time_rule": "只使用早于decision_market_date的完整逐合约与现货收盘；5日变化先在同一真实合约内计算，再做NEAR/PIT_MAIN选择。"}
    validated = {str(x.get("signal_id")) for x in conclusion.get("validated_signals") or [] if x.get("decision_eligible")}
    if not (conclusion.get("decision_eligible") and conclusion.get("production_context_integration") and {"IF_basis_change_5d", "IC_basis_change_5d"}.issubset(validated)):
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "formal IF/IC conversion contract not authorized"}

    decision_date = _decision_market_date(root)
    cutoff = decision_date - timedelta(days=1)
    # Only six same-contract observations are required. A bounded 28-calendar-day
    # window normally provides ~20 sessions and at most two monthly archives,
    # keeping formal context rebuilds fast. If an archive/provider is unavailable,
    # the evidence safely degrades instead of blocking the whole decision context.
    start = cutoff - timedelta(days=28)
    try:
        futures = _fetch_futures(start, cutoff)
        spots = {product: _fetch_spot(code, start, cutoff) for product, code in PRODUCTS.items()}
    except Exception as exc:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": f"dynamic input fetch failed: {type(exc).__name__}: {exc}"}
    dates = sorted({x["trade_date"] for x in futures if x["trade_date"] < decision_date})
    if not dates:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": "no PIT-usable CFFEX date"}
    fact_date = dates[-1]
    products = {}
    for product in ("IF", "IC"):
        history = _basis_history([x for x in futures if x["product"] == product], spots[product])
        near = _select(product, "NEAR", fact_date, futures, history)
        pit_main = _select(product, "PIT_MAIN", fact_date, futures, history)
        directions = [x.get("direction") for x in (near, pit_main) if x.get("status") == "READY"]
        consensus = directions[0] if len(directions) == 2 and directions[0] == directions[1] else "MIXED_OR_INCOMPLETE"
        products[product] = {"role": "PRIMARY" if product == "IF" else "SUPPLEMENTARY", "status": "READY" if near.get("status") == "READY" and pit_main.get("status") == "READY" else "DEGRADED", "near": near, "pit_main": pit_main, "consensus": consensus}
    ready = all(x.get("status") == "READY" for x in products.values())
    return {**base, "status": "READY" if ready else "DEGRADED", "use_in_current_decision": ready, "decision_market_date": decision_date.isoformat(), "fact_latest_date": fact_date.isoformat(), "provider": {"futures": "CFFEX official monthly history archive", "spot": "eastmoney_push2his CSI300/CSI500"}, "products": products, "interpretation_rule": "IF为主证据、IC为补充证据；正变化只增强、负变化只削弱。NEAR与PIT_MAIN均保留，分歧时显式保留分歧，不生成机械方向或阈值。", "rejected_or_research_only": ["IM_basis_change_5d", "IC_oi_change_1d"]}


if __name__ == "__main__":
    result = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result.get("status"), "decision_market_date": result.get("decision_market_date"), "fact_latest_date": result.get("fact_latest_date"), "IF": ((result.get("products") or {}).get("IF") or {}).get("consensus"), "IC": ((result.get("products") or {}).get("IC") or {}).get("consensus"), "trade_signal": result.get("trade_signal")}, ensure_ascii=False))
