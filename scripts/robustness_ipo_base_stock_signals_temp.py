import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_ipo_base_stock_signals_temp as study

ROOT = Path(__file__).resolve().parents[1]
BEIJING = ZoneInfo("Asia/Shanghai")
study.STOCKS["300750"]["benchmark"] = "000001.SS"
study.STOCKS["300750"]["benchmark_name"] = "上证指数（广义A股市场控制）"
BASE = json.loads((ROOT / "research/backtests/ipo_base_stock_signal_validation.json").read_text(encoding="utf-8"))
OUT = ROOT / "research/backtests/ipo_base_stock_signal_robustness.json"


def yearly_stats(frame, mask, h, year):
    start = f"{year}-01-01"
    end = f"{year+1}-01-01"
    return study.stats(frame, mask, h, start, end)


def main():
    now = datetime.now(BEIJING)
    completed_date = now.date() - timedelta(days=1) if now.hour < 15 else now.date()
    result = {
        "schema_version": "1.0",
        "generated_at_beijing": now.isoformat(timespec="seconds"),
        "mode": "IPO_BASE_STOCK_SIGNAL_YEARLY_ROBUSTNESS",
        "point_in_time": True,
        "automatic_trade": False,
        "trade_signal": None,
        "source_validation": "research/backtests/ipo_base_stock_signal_validation.json",
        "rule": "Only signals passing the fixed primary PIT gate are checked. Robust if expected-direction mean excess is positive in at least 2 of 3 validation-year slices (2024, 2025, 2026YTD) for both 5d and 10d, and no slice with n>=4 reverses by more than 2.0pp on either horizon. No parameter retuning.",
        "signals": [],
    }
    by_code = {s["code"]: s for s in BASE["stocks"]}
    for code, cfg in study.STOCKS.items():
        validated_ids = by_code[code]["validated_signal_ids"]
        if not validated_ids:
            continue
        stock = study.yahoo_history(cfg["symbol"], completed_date)
        bench = study.yahoo_history(cfg["benchmark"], completed_date)
        frame = study.build_frame(stock, bench)
        masks = study.signal_masks(frame)
        for signal_id in validated_ids:
            direction, mask = masks[signal_id]
            sign = 1 if direction == "POSITIVE" else -1
            years = {}
            for year in (2024, 2025, 2026):
                years[str(year)] = {str(h): yearly_stats(frame, mask, h, year) for h in study.HORIZONS}
            aligned = {}
            catastrophic = []
            for h in study.HORIZONS:
                vals = []
                for year in (2024, 2025, 2026):
                    st = years[str(year)][str(h)]
                    mean = st["mean_excess_pct"]
                    if st["n"] >= 2 and mean is not None:
                        vals.append(sign * mean > 0)
                    if st["n"] >= 4 and mean is not None and sign * mean < -2.0:
                        catastrophic.append(f"{year}_H{h}_REVERSAL_GT_2PP")
                aligned[str(h)] = {"positive_direction_year_slices": int(sum(vals)), "available_year_slices": len(vals), "pass": len(vals) >= 2 and sum(vals) >= 2}
            robust = all(x["pass"] for x in aligned.values()) and not catastrophic
            result["signals"].append({
                "code": code,
                "display_name": by_code[code]["display_name"],
                "signal_id": signal_id,
                "direction": direction,
                "yearly_validation": years,
                "alignment_summary": aligned,
                "catastrophic_reversals": catastrophic,
                "robustness_pass": robust,
                "decision_eligible_after_robustness": robust,
                "automatic_trade": False,
                "trade_signal": None,
            })
    result["robust_signal_count"] = sum(1 for x in result["signals"] if x["robustness_pass"])
    result["robust_signals"] = [{"code":x["code"],"signal_id":x["signal_id"]} for x in result["signals"] if x["robustness_pass"]]
    result["conclusion"] = "Primary PIT signals with yearly robustness remain eligible as read-only execution evidence." if result["robust_signal_count"] else "Primary PIT signals did not survive yearly robustness; keep research-only and do not promote to execution evidence."
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"robust_signal_count":result["robust_signal_count"],"signals":[{"code":x["code"],"signal_id":x["signal_id"],"pass":x["robustness_pass"],"alignment":x["alignment_summary"],"catastrophic":x["catastrophic_reversals"]} for x in result["signals"]]},ensure_ascii=False))

if __name__ == "__main__":
    main()
