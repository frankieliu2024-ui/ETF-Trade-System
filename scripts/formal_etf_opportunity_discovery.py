from __future__ import annotations

import csv
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
SPOT_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"
FIELDS = "f2,f3,f6,f7,f8,f10,f12,f13,f14,f15,f16,f17,f18,f24,f25,f26,f124"
MIN_AMOUNT = 10_000_000.0
MAX_OBSERVATION_INPUTS_PER_FAMILY = 4
MAX_OBSERVATION_CANDIDATES = 12
MIN_HISTORY = 65


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _request_json(url: str, params: dict[str, Any], timeout: int = 12) -> dict[str, Any]:
    req = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _spot_row(raw: dict[str, Any]) -> dict[str, Any]:
    code = str(raw.get("f12") or "")
    return {
        "code": code,
        "name": str(raw.get("f14") or code),
        "market_id": int(_num(raw.get("f13")) or (1 if code.startswith("5") else 0)),
        "price": _num(raw.get("f2")),
        "change_pct": _num(raw.get("f3")),
        "amount": _num(raw.get("f6")),
        "amplitude_pct": _num(raw.get("f7")),
        "turnover_pct": _num(raw.get("f8")),
        "volume_ratio": _num(raw.get("f10")),
        "high": _num(raw.get("f15")),
        "low": _num(raw.get("f16")),
        "open": _num(raw.get("f17")),
        "prev_close": _num(raw.get("f18")),
        "return_60d_pct": _num(raw.get("f24")),
        "return_ytd_pct": _num(raw.get("f25")),
        "listing_date": str(raw.get("f26") or ""),
        "provider_timestamp": raw.get("f124"),
    }


def fetch_broad_etf_spot() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        payload = _request_json(SPOT_URL, {
            "pn": page, "pz": 100, "po": 0, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2,
            "fid": "f12", "fs": ETF_FS, "fields": FIELDS,
        })
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        if total is None:
            total = int(data.get("total") or 0)
        if not diff:
            break
        rows.extend(_spot_row(x) for x in diff if isinstance(x, dict))
        if (total and len(rows) >= total) or len(diff) < 100:
            break
        page += 1
        if page > 50:
            break
    return rows


def _secid(code: str, market_id: int | None = None) -> str:
    market = market_id if market_id in {0, 1} else (1 if str(code).startswith("5") else 0)
    return f"{market}.{code}"


def fetch_daily_history(code: str, market_id: int, end_date: str, limit: int = 90) -> list[dict[str, Any]]:
    payload = _request_json(HISTORY_URL, {
        "secid": _secid(code, market_id),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 0, "end": end_date.replace("-", ""), "lmt": limit,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
    })
    klines = ((payload.get("data") or {}).get("klines") or [])
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        rows.append({
            "date": parts[0], "open": _num(parts[1]), "close": _num(parts[2]),
            "high": _num(parts[3]), "low": _num(parts[4]),
            "volume": _num(parts[5]), "amount": _num(parts[6]),
        })
    return [x for x in rows if x["date"] and x["close"] is not None]



def _history_snapshot_path(root: Path, market_date: str) -> Path:
    return root / "data/market/discovery_history" / f"{market_date}.json"


def load_discovery_history_snapshot(root: Path, market_date: str) -> dict[str, list[dict[str, Any]]]:
    path = _history_snapshot_path(root, market_date)
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if str(obj.get("market_date") or "") != market_date:
        return {}
    histories = obj.get("histories") or {}
    return {str(code): rows for code, rows in histories.items() if isinstance(rows, list) and len(rows) >= MIN_HISTORY}


