"""Research-only Sina 15m recovery and conservative intraday T backtest.

The endpoint is queried at run time; this script creates no provider, workflow,
warehouse, production state, or formal-universe mutation.  Signals use a
completed bar close and execute at the next bar open.  A mobile inventory must
be sold before it can be bought back; no same-bar OHLC ordering is inferred.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "config/market/etf_monitor_universe.json"
OUT = ROOT / "research/backtests/etf_t_swing_intraday_sina.json"
URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
HOLDS = (1, 2, 4, 8, 999)
COSTS = (0.001, 0.002, 0.003)


def symbol(code: str, thscode: str) -> str:
    return ("sh" if thscode.endswith(".SH") else "sz") + code


def fetch(code: str, thscode: str):
    params = urllib.parse.urlencode({"symbol": symbol(code, thscode), "scale": 15, "ma": "no", "datalen": 1023})
    url = URL + "?" + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 research-only"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    match = re.search(r"_data=\((.*)\)\s*;?\s*$", raw, re.S)
    if not match:
        raise ValueError("Sina JSONP payload not recognized")
    items = json.loads(match.group(1))
    rows = []
    for x in items:
        try:
            ts = datetime.strptime(x["day"], "%Y-%m-%d %H:%M:%S")
            rows.append({"timestamp": ts.isoformat(), "date": ts.date().isoformat(), "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]), "close": float(x["close"]), "volume": float(x.get("volume") or 0), "amount": float(x.get("amount") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda x: x["timestamp"])
    return url, rows


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def run_day(rows, hold, cost, threshold=0.01, trend_guard=False, multiple=False):
    if len(rows) < 12:
        return {"status": "INSUFFICIENT_EVIDENCE", "observations": len(rows)}
    first = rows[0]["open"]
    core_units, mobile_units, cash = 0.75 / first, 0.25 / first, 0.25
    # cash is released mobile value; initial mobile is invested, not cash.
    cash = 0.0
    state = "MOBILE_INVESTED"
    release = None
    events, turnover = [], 0.0
    right, wrong = [], []
    i = 8
    while i < len(rows) - 1:
        r = rows[i]
        mean = sum(x["close"] for x in rows[max(0, i - 8):i]) / min(8, i)
        dev = r["close"] / mean - 1.0
        action = None
        if state == "MOBILE_INVESTED" and dev >= threshold and (not trend_guard or r["close"] <= rows[i - 3]["close"] * (1 + threshold)):
            action = "release"
        elif state == "MOBILE_CASH" and i - release["i"] >= hold and dev <= -threshold:
            action = "rebuy"
        if action:
            ex = rows[i + 1]
            px = ex["open"]
            if action == "release":
                value = mobile_units * px
                fee = value * cost / 2
                cash = value - fee
                mobile_units = 0.0
                state = "MOBILE_CASH"
                release = {"i": i + 1, "price": px, "time": ex["timestamp"], "loss": 0.0}
                turnover += value
                events.append({"release_signal_time": r["timestamp"], "release_execution_time": ex["timestamp"], "release_price": px, "transaction_cost": fee, "completed_cycle": False})
            else:
                fee = cash * cost / 2
                mobile_units = max(0.0, (cash - fee) / px)
                cash = 0.0
                state = "MOBILE_INVESTED"
                release["rebuy_time"], release["rebuy_price"] = ex["timestamp"], px
                release["transaction_cost"] = release.get("transaction_cost", 0.0) + fee
                release["gross_spread"] = release["price"] / px - 1.0
                release["net_spread"] = release["gross_spread"] - cost
                release["right_tail_loss"] = release["loss"]
                release["holding_bars"] = i + 1 - release["i"]
                release["completed_cycle"] = True
                events[-1].update({"rebuy_signal_time": r["timestamp"], "rebuy_execution_time": ex["timestamp"], "rebuy_price": px, "gross_spread": release["gross_spread"], "net_spread": release["net_spread"], "holding_cash_bars": release["holding_bars"], "right_tail_loss": release["right_tail_loss"], "completed_cycle": True})
                turnover += mobile_units * px
                release = None
            i += 1
            if not multiple and action == "rebuy":
                break
            continue
        if state == "MOBILE_CASH" and release:
            loss = 0.25 * max(0.0, r["close"] / release["price"] - 1.0)
            release["loss"] = max(release["loss"], loss)
        i += 1
    if state == "MOBILE_CASH" and release:
        release["unclosed_at_day_end"] = True
        events[-1]["unclosed_at_day_end"] = True
    for e in events:
        e.setdefault("unclosed_at_day_end", False)
        if "loss" not in e and e.get("completed_cycle") is not True:
            e["right_tail_loss"] = release.get("loss", 0.0) if release else 0.0
    for e in events:
        if e.get("completed_cycle"):
            after = [x["close"] for x in rows if x["timestamp"] >= e["rebuy_execution_time"]]
            e["wrong_rebuy_loss"] = 0.25 * max([0.0] + [1 - x / e["rebuy_price"] for x in after])
            wrong.append(e["wrong_rebuy_loss"])
        if "right_tail_loss" in e:
            right.append(e["right_tail_loss"])
    final = core_units * rows[-1]["close"] + mobile_units * rows[-1]["close"] + cash
    bh = rows[-1]["close"] / first - 1.0
    return {"net_return": final - 1.0, "relative_to_buy_and_hold_alpha": final - 1.0 - bh, "trades": sum(1 for e in events for k in ("release_execution_time", "rebuy_execution_time") if k in e), "turnover": turnover, "events": events, "right_tail_loss": {"max_single_episode_loss": max(right, default=0.0), "mean_episode_loss": sum(right) / len(right) if right else 0.0, "median_episode_loss": median(right), "cumulative_portfolio_drag": sum(right)}, "wrong_rebuy_loss": {"max_single_episode_loss": max(wrong, default=0.0), "mean_episode_loss": sum(wrong) / len(wrong) if wrong else 0.0, "median_episode_loss": median(wrong), "cumulative_portfolio_drag": sum(wrong)}}


def main():
    objects = json.loads(UNIVERSE.read_text(encoding="utf-8"))["objects"]
    result = {"provider": "SINA_15M_RESEARCH_ONLY_NOT_PRODUCTION_PROVIDER", "endpoint": URL, "requested_datalen": 1023, "bar_interval": "15m", "timezone": "Asia/Shanghai", "execution": "completed_bar_close_signal_then_next_bar_open", "same_bar_high_low_order_used": False, "objects": {}, "coverage": {}}
    for obj in objects:
        code = str(obj["code"])
        try:
            url, rows = fetch(code, obj["thscode"])
            days = defaultdict(list)
            for row in rows:
                days[row["date"]].append(row)
            per_cost = {}
            families = {}
            for family, guard, multiple in (("no_trend_filter", False, False), ("trend_filter", True, False), ("multiple_cycles_control", False, True)):
                families[family] = {}
                for cost in COSTS:
                    ckey = f"{int(cost * 10000)}bp"
                    families[family][ckey] = {}
                    for hold in HOLDS:
                        samples = [run_day(day, hold, cost, multiple=multiple, trend_guard=guard) for day in days.values()]
                        valid = [x for x in samples if x.get("net_return") is not None]
                        families[family][ckey][str(hold)] = {"day_count": len(valid), "mean_net_alpha": sum(x["relative_to_buy_and_hold_alpha"] for x in valid) / len(valid) if valid else 0.0, "mean_trades": sum(x["trades"] for x in valid) / len(valid) if valid else 0.0, "unclosed_cycles": sum(1 for x in valid for e in x["events"] if e.get("unclosed_at_day_end")), "right_tail_cumulative": sum(x["right_tail_loss"]["cumulative_portfolio_drag"] for x in valid), "wrong_rebuy_cumulative": sum(x["wrong_rebuy_loss"]["cumulative_portfolio_drag"] for x in valid)}
            for cost in COSTS:
                key = f"{int(cost * 10000)}bp"
                per_cost[key] = {}
                for hold in HOLDS:
                    samples = [run_day(day, hold, cost, multiple=False) for day in days.values()]
                    valid = [x for x in samples if x.get("net_return") is not None]
                    alpha = sum(x["relative_to_buy_and_hold_alpha"] for x in valid) / len(valid) if valid else 0.0
                    per_cost[key][str(hold)] = {"day_count": len(valid), "mean_net_alpha": alpha, "mean_trades": sum(x["trades"] for x in valid) / len(valid) if valid else 0.0, "unclosed_cycles": sum(1 for x in valid for e in x["events"] if e.get("unclosed_at_day_end")), "right_tail_cumulative": sum(x["right_tail_loss"]["cumulative_portfolio_drag"] for x in valid), "wrong_rebuy_cumulative": sum(x["wrong_rebuy_loss"]["cumulative_portfolio_drag"] for x in valid)}
            result["objects"][code] = {"name": obj["name"], "url": url, "bars": len(rows), "sample_start": rows[0]["timestamp"] if rows else None, "sample_end": rows[-1]["timestamp"] if rows else None, "trading_days": len(days), "initial_total_etf_exposure": 1.0, "mobile_initial_state": "MOBILE_INVESTED", "execution_constraints": {"signal": "completed_15m_bar_close", "fill": "next_15m_bar_open", "same_bar_high_low_order_used": False, "t1_newly_bought_inventory_sold_same_day": False, "unclosed_cycle_explicit": True}, "events": per_cost.get("20bp", {}).get("4", {}), "cost_sensitivity": per_cost, "strategy_families": families}
        except Exception as exc:
            result["objects"][code] = {"name": obj["name"], "status": "FETCH_FAILED", "error": str(exc)}
    ok = [x for x in result["objects"].values() if x.get("bars", 0)]
    result["coverage"] = {"objects_success": len(ok), "objects_total": len(objects), "object_coverage": f"{len(ok)}/{len(objects)}", "trading_days_min": min((x["trading_days"] for x in ok), default=0), "trading_days_max": max((x["trading_days"] for x in ok), default=0), "historical_15m_backtest_executed": bool(ok), "research_only": True}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["coverage"], ensure_ascii=False))


if __name__ == "__main__":
    main()
