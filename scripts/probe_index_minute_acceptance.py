from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_minute_path_features import (
    ROOT,
    UA,
    fetch_one,
    latest_snapshot,
    pct,
    provider_symbol,
    quality_requirements,
    round4,
    universe,
)
from state_manager import now_utc, read_current
from tencent_quote import fetch_tencent_quotes

BEIJING = ZoneInfo("Asia/Shanghai")
INDEX_POC = [
    ("000001", "上证指数", "000001.SH"),
    ("399006", "创业板指", "399006.SZ"),
    ("000688", "科创50指数", "000688.SH"),
]


def _fetch_raw_points(thscode: str, market_date: str) -> list[dict]:
    symbol = provider_symbol(thscode)
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?" + urllib.parse.urlencode({"code": symbol, "r": str(time.time())})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/", "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if payload.get("code") != 0:
        raise RuntimeError(f"provider code={payload.get('code')} msg={payload.get('msg')}")
    node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
    date_text = str(node.get("date") or "")
    points = []
    for row in node.get("data") or []:
        parts = str(row).split()
        if len(parts) < 4:
            continue
        dt = datetime.strptime(f"{date_text} {parts[0]}", "%Y%m%d %H%M").replace(tzinfo=BEIJING)
        if dt.date().isoformat() != market_date:
            continue
        minute = dt.hour * 60 + dt.minute
        if not (570 <= minute <= 690 or 780 <= minute <= 900):
            continue
        points.append({"dt": dt, "price": float(parts[1]), "cum_volume": float(parts[2]), "cum_amount": float(parts[3])})
    points.sort(key=lambda x: x["dt"])
    return points


def _point_at_or_before(points: list[dict], target_ts: float) -> dict | None:
    eligible = [x for x in points if x["dt"].timestamp() <= target_ts]
    return eligible[-1] if eligible else None


