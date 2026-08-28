from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from build_minute_path_features import latest_snapshot, quality_requirements, universe
    from probe_index_minute_acceptance import INDEX_POC, _fetch_raw_points, acceptance_evidence
    from build_minute_path_features import fetch_one
    from state_manager import read_current
    from tencent_quote import fetch_tencent_quotes
except ModuleNotFoundError:
    from scripts.build_minute_path_features import latest_snapshot, quality_requirements, universe, fetch_one
    from scripts.probe_index_minute_acceptance import INDEX_POC, _fetch_raw_points, acceptance_evidence
    from scripts.state_manager import read_current
    from scripts.tencent_quote import fetch_tencent_quotes

BEIJING = ZoneInfo("Asia/Shanghai")
_ACTIVE_PHASES = {"CONTINUOUS_MORNING", "CONTINUOUS_AFTERNOON", "CLOSING_CALL_AUCTION", "OPENING_AUCTION"}
_CACHE: dict[tuple[str, str], dict] = {}


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _index_production_usable(item: dict, market_phase: str, requirements: dict) -> tuple[bool, str]:
    if item.get("status") != "READY":
        return False, "minute_fetch_not_ready"
    if not bool((item.get("point_in_time") or {}).get("pass")):
        return False, "point_in_time_failed"
    if not bool((item.get("sampling") or {}).get("continuity_pass")):
        return False, "continuity_failed"
    alignment = item.get("quote_alignment") or {}
    deviation = _safe_float(alignment.get("absolute_price_deviation_pct"))
    if deviation is None:
        deviation = _safe_float(alignment.get("price_deviation_pct"))
    if deviation is None or deviation > float(requirements.get("quote_price_deviation_pct", 0.25)):
        return False, "quote_price_alignment_failed"
    if market_phase in _ACTIVE_PHASES and not bool(item.get("item_quality_pass")):
        return False, "live_quality_gate_failed"
    # 收盘后允许使用同日已完成分钟路径作为结构证据；正式最新价仍来自CURRENT/quote router。
    return True, "live_quality_gate_pass" if market_phase in _ACTIVE_PHASES else "same_day_terminal_path_price_aligned"


def _acceptance_one(code: str, name: str, thscode: str, market_date: str, quote: dict) -> dict:
    points = _fetch_raw_points(thscode, market_date)
    provider_ts = quote.get("provider_timestamp_ms") if quote else None
    if provider_ts is not None:
        cutoff = datetime.fromtimestamp(float(provider_ts) / 1000.0, tz=BEIJING)
        points = [x for x in points if x["dt"] <= cutoff]
    evidence = acceptance_evidence(points)
    return {"symbol": code, "name": name, "evidence": evidence}


def build(root: Path) -> dict:
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    snapshot_rel, snapshot, formal_rows = latest_snapshot(root, current)
    market_phase = str(snapshot.get("market_phase") or current.get("market_phase") or "")
    cache_key = (market_date, snapshot_rel)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    requirements = quality_requirements(root)
    etfs = universe(root)
    objects = etfs + INDEX_POC
    quote_rows = fetch_tencent_quotes([thscode for _, _, thscode in objects], timeout=6)

    index_items: list[dict] = []
    acceptance_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for code, name, thscode in INDEX_POC:
            fut = pool.submit(fetch_one, code, name, thscode, market_date, formal_rows.get(code) or {}, quote_rows.get(thscode.upper()), requirements)
            futures[fut] = ("index", code, name)
        for code, name, thscode in etfs:
            fut = pool.submit(_acceptance_one, code, name, thscode, market_date, quote_rows.get(thscode.upper()) or {})
            futures[fut] = ("acceptance", code, name)
        for fut in as_completed(futures):
            kind, code, name = futures[fut]
            try:
                item = fut.result()
                if kind == "index":
                    item["asset_class"] = "A_SHARE_INDEX"
                    usable, reason = _index_production_usable(item, market_phase, requirements)
                    item["production_usable"] = usable
                    item["production_selection_reason"] = reason
                    index_items.append(item)
                else:
                    acceptance_items.append(item)
            except Exception as exc:
                target = {"symbol": code, "name": name, "status": "FAILED", "error": str(exc)[-500:]}
                if kind == "index":
                    target.update({"asset_class": "A_SHARE_INDEX", "production_usable": False, "production_selection_reason": "exception"})
                    index_items.append(target)
                else:
                    target["evidence"] = {"status": "FAILED", "error": str(exc)[-500:]}
                    acceptance_items.append(target)

    index_items.sort(key=lambda x: str(x.get("symbol") or ""))
    acceptance_items.sort(key=lambda x: str(x.get("symbol") or ""))
    result = {
        "market_date": market_date,
        "source_snapshot": snapshot_rel,
        "market_phase": market_phase,
        "index_items": index_items,
        "acceptance_items": acceptance_items,
        "index_usable_count": sum(1 for x in index_items if x.get("production_usable") is True),
        "acceptance_ready_count": sum(1 for x in acceptance_items if (x.get("evidence") or {}).get("status") == "READY"),
        "decision_boundary": "腾讯分钟仅增强指数日内路径与ETF最近10分钟边际承接；正式最新价仍由quote router/CURRENT决定，不产生风险许可、Trial/Confirm、金额或买卖动作。",
    }
    _CACHE[cache_key] = result
    return result


def acceptance_interpretation(evidence: dict) -> str:
    if (evidence or {}).get("status") != "READY":
        return "最近10分钟成交承接证据不可用，保留原时间归一化成交判断。"
    recent = evidence.get("recent_10m") or {}
    comp = evidence.get("comparison") or {}
    direction = str(comp.get("recent_price_direction") or "UNAVAILABLE")
    relation = str(comp.get("amount_relation") or "UNAVAILABLE")
    move = _safe_float(recent.get("price_change_pct"))
    ratio = _safe_float(comp.get("amount_expansion_ratio"))
    move_text = f"{move:+.2f}%" if move is not None else "不可用"
    ratio_text = f"{ratio:.2f}倍" if ratio is not None else "不可用"
    if direction == "UP" and relation == "EXPANDED_VS_PRIOR_10M":
        state = "上涨且成交参与扩张，短线承接增强"
    elif direction == "UP" and relation == "CONTRACTED_VS_PRIOR_10M":
        state = "上涨但成交参与收缩，跟随度有限"
    elif direction == "DOWN" and relation == "EXPANDED_VS_PRIOR_10M":
        state = "回落且成交参与扩张，短线抛压更实"
    elif direction == "DOWN" and relation == "CONTRACTED_VS_PRIOR_10M":
        state = "回落但成交参与收缩，抛压扩散有限"
    elif direction == "FLAT" and relation == "EXPANDED_VS_PRIOR_10M":
        state = "价格变化有限但成交参与扩张，换手活跃"
    elif direction == "FLAT" and relation == "CONTRACTED_VS_PRIOR_10M":
        state = "价格变化有限且成交参与收缩，交易兴趣下降"
    else:
        state = "价格与成交边际关系暂不明确"
    return f"最近10分钟{move_text}、成交额为前10分钟{ratio_text}，{state}。"