def persist_discovery_history_snapshot(root: Path, market_date: str, histories: dict[str, list[dict[str, Any]]]) -> None:
    path = _history_snapshot_path(root, market_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "market_date": market_date,
        "semantics": "NODE_LOCAL_REUSABLE_COMPLETED_HISTORY_INPUT_NOT_MANAGEMENT_STATE_NOT_TRADE_AUTHORITY",
        "histories": histories,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def load_validated_history(root: Path, code: str, market_date: str, limit: int = 90) -> list[dict[str, Any]] | None:
    result_dir = root / "data/market/on_demand/results"
    best: tuple[str, Path] | None = None
    for path in result_dir.glob(f"*_{code}_*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not obj.get("ok") or obj.get("asset_type") != "etf" or obj.get("mode") != "history":
            continue
        last_date, dataset = str(obj.get("last_date") or ""), obj.get("dataset")
        # A dataset may include the decision market-date bar. Discovery itself
        # filters rows to date < market_date, so such a dataset remains PIT-safe
        # and is preferable to a redundant network repair.
        if not dataset or not last_date:
            continue
        candidate = root / str(dataset)
        if candidate.exists() and (best is None or last_date > best[0]):
            best = (last_date, candidate)
    if best is None:
        return None
    rows: list[dict[str, Any]] = []
    with best[1].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("date") or "") >= market_date:
                continue
            close = _num(row.get("close"))
            if close is None:
                continue
            rows.append({"date": str(row["date"]), "open": _num(row.get("open")), "high": _num(row.get("high")), "low": _num(row.get("low")), "close": close, "volume": _num(row.get("volume")), "amount": _num(row.get("amount"))})
    return rows[-limit:] if len(rows) >= MIN_HISTORY else None


def _ret(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return closes[-1] / closes[-sessions - 1] - 1.0


def _max_drawdown(closes: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return abs(worst)


def classify_states(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [float(x["close"]) for x in rows if _num(x.get("close")) is not None]
    if len(closes) < MIN_HISTORY:
        return []
    states: list[dict[str, Any]] = []
    returns = {w: _ret(closes, w) for w in (20, 40, 60)}
    positives = [w for w, value in returns.items() if value is not None and value > 0]
    drawdown = _max_drawdown(closes[-61:])
    if len(positives) >= 2 and drawdown <= 0.12:
        states.append({"state": "PERSISTENT_TREND", "evidence": {
            "window_returns": {str(k): round(v, 6) if v is not None else None for k, v in returns.items()},
            "positive_windows": positives, "max_drawdown_60": round(drawdown, 6),
        }})

    short_ret, long_ret = _ret(closes, 10), _ret(closes, 40)
    if short_ret is not None and long_ret is not None:
        gap = short_ret - long_ret * 10 / 40
        if gap >= 0.05:
            states.append({"state": "TREND_CHANGE", "evidence": {
                "direction": "STRENGTHENING", "return_10": round(short_ret, 6),
                "return_40": round(long_ret, 6), "short_vs_long_normalized_gap": round(gap, 6),
            }})

    window = closes[-41:]
    if len(window) == 41:
        prior, current = window[:-1], window[-1]
        prior_high, prior_low = max(prior), min(prior)
        if prior_high > 0:
            prior_drawdown = 1.0 - prior_low / prior_high
            recovered = (current - prior_low) / max(prior_high - prior_low, 1e-12)
            if prior_drawdown >= 0.08 and recovered >= 0.65 and current >= prior_high * 0.985:
                states.append({"state": "RECOVERY_BREAKOUT", "evidence": {
                    "prior_drawdown": round(prior_drawdown, 6),
                    "recovery_fraction": round(recovered, 6),
                    "distance_to_prior_high": round(current / prior_high - 1.0, 6),
                }})
    return states


def _state_names(rows: list[dict[str, Any]]) -> set[str]:
    return {str(x.get("state")) for x in classify_states(rows)}


def _potential_families(row: dict[str, Any]) -> list[str]:
    if (_num(row.get("amount")) or 0) < MIN_AMOUNT or (_num(row.get("price")) or 0) <= 0:
        return []
    r60 = _num(row.get("return_60d_pct"))
    daily = _num(row.get("change_pct"))
    vr = _num(row.get("volume_ratio"))
    families = []
    if r60 is not None and r60 > 0:
        families.append("PERSISTENT_TREND")
    if daily is not None and daily > 0 and (r60 is None or r60 < 15):
        families.append("TREND_CHANGE")
    if r60 is not None and r60 <= 0 and daily is not None and daily > 0 and (vr is None or vr >= 1.0):
        families.append("RECOVERY_BREAKOUT")
    return families


def _bounded_prefilter(rows: list[dict[str, Any]], held_codes: set[str] | None = None) -> list[dict[str, Any]]:
    held_codes = {str(x) for x in (held_codes or set())}
    buckets: dict[str, list[dict[str, Any]]] = {x: [] for x in ("PERSISTENT_TREND", "TREND_CHANGE", "RECOVERY_BREAKOUT")}
    for row in rows:
        if str(row.get("code") or "") in held_codes:
            continue
        for family in _potential_families(row):
            buckets[family].append(row)
    selected: dict[str, dict[str, Any]] = {}
    for family, members in buckets.items():
        members.sort(key=lambda x: (-(float(x.get("amount") or 0)), str(x.get("code") or "")))
        for item in members[:MAX_OBSERVATION_INPUTS_PER_FAMILY]:
            selected.setdefault(str(item["code"]), item)
    return list(selected.values())


def _candidate(row: dict[str, Any], history: list[dict[str, Any]], market_date: str) -> dict[str, Any] | None:
    completed = [x for x in history if str(x.get("date") or "") < market_date]
    if len(completed) < MIN_HISTORY:
        return None
    current_states = classify_states(completed)
    previous_states = _state_names(completed[:-1]) if len(completed) > MIN_HISTORY else set()
    entered = sorted({x["state"] for x in current_states} - previous_states)
    aligned = [x for x in current_states if x["state"] in set(_potential_families(row))]
    if not entered and not aligned:
        return None
    avg_amount = sum(float(x.get("amount") or 0) for x in completed[-20:]) / min(20, len(completed))
    return {
        "code": row["code"], "name": row["name"], "display_name": f'{row["name"]}（{row["code"]}）',
        "category": "OBSERVATION_EVALUATION_INPUT", "eligibility": "OBSERVATION_FULL_EVALUATION",
        "management_identity": None, "auto_promote_to_observation": False,
        "trial_confirm_permission": False, "trade_signal": None, "decision_output_generated": False,
        "discovery_semantic": "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT",
        "entered_states": entered, "surfaced_states": current_states,
        "discovery_spot": {k: row.get(k) for k in (
            "price", "change_pct", "amount", "amplitude_pct", "turnover_pct", "volume_ratio",
            "high", "low", "open", "prev_close", "return_60d_pct", "return_ytd_pct", "provider_timestamp"
        )},
        "historical_context": {
            "status": "READY", "as_of": completed[-1]["date"], "sample_count": len(completed),
            "avg_amount_20": round(avg_amount, 2),
        },
        "comparison_basis": ["历史趋势/状态变化", "当前结构", "成交与可执行性", "风险收益", "资本效率"],
        "decision_boundary": "仅取得本节点Observation资格完整评估；Observation身份只能由同节点正式决策ADMIT/RETAIN形成，发现本身不产生Trial/Confirm、金额或交易动作。",
    }



def attach_formal_quotes(discovery: dict[str, Any], market_quote: dict[str, Any]) -> dict[str, Any]:
    quotes = {}
    for quote in market_quote.get("quotes") or []:
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or quote.get("code") or "").upper().replace(".SH", "").replace(".SZ", "")
        if symbol:
            quotes[symbol] = quote
    candidates = []
    for item in discovery.get("candidates") or []:
        code = str(item.get("code") or "").upper()
        quote = quotes.get(code)
        quality = str((quote or {}).get("quality_status") or "").upper()
        usable = bool(quote and quality not in {"", "FAILED", "FAIL", "STALE", "INVALID"})
        candidates.append({
            **item,
            "formal_quote": quote or {},
            "formal_quote_status": "READY" if usable else "UNAVAILABLE",
            "formal_quote_rule": "发现源只负责缩小评估对象；正式当前行情必须由现有market_quote_router对象级补采链取得。",
        })
    return {**discovery, "candidates": candidates, "formal_quote_coverage": sum(x["formal_quote_status"] == "READY" for x in candidates)}


def discover_formal_candidates(
    root: Path,
    *,
    market_date: str,
    managed_codes: set[str],
    held_codes: set[str] | None = None,
    spot_rows: list[dict[str, Any]] | None = None,
    history_by_code: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    generated = datetime.now(BEIJING).isoformat(timespec="seconds")
    try:
        broad = list(spot_rows) if spot_rows is not None else fetch_broad_etf_spot()
    except Exception as exc:
        return {
            "status": "DEGRADED", "generated_at_beijing": generated, "source": "EASTMONEY_BROAD_ETF_SPOT",
            "broad_universe_count": 0, "candidates": [], "error": str(exc)[-500:],
            "decision_boundary": "广域发现失败不删除持仓/观察ETF，也不阻塞其现有正式MASTER链。",
        }
    held_codes = {str(x) for x in (held_codes or set())}
    prefiltered = _bounded_prefilter(broad, held_codes)
    candidates = []
    failures = []
    node_history = load_discovery_history_snapshot(root, market_date) if history_by_code is None else {}
    snapshot_dirty = False
    history_attempted = 0
    history_succeeded = 0
    history_reused = 0
    history_repair_attempted = 0
    started = time.monotonic()
    for row in prefiltered:
        code = str(row["code"])
        history_attempted += 1
        try:
            history = (history_by_code or {}).get(code) if history_by_code is not None else node_history.get(code)
            history_source = "INJECTED" if history_by_code is not None and history is not None else ("DISCOVERY_NODE_HISTORY" if history is not None else None)
            if history_source == "DISCOVERY_NODE_HISTORY":
                history_reused += 1
            if history is None:
                history = load_validated_history(root, code, market_date, 90)
                history_source = "VALIDATED_EXISTING_HISTORY" if history is not None else None
                if history is not None:
                    history_reused += 1
                    node_history[code] = history
                    snapshot_dirty = True
            if history is None:
                history_repair_attempted += 1
                history = fetch_daily_history(code, int(row.get("market_id") or 0), market_date, 90)
                history_source = "EASTMONEY_BOUNDED_REPAIR"
                node_history[code] = history
                snapshot_dirty = True
            history_succeeded += 1
            item = _candidate(row, history, market_date)
            if item:
                item["history_source"] = history_source
            if item:
                code = str(item.get("code") or "")
                item["management_identity"] = "MANAGED" if code in managed_codes else None
                item["discovery_semantic"] = (
                    "NODE_LOCAL_ALL_MARKET_OPPORTUNITY_SIGNAL_FOR_EXISTING_MANAGED_ETF"
                    if code in managed_codes
                    else "NODE_LOCAL_OBSERVATION_EVALUATION_INPUT"
                )
                candidates.append(item)
        except Exception as exc:
            failures.append({"code": code, "error": str(exc)[-300:]})
    if history_by_code is None and snapshot_dirty:
        persist_discovery_history_snapshot(root, market_date, node_history)
    elapsed = round(time.monotonic() - started, 3)
    state_priority = {"RECOVERY_BREAKOUT": 0, "TREND_CHANGE": 1, "PERSISTENT_TREND": 2}
    def key(item: dict[str, Any]) -> tuple:
        states = item.get("entered_states") or [x.get("state") for x in item.get("surfaced_states") or []]
        priority = min((state_priority.get(str(x), 9) for x in states), default=9)
        amount = float((item.get("historical_context") or {}).get("avg_amount_20") or 0)
        return (priority, -amount, str(item.get("code") or ""))
    candidates.sort(key=key)
    candidates = candidates[:MAX_OBSERVATION_CANDIDATES]
    return {
        "schema_version": "1.0", "status": "READY" if broad and not failures else "DEGRADED",
        "generated_at_beijing": generated, "market_date": market_date,
        "source": "EASTMONEY_BROAD_ETF_SPOT_PLUS_OBJECT_DAILY_HISTORY",
        "source_role": "DISCOVERY_ONLY; formal trade decision remains MASTER-owned",
        "broad_universe_count": len(broad),
        "managed_identity_count": sum(1 for x in broad if x.get("code") in managed_codes),
        "held_identity_count": sum(1 for x in broad if x.get("code") in held_codes),
        "managed_excluded_count": 0,
        "history_prefilter_count": len(prefiltered), "candidate_count": len(candidates),
        "history_attempted_count": history_attempted, "history_succeeded_count": history_succeeded,
        "history_reused_count": history_reused, "history_repair_attempted_count": history_repair_attempted,
        "history_failure_count": len(failures), "history_elapsed_seconds": elapsed,
        "coverage_status": "COMPLETE" if not failures else ("UNAVAILABLE" if history_succeeded == 0 else "PARTIAL"),
        "history_failures": failures, "candidates": candidates,
        "observation_capacity": {"target_typical": "5-10", "allowed_min": 0, "resource_protection_max": MAX_OBSERVATION_CANDIDATES},
        "selection_contract": {
            "all_market_boundary": True,
            "no_gain_ranking": True,
            "no_hidden_score": True,
            "prefilter": "multi-horizon state-family plausibility plus minimum executability; liquidity only bounds provider work within each state family",
            "final_ingress": "validated interpretable state evidence; at most a small node-local set enters full formal evaluation",
        },
        "decision_boundary": "发现对象是本节点临时正式评估输入，不是第三种ETF身份；不得自动写观察池、生成Trial/Confirm或交易动作。",
    }
