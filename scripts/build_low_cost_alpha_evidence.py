from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import median

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
OUT = ROOT / "data/state/low_cost_alpha_evidence.json"
DAILY = ROOT / "events/research/daily_features"
REVIEW = ROOT / "research/backtests/low_cost_alpha_formal_conversion_review.json"
CALIBRATION = ROOT / "research/backtests/opening_residual_561980_production_calibration.json"
MARGIN_CODES = {"561980", "588000", "159781", "159992", "159326"}


def loadj(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def r4(v):
    x = num(v)
    return None if x is None else round(x, 4)


def daily_panel(market_date: str):
    rows = []
    for p in sorted(DAILY.glob("*.json")):
        d = p.stem
        if not d[:4].isdigit() or (market_date and d > market_date):
            continue
        obj = loadj(p, {})
        for x in obj.get("features") or []:
            code = str(x.get("code") or "")
            o, h, l, c, v = map(num, (x.get("open"), x.get("high"), x.get("low"), x.get("close"), x.get("volume")))
            if not code or None in (o, h, l, c, v) or o <= 0 or c <= 0:
                continue
            ret = num(x.get("close_return_pct"))
            rows.append({"date": d, "code": code, "name": x.get("name"), "open": o, "high": h, "low": l, "close": c, "volume": v, "ret": ret})
    rows.sort(key=lambda x: (x["code"], x["date"]))
    by_code = {}
    for row in rows:
        by_code.setdefault(row["code"], []).append(row)
    enriched = []
    for code, items in by_code.items():
        vols = []
        for row in items:
            prior = vols[-20:]
            row = dict(row)
            row["vol20_lag1"] = sum(prior) / len(prior) if len(prior) >= 15 else None
            row["volume_ratio"] = row["volume"] / row["vol20_lag1"] if row["vol20_lag1"] else None
            rng = row["high"] - row["low"]
            row["close_location"] = (row["close"] - row["low"]) / rng if rng > 1e-12 else 0.5
            enriched.append(row)
            vols.append(row["volume"])
    return enriched


def opening_residual(root: Path, current: dict, calibration: dict):
    snap = loadj(root / str(current.get("latest_snapshot") or ""), {}) if current.get("latest_snapshot") else {}
    row = next((x for x in (snap.get("rows") or []) if str(x.get("symbol") or "") == "561980"), {})
    overseas = loadj(root / "data/state/overseas_context.json", {})
    objects = overseas.get("objects") or {}
    ndx, sox = objects.get("NDX") or {}, objects.get("SOX") or {}

    def completed_ret(obj):
        latest = obj.get("latest") or {}
        c, pc = num(latest.get("close")), num(latest.get("previous_close"))
        if obj.get("quality_status") != "PASS" or c is None or pc in (None, 0):
            return None
        if obj.get("time_relation_to_a_share") != "PREVIOUS_US_SESSION_REFERENCE":
            return None
        return (c / pc - 1.0) * 100.0

    ndx_ret, sox_ret = completed_ret(ndx), completed_ret(sox)
    op, pc = num(row.get("open")), num(row.get("prev_close"))
    model = calibration.get("expected_gap_model") or {}
    intercept, beta = num(model.get("intercept_pct")), num(model.get("beta_us_tech_equal_1d"))
    ready = None not in (ndx_ret, sox_ret, op, pc, intercept, beta) and pc != 0
    base = {
        "evidence_id": "opening_residual_561980",
        "display_name": "半导体设备ETF（561980）海外开盘定价残差证据",
        "status": "READY" if ready else "DEGRADED",
        "use_in_current_decision": bool(ready),
        "use_as_decision_evidence": bool(ready),
        "decision_eligible": False,
        "can_generate_decision_independently": False,
        "trade_signal": None,
        "scope": ["561980"],
        "calibration_cutoff": calibration.get("data_cutoff"),
        "interpretation_rule": "正残差表示实际A股开盘相对历史SOX/NDX传导估计更低定价，负残差表示相对更高定价；仅参与半导体设备ETF机会强弱和风险收益判断，不是买入阈值。"
    }
    if not ready:
        return {**base, "reason": "requires current 561980 open/prev_close plus prior completed NDX and SOX close/previous_close"}
    us = (ndx_ret + sox_ret) / 2.0
    actual = (op / pc - 1.0) * 100.0
    expected = intercept + beta * us
    residual = expected - actual
    q = calibration.get("continuous_evidence") or {}
    zone = "UNDERPRICED_TAIL" if residual >= num(q.get("historical_residual_q67_pct")) else ("RICHLY_PRICED_TAIL" if residual <= num(q.get("historical_residual_q33_pct")) else "MID_RANGE")
    return {**base, "a_share_market_date": current.get("market_date"), "us_tech_equal_1d_pct": r4(us), "ndx_1d_pct": r4(ndx_ret), "sox_1d_pct": r4(sox_ret), "actual_open_gap_pct": r4(actual), "expected_open_gap_pct": r4(expected), "opening_underpricing_residual_pct": r4(residual), "historical_residual_zone": zone, "threshold_not_a_trade_rule": True}


def selling_exhaustion(panel: list[dict]):
    dates = sorted({x["date"] for x in panel})
    if not dates:
        return {"evidence_id": "selling_exhaustion", "status": "DEGRADED", "use_in_current_decision": False, "trade_signal": None, "reason": "no completed daily features"}
    d = dates[-1]
    hist_ratios = [x["volume_ratio"] for x in panel if x["date"] < d and x.get("volume_ratio") is not None]
    if len(hist_ratios) < 200:
        return {"evidence_id": "selling_exhaustion", "status": "DEGRADED", "use_in_current_decision": False, "trade_signal": None, "reason": "insufficient PIT volume-ratio history"}
    hist_ratios.sort()
    q33 = hist_ratios[int((len(hist_ratios) - 1) / 3)]
    items = []
    for x in panel:
        if x["date"] != d or x.get("volume_ratio") is None or x.get("ret") is None:
            continue
        match = x["ret"] < 0 and x["volume_ratio"] <= q33 and x["close_location"] >= 0.55
        items.append({"code": x["code"], "name": x.get("name"), "display_name": f"{x.get('name') or x['code']}（{x['code']}）", "close_return_pct": r4(x["ret"]), "volume_ratio_vs_prior20d": r4(x["volume_ratio"]), "historical_low_volume_q33": r4(q33), "close_location": r4(x["close_location"]), "pattern_match": bool(match)})
    return {"evidence_id": "selling_exhaustion", "display_name": "缩量下跌但收离低点证据", "status": "READY", "use_in_current_decision": True, "use_as_decision_evidence": True, "decision_eligible": False, "can_generate_decision_independently": False, "trade_signal": None, "completed_bar_date": d, "historical_low_volume_q33": r4(q33), "items": items, "matched_codes": [x["code"] for x in items if x["pattern_match"]], "interpretation_rule": "完整日线下，下跌、成交量处于历史低量环境且收盘明显离开日内低点，只表示抛压衰竭候选；必须与趋势、日内路径、承接、相对强弱和风险收益共同判断，不等于反转或买入。"}


def margin_feedback(panel: list[dict], market_date: str, margin: dict):
    dates = sorted({x["date"] for x in panel if not market_date or x["date"] < market_date})
    d = dates[-1] if dates else None
    rows = [x for x in panel if d and x["date"] == d and x.get("ret") is not None]
    med = median([x["ret"] for x in rows]) if rows else None
    state = ((margin.get("primary_evidence") or {}).get("state"))
    ready = bool(d and med is not None and margin.get("status") == "READY")
    items = []
    for x in rows:
        if x["code"] not in MARGIN_CODES:
            continue
        rel = x["ret"] - med
        items.append({"code": x["code"], "name": x.get("name"), "display_name": f"{x.get('name') or x['code']}（{x['code']}）", "prior_day_return_pct": r4(x["ret"]), "relative_to_etf_universe_median_pct_points": r4(rel), "relative_feedback": "STRONGER" if rel > 0 else "NOT_STRONGER", "conditional_support_active": bool(ready and state == "EXPANDING" and rel > 0), "evidence_strength": "MOST_STABLE" if x["code"] == "588000" else "SUPPORTED_HETEROGENEOUS"})
    return {"evidence_id": "margin_feedback_interaction", "display_name": "融资扩张×ETF相对反馈证据", "status": "READY" if ready else "DEGRADED", "use_in_current_decision": ready, "use_as_decision_evidence": ready, "decision_eligible": False, "can_generate_decision_independently": False, "trade_signal": None, "margin_fact_latest_date": margin.get("fact_latest_date"), "margin_5d_state": state, "etf_feedback_date": d, "etf_universe_median_return_pct": r4(med), "items": items, "active_support_codes": [x["code"] for x in items if x["conditional_support_active"]], "interpretation_rule": "只有融资余额5日状态为EXPANDING时，前一完整交易日相对ETF池更强才作为3—5日持续性的条件增强证据；融资非扩张只表示本交互不激活，不形成反向看空；不得机械买强。"}


def build(root: Path = ROOT):
    review = loadj(root / REVIEW.relative_to(ROOT), {})
    calibration = loadj(root / CALIBRATION.relative_to(ROOT), {})
    current = loadj(root / "data/state/CURRENT.json", {})
    if review.get("overall_status") != "PASS_WITH_SCOPED_CONVERSION" or not review.get("production_context_integration"):
        return {"schema_version": "1.0", "mode": "FORMAL_LOW_COST_ALPHA_EVIDENCE", "status": "BLOCKED", "use_in_current_decision": False, "reason": "formal conversion review not authorized", "trade_signal": None}
    panel = daily_panel(str(current.get("market_date") or ""))
    margin = loadj(root / "data/state/margin_financing_evidence.json", {})
    opening = opening_residual(root, current, calibration)
    exhaustion = selling_exhaustion(panel)
    interaction = margin_feedback(panel, str(current.get("market_date") or ""), margin)
    return {"schema_version": "1.0", "mode": "FORMAL_LOW_COST_ALPHA_EVIDENCE", "status": "READY" if any(x.get("status") == "READY" for x in (opening, exhaustion, interaction)) else "DEGRADED", "read_only": True, "use_in_current_decision": True, "automatic_trade": False, "trade_signal": None, "can_generate_decision_independently": False, "formal_conversion_review": "research/backtests/low_cost_alpha_formal_conversion_review.json", "opening_residual_561980": opening, "selling_exhaustion": exhaustion, "margin_feedback_interaction": interaction, "rejected_research": ["weak_market_resilience", "dispersion_conditioned_migration"], "decision_boundary": "三类证据只增强或削弱既有机会、持仓风险收益、金额与资本比较；不得独立生成风险许可、Trial/Confirm、金额、卖出份额或订单。"}


if __name__ == "__main__":
    payload = build(ROOT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload.get("status"), "opening": (payload.get("opening_residual_561980") or {}).get("status"), "exhaustion_matches": (payload.get("selling_exhaustion") or {}).get("matched_codes"), "margin_support": (payload.get("margin_feedback_interaction") or {}).get("active_support_codes")}, ensure_ascii=False))
