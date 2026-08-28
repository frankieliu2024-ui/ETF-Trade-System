from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data/state"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value):
    v = _f(value)
    return "数据不可用" if v is None else f"{v:+.2f}%"


def _fmt_ratio(value):
    v = _f(value)
    return "数据不可用" if v is None else f"{v:.2f}倍"


def _hm(value) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else text or "未知"


def minute_notification_note(code: str) -> str:
    code = str(code or "").split(".")[0]
    structure = _read(STATE / "market_structure_context.json")
    item = next((x for x in (structure.get("items") or []) if str(x.get("code") or "") == code), None)
    if item:
        intraday = item.get("intraday_context") or {}
        turnover = item.get("turnover_acceptance_context") or {}
        recent = turnover.get("recent_10m_marginal_acceptance") or {}
        parts = []
        low_t, high_t = intraday.get("path_low_as_of_beijing"), intraday.get("path_high_as_of_beijing")
        seq = str(intraday.get("extreme_sequence") or "")
        if low_t and high_t:
            seq_cn = "先低后高" if seq == "LOW_THEN_HIGH" else "先高后低" if seq == "HIGH_THEN_LOW" else "高低点时序未定"
            parts.append(f"1分钟路径{seq_cn}（低点{_hm(low_t)}、高点{_hm(high_t)}）")
        if recent.get("status") == "READY":
            move = _fmt_pct(recent.get("recent_10m_price_change_pct"))
            ratio = _fmt_ratio(recent.get("amount_expansion_ratio"))
            interpretation = str(recent.get("interpretation_cn") or "").strip().rstrip("。")
            parts.append(f"最近10分钟{move}，成交额为前10分钟{ratio}" + (f"，{interpretation.split('，', 2)[-1]}" if interpretation else ""))
        return "；".join(parts)

    regime = _read(STATE / "market_regime_context.json")
    idx = next((x for x in (regime.get("indices") or []) if str(x.get("code") or "") == code), None)
    if idx and str(idx.get("minute_path_source") or "") == "TENCENT_1M":
        low_t, high_t = idx.get("path_low_as_of_beijing"), idx.get("path_high_as_of_beijing")
        slope = _fmt_pct(idx.get("recent_slope_pct_per_10m"))
        path = _fmt_pct(idx.get("path_change_pct"))
        bits = [f"腾讯1分钟路径累计{path}、最近10分钟斜率{slope}"]
        if low_t and high_t:
            bits.append(f"日内低点{_hm(low_t)}、高点{_hm(high_t)}")
        return "；".join(bits)
    return ""


def enrich_market_alert_content(content: str, code: str) -> str:
    note = minute_notification_note(code)
    if not note or "分钟级结构" in str(content or ""):
        return str(content or "")
    text = str(content or "")
    bullet = f"- **分钟级结构**：{note}\n"
    markers = ("\n## 为什么重要", "\n### 为什么重要", "\n**为什么重要**", "\n为什么重要")
    for marker in markers:
        if marker in text:
            return text.replace(marker, "\n" + bullet + marker, 1)
    return text + "\n\n" + bullet.rstrip()
