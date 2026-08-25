from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
CURRENT = ROOT / "data/state/CURRENT.json"
VALIDATION = ROOT / "research/backtests/margin_share_control_validation.json"
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

A_SHARE_TARGETS = [
    {"code": "561980", "name": "半导体设备ETF"},
    {"code": "588000", "name": "科创50ETF"},
    {"code": "159781", "name": "科创创业ETF"},
    {"code": "159992", "name": "创新药ETF"},
    {"code": "515880", "name": "通信ETF"},
    {"code": "159326", "name": "电网设备ETF"},
]


def load(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def r4(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def fetch_history() -> list[dict]:
    params = {
        "reportName": "RPTA_WEB_MARGIN_DAILYTRADE",
        "columns": "STATISTICS_DATE,FIN_BALANCE,FIN_BUY_AMT",
        "pageNumber": "1",
        "pageSize": "120",
        "sortColumns": "STATISTICS_DATE",
        "sortTypes": "-1",
        "p": "1",
        "pageNo": "1",
        "pageNum": "1",
    }
    req = Request(
        URL + "?" + urlencode(params),
        headers={"User-Agent": "Mozilla/5.0 ETF-Trade-System research evidence", "Accept": "application/json"},
    )
    raw = urlopen(req, timeout=18).read().decode("utf-8")
    data = json.loads(raw)
    rows = ((data.get("result") or {}).get("data") or [])
    out = []
    for row in rows:
        ds = str(row.get("STATISTICS_DATE") or "")[:10]
        try:
            d = date.fromisoformat(ds)
            bal = float(row["FIN_BALANCE"])
            buy = float(row["FIN_BUY_AMT"])
        except (ValueError, TypeError, KeyError):
            continue
        if bal > 0 and buy >= 0:
            out.append({"date": d, "balance": bal, "buy": buy})
    out.sort(key=lambda x: x["date"])
    return out


def build(root: Path = ROOT) -> dict:
    validation = load(root / "research/backtests/margin_share_control_validation.json", {})
    base = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "mode": "VALIDATED_MARGIN_FINANCING_EVIDENCE",
        "read_only": True,
        "decision_eligible": False,
        "trade_signal": None,
        "trial_confirm": None,
        "portfolio_target": None,
        "master_override": False,
        "capital_efficiency_score": None,
    }
    if validation.get("research_interpretation") != "PROMISING_MARGIN_INCREMENT_BEYOND_SHARE_FLOW":
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "final share-flow-controlled validation not passed"}
    validated = list(validation.get("passing_signals") or [])
    required = {"margin_balance_5d_change_lag1", "margin_buy_5d_vs20_lag1"}
    if not required.issubset(set(validated)):
        return {**base, "status": "BLOCKED", "use_in_current_decision": False, "reason": "validated signal set incomplete"}

    current = load(root / "data/state/CURRENT.json", {})
    market_date_text = str(current.get("market_date") or "")
    try:
        market_date = date.fromisoformat(market_date_text)
    except ValueError:
        market_date = datetime.now(timezone.utc).date()

    try:
        history = fetch_history()
    except Exception as exc:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "market_date": market_date_text, "reason": f"margin history fetch failed: {type(exc).__name__}: {exc}"}

    # Strict PIT: trading-day T margin facts first participate from T+1.
    usable = [x for x in history if x["date"] < market_date]
    if len(usable) < 21:
        return {**base, "status": "DEGRADED", "use_in_current_decision": False, "market_date": market_date_text, "coverage": len(usable), "reason": "fewer than 21 PIT-usable observations"}

    latest = usable[-1]
    b5 = usable[-6]
    recent5 = usable[-5:]
    recent20 = usable[-20:]
    balance_5d = (latest["balance"] / b5["balance"] - 1.0) * 100.0 if b5["balance"] else None
    buy5 = sum(x["buy"] for x in recent5) / 5.0
    buy20 = sum(x["buy"] for x in recent20) / 20.0
    buy_accel = (buy5 / buy20 - 1.0) * 100.0 if buy20 else None
    age_days = (market_date - latest["date"]).days
    status = "READY" if age_days <= 4 else "DEGRADED"

    def direction(x, eps=0.02):
        if x is None:
            return "UNKNOWN"
        if x > eps:
            return "EXPANDING"
        if x < -eps:
            return "CONTRACTING"
        return "FLAT"

    return {
        **base,
        "status": status,
        "use_in_current_decision": status == "READY",
        "market_date": market_date_text,
        "provider": "Eastmoney aggregate margin daily trade",
        "provider_note": "Current evidence acquisition only; final historical independence validation used SSE+SZSE official margin history.",
        "fact_latest_date": latest["date"].isoformat(),
        "availability_rule": "交易日T的融资融券收盘事实只从下一A股交易日T+1进入决策；当前构建严格选择早于market_date的最近事实。",
        "coverage": len(usable),
        "primary_evidence": {
            "name": "融资余额5日变化",
            "signal": "margin_balance_5d_change_lag1",
            "value_pct": r4(balance_5d),
            "state": direction(balance_5d),
            "role": "PRIMARY",
        },
        "secondary_evidence": {
            "name": "融资买入5日均值相对20日均值",
            "signal": "margin_buy_5d_vs20_lag1",
            "value_pct": r4(buy_accel),
            "state": direction(buy_accel),
            "role": "SECONDARY",
        },
        "applies_to": [{**x, "display_name": f"{x['name']}（{x['code']}）"} for x in A_SHARE_TARGETS],
        "validated_reference": {
            "stage1": "research/backtests/market_breadth_margin_stage1_validation.json",
            "final": "research/backtests/margin_share_control_validation.json",
            "result": "PROMISING_MARGIN_INCREMENT_BEYOND_SHARE_FLOW",
            "controls": validation.get("controls") or ["mom5_lag1", "mom20_lag1", "reverse_share_change_5d_lag1"],
            "primary_signal": "margin_balance_5d_change_lag1",
            "secondary_signal": "margin_buy_5d_vs20_lag1",
        },
        "interpretation_rule": "作为A股风险资产ETF的市场级杠杆资金证据：融资余额5日变化为主证据，融资买入5日/20日加速度为辅助证据。只能与ETF自身结构、相对强弱、份额变化、生命周期、风险收益和替代机会共同参与机会比较；不得单独产生风险许可、Trial/Confirm、金额、降低风险或退出动作。",
    }


if __name__ == "__main__":
    out = build()
    path = ROOT / "data/state/margin_financing_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out.get("status"), "fact_latest_date": out.get("fact_latest_date"), "use_in_current_decision": out.get("use_in_current_decision")}, ensure_ascii=False))
