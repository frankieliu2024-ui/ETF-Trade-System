from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import median

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build(root: Path | None = None) -> dict:
    root = root or ROOT
    current = load_json(root / "data/state/CURRENT.json")
    snapshot_path = root / str(current.get("latest_snapshot") or "")
    snapshot = load_json(snapshot_path) if snapshot_path.exists() else {}
    path_features = load_json(root / "data/state/intraday_path_features.json")
    feature_map = {str(x.get("symbol")): x for x in (path_features.get("features") or [])}
    rows = {str(x.get("symbol")): x for x in (snapshot.get("rows") or []) if x.get("quality_status") == "PASS"}

    index_items = []
    for code, name in (("000001", "上证指数"), ("399006", "创业板指")):
        row = rows.get(code) or {}
        feat = feature_map.get(code) or {}
        if not row:
            continue
        index_items.append({
            "display_name": f"{name}（{code}）",
            "code": code,
            "as_of_beijing": row.get("as_of_beijing"),
            "price": row.get("close"),
            "change_pct": _f(row.get("change_pct")),
            "day_range_position": _f(feat.get("latest_day_range_position")),
            "path_change_pct": _f(feat.get("path_change_pct")),
            "recent_slope_pct_per_10m": _f(feat.get("recent_slope_pct_per_10m")),
            "recovery_from_path_low_pct": _f(feat.get("recovery_from_path_low_pct")),
            "retreat_from_path_high_pct": _f(feat.get("retreat_from_path_high_pct")),
            "sampling_coverage": (feat.get("sampling") or {}).get("coverage"),
        })

    etf_rows = [x for x in rows.values() if x.get("asset_class") == "ETF"]
    returns = [_f(x.get("change_pct")) for x in etf_rows]
    returns = [x for x in returns if x is not None]
    positive = sum(1 for x in returns if x > 0)
    negative = sum(1 for x in returns if x < 0)
    flat = len(returns) - positive - negative
    breadth = {
        "etf_count": len(returns),
        "positive_count": positive,
        "negative_count": negative,
        "flat_count": flat,
        "positive_share": round(positive / len(returns), 4) if returns else None,
        "median_return_pct": round(median(returns), 4) if returns else None,
        "breadth_state": (
            "BROAD_RISK_ON" if returns and positive / len(returns) >= 0.7
            else "BROAD_RISK_OFF" if returns and negative / len(returns) >= 0.7
            else "MIXED_BREADTH"
        ),
    }

    sh = next((x for x in index_items if x.get("code") == "000001"), {})
    cyb = next((x for x in index_items if x.get("code") == "399006"), {})
    sh_ret = _f(sh.get("change_pct"))
    cyb_ret = _f(cyb.get("change_pct"))
    style_spread = round(cyb_ret - sh_ret, 4) if sh_ret is not None and cyb_ret is not None else None
    if style_spread is None:
        style_state = "UNKNOWN"
    elif style_spread >= 0.5:
        style_state = "GROWTH_OUTPERFORMS"
    elif style_spread <= -0.5:
        style_state = "LARGE_CAP_OUTPERFORMS"
    else:
        style_state = "BALANCED"

    if breadth["breadth_state"] == "BROAD_RISK_ON" and sh_ret is not None and cyb_ret is not None and sh_ret > 0 and cyb_ret > 0:
        regime = "BROAD_REPAIR_OR_RISK_ON"
        regime_cn = "指数与ETF宽度同步偏强，市场更接近广泛修复/风险偏好改善"
    elif breadth["breadth_state"] == "BROAD_RISK_OFF" and sh_ret is not None and cyb_ret is not None and sh_ret < 0 and cyb_ret < 0:
        regime = "BROAD_RISK_OFF"
        regime_cn = "指数与ETF宽度同步偏弱，市场更接近广泛风险收缩"
    else:
        regime = "MIXED_OR_STRUCTURAL"
        regime_cn = "指数与ETF宽度并非同向极端，市场更接近结构性分化/震荡"

    return {
        "schema_version": "1.0",
        "mode": "MARKET_LEVEL_REGIME_CONTEXT",
        "status": "READY" if len(index_items) == 2 and bool(returns) else "DEGRADED",
        "market_date": snapshot.get("market_date") or current.get("market_date"),
        "as_of_beijing": snapshot.get("captured_at_beijing") or current.get("captured_at"),
        "source_snapshot": current.get("latest_snapshot", ""),
        "indices": index_items,
        "etf_breadth": breadth,
        "style_context": {
            "chinext_minus_shanghai_pct_points": style_spread,
            "state": style_state,
            "interpretation_cn": {
                "GROWTH_OUTPERFORMS": "成长风险偏好明显强于大盘",
                "LARGE_CAP_OUTPERFORMS": "大盘/低波方向明显强于成长",
                "BALANCED": "上证与创业板差异有限，风格暂不极端",
                "UNKNOWN": "风格证据不足",
            }[style_state],
        },
        "market_regime": regime,
        "market_regime_interpretation_cn": regime_cn,
        "analysis_contract": {
            "rule": "正式盘中决策必须先完成市场层分析，再进入账户、持仓、观察ETF和现金的统一比较。",
            "required_dimensions": [
                "上证指数（000001）日内位置与路径",
                "创业板指（399006）日内位置与路径",
                "ETF全集上涨/下跌宽度与中位收益",
                "上证与创业板的风格差异",
                "必要时结合海外/亚洲反馈，但不得用陈旧海外数据替代A股自身反馈",
            ],
            "forbidden_shortcut": "只分析持仓和观察ETF、跳过市场层后直接形成主候选或金额动作",
        },
        "decision_boundary": "本文件只描述市场环境、指数路径、ETF宽度与风格，不直接生成风险许可、Trial/Confirm、金额或买卖动作。",
        "read_only": True,
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