def acceptance_evidence(points: list[dict]) -> dict:
    if len(points) < 3:
        return {"status": "MISSING", "reason": "insufficient minute points"}
    latest = points[-1]
    ref10 = _point_at_or_before(points, latest["dt"].timestamp() - 600)
    ref20 = _point_at_or_before(points, latest["dt"].timestamp() - 1200)
    if not ref10 or not ref20:
        return {"status": "MISSING", "reason": "insufficient 20-minute history"}

    recent_amount = latest["cum_amount"] - ref10["cum_amount"]
    prior_amount = ref10["cum_amount"] - ref20["cum_amount"]
    recent_volume = latest["cum_volume"] - ref10["cum_volume"]
    prior_volume = ref10["cum_volume"] - ref20["cum_volume"]
    recent_move = pct(latest["price"], ref10["price"])
    prior_move = pct(ref10["price"], ref20["price"])
    amount_ratio = recent_amount / prior_amount if prior_amount > 0 else None
    volume_ratio = recent_volume / prior_volume if prior_volume > 0 else None

    if amount_ratio is None:
        amount_relation = "UNAVAILABLE"
    elif amount_ratio > 1.0:
        amount_relation = "EXPANDED_VS_PRIOR_10M"
    elif amount_ratio < 1.0:
        amount_relation = "CONTRACTED_VS_PRIOR_10M"
    else:
        amount_relation = "UNCHANGED_VS_PRIOR_10M"

    if recent_move is None:
        price_direction = "UNAVAILABLE"
    elif recent_move > 0:
        price_direction = "UP"
    elif recent_move < 0:
        price_direction = "DOWN"
    else:
        price_direction = "FLAT"

    return {
        "status": "READY",
        "window_end_beijing": latest["dt"].isoformat(timespec="seconds"),
        "recent_10m": {
            "start_beijing": ref10["dt"].isoformat(timespec="seconds"),
            "price_change_pct": round4(recent_move),
            "amount_delta": round4(recent_amount),
            "volume_delta": round4(recent_volume),
        },
        "prior_10m": {
            "start_beijing": ref20["dt"].isoformat(timespec="seconds"),
            "end_beijing": ref10["dt"].isoformat(timespec="seconds"),
            "price_change_pct": round4(prior_move),
            "amount_delta": round4(prior_amount),
            "volume_delta": round4(prior_volume),
        },
        "comparison": {
            "amount_expansion_ratio": round4(amount_ratio),
            "volume_expansion_ratio": round4(volume_ratio),
            "amount_relation": amount_relation,
            "recent_price_direction": price_direction,
        },
        "interpretation_boundary": "仅描述最近10分钟价格方向及成交额/量相对前10分钟是否扩张；不设买卖阈值、不形成评分、不独立产生Trial/Confirm或卖出动作。",
    }


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    market_date = str(current.get("market_date") or "")
    snapshot_rel, _, formal_rows = latest_snapshot(root, current)
    requirements = quality_requirements(root)
    etfs = universe(root)
    all_objects = etfs + INDEX_POC
    quote_rows = fetch_tencent_quotes([thscode for _, _, thscode in all_objects], timeout=6)

    index_results = []
    for code, name, thscode in INDEX_POC:
        try:
            item = fetch_one(code, name, thscode, market_date, formal_rows.get(code) or {}, quote_rows.get(thscode.upper()), requirements)
            item["asset_class"] = "A_SHARE_INDEX"
            item["poc_role"] = "INDEX_MINUTE_POC_ONLY_NOT_PRODUCTION"
            index_results.append(item)
        except Exception as exc:
            index_results.append({"symbol": code, "name": name, "asset_class": "A_SHARE_INDEX", "status": "FAILED", "item_quality_pass": False, "error": str(exc)[-500:]})

    acceptance_results = []
    for code, name, thscode in etfs:
        try:
            points = _fetch_raw_points(thscode, market_date)
            quote = quote_rows.get(thscode.upper()) or {}
            provider_ts = quote.get("provider_timestamp_ms")
            if provider_ts is not None:
                cutoff = datetime.fromtimestamp(float(provider_ts) / 1000.0, tz=BEIJING)
                points = [x for x in points if x["dt"] <= cutoff]
            acceptance_results.append({"symbol": code, "name": name, "evidence": acceptance_evidence(points)})
        except Exception as exc:
            acceptance_results.append({"symbol": code, "name": name, "evidence": {"status": "FAILED", "error": str(exc)[-500:]}})

    index_ready = [x for x in index_results if x.get("item_quality_pass") is True]
    acceptance_ready = [x for x in acceptance_results if (x.get("evidence") or {}).get("status") == "READY"]
    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "generated_at_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "market_date": market_date,
        "source_snapshot": snapshot_rel,
        "mode": "TENCENT_INDEX_MINUTE_AND_10M_ACCEPTANCE_POC",
        "read_only": True,
        "decision_boundary": "PoC结果只验证指数分钟覆盖和近10分钟承接证据的数据可用性；不进入正式production selection，不改变MASTER、风险许可、机会状态、金额或交易权限。",
        "index_minute_poc": {
            "target_count": len(INDEX_POC),
            "quality_pass_count": len(index_ready),
            "all_pass": len(index_ready) == len(INDEX_POC),
            "items": sorted(index_results, key=lambda x: x.get("symbol", "")),
        },
        "ten_minute_acceptance_poc": {
            "target_count": len(etfs),
            "ready_count": len(acceptance_ready),
            "all_ready": len(acceptance_ready) == len(etfs),
            "design": {
                "primary_comparison": "最近10分钟 vs 紧邻前10分钟",
                "facts": ["price_change_pct", "amount_delta", "volume_delta", "amount_expansion_ratio", "volume_expansion_ratio"],
                "no_hidden_score": True,
                "no_trade_signal": True,
            },
            "items": sorted(acceptance_results, key=lambda x: x.get("symbol", "")),
        },
    }


def main() -> None:
    payload = build(ROOT)
    target = ROOT / "data/state/minute_index_acceptance_poc.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "index_all_pass": (payload.get("index_minute_poc") or {}).get("all_pass"),
        "index_quality_pass_count": (payload.get("index_minute_poc") or {}).get("quality_pass_count"),
        "acceptance_all_ready": (payload.get("ten_minute_acceptance_poc") or {}).get("all_ready"),
        "acceptance_ready_count": (payload.get("ten_minute_acceptance_poc") or {}).get("ready_count"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
