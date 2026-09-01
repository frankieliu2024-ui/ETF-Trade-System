from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "data/state/561980_component_lead_evidence.json"
FORMAL = ROOT / "research/backtests/561980_component_lead_formal_conclusion.json"
CALENDAR = ROOT / "config/market/a_share_trading_calendar_2026.json"
TARGET = "561980"
PCF_HOST = "https://static.cmfchina.com"
PCF_PATH = "/ws-business-server/fund/getEtfStockList"
SECRET = "d274d06273f96656442b0728316026a1"
ENVELOPE_REQUEST_ID = "01ab90a0d4ce45f3bb35b919099d9da1"
UA = "Mozilla/5.0 ETF-Trade-System formal-evidence"


def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def _sm3_hex(data: bytes) -> str:
    h = hashlib.new("sm3")
    h.update(data)
    return h.hexdigest()


def _sm4_encrypt_pkcs7(raw: bytes, key: bytes) -> bytes:
    pad = 16 - (len(raw) % 16)
    padded = raw + bytes([pad]) * pad
    try:
        proc = subprocess.run(
            ["openssl", "enc", "-sm4-ecb", "-K", key.hex(), "-nopad"],
            input=padded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"openssl binary unavailable: {type(exc).__name__}: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("openssl sm4 unavailable: " + proc.stderr.decode("utf-8", "ignore")[-300:])
    return proc.stdout


def _encrypted_pcf_params(payload: dict) -> tuple[dict, dict]:
    data = dict(payload)
    data["siteno"] = "web"
    data["merchantId"] = 0
    data["request_id"] = str(uuid.uuid4())
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    key = _sm3_hex(SECRET.encode("utf-8"))[:16].encode("ascii")
    encrypted = _sm4_encrypt_pkcs7(raw, key)
    b64 = base64.b64encode(encrypted).decode("ascii")
    signature_text = f"secret={SECRET}data={b64}request_id={ENVELOPE_REQUEST_ID}encrypted=true"
    signature = _sm3_hex(signature_text.encode("utf-8"))
    return (
        {"data": b64, "request_id": ENVELOPE_REQUEST_ID, "encrypted": "true"},
        {
            "User-Agent": UA,
            "Referer": "https://static.cmfchina.com/web/fundDetail/561980/",
            "tk-trans-merchant-key": "thinkive",
            "tk-trans-signature": signature,
        },
    )


def _http_json(url: str, params: dict, headers: dict, timeout: int = 20) -> dict:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + query, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_pcf(market_date: str) -> list[dict]:
    payload = {
        "startDate": market_date,
        "productCode": TARGET,
        "pageNum": 1,
        "pageSize": 1000,
        "isPreview": "0",
    }
    params, headers = _encrypted_pcf_params(payload)
    obj = _http_json(PCF_HOST + PCF_PATH, params, headers)
    data = obj.get("data") or {}
    rows = data.get("list") or []
    if obj.get("code") != 0 or len(rows) < 30:
        raise RuntimeError(f"official PCF invalid: code={obj.get('code')} rows={len(rows)}")
    out = []
    for row in rows:
        code = str(row.get("stockCode") or "")
        if len(code) != 6:
            continue
        try:
            amount = float(row.get("tdAmount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        out.append({
            "code": code,
            "name": row.get("stockName"),
            "td_amount": amount,
            "cash_flag": row.get("cashFlag"),
            "ann_date": row.get("annDate"),
        })
    if len(out) < 30:
        raise RuntimeError(f"official PCF normalized rows insufficient: {len(out)}")
    return out


def _symbol(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


def _fetch_daily_closes(code: str, start: str, end: str) -> dict[str, float]:
    sym = _symbol(code)
    params = {"param": f"{sym},day,{start},{end},80,qfq"}
    req = urllib.request.Request(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode(params),
        headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        obj = json.loads(response.read().decode("utf-8"))
    data = ((obj.get("data") or {}).get(sym) or {})
    rows = data.get("qfqday") or data.get("day") or []
    closes = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            closes[str(row[0])] = float(row[2])
        except (TypeError, ValueError):
            continue
    return closes


def _is_trading_day(d: date, closed: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in closed


def _decision_and_cutoff_dates(root: Path) -> tuple[str, str]:
    calendar = _load(root / CALENDAR.relative_to(ROOT), {})
    closed = set(calendar.get("closed_dates") or [])
    now = datetime.now(timezone(timedelta(hours=8))).date()
    decision = now
    while not _is_trading_day(decision, closed):
        decision += timedelta(days=1)
    cutoff = decision - timedelta(days=1)
    while not _is_trading_day(cutoff, closed):
        cutoff -= timedelta(days=1)
    return decision.isoformat(), cutoff.isoformat()


def _degraded(reason: str, decision_date: str | None = None, cutoff: str | None = None, **extra) -> dict:
    payload = {
        "schema_version": "1.0",
        "mode": "FORMAL_561980_COMPONENT_LEAD_EVIDENCE",
        "evidence_id": "component_lead_561980_3d",
        "display_name": "半导体设备ETF（561980）核心成分3日相对领先证据",
        "status": "DEGRADED",
        "use_in_current_decision": False,
        "use_as_decision_evidence": False,
        "decision_eligible": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "scope": ["561980"],
        "decision_market_date": decision_date,
        "price_cutoff_market_date": cutoff,
        "reason": reason,
        "failure_policy": "关键PCF或完整价格输入不可验证时不沿用旧动态值，不形成方向性证据。",
    }
    payload.update(extra)
    return payload


def build(root: Path = ROOT) -> dict:
    formal = _load(root / FORMAL.relative_to(ROOT), {})
    if not formal.get("decision_eligible") or not formal.get("production_context_integration"):
        return _degraded("formal conversion conclusion not authorized")

    decision_date, cutoff = _decision_and_cutoff_dates(root)
    try:
        pcf = _fetch_pcf(decision_date)
    except Exception as exc:
        return _degraded(
            f"official PCF unavailable: {type(exc).__name__}: {str(exc)[-240:]}",
            decision_date,
            cutoff,
            reviewed_current_top5=(formal.get("reviewed_current_top5") or {}).get("codes") or [],
        )

    ranked = sorted(pcf, key=lambda row: (row.get("td_amount", 0.0), row.get("code", "")), reverse=True)
    top5 = ranked[:5]
    codes = [row["code"] for row in top5]
    start_date = (date.fromisoformat(cutoff) - timedelta(days=20)).isoformat()
    price_maps: dict[str, dict[str, float]] = {}
    errors = []
    for code in codes + [TARGET]:
        try:
            price_maps[code] = _fetch_daily_closes(code, start_date, cutoff)
        except Exception as exc:
            errors.append({"code": code, "error": f"{type(exc).__name__}: {str(exc)[-180:]}"})
    if errors:
        return _degraded("daily price fetch failed", decision_date, cutoff, top5=codes, price_errors=errors)

    common_dates = None
    for code in codes + [TARGET]:
        dates = {d for d in price_maps[code] if d <= cutoff}
        common_dates = dates if common_dates is None else common_dates & dates
    ordered = sorted(common_dates or [])
    if len(ordered) < 4:
        return _degraded("fewer than four common completed trading closes", decision_date, cutoff, top5=codes)
    d0, d3 = ordered[-1], ordered[-4]
    if d0 != cutoff:
        return _degraded(
            f"latest common completed close {d0} does not match required cutoff {cutoff}",
            decision_date,
            cutoff,
            top5=codes,
        )

    component_returns = []
    for code in codes:
        p0, p3 = price_maps[code][d0], price_maps[code][d3]
        if p3 <= 0:
            return _degraded("invalid component base price", decision_date, cutoff, top5=codes)
        component_returns.append((p0 / p3 - 1.0) * 100.0)
    etf0, etf3 = price_maps[TARGET][d0], price_maps[TARGET][d3]
    if etf3 <= 0:
        return _degraded("invalid ETF base price", decision_date, cutoff, top5=codes)

    top5_avg = sum(component_returns) / len(component_returns)
    etf_return = (etf0 / etf3 - 1.0) * 100.0
    lead = top5_avg - etf_return
    direction = "STRENGTHEN" if lead > 0 else ("WEAKEN" if lead < 0 else "NEUTRAL")
    return {
        "schema_version": "1.0",
        "mode": "FORMAL_561980_COMPONENT_LEAD_EVIDENCE",
        "evidence_id": "component_lead_561980_3d",
        "display_name": "半导体设备ETF（561980）核心成分3日相对领先证据",
        "status": "READY",
        "use_in_current_decision": True,
        "use_as_decision_evidence": True,
        "decision_eligible": True,
        "can_generate_decision_independently": False,
        "automatic_trade": False,
        "trade_signal": None,
        "scope": ["561980"],
        "decision_market_date": decision_date,
        "price_cutoff_market_date": cutoff,
        "return_window_start": d3,
        "return_window_end": d0,
        "pcf_source": PCF_HOST + PCF_PATH,
        "pcf_component_count": len(pcf),
        "top5": [
            {
                **row,
                "three_day_return_pct": round(component_returns[idx], 4),
            }
            for idx, row in enumerate(top5)
        ],
        "top5_three_day_average_return_pct": round(top5_avg, 4),
        "etf_three_day_return_pct": round(etf_return, 4),
        "leader_minus_etf_3d_lag1_pct_points": round(lead, 4),
        "evidence_direction": direction,
        "interpretation_rule": "正值只表示核心成分相对ETF提前走强，负值只表示核心成分相对滞后；必须与561980自身价格结构、承接、相对强弱、生命周期、风险收益和其他正式证据联合判断，不设置机械阈值。",
        "point_in_time": "交易日D使用D日开盘前官方PCF；全部价格仅截止D-1完整收盘。",
        "formal_conclusion": "research/backtests/561980_component_lead_formal_conclusion.json",
    }


if __name__ == "__main__":
    payload = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload.get("status"),
        "decision_market_date": payload.get("decision_market_date"),
        "price_cutoff_market_date": payload.get("price_cutoff_market_date"),
        "top5": [x.get("code") for x in (payload.get("top5") or [])],
        "leader_minus_etf_3d_lag1_pct_points": payload.get("leader_minus_etf_3d_lag1_pct_points"),
        "reason": payload.get("reason"),
    }, ensure_ascii=False))
