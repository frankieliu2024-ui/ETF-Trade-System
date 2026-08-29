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
UA = "Mozilla/5.0 ETF-Trade-System formal evidence"
BJ = ZoneInfo("Asia/Shanghai")


def _num(v):
    try:
        x = float(v)
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
        d = date.fromisoformat(str(current.get("market_date") or ""))
        if _is_trading_day(d, calendar):
            return d
    except ValueError:
        pass
    d = today
    while not _is_trading_day(d, calendar):
        d -= timedelta(days=1)
    return d


def _http(url: str, timeout: int) -> bytes:
    return urlopen(Request(url, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=timeout).read()


def _parse_cffex(raw: bytes, d: date) -> list[dict]:
    reader = csv.reader(io.StringIO(raw.decode("gb2312", errors="ignore")))
    next(reader, None)
    out = []
    for p in reader:
        if len(p) < 10:
            continue
        contract = str(p[0]).strip().upper()
        if not re.fullmatch(r"(?:IF|IC)\d{4}", contract):
            continue
        close, volume, oi = _num(p[8]), _num(p[4]), _num(p[6])
        if None in (close, volume, oi):
            continue
        out.append({"trade_date": d, "product": contract[:2], "contract": contract, "close": close, "volume": volume, "open_interest": oi})
    return out


def _daily_url(d: date) -> str:
    ds = d.strftime("%Y%m%d")
    return f"https://www.cffex.com.cn/fzjy/mrhq/{d.year:04d}/{d.month:02d}/{ds}_1.csv"


def _trading_days(start: date, end: date, calendar: dict) -> list[date]:
    out = []
    d = start
    while d <= end:
        if _is_trading_day(d, calendar):
            out.append(d)
        d += timedelta(days=1)
    return out


def _month_keys(start: date, end: date) -> list[str]:
    y, m = start.year, start.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _fetch_month(ym: str, start: date, end: date) -> list[dict]:
    raw = _http(f"https://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip", 10)
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            m = re.fullmatch(r"(\d{8})_1\.csv", name.split("/")[-1])
            if not m:
                continue
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
            if start <= d <= end:
                out.extend(_parse_cffex(z.read(name), d))
    return out


def _fetch_futures(start: date, end: date, calendar: dict) -> tuple[list[dict], str, list[str]]:
    """Bounded official fetch: probe one daily file; if unavailable, jump to monthly archive.

    We need only six same-contract observations. Repeatedly timing out on every historical
    day provides no extra information once the official daily endpoint is unavailable.
    """
    days = _trading_days(start, end, calendar)
    if not days:
        raise RuntimeError("no trading days in IF/IC evidence window")
    errors: list[str] = []
    rows: list[dict] = []

    probe = days[-1]
    try:
        probe_rows = _parse_cffex(_http(_daily_url(probe), 4), probe)
        if not probe_rows:
            raise RuntimeError("official daily csv empty")
        rows.extend(probe_rows)
        for d in reversed(days[:-1]):
            if len({x["trade_date"] for x in rows}) >= 8:
                break
            try:
                rows.extend(_parse_cffex(_http(_daily_url(d), 4), d))
            except Exception as exc:
                errors.append(f"daily {d}:{type(exc).__name__}:{exc}")
        if len({x["trade_date"] for x in rows}) >= 6:
            uniq = {(x["trade_date"], x["product"], x["contract"]): x for x in rows}
            return sorted(uniq.values(), key=lambda x: (x["trade_date"], x["product"], x["contract"])), "CFFEX_OFFICIAL_DAILY_CSV", errors
    except Exception as exc:
        errors.append(f"daily_probe {probe}:{type(exc).__name__}:{exc}")

    rows = []
    for ym in _month_keys(start, end):
        try:
            rows.extend(_fetch_month(ym, start, end))
        except Exception as exc:
            errors.append(f"monthly {ym}:{type(exc).__name__}:{exc}")
    uniq = {(x["trade_date"], x["product"], x["contract"]): x for x in rows}
    if not uniq:
        raise RuntimeError("; ".join(errors) or "CFFEX official history unavailable")
    return sorted(uniq.values(), key=lambda x: (x["trade_date"], x["product"], x["contract"])), "CFFEX_MONTHLY_ARCHIVE_FALLBACK", errors


def _fetch_spot_eastmoney(code: str, start: date, end: date) -> dict[date, float]:
    p = {"secid": f"1.{code}", "klt": "101", "fqt": "0", "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "lmt": "100", "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
    obj = json.loads(_http("https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(p), 5).decode("utf-8"))
    out = {}
    for line in ((obj.get("data") or {}).get("klines") or []):
        parts = str(line).split(",")
        if len(parts) >= 3:
            try:
                d = date.fromisoformat(parts[0])
            except ValueError:
                continue
            close = _num(parts[2])
            if close and close > 0:
                out[d] = close
    if not out:
        raise RuntimeError("eastmoney spot empty")
    return out


def _fetch_spot_tencent(code: str, start: date, end: date) -> dict[date, float]:
    sym = "sh" + code
    obj = json.loads(_http("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode({"param": f"{sym},day,{start.isoformat()},{end.isoformat()},100,qfq"}), 6).decode("utf-8"))
    data = ((obj.get("data") or {}).get(sym) or {})
    out = {}
    for row in (data.get("qfqday") or data.get("day") or []):
        if isinstance(row, list) and len(row) >= 3:
            try:
                d = date.fromisoformat(str(row[0]))
            except ValueError:
                continue
            close = _num(row[2])
            if close and close > 0:
                out[d] = close
    if not out:
        raise RuntimeError("tencent spot empty")
    return out


def _fetch_spot(code: str, start: date, end: date) -> tuple[dict[date, float], str, list[str]]:
    errors = []
    try:
        return _fetch_spot_eastmoney(code, start, end), "EASTMONEY_PUSH2HIS", errors
    except Exception as exc:
        errors.append(f"eastmoney:{type(exc).__name__}:{exc}")
    try:
        return _fetch_spot_tencent(code, start, end), "TENCENT_IFZQ", errors
    except Exception as exc:
        errors.append(f"tencent:{type(exc).__name__}:{exc}")
    raise RuntimeError(" | ".join(errors))


def _third_friday(y: int, m: int) -> date:
    fs = [w[FRIDAY] for w in monthcalendar(y, m) if w[FRIDAY]]
    return date(y, m, fs[2])


def _expiry(contract: str) -> date | None:
    m = re.fullmatch(r"(?:IF|IC)(\d{4})", contract)
    if not m:
        return None
    yy, mm = int(m.group(1)[:2]), int(m.group(1)[2:])
    return _third_friday(2000 + yy, mm)


def _basis_history(rows: list[dict], spot: dict[date, float]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        s = spot.get(r["trade_date"])
        if s:
            out.setdefault(r["contract"], []).append({**r, "spot_close": s, "basis_close_pct": (r["close"] / s - 1.0) * 100.0})
    for series in out.values():
        series.sort(key=lambda x: x["trade_date"])
        for i, x in enumerate(series):
            x["basis_change_5d_pct_points"] = x["basis_close_pct"] - series[i - 5]["basis_close_pct"] if i >= 5 else None
    return out


def _select(product: str, mode: str, fact_date: date, rows: list[dict], history: dict[str, list[dict]]) -> dict:
    c = [x for x in rows if x["product"] == product and x["trade_date"] == fact_date and (_expiry(x["contract"]) or fact_date) >= fact_date]
    if not c:
        return {"status": "DEGRADED", "selection_mode": mode, "reason": "no unexpired contract"}
    chosen = min(c, key=lambda x: (_expiry(x["contract"]) or date.max, x["contract"])) if mode == "NEAR" else max(c, key=lambda x: (x["open_interest"], x["volume"], x["contract"]))
    point = next((x for x in history.get(chosen["contract"], []) if x["trade_date"] == fact_date), None)
    if not point or point.get("basis_change_5d_pct_points") is None:
        return {"status": "DEGRADED", "selection_mode": mode, "contract": chosen["contract"], "reason": "selected contract lacks five prior same-contract observations"}
    delta = point["basis_change_5d_pct_points"]
    return {"status": "READY", "selection_mode": mode, "contract": chosen["contract"], "trade_date": fact_date.isoformat(), "basis_close_pct": round(point["basis_close_pct"], 4), "basis_change_5d_pct_points": round(delta, 4), "direction": "STRENGTHENING" if delta > 0 else "WEAKENING" if delta < 0 else "FLAT", "open_interest": point["open_interest"], "volume": point["volume"]}


def build(root: Path = ROOT) -> dict:
    conclusion = read_json(root / CONCLUSION.relative_to(ROOT), {})
    base = {"schema_version": "1.2", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "mode": "FORMAL_IF_IC_5D_BASIS_DYNAMIC_EVIDENCE", "evidence_id": "if_ic_basis_5d", "display_name": "IF／IC 5日基差变化证据", "read_only": True, "use_as_decision_evidence": True, "can_generate_decision_independently": False, "automatic_trade": False, "trade_signal": None, "applies_to_etf_codes": ["561980", "588000", "159781", "159992", "515880", "159326"], "research_source": "research/backtests/index_futures_if_ic_basis_formal_conclusion.json"}
    valid = {str(x.get("signal_id")) for x in conclusion.get("validated_signals") or [] if x.get("decision_eligible")}
    if not (conclusion.get("decision_eligible") and conclusion.get("production_context_integration") and {"IF_basis_change_5d", "IC_basis_change_5d"}.issubset(valid)):
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "formal IF/IC conversion contract not authorized"}

    decision_date = _decision_market_date(root)
    calendar = read_json(root / CALENDAR.relative_to(ROOT), {})
    cutoff = decision_date - timedelta(days=1)
    while not _is_trading_day(cutoff, calendar):
        cutoff -= timedelta(days=1)
    start = cutoff - timedelta(days=28)
    try:
        futures, fp, ferr = _fetch_futures(start, cutoff, calendar)
        spots, sprov, serr = {}, {}, {}
        for product, code in PRODUCTS.items():
            values, provider, errors = _fetch_spot(code, start, cutoff)
            spots[product], sprov[product], serr[product] = values, provider, errors
    except Exception as exc:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": f"dynamic input fetch failed: {type(exc).__name__}: {exc}"}

    dates = sorted({x["trade_date"] for x in futures if x["trade_date"] <= cutoff})
    if not dates:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "decision_market_date": decision_date.isoformat(), "reason": "no PIT-usable CFFEX date"}
    fact_date = dates[-1]
    products = {}
    for product in ("IF", "IC"):
        hist = _basis_history([x for x in futures if x["product"] == product], spots[product])
        near = _select(product, "NEAR", fact_date, futures, hist)
        pit = _select(product, "PIT_MAIN", fact_date, futures, hist)
        dirs = [x.get("direction") for x in (near, pit) if x.get("status") == "READY"]
        products[product] = {"role": "PRIMARY" if product == "IF" else "SUPPLEMENTARY", "status": "READY" if near.get("status") == pit.get("status") == "READY" else "DEGRADED", "near": near, "pit_main": pit, "consensus": dirs[0] if len(dirs) == 2 and dirs[0] == dirs[1] else "MIXED_OR_INCOMPLETE"}
    ready = all(x["status"] == "READY" for x in products.values())
    return {**base, "status": "READY" if ready else "DEGRADED", "use_in_current_decision": ready, "decision_market_date": decision_date.isoformat(), "fact_latest_date": fact_date.isoformat(), "provider": {"futures": fp, "spot": sprov}, "provider_diagnostics": {"futures_errors": ferr[-4:], "spot_errors": serr}, "products": products, "interpretation_rule": "IF为主证据、IC为补充证据；正变化只增强、负变化只削弱，NEAR与PIT_MAIN分歧时显式保留分歧，不生成机械方向。"}


if __name__ == "__main__":
    result = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result.get("status"), "fact_latest_date": result.get("fact_latest_date"), "provider": result.get("provider"), "trade_signal": result.get("trade_signal")}, ensure_ascii=False))
